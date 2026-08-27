"""Setting up a farming spot, now that there is nothing to point at.

The wizard this replaces was eleven hundred lines, and almost all of it existed
to teach the bot what things looked like: draw a box round the play area, click
a monster so its colour could be sampled, mark where the health bar sits, tune a
match threshold until the previews stopped lying. None of that is a question any
more. The bot reads the world out of the client, so a monster's level is a
number it already has and a monster's name is a string it already has.

What is left is preferences, and preferences are a form. One screen, no steps,
no live previews, because there is nothing to preview - either the offsets fit
the client or the panel says so in words.

Two fields are worth explaining rather than merely labelling, and both are
explained in the form itself: the farming radius is in world units rather than
pixels, so it means the same thing whatever the window size, and the origin is
left blank on purpose - the bot takes it from wherever the character is standing
when it starts, which is nearly always what somebody wants and is one less thing
to get wrong.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, List, Optional

from .. import winutil
from ..mem import LayoutStore, build_key, is_client, open_client
from ..mem.layout import resolve_player
from ..profile import Profile
from . import theme
from .widgets import Card, Chip, KeyCaptureButton, ScrollFrame

#: Windows worth offering. The client is the only thing this bot can read.
LIKELY = "airborn"


def standing_at(hwnd: int):
    """Where the character in that window is standing, in world units.

    A route is a list of places, and the only way to say "here" without asking
    somebody to read coordinates off a screen is to go and stand there. So the
    button that adds a spot reads the position out of the client, which is the
    same number the bot will steer by.
    """
    import win32process
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not is_client(pid):
            return None
        process, module = open_client(pid)
    except Exception:                                  # noqa: BLE001
        return None
    try:
        layout = LayoutStore().load(build_key(process, module))
        if layout is None or layout.position is None:
            return None
        me = resolve_player(process, module, layout)
        return process.vec3(me + layout.position) if me else None
    finally:
        process.close()


class SetupWizard(tk.Toplevel):
    """One form. Same name and signature as the thing it replaced."""

    def __init__(self, parent, store, profile: Optional[Profile] = None,
                 on_saved: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.store = store
        self.on_saved = on_saved
        self.profile = profile or Profile()
        self.making = profile is None

        self.title("New farming spot" if self.making else "Edit farming spot")
        self.configure(background=theme.BG)
        self.geometry("560x720")
        self.transient(parent)
        self.grab_set()

        self.windows: List[winutil.WindowInfo] = []
        self.route: List[tuple] = [tuple(spot) for spot in self.profile.route]
        self._build()
        self._fill()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        outer = ttk.Frame(self, style="TFrame", padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Where to farm, and what to hit",
                  font=theme.FONT_TITLE).pack(anchor="w")
        Chip(outer, "Everything the bot knows about monsters it reads out of "
                    "the client, so there is nothing to point at any more.",
             wrap=500).pack(anchor="w", pady=(4, 12))

        scroll = ScrollFrame(outer)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        # -- which client -----------------------------------------------------
        card = Card(body, "The game window")
        card.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(card.body, style="Panel.TFrame")
        row.pack(fill="x")
        self.window_box = ttk.Combobox(row, state="readonly", font=theme.FONT)
        self.window_box.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Refresh", command=self._find_windows,
                   width=9).pack(side="left", padx=(8, 0))
        self.window_note = Chip(card.body, "", panel=True, wrap=470)
        self.window_note.pack(anchor="w", pady=(6, 0))

        # -- name -------------------------------------------------------------
        card = Card(body, "Call this spot")
        card.pack(fill="x", pady=(0, 10))
        self.name_var = tk.StringVar()
        ttk.Entry(card.body, textvariable=self.name_var,
                  font=theme.FONT).pack(fill="x")

        # -- what to attack ---------------------------------------------------
        card = Card(body, "What counts as a monster")
        card.pack(fill="x", pady=(0, 10))
        grid = ttk.Frame(card.body, style="Panel.TFrame")
        grid.pack(fill="x")

        ttk.Label(grid, text="Level range", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", pady=3)
        self.level_low = tk.StringVar()
        self.level_high = tk.StringVar()
        pair = ttk.Frame(grid, style="Panel.TFrame")
        pair.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Entry(pair, textvariable=self.level_low, width=6,
                  font=theme.FONT).pack(side="left")
        ttk.Label(pair, text=" to ", style="Panel.TLabel").pack(side="left")
        ttk.Entry(pair, textvariable=self.level_high, width=6,
                  font=theme.FONT).pack(side="left")
        ttk.Label(pair, text="  (blank for any)", style="Panel.TLabel",
                  font=theme.FONT_SMALL).pack(side="left")

        ttk.Label(grid, text="Only these names", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", pady=3)
        self.names_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.names_var, font=theme.FONT,
                  width=34).grid(row=1, column=1, sticky="we", padx=(10, 0))
        Chip(card.body, "Separate names with commas. Leave empty to attack "
                        "anything in the level range that is not a player or a "
                        "pet.", panel=True, wrap=470).pack(anchor="w", pady=(6, 0))

        # -- where ------------------------------------------------------------
        card = Card(body, "How far to roam")
        card.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(card.body, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Radius", style="Panel.TLabel").pack(side="left")
        self.radius_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.radius_var, width=8,
                  font=theme.FONT).pack(side="left", padx=(10, 0))
        ttk.Label(row, text="world units from where you start  (0 = no limit)",
                  style="Panel.TLabel", font=theme.FONT_SMALL).pack(
                      side="left", padx=(8, 0))
        Chip(card.body, "The starting point is wherever the character is "
                        "standing when you press start, so there is nothing to "
                        "set here.", panel=True, wrap=470).pack(
                            anchor="w", pady=(6, 0))

        # -- route ------------------------------------------------------------
        card = Card(body, "Places to farm, in order")
        card.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(card.body, style="Panel.TFrame")
        row.pack(fill="x")
        self.route_list = tk.Listbox(row, height=4, font=theme.FONT_MONO,
                                     background=theme.RAISED,
                                     foreground=theme.FG,
                                     highlightthickness=0, bd=0,
                                     selectbackground=theme.ACCENT)
        self.route_list.pack(side="left", fill="x", expand=True)
        buttons = ttk.Frame(row, style="Panel.TFrame")
        buttons.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Add where I am", width=15,
                   command=self._add_spot).pack()
        ttk.Button(buttons, text="Remove", width=15,
                   command=self._drop_spot).pack(pady=(4, 0))
        self.route_note = Chip(card.body, "", panel=True, wrap=470)
        self.route_note.pack(anchor="w", pady=(6, 0))
        Chip(card.body, "Stand somewhere and press Add. With a route the bot "
                        "works each spot in turn instead of staying put; with "
                        "none it farms around wherever you start it.",
             panel=True, wrap=470).pack(anchor="w", pady=(4, 0))

        # -- fighting ---------------------------------------------------------
        card = Card(body, "Fighting")
        card.pack(fill="x", pady=(0, 10))
        grid = ttk.Frame(card.body, style="Panel.TFrame")
        grid.pack(fill="x")

        ttk.Label(grid, text="Attack key", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", pady=3)
        self.attack_key = KeyCaptureButton(grid, "1")
        self.attack_key.grid(row=0, column=1, sticky="w", padx=(10, 0))

        ttk.Label(grid, text="Protect", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", pady=3)
        self.protect_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.protect_var, width=22,
                  font=theme.FONT).grid(row=1, column=1, sticky="w", padx=(10, 0))
        ttk.Label(grid, text="  a character to defend, if any",
                  style="Panel.TLabel", font=theme.FONT_SMALL).grid(
                      row=1, column=2, sticky="w")

        ttk.Label(grid, text="Heal key", style="Panel.TLabel").grid(
            row=2, column=0, sticky="w", pady=3)
        self.heal_key = KeyCaptureButton(grid, "")
        self.heal_key.grid(row=2, column=1, sticky="w", padx=(10, 0))
        ttk.Label(grid, text="  food or a heal, pressed while resting",
                  style="Panel.TLabel", font=theme.FONT_SMALL).grid(
                      row=2, column=2, sticky="w")

        ttk.Label(grid, text="Rest below", style="Panel.TLabel").grid(
            row=3, column=0, sticky="w", pady=3)
        pair = ttk.Frame(grid, style="Panel.TFrame")
        pair.grid(row=3, column=1, columnspan=2, sticky="w", padx=(10, 0))
        self.rest_var = tk.StringVar()
        ttk.Entry(pair, textvariable=self.rest_var, width=5,
                  font=theme.FONT).pack(side="left")
        ttk.Label(pair, text="%  and fight again above ",
                  style="Panel.TLabel", font=theme.FONT_SMALL).pack(side="left")
        self.well_var = tk.StringVar()
        ttk.Entry(pair, textvariable=self.well_var, width=5,
                  font=theme.FONT).pack(side="left")
        ttk.Label(pair, text="%", style="Panel.TLabel",
                  font=theme.FONT_SMALL).pack(side="left")

        self.killsteal_var = tk.BooleanVar(value=True)
        self.defend_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(card.body, text="Leave monsters somebody else is "
                                        "already fighting",
                        variable=self.killsteal_var,
                        style="Panel.TCheckbutton").pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(card.body, text="Hit back when something attacks us",
                        variable=self.defend_var,
                        style="Panel.TCheckbutton").pack(anchor="w")

        # -- hotkeys ----------------------------------------------------------
        card = Card(body, "Hotkeys")
        card.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(card.body, style="Panel.TFrame")
        row.pack(fill="x")
        self.key_buttons = {}
        for label, field in (("Pause", "pause"), ("Resume", "resume"),
                             ("Stop", "stop")):
            cell = ttk.Frame(row, style="Panel.TFrame")
            cell.pack(side="left", padx=(0, 14))
            ttk.Label(cell, text=label, style="Panel.TLabel",
                      font=theme.FONT_SMALL).pack(anchor="w")
            button = KeyCaptureButton(cell, "f9")
            button.pack()
            self.key_buttons[field] = button

        # -- buttons ----------------------------------------------------------
        feet = ttk.Frame(outer, style="TFrame")
        feet.pack(fill="x", pady=(12, 0))
        self.problem = Chip(feet, "", wrap=380)
        self.problem.pack(side="left", fill="x", expand=True)
        ttk.Button(feet, text="Cancel", command=self.destroy,
                   width=10).pack(side="right", padx=(8, 0))
        ttk.Button(feet, text="Save", command=self._save, width=10,
                   style="Accent.TButton").pack(side="right")

    # ------------------------------------------------------------------ route
    def _chosen_window(self):
        index = self.window_box.current()
        if 0 <= index < len(self.windows):
            return self.windows[index]
        return None

    def _show_route(self) -> None:
        self.route_list.delete(0, tk.END)
        for number, spot in enumerate(self.route, start=1):
            self.route_list.insert(
                tk.END, "%d.  %7.0f, %7.0f" % (number, spot[0], spot[2]))
        self.route_note.set(
            "%d spot%s; the bot moves on when it runs out of monsters at one."
            % (len(self.route), "" if len(self.route) == 1 else "s")
            if self.route else "No route: it farms around where you start it.",
            "good" if self.route else "info")

    def _add_spot(self) -> None:
        window = self._chosen_window()
        if window is None:
            self.route_note.set("Choose the game window first.", "bad")
            return
        spot = standing_at(window.hwnd)
        if spot is None:
            self.route_note.set(
                "Could not read that character's position. It has to be logged "
                "in and in the world, and the offsets have to be known.", "bad")
            return
        self.route.append(tuple(float(v) for v in spot))
        self._show_route()

    def _drop_spot(self) -> None:
        picked = list(self.route_list.curselection())
        for index in reversed(picked):
            if 0 <= index < len(self.route):
                self.route.pop(index)
        self._show_route()

    # -------------------------------------------------------------------- data
    def _find_windows(self) -> None:
        self.windows = [w for w in winutil.list_candidate_windows()
                        if LIKELY in w.title.lower() or LIKELY in w.process.lower()]
        if not self.windows:
            self.windows = winutil.list_candidate_windows()
            self.window_note.set("No window looked like the game, so every "
                                 "window is listed.", "warn")
        else:
            self.window_note.set("%d client window%s open."
                                 % (len(self.windows),
                                    "" if len(self.windows) == 1 else "s"),
                                 "good")
        self.window_box["values"] = [w.label for w in self.windows]
        if self.windows and not self.window_box.get():
            self.window_box.current(0)

    def _fill(self) -> None:
        self._find_windows()
        profile = self.profile
        self.name_var.set(profile.name if not self.making else "")
        if profile.levels:
            self.level_low.set(str(profile.levels[0]))
            self.level_high.set(str(profile.levels[1]))
        self.names_var.set(", ".join(profile.monster_names))
        self._show_route()
        self.radius_var.set("%g" % (profile.farm_radius or 0))
        self.protect_var.set(profile.protect)
        self.rest_var.set("%g" % round((profile.rest_below or 0.35) * 100))
        self.well_var.set("%g" % round((profile.fight_above or 0.80) * 100))
        self.heal_key.set_value(profile.heal_key or "")
        self.attack_key.set_value(profile.attack_key or "1")
        self.killsteal_var.set(bool(profile.avoid_killsteal))
        self.defend_var.set(bool(profile.self_defence))
        for field, button in self.key_buttons.items():
            button.set_value(getattr(profile.hotkeys, field))

        if profile.window_title:
            for index, window in enumerate(self.windows):
                if profile.window_title.lower() in window.title.lower():
                    self.window_box.current(index)
                    break

    def _numbers(self):
        """The typed-in numbers, or a complaint naming the one that is wrong."""
        low = high = None
        if self.level_low.get().strip() or self.level_high.get().strip():
            try:
                low = int(self.level_low.get() or 1)
                high = int(self.level_high.get() or 999)
            except ValueError:
                return None, "The level range has to be two whole numbers."
            if low > high:
                return None, "The lowest level is above the highest one."
        try:
            radius = float(self.radius_var.get() or 0)
        except ValueError:
            return None, "The radius has to be a number of world units."
        try:
            rest = float(self.rest_var.get() or 35)
            well = float(self.well_var.get() or 80)
        except ValueError:
            return None, "The health thresholds have to be percentages."
        if not (0 <= rest <= 100 and 0 <= well <= 100):
            return None, "The health thresholds are percentages, so 0 to 100."
        if well < rest:
            return None, ("It would fight on at less health than it stopped at, "
                          "which means it never really rests.")
        return ((low, high) if low is not None else None, radius, rest, well), ""

    def _save(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            self.problem.set("Give this spot a name first.", "bad")
            return
        parsed, complaint = self._numbers()
        if complaint:
            self.problem.set(complaint, "bad")
            return
        levels, radius, rest, well = parsed

        index = self.window_box.current()
        if index < 0 or index >= len(self.windows):
            self.problem.set("Choose the game window.", "bad")
            return
        window = self.windows[index]

        if self.making and self.store.exists(name):
            self.problem.set("There is already a spot called that.", "bad")
            return

        profile = self.profile
        profile.name = name
        profile.window_title = window.title
        profile.window_process = window.process
        profile.client_size = window.client_size
        profile.levels = levels
        profile.monster_names = [part.strip()
                                 for part in self.names_var.get().split(",")
                                 if part.strip()]
        profile.farm_radius = radius
        profile.route = [tuple(spot) for spot in self.route]
        profile.protect = self.protect_var.get().strip()
        profile.rest_below = rest / 100.0
        profile.fight_above = well / 100.0
        profile.heal_key = self.heal_key.value or None
        profile.attack_key = self.attack_key.value
        profile.avoid_killsteal = bool(self.killsteal_var.get())
        profile.self_defence = bool(self.defend_var.get())
        for field, button in self.key_buttons.items():
            setattr(profile.hotkeys, field, button.value)

        try:
            self.store.save(profile)
        except Exception as bad:                       # noqa: BLE001
            messagebox.showerror("Flyfou", "Could not save that spot:\n%s" % bad,
                                 parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.destroy()
