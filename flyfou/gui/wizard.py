"""The setup wizard: one screen per decision, nothing typed that can be shown."""

from __future__ import annotations

import shutil
import tempfile
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, messagebox, ttk
from typing import Dict, List, Optional

import numpy as np

from .. import capture, errors, inputs, vision, winutil
from ..errors import FlyfouError
from ..profile import Profile, ProfileStore, Skill, TemplateRef
from . import imaging, snip, theme
from .feed import FeedFrame, GameFeed
from .previews import BarPreview, MatchPreview
from .widgets import Card, Chip, KeyCaptureButton, LabelledScale, ScrollFrame, separator

_PEEK_HOLD = 5.0


class SetupWizard(tk.Toplevel):
    def __init__(self, master, store: ProfileStore, profile: Optional[Profile] = None, on_saved=None):
        super().__init__(master)
        self.store = store
        self.profile = profile or Profile()
        self.original_name = profile.name if profile else ""
        self.on_saved = on_saved
        self.hwnd: Optional[int] = None
        self._templates: Dict[str, np.ndarray] = {}
        self._staging = Path(tempfile.mkdtemp(prefix="flyfou-setup-"))

        if profile and profile.directory:
            source = profile.directory / "templates"
            if source.exists():
                for item in source.iterdir():
                    if item.is_file():
                        shutil.copy2(item, self._staging / item.name)
            found = winutil.find_window(profile.window_title, profile.window_process)
            if found:
                self.hwnd = found.hwnd

        self.feed = GameFeed(lambda: self.hwnd)
        self.feed.start()

        self.title(f"{'Edit' if profile else 'New'} profile — Flyfou setup")
        self.configure(bg=theme.BG)
        self.geometry("980x700")
        self.minsize(900, 640)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.transient(master)

        self.steps: List["Step"] = [
            NameStep(self), WindowStep(self), MonsterStep(self), HomeStep(self),
            PlayerHpStep(self), TargetHpStep(self), SkillsStep(self),
            TuningStep(self), FinishStep(self),
        ]
        self.index = 0
        self._build_chrome()
        self._show(0)
        self._tick()

    # ---- chrome ----------------------------------------------------------- #

    def _build_chrome(self) -> None:
        header = ttk.Frame(self, padding=(24, 18, 24, 10))
        header.pack(fill="x")
        self.step_counter = ttk.Label(header, text="", style="Muted.TLabel")
        self.step_counter.pack(anchor="w")
        self.step_title = ttk.Label(header, text="", style="Title.TLabel")
        self.step_title.pack(anchor="w", pady=(2, 2))
        self.step_subtitle = ttk.Label(header, text="", style="Muted.TLabel", wraplength=900,
                                       justify="left")
        self.step_subtitle.pack(anchor="w")

        self.progress = ttk.Frame(self, padding=(24, 6, 24, 6))
        self.progress.pack(fill="x")
        self._dots = []
        for _ in self.steps:
            dot = tk.Canvas(self.progress, width=26, height=5, bg=theme.BG,
                            highlightthickness=0, bd=0)
            dot.pack(side="left", padx=2)
            dot.create_rectangle(0, 0, 26, 5, fill=theme.LINE, outline="")
            self._dots.append(dot)

        self.body = ttk.Frame(self, padding=(24, 8, 24, 4))
        self.body.pack(fill="both", expand=True)

        self.error = Chip(self, "", "warn", wrap=920)
        self.error.pack(fill="x", padx=24)

        footer = ttk.Frame(self, padding=(24, 12, 24, 18))
        footer.pack(fill="x")
        ttk.Button(footer, text="Cancel", command=self._cancel).pack(side="left")
        self.next_button = ttk.Button(footer, text="Next", style="Accent.TButton", command=self._next)
        self.next_button.pack(side="right")
        self.back_button = ttk.Button(footer, text="Back", command=self._back)
        self.back_button.pack(side="right", padx=(0, 8))

    def _show(self, index: int) -> None:
        for step in self.steps:
            if step.frame is not None:
                step.frame.pack_forget()
        self.index = index
        step = self.steps[index]
        if step.frame is None:
            step.frame = step.build(self.body)
        step.frame.pack(fill="both", expand=True)
        step.enter()

        self.step_counter.configure(text=f"Step {index + 1} of {len(self.steps)}")
        self.step_title.configure(text=step.title)
        self.step_subtitle.configure(text=step.subtitle)
        self.error.set("", "warn")
        self.back_button.state(["disabled"] if index == 0 else ["!disabled"])
        self.next_button.configure(text=step.next_label)
        for position, dot in enumerate(self._dots):
            colour = theme.ACCENT if position <= index else theme.LINE
            dot.itemconfigure(1, fill=colour)

    def _next(self) -> None:
        step = self.steps[self.index]
        problem = step.validate()
        if problem:
            self.error.set(problem, "warn")
            return
        step.leave()
        if self.index == len(self.steps) - 1:
            self._save()
        else:
            self._show(self.index + 1)

    def _back(self) -> None:
        if self.index > 0:
            self.steps[self.index].leave()
            self._show(self.index - 1)

    def _cancel(self) -> None:
        if messagebox.askokcancel("Leave setup", "Leave setup? Anything captured here is lost.",
                                  parent=self):
            self._teardown()

    def _teardown(self) -> None:
        self.feed.stop()
        shutil.rmtree(self._staging, ignore_errors=True)
        self.destroy()

    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.steps[self.index].tick()
        except Exception:
            pass
        self.after(140, self._tick)

    # ---- shared services for steps ---------------------------------------- #

    def frame_now(self) -> Optional[FeedFrame]:
        return self.feed.latest()

    def take_snip(self, instruction: str, initial=None) -> Optional[snip.Snip]:
        if self.hwnd is None:
            messagebox.showwarning("No game window",
                                   "Go back to the Game window step and pick the game first.",
                                   parent=self)
            return None
        try:
            return snip.capture_region(self, self.hwnd, instruction, initial)
        except FlyfouError as exc:
            messagebox.showwarning("Couldn't take the screenshot", exc.full(), parent=self)
            return None

    def peek(self) -> Optional[FeedFrame]:
        """Get out of the way and grab one clean frame of the game."""
        if self.hwnd is None:
            return None
        self.withdraw()
        self.update()
        try:
            winutil.bring_to_front(self.hwnd)
            time.sleep(0.4)
            return self.feed.grab_now(prefer_screen=True)
        finally:
            self.deiconify()
            self.lift()
            self.focus_force()

    def stage_template(self, image: np.ndarray, prefix: str) -> str:
        index = 1
        while (self._staging / f"{prefix}_{index}.png").exists():
            index += 1
        filename = f"{prefix}_{index}.png"
        vision.imwrite(str(self._staging / filename), image)
        self._templates[filename] = image
        return filename

    def template_image(self, ref: Optional[TemplateRef]) -> Optional[np.ndarray]:
        if ref is None:
            return None
        if ref.file not in self._templates:
            loaded = vision.imread(str(self._staging / ref.file))
            if loaded is None:
                return None
            self._templates[ref.file] = loaded
        return self._templates[ref.file]

    def monster_images(self) -> List[np.ndarray]:
        images = [self.template_image(ref) for ref in self.profile.monsters]
        return [image for image in images if image is not None]

    # ---- saving ------------------------------------------------------------ #

    def _save(self) -> None:
        profile = self.profile
        try:
            directory = self.store.directory_for(profile.name)
            templates_dir = directory / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)

            referenced = {ref.file for ref in profile.monsters}
            if profile.home:
                referenced.add(profile.home.file)
            for existing in templates_dir.iterdir():
                if existing.is_file() and existing.name not in referenced:
                    existing.unlink()
            for filename in referenced:
                shutil.copy2(self._staging / filename, templates_dir / filename)

            self.store.save(profile)
            if self.original_name and self.store.directory_for(self.original_name) != directory:
                self.store.delete(self.original_name)
        except OSError as exc:
            messagebox.showerror("Couldn't save", f"Writing the profile failed:\n\n{exc}",
                                 parent=self)
            return

        saved_name = profile.name
        self._teardown()
        if self.on_saved:
            self.on_saved(saved_name)


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #

