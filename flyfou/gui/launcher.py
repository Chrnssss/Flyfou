"""The first window: pick a profile, check it still fits the game, start."""

from __future__ import annotations

import datetime
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import List, Optional

from .. import VERSION, errors, winutil
from ..errors import FlyfouError
from ..profile import Profile, ProfileStore
from . import imaging, theme
from .panel import ControlPanel
from .widgets import Card, Chip
from .wizard import SetupWizard

_THUMB = (84, 84)
_WINDOW_CHECK_MS = 2000


class Launcher(tk.Tk):
    def __init__(self, store: ProfileStore):
        super().__init__()
        self.store = store
        self.profile: Optional[Profile] = None
        self._images = imaging.ImageHolder()
        self._panel: Optional[ControlPanel] = None
        self._found: Optional[winutil.WindowInfo] = None

        self.title("Flyfou")
        theme.apply(self)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._quit)

        self._build()
        self._reload()
        self._check_window()

    # ---- layout ----------------------------------------------------------- #

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="Flyfou", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text=f"v{VERSION}", style="Muted.TLabel").pack(side="right", pady=(6, 0))
        ttk.Label(outer, text="Pick a profile, make sure the game is running, then start.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        missing = errors.missing_dependencies()
        self._crippled = bool(missing)
        if missing:
            warning = Card(outer)
            warning.pack(fill="x", pady=(14, 0))
            ttk.Label(warning.body, text=errors.dependency_report(missing),
                      style="Panel.TLabel", foreground=theme.BAD, justify="left").pack(anchor="w")

        picker = ttk.Frame(outer)
        picker.pack(fill="x", pady=(16, 0))
        ttk.Label(picker, text="Profile", style="TLabel").pack(anchor="w")
        row = ttk.Frame(picker)
        row.pack(fill="x", pady=(4, 0))
        self.choice = tk.StringVar()
        self.combo = ttk.Combobox(row, textvariable=self.choice, state="readonly", width=34)
        self.combo.pack(side="left", fill="x", expand=True)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._select())
        ttk.Button(row, text="New…", command=self._new).pack(side="left", padx=(8, 0))

        tools = ttk.Frame(picker)
        tools.pack(fill="x", pady=(8, 0))
        self.edit_button = ttk.Button(tools, text="Set up", command=self._edit)
        self.edit_button.pack(side="left")
        self.copy_button = ttk.Button(tools, text="Duplicate", command=self._duplicate)
        self.copy_button.pack(side="left", padx=(8, 0))
        self.delete_button = ttk.Button(tools, text="Delete", style="Danger.TButton",
                                        command=self._delete)
        self.delete_button.pack(side="left", padx=(8, 0))

        details = Card(outer, "This profile")
        details.pack(fill="x", pady=(16, 0))
        self.thumbs = ttk.Frame(details.body, style="Panel.TFrame")
        self.thumbs.pack(fill="x")
        self.summary = ttk.Label(details.body, text="", style="PanelMuted.TLabel", justify="left",
                                 wraplength=430)
        self.summary.pack(anchor="w", pady=(10, 0))
        self.fit_chip = Chip(details.body, "", "info", panel=True, wrap=430)
        self.fit_chip.pack(anchor="w", pady=(8, 0))

        self.ready_chip = Chip(outer, "", "info", wrap=460)
        self.ready_chip.pack(anchor="w", pady=(14, 0))

        self.start_button = ttk.Button(outer, text="Start farming", style="Accent.TButton",
                                       command=self._start)
        self.start_button.pack(fill="x", pady=(6, 0))

        ttk.Label(outer, text=f"Profiles are stored in {self.store.root}",
                  style="Muted.TLabel").pack(anchor="w", pady=(12, 0))

    # ---- profile list ----------------------------------------------------- #

    def _reload(self, select: Optional[str] = None) -> None:
        names = self.store.names()
        self.combo.configure(values=names)
        if names:
            if select not in names:
                select = self.choice.get() if self.choice.get() in names else names[0]
            self.choice.set(select)
        else:
            self.choice.set("")
        self._select()

    def _select(self) -> None:
        name = self.choice.get()
        if not name:
            self.profile = None
            self._render_empty()
            return
        try:
            self.profile = self.store.load(name)
        except FlyfouError as exc:
            self.profile = None
            self.summary.configure(text=exc.full())
            self.ready_chip.set("That profile couldn't be opened.", "bad")
            self.start_button.configure(state="disabled")
            return
        self._render(self.profile)
        self._check_window()

    def _render_empty(self) -> None:
        for child in self.thumbs.winfo_children():
            child.destroy()
        self.summary.configure(
            text="No profiles yet.\n\nPress New… and Flyfou will walk you through pointing it at "
                 "the game, showing it a monster, and marking the HP bars. It takes a few minutes."
        )
        self.fit_chip.set("", "info")
        self.ready_chip.set("Create a profile to get going.", "info")
        self.start_button.configure(state="disabled")
        for button in (self.edit_button, self.copy_button, self.delete_button):
            button.configure(state="disabled")

    def _render(self, profile: Profile) -> None:
        for button in (self.edit_button, self.copy_button, self.delete_button):
            button.configure(state="normal")

        for child in self.thumbs.winfo_children():
            child.destroy()
        self._images.clear()
        for index, ref in enumerate(profile.monsters[:5]):
            self._thumbnail(profile, ref, f"m{index}", "Monster")
        if profile.home:
            self._thumbnail(profile, profile.home, "home", "Home")
        if not profile.monsters and not profile.home:
            ttk.Label(self.thumbs, text="Nothing captured yet.", style="PanelMuted.TLabel").pack(anchor="w")

        keys = [skill.key.upper() for skill in profile.skills]
        rotation = " → ".join(keys) if keys else "none"
        attack = profile.attack_key.upper() if profile.attack_key else "click only"
        lines = [
            f"Game window:  {profile.window_title or profile.window_process or 'not set'}",
            f"Captured at:  {_size_text(profile.client_size)}",
            f"Match threshold:  {profile.match_threshold:.2f}",
            f"Attack:  {attack}    Skills:  {rotation}",
            f"Pause below:  {profile.critical_fraction:.0%} HP",
            f"Created:  {_date_text(profile.created)}",
        ]
        self.summary.configure(text="\n".join(lines))

        problems = profile.problems()
        if problems:
            self.ready_chip.set("Not finished: " + problems[0] + "  (press Set up)", "warn")
            self.start_button.configure(state="disabled")
        elif self._crippled:
            self.ready_chip.set("Missing components — see above.", "bad")
            self.start_button.configure(state="disabled")
        else:
            self.ready_chip.set("Ready to farm.", "good")
            self.start_button.configure(state="normal")

    def _thumbnail(self, profile: Profile, ref, key: str, caption: str) -> None:
        cell = ttk.Frame(self.thumbs, style="Panel.TFrame")
        cell.pack(side="left", padx=(0, 10))
        try:
            image = profile.load_template(ref)
        except FlyfouError:
            ttk.Label(cell, text="missing", style="PanelMuted.TLabel",
                      foreground=theme.BAD).pack()
            return
        photo, _scale, size = imaging.fitted_photo(image, _THUMB, allow_upscale=True)
        self._images.set(key, photo)
        canvas = tk.Canvas(cell, width=_THUMB[0], height=_THUMB[1], bg="#101116", bd=0,
                           highlightthickness=1, highlightbackground=theme.LINE)
        canvas.pack()
        canvas.create_image((_THUMB[0] - size[0]) // 2, (_THUMB[1] - size[1]) // 2,
                            anchor="nw", image=photo)
        ttk.Label(cell, text=caption, style="PanelMuted.TLabel").pack(anchor="w", pady=(2, 0))

    # ---- live window check ------------------------------------------------ #

    def _check_window(self) -> None:
        profile = self.profile
        if profile is not None and not self._crippled:
            ranked = winutil.rank_windows(profile.window_title, profile.window_process)
            self._found = ranked[0][1] if ranked else None
            info = self._found
            if info is None:
                self.fit_chip.set("The game window isn't open right now — Flyfou will wait for it.",
                                  "warn")
            elif winutil.is_ambiguous(ranked):
                self.fit_chip.set(errors.ambiguous_window(info.title, ranked[1][1].title), "warn")
            elif profile.size_changed(*info.client_size):
                self.fit_chip.set(
                    f"The game is {_size_text(info.client_size)} now, but this profile was made at "
                    f"{_size_text(profile.client_size)}. Regions will follow the new size; if "
                    f"matching gets flaky, re-capture the monster.",
                    "warn",
                )
            else:
                self.fit_chip.set(f"Will farm in '{info.title}' ({_size_text(info.client_size)}).",
                                  "good")
        self.after(_WINDOW_CHECK_MS, self._check_window)

    # ---- actions ---------------------------------------------------------- #

    def _new(self) -> None:
        SetupWizard(self, self.store, None, on_saved=self._reload)

    def _edit(self) -> None:
        if self.profile is not None:
            SetupWizard(self, self.store, self.profile, on_saved=self._reload)

    def _duplicate(self) -> None:
        if self.profile is None:
            return
        suggestion = _next_copy_name(self.profile.name, self.store.names())
        name = simpledialog.askstring("Duplicate profile", "Name for the copy:",
                                      initialvalue=suggestion, parent=self)
        if not name:
            return
        try:
            copy = self.store.duplicate(self.profile.name, name.strip())
        except FlyfouError as exc:
            messagebox.showerror("Flyfou", exc.full(), parent=self)
            return
        self._reload(copy.name)

    def _delete(self) -> None:
        if self.profile is None:
            return
        name = self.profile.name
        if not messagebox.askyesno(
            "Delete profile",
            f"Delete '{name}' and the pictures captured for it?\n\nThis can't be undone.",
            parent=self,
        ):
            return
        self.store.delete(name)
        self._reload()

    def _start(self) -> None:
        if self.profile is None:
            return
        try:
            profile = self.store.load(self.profile.name)
        except FlyfouError as exc:
            messagebox.showerror("Flyfou", exc.full(), parent=self)
            return
        hwnd = self._found.hwnd if self._found else None
        self.withdraw()
        self._panel = ControlPanel(self, profile, hwnd=hwnd, on_closed=self._panel_closed)

    def _panel_closed(self) -> None:
        self._panel = None
        self.deiconify()
        self.lift()
        self._select()

    def _quit(self) -> None:
        if self._panel is not None:
            self._panel.close()
        self.destroy()


def _size_text(size) -> str:
    width, height = size
    return f"{width}×{height}" if width and height else "unknown size"


def _date_text(created: str) -> str:
    if not created:
        return "unknown"
    try:
        return datetime.datetime.fromisoformat(created).strftime("%d %b %Y")
    except ValueError:
        return created


def _next_copy_name(name: str, taken: List[str]) -> str:
    candidate = f"{name} copy"
    index = 2
    while candidate in taken:
        candidate = f"{name} copy {index}"
        index += 1
    return candidate


def run() -> int:
    winutil.enable_dpi_awareness()
    app = Launcher(ProfileStore())
    app.mainloop()
    return 0
