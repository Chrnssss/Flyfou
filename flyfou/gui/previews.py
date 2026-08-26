"""Live detection previews — the point of these is that a bad threshold, region
or colour is visible while you're setting it, not an hour into a run."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional, Sequence

import numpy as np

from .. import capture, vision
from ..profile import BarConfig, FracRect
from . import imaging, theme
from .feed import FeedFrame
from .widgets import Chip

_GREEN = "#46cf94"
_AMBER = "#f0b429"


class MatchPreview(ttk.Frame):
    """The game with a box drawn over whatever the template matched."""

    def __init__(self, master, width: int = 440, height: int = 248):
        super().__init__(master, style="Panel.TFrame", padding=10)
        self.box = (width, height)
        self._images = imaging.ImageHolder()

        # The wizard is resizable and each step gives this column a different
        # amount of room, so the canvas takes what it gets rather than a fixed
        # size it would be clipped down to.
        self.canvas = tk.Canvas(self, width=width, height=height, bg="#101116",
                                highlightthickness=1, highlightbackground=theme.LINE, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._resized)
        self._placeholder()

        self.verdict = ttk.Label(self, text="", style="Panel.TLabel", font=theme.FONT_BOLD,
                                 wraplength=width, justify="left")
        self.verdict.pack(anchor="w", pady=(8, 0))
        self.source = Chip(self, "Waiting for the game window…", "info", panel=True, wrap=width)
        self.source.pack(anchor="w")

    def _resized(self, event) -> None:
        self.box = (event.width, event.height)
        self.verdict.configure(wraplength=event.width)
        self.source.configure(wraplength=event.width)

    def _placeholder(self) -> None:
        self.canvas.delete("all")
        self.canvas.create_text(
            self.box[0] // 2, self.box[1] // 2, fill=theme.MUTED, font=theme.FONT,
            text="No picture from the game yet.\nMake sure it's running and not minimised.",
            justify="center",
        )

    def update_view(self, frame: Optional[FeedFrame], templates: Sequence[np.ndarray],
                    threshold: float, label: str = "monster") -> Optional[vision.Match]:
        if frame is None:
            self._placeholder()
            self.verdict.configure(text="", foreground=theme.FG)
            self.source.set("Waiting for the game window…", "info")
            return None

        text, level = frame.caption
        self.source.set(text, level)

        photo, scale, size = imaging.fitted_photo(frame.image, self.box)
        self._images.set("frame", photo)
        self.canvas.delete("all")
        offset_x = (self.box[0] - size[0]) // 2
        offset_y = (self.box[1] - size[1]) // 2
        self.canvas.create_image(offset_x, offset_y, anchor="nw", image=photo)

        if not templates:
            self.verdict.configure(text=f"Nothing captured yet — no {label} to look for.",
                                   foreground=theme.MUTED)
            return None

        match = vision.best_match(frame.image, templates)
        if match is None:
            self.verdict.configure(text="The captured image is bigger than the game window.",
                                   foreground=theme.BAD)
            return None

        hit = match.score >= threshold
        colour = _GREEN if hit else _AMBER
        self.canvas.create_rectangle(
            offset_x + match.x * scale, offset_y + match.y * scale,
            offset_x + (match.x + match.w) * scale, offset_y + (match.y + match.h) * scale,
            outline=colour, width=3 if hit else 2, dash=() if hit else (5, 4),
        )
        self.canvas.create_text(
            offset_x + match.x * scale, offset_y + match.y * scale - 9,
            anchor="sw", fill=colour, font=theme.FONT_BOLD, text=f"{match.score:.2f}",
        )

        if hit:
            self.verdict.configure(
                text=f"Matched at {match.score:.2f} — above your {threshold:.2f} threshold.",
                foreground=theme.GOOD,
            )
        else:
            self.verdict.configure(
                text=f"Best guess is only {match.score:.2f}, under the {threshold:.2f} "
                     f"threshold (dashed box). Lower the threshold or re-capture.",
                foreground=theme.WARN,
            )
        return match


class HuntingGroundPreview(ttk.Frame):
    """The game with the hunting ground outlined and every candidate boxed.

    This is what the bot itself sees: things that aren't the ground it stands on.
    The boxes carry no identity — a monster and a signpost look alike here, and
    the cursor decides between them at run time — so the honest thing to show is
    the shortlist, not a verdict.
    """

    def __init__(self, master, width: int = 440, height: int = 248):
        super().__init__(master, style="Panel.TFrame", padding=10)
        self.box = (width, height)
        self._images = imaging.ImageHolder()

        self.canvas = tk.Canvas(self, width=width, height=height, bg="#101116",
                                highlightthickness=1, highlightbackground=theme.LINE, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._resized)
        self._placeholder()

        self.verdict = ttk.Label(self, text="", style="Panel.TLabel", font=theme.FONT_BOLD,
                                 wraplength=width, justify="left")
        self.verdict.pack(anchor="w", pady=(8, 0))
        self.source = Chip(self, "Waiting for the game window…", "info", panel=True, wrap=width)
        self.source.pack(anchor="w")

    def _resized(self, event) -> None:
        self.box = (event.width, event.height)
        self.verdict.configure(wraplength=event.width)
        self.source.configure(wraplength=event.width)

    def _placeholder(self) -> None:
        self.canvas.delete("all")
        self.canvas.create_text(
            self.box[0] // 2, self.box[1] // 2, fill=theme.MUTED, font=theme.FONT,
            text="No picture from the game yet.\nMake sure it's running and not minimised.",
            justify="center",
        )

    def update_view(self, frame: Optional[FeedFrame], area: FracRect) -> int:
        if frame is None:
            self._placeholder()
            self.verdict.configure(text="", foreground=theme.FG)
            self.source.set("Waiting for the game window…", "info")
            return 0

        text, level = frame.caption
        self.source.set(text, level)

        photo, scale, size = imaging.fitted_photo(frame.image, self.box)
        self._images.set("frame", photo)
        self.canvas.delete("all")
        offset_x = (self.box[0] - size[0]) // 2
        offset_y = (self.box[1] - size[1]) // 2
        self.canvas.create_image(offset_x, offset_y, anchor="nw", image=photo)

        client_h, client_w = frame.image.shape[:2]
        ax, ay, aw, ah = area.to_pixels(client_w, client_h)
        if aw < 8 or ah < 8:
            self.verdict.configure(text="The hunting ground is empty — mark one out.",
                                   foreground=theme.WARN)
            return 0

        def place(x, y):
            return offset_x + x * scale, offset_y + y * scale

        self.canvas.create_rectangle(*place(ax, ay), *place(ax + aw, ay + ah),
                                     outline=theme.ACCENT, width=2, dash=(6, 4))

        # Skipping our own character is the bot's rule, not this widget's — share
        # it, or the boxes drawn here stop matching the ones it actually probes.
        others, mine = vision.split_self(
            vision.find_blobs(frame.image[ay:ay + ah, ax:ax + aw]),
            (client_w // 2 - ax, client_h // 2 - ay),
            vision.self_radius(client_h))
        for blob in mine:
            self.canvas.create_rectangle(
                *place(ax + blob.x, ay + blob.y),
                *place(ax + blob.x + blob.w, ay + blob.y + blob.h),
                outline=_AMBER, width=2, dash=(4, 3),
            )
        if mine:
            self.canvas.create_text(*place(client_w // 2, client_h // 2), fill=_AMBER,
                                    font=theme.FONT_SMALL, text="you")
        for blob in others:
            self.canvas.create_rectangle(
                *place(ax + blob.x, ay + blob.y),
                *place(ax + blob.x + blob.w, ay + blob.y + blob.h),
                outline=_GREEN, width=2,
            )

        found = len(others)
        if found:
            self.verdict.configure(
                text=f"{found} thing{'' if found == 1 else 's'} to check in here. Flyfou hovers "
                     f"each one nearest-first and reads the cursor to find the monsters.",
                foreground=theme.GOOD,
            )
        else:
            self.verdict.configure(
                text="Nothing but ground in there. Either no monsters are on screen, or the box "
                     "is too small — it should cover the scenery but not the interface.",
                foreground=theme.WARN,
            )
        return found


class BarPreview(ttk.Frame):
    """A magnified HP bar, the pixels the colour rule accepts, and the percentage."""

    def __init__(self, master, width: int = 380):
        super().__init__(master, style="Panel.TFrame", padding=10)
        self.width = width
        self._images = imaging.ImageHolder()

        ttk.Label(self, text="What Flyfou sees", style="PanelMuted.TLabel").pack(anchor="w")
        self.region_canvas = tk.Canvas(self, width=width, height=52, bg="#101116",
                                       highlightthickness=1, highlightbackground=theme.LINE, bd=0)
        self.region_canvas.pack(fill="x", pady=(4, 8))
        self.region_canvas.bind("<Configure>", self._resized)

        ttk.Label(self, text="Pixels counted as filled", style="PanelMuted.TLabel").pack(anchor="w")
        self.mask_canvas = tk.Canvas(self, width=width, height=52, bg="#101116",
                                     highlightthickness=1, highlightbackground=theme.LINE, bd=0)
        self.mask_canvas.pack(fill="x", pady=(4, 10))

        row = ttk.Frame(self, style="Panel.TFrame")
        row.pack(fill="x")
        self.percent = ttk.Label(row, text="—", style="Huge.TLabel")
        self.percent.pack(side="left")
        self.bar = ttk.Progressbar(row, style="Good.Horizontal.TProgressbar",
                                   length=width - 110, maximum=100)
        self.bar.pack(side="right", pady=8)

        self.note = Chip(self, "Mark out the bar to see a reading.", "info", panel=True, wrap=width)
        self.note.pack(anchor="w", pady=(8, 0))

    def _resized(self, event) -> None:
        self.width = event.width
        self.note.configure(wraplength=event.width)

    def update_view(self, frame: Optional[FeedFrame], bar: BarConfig) -> Optional[float]:
        if not bar.configured():
            self.note.set("Mark out the bar to see a reading.", "info")
            return None
        if frame is None:
            self.note.set("Waiting for the game window…", "info")
            return None

        region = capture.crop_fraction(frame.image, bar.rect)
        if region.size == 0:
            self.note.set("That region falls outside the game window now.", "bad")
            return None

        fraction = vision.bar_fill_fraction(region, bar.filled_color, bar.tolerance)
        masked = vision.color_mask_preview(region, bar.filled_color, bar.tolerance)
        self._draw(self.region_canvas, "region", region)
        self._draw(self.mask_canvas, "mask", masked)

        percent = fraction * 100
        self.percent.configure(text=f"{percent:.0f}%")
        self.bar.configure(value=percent)
        self.bar.configure(style=_bar_style(fraction))

        source_text, source_level = frame.caption
        if fraction <= 0.005:
            self.note.set(
                "Reading 0% — either the bar really is empty, or the colour below "
                "doesn't match it. Raise the tolerance or re-pick the colour.", "warn",
            )
        elif fraction >= 0.995:
            self.note.set(f"Reading full. {source_text}.", "good")
        else:
            self.note.set(f"{source_text}.", source_level)
        return fraction

    def _draw(self, canvas: tk.Canvas, key: str, region: np.ndarray) -> None:
        canvas.delete("all")
        photo, _scale, size = imaging.fitted_photo(region, (self.width - 4, 48), allow_upscale=True)
        self._images.set(key, photo)
        canvas.create_image((self.width - size[0]) // 2, (52 - size[1]) // 2, anchor="nw", image=photo)


def _bar_style(fraction: float) -> str:
    if fraction >= 0.5:
        return "Good.Horizontal.TProgressbar"
    return "Warn.Horizontal.TProgressbar" if fraction >= 0.25 else "Bad.Horizontal.TProgressbar"