class Step:
    title = ""
    subtitle = ""
    next_label = "Next"

    def __init__(self, wizard: SetupWizard):
        self.wizard = wizard
        self.profile = wizard.profile
        self.frame: Optional[ttk.Frame] = None

    def build(self, parent) -> ttk.Frame:
        raise NotImplementedError

    def enter(self) -> None:
        pass

    def leave(self) -> None:
        pass

    def tick(self) -> None:
        pass

    def validate(self) -> Optional[str]:
        return None


class NameStep(Step):
    title = "Name this farm spot"
    subtitle = ("Each profile remembers one monster, one place and one set of skills. "
                "Give it a name you'll recognise in the list later.")

    def build(self, parent):
        frame = ttk.Frame(parent)
        card = Card(frame, "Profile name")
        card.pack(fill="x")
        self.var = tk.StringVar(value=self.profile.name if self.profile.name != "New profile" else "")
        entry = ttk.Entry(card.body, textvariable=self.var, font=theme.FONT)
        entry.pack(fill="x")
        entry.focus_set()
        ttk.Label(card.body, text="For example:  aibatt-lv30   ·   mushpoie-spot2   ·   darkon-mine",
                  style="PanelMuted.TLabel").pack(anchor="w", pady=(8, 0))

        note = Card(frame, "What happens next")
        note.pack(fill="x", pady=(14, 0))
        for line in (
            "1.  Pick the game window from a list of pictures — no typing.",
            "2.  Drag a box around the monster you want to farm.",
            "3.  Drag a box around your HP bar and the target's HP bar.",
            "4.  Press your skill keys in the order you use them.",
            "Everything is checked live as you go, so you'll know it works before you start.",
        ):
            ttk.Label(note.body, text=line, style="Panel.TLabel").pack(anchor="w", pady=1)
        return frame

    def validate(self):
        name = self.var.get().strip()
        if not name:
            return "Give this profile a name first."
        if name != self.wizard.original_name and self.wizard.store.exists(name):
            return f"There's already a profile called '{name}'. Pick another name."
        self.profile.name = name
        return None


