"""The small window you actually watch while farming.

It owns a Bot instance, polls its status on the Tk main loop, and keeps the
global hotkeys working so the keyboard still wins if the mouse is busy.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .. import runner as botmod
from .. import report as states
from .. import errors, inputs
from ..errors import FlyfouError
from ..hotkeys import HotkeyManager
from ..profile import Profile
from . import theme
from .widgets import Card, Chip

_STATE_COLORS = {
    states.SEARCHING: theme.ACCENT,
    states.FIGHTING: theme.GOOD,
    states.RETURNING: theme.WARN,
    states.PAUSED: theme.MUTED,
    states.WAITING: theme.WARN,
    states.STOPPED: theme.MUTED,
}

_POLL_MS = 150
_LOG_LINES = 6


def _clock(seconds: float) -> str:
    seconds = int(max(0.0, seconds))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class ControlPanel(tk.Toplevel):
    def __init__(self, master, profile: Profile, hwnd: Optional[int] = None,
                 on_closed: Optional[Callable[[], None]] = None):
        super().__init__(master)
        self.profile = profile
        self.on_closed = on_closed
        self.bot = botmod.Bot(profile, hwnd=hwnd)
        self.hotkeys = HotkeyManager()
        self._cursor = 0
        self._poll_job: Optional[str] = None
        self._closed = False

        self.title(f"Flyfou — {profile.name}")
        self.configure(bg=theme.BG)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._build()
        self._place_top_right()
        self._start_bot()
        self._bind_hotkeys()
        self._poll()

    # ---- layout ----------------------------------------------------------- #

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        head = Card(outer)
        head.pack(fill="x")
        self.state_label = ttk.Label(head.body, text=states.STOPPED, style="Panel.TLabel",
                                     font=theme.FONT_TITLE)
        self.state_label.pack(anchor="w")
        self.window_chip = Chip(head.body, "Looking for the game window…", "info", panel=True,
                                wrap=300)
        self.window_chip.pack(anchor="w", pady=(2, 0))

        stats = Card(outer)
        stats.pack(fill="x", pady=(10, 0))
        grid = stats.body
        for column in range(3):
            grid.columnconfigure(column, weight=1, uniform="stat")
        self.kills_value = self._stat(grid, 0, "Kills")
        self.runtime_value = self._stat(grid, 1, "Session")
        self.rate_value = self._stat(grid, 2, "Kills/hr")

        health = Card(outer)
        health.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(health.body, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Your HP", style="PanelMuted.TLabel").pack(side="left")
        self.player_value = ttk.Label(row, text="—", style="Panel.TLabel", font=theme.FONT_BOLD)
        self.player_value.pack(side="right")
        self.player_bar = ttk.Progressbar(health.body, style="Good.Horizontal.TProgressbar",
                                          maximum=100, length=316)
        self.player_bar.pack(fill="x", pady=(4, 0))

        if True:        # there is always a target bar now: it is a number
            row2 = ttk.Frame(health.body, style="Panel.TFrame")
            row2.pack(fill="x", pady=(10, 0))
            ttk.Label(row2, text="Target HP", style="PanelMuted.TLabel").pack(side="left")
            self.target_value = ttk.Label(row2, text="—", style="Panel.TLabel", font=theme.FONT_BOLD)
            self.target_value.pack(side="right")
            self.target_bar = ttk.Progressbar(health.body, style="Panel.Horizontal.TProgressbar",
                                              maximum=100, length=316)
            self.target_bar.pack(fill="x", pady=(4, 0))
        else:
            self.target_value = None
            self.target_bar = None

        self.log = tk.Text(outer, height=_LOG_LINES, width=46, bg="#101116", fg=theme.MUTED,
                           font=theme.FONT_MONO, relief="flat", wrap="word", state="disabled",
                           highlightthickness=1, highlightbackground=theme.LINE, padx=8, pady=6)
        self.log.pack(fill="x", pady=(10, 0))
        self.log.tag_configure("info", foreground=theme.MUTED)
        self.log.tag_configure("good", foreground=theme.GOOD)
        self.log.tag_configure("warn", foreground=theme.WARN)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 0))
        self.primary = ttk.Button(buttons, text="Start", style="Accent.TButton", command=self._primary)
        self.primary.pack(side="left", fill="x", expand=True)
        self.stop_button = ttk.Button(buttons, text="Stop", style="Danger.TButton", command=self._stop)
        self.stop_button.pack(side="left", padx=(8, 0))

        self.hotkey_chip = Chip(outer, "", "info", wrap=330)
        self.hotkey_chip.pack(anchor="w", pady=(8, 0))

    def _stat(self, grid: ttk.Frame, column: int, caption: str) -> ttk.Label:
        cell = ttk.Frame(grid, style="Panel.TFrame")
        cell.grid(row=0, column=column, sticky="w")
        value = ttk.Label(cell, text="0", style="Huge.TLabel")
        value.pack(anchor="w")
        ttk.Label(cell, text=caption, style="PanelMuted.TLabel").pack(anchor="w")
        return value

    def _place_top_right(self) -> None:
        self.update_idletasks()
        x = max(0, self.winfo_screenwidth() - self.winfo_reqwidth() - 28)
        self.geometry(f"+{x}+40")

    # ---- bot -------------------------------------------------------------- #

    def _start_bot(self) -> None:
        try:
            self.bot.start()
        except FlyfouError as exc:
            self._append(exc.full(), "warn")
            self.primary.configure(state="disabled")

    def _bind_hotkeys(self) -> None:
        keys = self.profile.hotkeys
        failure = self.hotkeys.register({
            keys.pause: self.bot.pause,
            keys.resume: self.bot.resume,
            keys.stop: lambda: self.bot.stop("Kill switch pressed."),
        })
        if failure:
            self.hotkey_chip.set(errors.hotkeys_unavailable(failure), "warn")
        else:
            self.hotkey_chip.set(
                f"{inputs.display_name(keys.pause)} pause · "
                f"{inputs.display_name(keys.resume)} resume · "
                f"{inputs.display_name(keys.stop)} stop — these work from inside the game.",
                "info",
            )

    def _primary(self) -> None:
        if not self.bot.is_running():
            self._cursor = 0
            self._start_bot()
            return
        self.bot.toggle()

    def _stop(self) -> None:
        if self.bot.is_running():
            self.bot.stop("Stopped from the panel.")
        else:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        self.hotkeys.clear()
        self.bot.stop()
        inputs.release_all()
        self.destroy()
        if self.on_closed:
            self.on_closed()

    # ---- polling ---------------------------------------------------------- #

    def _poll(self) -> None:
        status = self.bot.snapshot()
        self._render(status)
        lines, self._cursor = self.bot.reporter.read(self._cursor)
        for line in lines:
            self._append(f"{line.clock}  {line.text}", line.level)
        self._poll_job = self.after(_POLL_MS, self._poll)

    def _render(self, status: states.Status) -> None:
        self.state_label.configure(text=status.state,
                                   foreground=_STATE_COLORS.get(status.state, theme.FG))
        self.kills_value.configure(text=str(status.kills))
        self.runtime_value.configure(text=_clock(status.runtime))
        rate = status.kills_per_hour
        self.rate_value.configure(text=f"{rate:.0f}" if rate else "—")

        self._render_bar(self.player_bar, self.player_value, status.player_hp, colour_coded=True)
        if self.target_bar is not None:
            self._render_bar(self.target_bar, self.target_value, status.target_hp)

        if not status.running:
            self.window_chip.set("Not running.", "info")
        elif not status.window_found:
            self.window_chip.set("Can't see the game window right now.", "bad")
        elif not status.window_focused and not status.paused:
            self.window_chip.set("Game is behind another window — click it to let Flyfou act.", "warn")
        else:
            width, height = status.client_size
            size = f" ({width}×{height})" if width else ""
            self.window_chip.set(f"Game window found{size}.", "good")

        if not status.running:
            self.primary.configure(text="Start again", style="Accent.TButton")
            self.stop_button.configure(text="Close")
        else:
            self.primary.configure(text="Start" if status.paused else "Pause",
                                   style="Accent.TButton" if status.paused else "TButton")
            self.stop_button.configure(text="Stop")

    def _render_bar(self, bar: ttk.Progressbar, label: ttk.Label,
                    fraction: Optional[float], colour_coded: bool = False) -> None:
        if fraction is None:
            label.configure(text="—")
            bar.configure(value=0)
            return
        label.configure(text=f"{fraction:.0%}")
        bar.configure(value=fraction * 100)
        if colour_coded:
            critical = self.profile.rest_below
            if fraction <= critical:
                bar.configure(style="Bad.Horizontal.TProgressbar")
            elif fraction <= critical * 2:
                bar.configure(style="Warn.Horizontal.TProgressbar")
            else:
                bar.configure(style="Good.Horizontal.TProgressbar")

    def _append(self, text: str, level: str = "info") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", level)
        excess = int(self.log.index("end-1c").split(".")[0]) - 200
        if excess > 0:
            self.log.delete("1.0", f"{excess}.0")
        self.log.see("end")
        self.log.configure(state="disabled")