class WindowStep(Step):
    title = "Pick the game window"
    subtitle = ("These are the windows Flyfou can see right now. Click the one that shows "
                "your game. If it isn't there, start the game and press Refresh.")

    def __init__(self, wizard):
        super().__init__(wizard)
        self._candidates: List[winutil.WindowInfo] = []
        self._cards: Dict[int, ttk.Frame] = {}
        self._thumbs = imaging.ImageHolder()
        self._cycle = 0
        self._last_scan = 0.0

    def build(self, parent):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)

        left = Card(frame, "Windows found")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.list = ScrollFrame(left.body, height=360)
        self.list.pack(fill="both", expand=True)
        ttk.Button(left.body, text="Refresh", command=self._rescan).pack(anchor="w", pady=(10, 0))

        right = Card(frame, "Preview")
        right.grid(row=0, column=1, sticky="nsew")
        self.preview = tk.Canvas(right.body, width=420, height=236, bg="#101116",
                                 highlightthickness=1, highlightbackground=theme.LINE, bd=0)
        self.preview.pack()
        self.detail = ttk.Label(right.body, text="Nothing picked yet.", style="Panel.TLabel",
                                wraplength=420, justify="left")
        self.detail.pack(anchor="w", pady=(10, 0))
        self.hint = Chip(right.body, "", "info", panel=True, wrap=420)
        self.hint.pack(anchor="w", pady=(6, 0))
        return frame

    def enter(self):
        self._rescan()

    def _rescan(self):
        self._last_scan = time.time()
        own = [self.wizard.title(), self.wizard.master.title()]
        self._candidates = winutil.list_candidate_windows(own)[:10]
        self.list.clear()
        self._cards = {}
        if not self._candidates:
            ttk.Label(self.list.inner, text="No windows found. Start the game, then press Refresh.",
                      style="Muted.TLabel", wraplength=380).pack(anchor="w", pady=8)
            return
        for info in self._candidates:
            self._cards[info.hwnd] = self._make_card(info)

        current = next((i for i in self._candidates if i.hwnd == self.wizard.hwnd), None)
        if current is None and not (self.profile.window_process or self.profile.window_title):
            current = self._candidates[0]  # new profile: show something rather than a blank preview
        if current is not None:
            self._select(current)
            return
        # An existing profile whose window isn't open: say so instead of quietly
        # repointing it at whatever happens to be first in the list.
        self.wizard.hwnd = None
        self._highlight()
        self.detail.configure(
            text=f"'{self.profile.window_title or self.profile.window_process}' isn't open right now."
        )
        self.hint.set("Start the game and press Refresh, or click a different window above.", "warn")

    def _make_card(self, info: winutil.WindowInfo) -> ttk.Frame:
        card = ttk.Frame(self.list.inner, style="Raised.TFrame", padding=8)
        card.pack(fill="x", pady=4)
        thumb = tk.Canvas(card, width=128, height=72, bg="#101116", highlightthickness=0, bd=0)
        thumb.pack(side="left")
        card.thumb = thumb

        text = ttk.Frame(card, style="Raised.TFrame")
        text.pack(side="left", fill="both", expand=True, padx=(10, 0))
        title = tk.Label(text, text=_shorten(info.title, 42), bg=theme.RAISED, fg=theme.FG,
                         font=theme.FONT_BOLD, anchor="w", justify="left")
        title.pack(anchor="w")
        meta = tk.Label(text, text=f"{info.process or 'unknown'}   ·   "
                                   f"{info.client[2]}×{info.client[3]}",
                        bg=theme.RAISED, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w")
        meta.pack(anchor="w")

        for widget in (card, thumb, text, title, meta):
            widget.bind("<Button-1>", lambda _e, i=info: self._select(i))
        return card

    def _select(self, info: winutil.WindowInfo):
        self.wizard.hwnd = info.hwnd
        self.profile.window_title = info.title
        self.profile.window_process = info.process
        self.profile.client_size = info.client_size
        self.detail.configure(
            text=f"{info.title}\n{info.process or 'unknown program'} · "
                 f"{info.client[2]}×{info.client[3]} pixels"
        )
        self.hint.set(
            "Flyfou will find this window again by its program name, so a changing "
            "title bar won't break the profile.", "info",
        )
        self._highlight()

    def _highlight(self):
        for hwnd, card in self._cards.items():
            selected = hwnd == self.wizard.hwnd
            card.configure(style="Panel.TFrame" if selected else "Raised.TFrame")
            for child in card.winfo_children():
                if isinstance(child, tk.Canvas):
                    child.configure(highlightthickness=2 if selected else 0,
                                    highlightbackground=theme.ACCENT)

    def tick(self):
        if time.time() - self._last_scan > 6.0:
            self._rescan()
            return
        if not self._candidates:
            return
        self._cycle = (self._cycle + 1) % max(1, len(self._candidates))
        info = self._candidates[self._cycle]
        card = self._cards.get(info.hwnd)
        if card is not None:
            image = _window_image(info.hwnd)
            if image is not None:
                _paint(card.thumb, self._thumbs, f"t{info.hwnd}", image, (128, 72))

        if self.wizard.hwnd:
            image = _window_image(self.wizard.hwnd)
            if image is not None:
                _paint(self.preview, self._thumbs, "big", image, (420, 236))

    def validate(self):
        if not self.wizard.hwnd:
            return "Click the window that shows your game."
        if not winutil.window_exists(self.wizard.hwnd):
            return "That window has closed. Press Refresh and pick it again."
        return None


class _TemplateStep(Step):
    """Shared behaviour for the monster and home-landmark screens."""

    prefix = "template"
    what = "monster"

    def _template_row(self, parent, ref: TemplateRef, on_remove):
        row = ttk.Frame(parent, style="Raised.TFrame", padding=6)
        row.pack(fill="x", pady=3)
        canvas = tk.Canvas(row, width=72, height=72, bg="#101116", highlightthickness=0, bd=0)
        canvas.pack(side="left")
        image = self.wizard.template_image(ref)
        if image is not None:
            _paint(canvas, self._thumbs, ref.file, image, (72, 72))
        info = tk.Label(
            row, bg=theme.RAISED, fg=theme.MUTED, font=theme.FONT_SMALL, justify="left",
            anchor="w", text=f"{image.shape[1]}×{image.shape[0]} px\ncaptured at "
                             f"{ref.client_size[0]}×{ref.client_size[1]}" if image is not None else "missing",
        )
        info.pack(side="left", padx=10)
        ttk.Button(row, text="Remove", command=on_remove).pack(side="right")
        return row


class MonsterStep(_TemplateStep):
    title = "Show it the monster"
    subtitle = ("Get a monster on screen, then drag a box around it. Capture it at the camera "
                "zoom you actually farm at — that matters more than anything else here.")
    prefix = "monster"

    def __init__(self, wizard):
        super().__init__(wizard)
        self._thumbs = imaging.ImageHolder()
        self._pinned: Optional[FeedFrame] = None
        self._pinned_at = 0.0

    def build(self, parent):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=0)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        capture_card = Card(left, "Captured monsters")
        capture_card.pack(fill="both", expand=True)
        ttk.Button(capture_card.body, text="Capture a monster", style="Accent.TButton",
                   command=self._capture).pack(fill="x")
        ttk.Label(capture_card.body, style="PanelMuted.TLabel", wraplength=300, justify="left",
                  text="Capture two or three of the same monster facing different ways if "
                       "matching is unreliable.").pack(anchor="w", pady=(8, 8))
        self.list = ScrollFrame(capture_card.body, height=210)
        self.list.pack(fill="both", expand=True)

        tuning = Card(left, "How sure it has to be")
        tuning.pack(fill="x", pady=(12, 0))
        self.threshold = LabelledScale(
            tuning.body, "Match threshold", 0.45, 0.98, self.profile.match_threshold,
            lambda v: f"{v:.2f}", self._set_threshold, step=0.01,
        )
        self.threshold.pack(fill="x")
        ttk.Label(tuning.body, style="PanelMuted.TLabel", wraplength=300, justify="left",
                  text="Higher is stricter. Too high and it never finds anything; too low and it "
                       "attacks rocks. 0.80 is a sensible start.").pack(anchor="w", pady=(8, 0))

        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="nsew")
        self.preview = MatchPreview(right)
        self.preview.pack(fill="both", expand=True)
        ttk.Button(right, text="Check now (hides this window for a moment)",
                   command=self._check_now).pack(anchor="w", pady=(10, 0))
        return frame

    def enter(self):
        self._refresh_list()

    def _set_threshold(self, value):
        self.profile.match_threshold = float(value)

    def _refresh_list(self):
        self.list.clear()
        if not self.profile.monsters:
            ttk.Label(self.list.inner, text="Nothing captured yet.", style="Muted.TLabel").pack(
                anchor="w", pady=6)
            return
        for ref in list(self.profile.monsters):
            self._template_row(self.list.inner, ref, lambda r=ref: self._remove(r))

    def _remove(self, ref):
        self.profile.monsters = [r for r in self.profile.monsters if r is not ref]
        self._refresh_list()

    def _capture(self):
        result = self.wizard.take_snip("Drag a box tightly around the monster.")
        if result is None:
            return
        filename = self.wizard.stage_template(result.image, self.prefix)
        self.profile.monsters.append(TemplateRef(filename, result.client_size))
        self.profile.client_size = result.client_size
        self._refresh_list()

    def _check_now(self):
        frame = self.wizard.peek()
        if frame is not None:
            self._pinned, self._pinned_at = frame, time.time()

    def _frame(self) -> Optional[FeedFrame]:
        if self._pinned and time.time() - self._pinned_at < _PEEK_HOLD:
            return self._pinned
        return self.wizard.frame_now()

    def tick(self):
        self.preview.update_view(self._frame(), self.wizard.monster_images(),
                                 self.profile.match_threshold, label=self.what)

    def validate(self):
        if not self.profile.monsters:
            return "Capture at least one monster — that's what the bot looks for."
        return None


class HomeStep(_TemplateStep):
    title = "Mark your home landmark (optional)"
    subtitle = ("Drag a box around something that doesn't move near your spot — a rock, a "
                "signpost, a building corner. Flyfou walks back to it every so often so it "
                "doesn't drift away. Skip this if you'd rather it wander freely.")
    prefix = "home"
    what = "landmark"

    def __init__(self, wizard):
        super().__init__(wizard)
        self._thumbs = imaging.ImageHolder()
        self._pinned: Optional[FeedFrame] = None
        self._pinned_at = 0.0

    def build(self, parent):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=0)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        card = Card(left, "Home landmark")
        card.pack(fill="x")
        ttk.Button(card.body, text="Capture the landmark", style="Accent.TButton",
                   command=self._capture).pack(fill="x")
        self.list = ScrollFrame(card.body, height=120)
        self.list.pack(fill="x", pady=(10, 0))
        ttk.Button(card.body, text="Skip — let it wander", command=self._skip).pack(
            anchor="w", pady=(10, 0))

        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="nsew")
        self.preview = MatchPreview(right)
        self.preview.pack(fill="both", expand=True)
        ttk.Button(right, text="Check now (hides this window for a moment)",
                   command=self._check_now).pack(anchor="w", pady=(10, 0))
        return frame

    def enter(self):
        self._refresh_list()

    def _refresh_list(self):
        self.list.clear()
        if self.profile.home is None:
            ttk.Label(self.list.inner, style="Muted.TLabel", wraplength=300, justify="left",
                      text="No landmark. The bot will wander around its search area instead of "
                           "returning to a fixed point.").pack(anchor="w", pady=6)
            return
        self._template_row(self.list.inner, self.profile.home, self._skip)

    def _skip(self):
        self.profile.home = None
        self._refresh_list()

    def _capture(self):
        result = self.wizard.take_snip("Drag a box around a landmark that never moves.")
        if result is None:
            return
        filename = self.wizard.stage_template(result.image, self.prefix)
        self.profile.home = TemplateRef(filename, result.client_size)
        self._refresh_list()

    def _check_now(self):
        frame = self.wizard.peek()
        if frame is not None:
            self._pinned, self._pinned_at = frame, time.time()

    def tick(self):
        frame = self._pinned if self._pinned and time.time() - self._pinned_at < _PEEK_HOLD \
            else self.wizard.frame_now()
        image = self.wizard.template_image(self.profile.home)
        self.preview.update_view(frame, [image] if image is not None else [],
                                 self.profile.match_threshold, label=self.what)


class _BarStep(Step):
    """Shared HP-bar screen: drag a box, colour is worked out for you, watch it read."""

    bar_name = "HP bar"
    instruction = "Drag a box around the bar."

    def bar(self):
        raise NotImplementedError

    def extras(self, parent):
        pass

    def __init__(self, wizard):
        super().__init__(wizard)
        self._pinned: Optional[FeedFrame] = None
        self._pinned_at = 0.0

    def build(self, parent):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=0)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        region_card = Card(left, f"The {self.bar_name}")
        region_card.pack(fill="x")
        ttk.Button(region_card.body, text=f"Mark out the {self.bar_name}", style="Accent.TButton",
                   command=self._mark).pack(fill="x")
        self.region_label = Chip(region_card.body, "Not marked out yet.", "warn", panel=True,
                                 wrap=280)
        self.region_label.pack(anchor="w", pady=(8, 0))

        colour_card = Card(left, "Filled colour")
        colour_card.pack(fill="x", pady=(12, 0))
        swatch_row = ttk.Frame(colour_card.body, style="Panel.TFrame")
        swatch_row.pack(fill="x")
        self.swatch = tk.Label(swatch_row, width=8, height=2, text="", relief="flat")
        self.swatch.pack(side="left")
        self.colour_text = ttk.Label(swatch_row, text="", style="Panel.TLabel")
        self.colour_text.pack(side="left", padx=10)
        ttk.Button(colour_card.body, text="Choose a different colour",
                   command=self._pick_colour).pack(anchor="w", pady=(10, 0))
        self.tolerance = LabelledScale(
            colour_card.body, "Colour tolerance", 5, 90, self.bar().tolerance,
            lambda v: f"±{int(v)}", self._set_tolerance, step=1,
        )
        self.tolerance.pack(fill="x", pady=(10, 0))
        ttk.Label(colour_card.body, style="PanelMuted.TLabel", wraplength=300, justify="left",
                  text="Raise this if the bar reads low when it's full; lower it if the "
                       "reading never drops.").pack(anchor="w", pady=(6, 0))

        self.extras(left)

        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="nsew")
        self.preview = BarPreview(right)
        self.preview.pack(fill="x")
        ttk.Button(right, text="Check now (hides this window for a moment)",
                   command=self._check_now).pack(anchor="w", pady=(10, 0))
        return frame

    def enter(self):
        self._render_colour()
        self._render_region()

    def _render_region(self):
        bar = self.bar()
        if bar.configured():
            x, y, w, h = bar.rect.to_pixels(*(self.profile.client_size or (1, 1)))
            self.region_label.set(f"Marked: {w}×{h} pixels at {x}, {y}.", "good")
        else:
            self.region_label.set("Not marked out yet.", "warn")

    def _render_colour(self):
        bar = self.bar()
        colour = theme.hex_color(bar.filled_color)
        self.swatch.configure(bg=colour)
        r, g, b = bar.filled_color
        self.colour_text.configure(text=f"{colour.upper()}\nRGB {r}, {g}, {b}")

    def _mark(self):
        bar = self.bar()
        result = self.wizard.take_snip(self.instruction, bar.rect if bar.configured() else None)
        if result is None:
            return
        bar.rect = result.frac
        self.profile.client_size = result.client_size
        guess = vision.dominant_bar_color(result.image)
        if guess is None:
            messagebox.showinfo("Colour not obvious", errors.color_detection_failed(), parent=self.wizard)
        else:
            bar.filled_color = guess.rgb
            bar.tolerance = guess.tolerance
            self.tolerance.var.set(guess.tolerance)
            self.tolerance.value_label.configure(text=f"±{guess.tolerance}")
        self._render_colour()
        self._render_region()

    def _pick_colour(self):
        bar = self.bar()
        chosen = colorchooser.askcolor(color=theme.hex_color(bar.filled_color),
                                       title="Pick the filled colour", parent=self.wizard)
        if chosen and chosen[0]:
            bar.filled_color = tuple(int(c) for c in chosen[0])
            self._render_colour()

    def _set_tolerance(self, value):
        self.bar().tolerance = int(value)

    def _check_now(self):
        frame = self.wizard.peek()
        if frame is not None:
            self._pinned, self._pinned_at = frame, time.time()

    def tick(self):
        frame = self._pinned if self._pinned and time.time() - self._pinned_at < _PEEK_HOLD \
            else self.wizard.frame_now()
        self.preview.update_view(frame, self.bar())

    def validate(self):
        if not self.bar().configured():
            return f"Mark out the {self.bar_name} — the bot can't run without it."
        return None


class PlayerHpStep(_BarStep):
    title = "Mark your own HP bar"
    subtitle = ("Drag a box around the filled part of your health bar. Flyfou reads the colour "
                "itself; the percentage on the right should match what the game shows.")
    bar_name = "HP bar"
    instruction = "Drag a box around YOUR health bar — just the bar, not the numbers."

    def bar(self):
        return self.profile.player_hp

    def extras(self, parent):
        card = Card(parent, "Safety")
        card.pack(fill="x", pady=(12, 0))
        self.critical = LabelledScale(
            card.body, "Pause the run below", 0.05, 0.6, self.profile.critical_fraction,
            lambda v: f"{v * 100:.0f}% HP", self._set_critical, step=0.01,
        )
        self.critical.pack(fill="x")
        ttk.Label(card.body, style="PanelMuted.TLabel", wraplength=300, justify="left",
                  text="Drop below this and Flyfou stops fighting and pauses itself. There's no "
                       "auto-potion — you resume once you're safe.").pack(anchor="w", pady=(8, 0))

    def _set_critical(self, value):
        self.profile.critical_fraction = float(value)


class TargetHpStep(_BarStep):
    title = "Mark the target's HP bar"
    subtitle = ("Click a monster in the game so its health bar appears, then come back and mark "
                "it out. This is how Flyfou knows it has a target and when the target dies.")
    bar_name = "target HP bar"
    instruction = "Drag a box around the TARGET's health bar at the top of the screen."

    def bar(self):
        return self.profile.target_hp


class SkillsStep(Step):
    title = "Record your attack rotation"
    subtitle = ("Press Record, then press your skill keys in the order you use them. Set each "
                "one's cooldown so Flyfou doesn't spam a skill that isn't ready.")

    def __init__(self, wizard):
        super().__init__(wizard)
        self._recording = False
        self._binding = None

    def build(self, parent):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        card = Card(frame, "Skill rotation")
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        buttons = ttk.Frame(card.body, style="Panel.TFrame")
        buttons.pack(fill="x")
        self.record_button = ttk.Button(buttons, text="Record keys", style="Accent.TButton",
                                        command=self._toggle_record)
        self.record_button.pack(side="left")
        ttk.Button(buttons, text="Clear all", command=self._clear).pack(side="left", padx=8)
        self.record_hint = Chip(card.body, "", "info", panel=True, wrap=440)
        self.record_hint.pack(anchor="w", pady=(8, 6))
        self.list = ScrollFrame(card.body, height=280)
        self.list.pack(fill="both", expand=True)

        side = ttk.Frame(frame)
        side.grid(row=0, column=1, sticky="nsew")
        attack = Card(side, "Basic attack key (optional)")
        attack.pack(fill="x")
        ttk.Label(attack.body, style="PanelMuted.TLabel", wraplength=340, justify="left",
                  text="Pressed whenever no skill is off cooldown. Leave this unset if you only "
                       "use skills, or if you attack by clicking.").pack(anchor="w", pady=(0, 8))
        self.attack_button = KeyCaptureButton(attack.body, self.profile.attack_key or "",
                                              self._set_attack, "Click, then press your attack key")
        self.attack_button.pack(fill="x")
        ttk.Button(attack.body, text="Clear", command=self._clear_attack).pack(anchor="w", pady=(8, 0))

        timing = Card(side, "Deciding a target is dead")
        timing.pack(fill="x", pady=(12, 0))
        self.timeout = LabelledScale(
            timing.body, "Empty target bar for", 1.0, 12.0, self.profile.target_lost_timeout,
            lambda v: f"{v:.1f} s", self._set_timeout, step=0.5,
        )
        self.timeout.pack(fill="x")
        ttk.Label(timing.body, style="PanelMuted.TLabel", wraplength=340, justify="left",
                  text="How long the target's bar has to read empty before Flyfou counts a kill "
                       "and looks for the next one.").pack(anchor="w", pady=(8, 0))
        return frame

    def enter(self):
        self._refresh()
        self._update_hint()

    def leave(self):
        self._stop_record()

    def _update_hint(self):
        if self._recording:
            self.record_hint.set("Listening — press your skill keys now. Esc or Stop to finish.", "good")
        else:
            self.record_hint.set("Keys are recorded in the order you press them.", "info")

    def _toggle_record(self):
        self._stop_record() if self._recording else self._start_record()

    def _start_record(self):
        self._recording = True
        self.record_button.configure(text="Stop recording")
        self._binding = self.wizard.bind("<KeyPress>", self._on_key, add="+")
        self.wizard.focus_set()
        self._update_hint()

    def _stop_record(self):
        if not self._recording:
            return
        self._recording = False
        self.record_button.configure(text="Record keys")
        if self._binding:
            self.wizard.unbind("<KeyPress>", self._binding)
            self._binding = None
        self._update_hint()

    def _on_key(self, event):
        if not self._recording:
            return None
        if event.keysym == "Escape":
            self._stop_record()
            return "break"
        key = inputs.from_keysym(event.keysym)
        if key is None:
            return "break"
        self.profile.skills.append(Skill(key, 3.0))
        self._refresh()
        return "break"

    def _clear(self):
        self.profile.skills.clear()
        self._refresh()

    def _remove(self, skill):
        self.profile.skills = [s for s in self.profile.skills if s is not skill]
        self._refresh()

    def _set_attack(self, key):
        self.profile.attack_key = key

    def _clear_attack(self):
        self.profile.attack_key = None
        self.attack_button.set_value("")

    def _set_timeout(self, value):
        self.profile.target_lost_timeout = float(value)

    def _refresh(self):
        self.list.clear()
        if not self.profile.skills:
            ttk.Label(self.list.inner, style="Muted.TLabel", wraplength=320, justify="left",
                      text="No skills recorded yet.").pack(anchor="w", pady=6)
            return
        for position, skill in enumerate(self.profile.skills, start=1):
            row = ttk.Frame(self.list.inner, style="Raised.TFrame", padding=8)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=str(position), bg=theme.RAISED, fg=theme.MUTED,
                     font=theme.FONT_SMALL, width=2).pack(side="left")
            tk.Label(row, text=inputs.display_name(skill.key), bg=theme.PANEL, fg=theme.FG,
                     font=theme.FONT_BOLD, width=6, pady=4).pack(side="left", padx=(4, 12))
            tk.Label(row, text="cooldown", bg=theme.RAISED, fg=theme.MUTED,
                     font=theme.FONT_SMALL).pack(side="left")
            var = tk.StringVar(value=f"{skill.cooldown:g}")
            spin = ttk.Spinbox(row, from_=0.2, to=180.0, increment=0.5, width=6, textvariable=var)
            spin.pack(side="left", padx=6)
            var.trace_add("write", lambda *_a, s=skill, v=var: _apply_cooldown(s, v))
            tk.Label(row, text="s", bg=theme.RAISED, fg=theme.MUTED,
                     font=theme.FONT_SMALL).pack(side="left")
            ttk.Button(row, text="Remove", command=lambda s=skill: self._remove(s)).pack(side="right")

    def validate(self):
        if not self.profile.skills and not self.profile.attack_key:
            return "Record at least one skill key, or set a basic attack key."
        return None


class TuningStep(Step):
    title = "Movement and hotkeys"
    subtitle = ("Sensible defaults are already set — change them only if the bot wanders too far "
                "or walks home too slowly. Distances are proportions of the window, so they hold "
                "up if you resize the game.")

    def build(self, parent):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        movement = Card(frame, "Moving around")
        movement.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.search = LabelledScale(
            movement.body, "How far it wanders while searching", 0.05, 0.45,
            self.profile.search_radius_frac, _percent_of_window,
            lambda v: setattr(self.profile, "search_radius_frac", float(v)), step=0.01)
        self.search.pack(fill="x", pady=(0, 14))
        self.step = LabelledScale(
            movement.body, "Step size when walking home", 0.04, 0.30,
            self.profile.return_step_frac, _percent_of_window,
            lambda v: setattr(self.profile, "return_step_frac", float(v)), step=0.01)
        self.step.pack(fill="x", pady=(0, 14))
        self.tolerance = LabelledScale(
            movement.body, "Close enough to count as home", 0.02, 0.20,
            self.profile.home_tolerance_frac, _percent_of_window,
            lambda v: setattr(self.profile, "home_tolerance_frac", float(v)), step=0.005)
        self.tolerance.pack(fill="x", pady=(0, 14))
        self.kills = LabelledScale(
            movement.body, "Kills between trips home", 1, 25,
            self.profile.kills_before_home_check, lambda v: f"{int(v)}",
            lambda v: setattr(self.profile, "kills_before_home_check", int(v)), step=1)
        self.kills.pack(fill="x")

        side = ttk.Frame(frame)
        side.grid(row=0, column=1, sticky="nsew")

        keys = Card(side, "Hotkeys (work while you're in the game)")
        keys.pack(fill="x")
        self._hotkey_row(keys.body, "Pause", "pause")
        self._hotkey_row(keys.body, "Resume", "resume")
        self._hotkey_row(keys.body, "Stop everything", "stop")
        ttk.Label(keys.body, style="PanelMuted.TLabel", wraplength=340, justify="left",
                  text="Pick keys you don't use while playing. Stop halts the run and lets go of "
                       "every key immediately.").pack(anchor="w", pady=(10, 0))

        speed = Card(side, "Reaction speed")
        speed.pack(fill="x", pady=(12, 0))
        self.loop = LabelledScale(
            speed.body, "Checks per second", 3, 15, self.profile.loop_hz,
            lambda v: f"{int(v)} per second",
            lambda v: setattr(self.profile, "loop_hz", int(v)), step=1)
        self.loop.pack(fill="x")
        ttk.Label(speed.body, style="PanelMuted.TLabel", wraplength=340, justify="left",
                  text="Higher reacts faster and uses more CPU. 8 is a good balance.").pack(
            anchor="w", pady=(8, 0))
        return frame

    def _hotkey_row(self, parent, label, attribute):
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, style="Panel.TLabel", width=16).pack(side="left")
        KeyCaptureButton(row, getattr(self.profile.hotkeys, attribute),
                         lambda key, a=attribute: setattr(self.profile.hotkeys, a, key)).pack(
            side="left", fill="x", expand=True)


class FinishStep(Step):
    title = "Ready to farm"
    subtitle = "Here's what this profile will do. Save it and it shows up in the main window."
    next_label = "Save profile"

    def build(self, parent):
        frame = ttk.Frame(parent)
        self.summary_card = Card(frame, "Summary")
        self.summary_card.pack(fill="both", expand=True)
        self.summary = ttk.Frame(self.summary_card.body, style="Panel.TFrame")
        self.summary.pack(fill="both", expand=True)
        self.problems = Chip(frame, "", "warn", wrap=900)
        self.problems.pack(anchor="w", pady=(12, 0))
        return frame

    def enter(self):
        for child in self.summary.winfo_children():
            child.destroy()
        profile = self.profile
        home = "a landmark to walk back to" if profile.home else "no landmark (it wanders)"
        skills = ", ".join(inputs.display_name(s.key) for s in profile.skills) or "none"
        rows = [
            ("Name", profile.name),
            ("Game window", f"{profile.window_title or '—'}  ({profile.window_process or 'unknown'})"),
            ("Captured at", f"{profile.client_size[0]}×{profile.client_size[1]} pixels"),
            ("Monsters captured", f"{len(profile.monsters)}, matched at {profile.match_threshold:.2f}"),
            ("Home", home),
            ("Skills in order", skills),
            ("Basic attack", inputs.display_name(profile.attack_key) if profile.attack_key else "none"),
            ("Pauses below", f"{profile.critical_fraction:.0%} HP"),
            ("Hotkeys", f"{profile.hotkeys.pause.upper()} pause · "
                        f"{profile.hotkeys.resume.upper()} resume · "
                        f"{profile.hotkeys.stop.upper()} stop"),
        ]
        for label, value in rows:
            row = ttk.Frame(self.summary, style="Panel.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, style="PanelMuted.TLabel", width=20).pack(side="left")
            ttk.Label(row, text=value, style="Panel.TLabel", wraplength=620,
                      justify="left").pack(side="left")

        issues = profile.problems()
        remaining = [i for i in issues if "missing from disk" not in i]
        if remaining:
            self.problems.set("Still to do: " + "  ".join(remaining), "warn")
        else:
            self.problems.set("Everything's set. Saving takes you back to the main window.", "good")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _apply_cooldown(skill: Skill, var: tk.StringVar) -> None:
    try:
        skill.cooldown = max(0.1, float(var.get()))
    except ValueError:
        pass


def _percent_of_window(value: float) -> str:
    return f"{value * 100:.0f}% of the window"


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _window_image(hwnd: int) -> Optional[np.ndarray]:
    image = winutil.print_window(hwnd)
    if image is not None and not vision.frame_is_blank(image):
        return image
    grabbed = capture.grab_client(hwnd)
    return grabbed[0] if grabbed else None


def _paint(canvas: tk.Canvas, holder: imaging.ImageHolder, key: str,
           image: np.ndarray, box) -> None:
    photo, _scale, size = imaging.fitted_photo(image, box)
    holder.set(key, photo)
    canvas.delete("all")
    canvas.create_image((box[0] - size[0]) // 2, (box[1] - size[1]) // 2, anchor="nw", image=photo)
