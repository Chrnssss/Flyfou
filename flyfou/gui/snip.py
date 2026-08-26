"""Freeze the game, drag a box on it.

The old flow was: hover a corner of the live screen, press '[', hover the other
corner, press ']'. This replaces it with a still frame you can take your time
over, complete with a magnifier for the two-pixel-tall HP bars.
"""

from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .. import capture, errors, vision, winutil
from ..errors import FlyfouError
from ..profile import FracRect
from . import imaging, theme

_SELECT = "#46cf94"
_MIN_SIZE = 3
_LOUPE_HALF = 22
_LOUPE_ZOOM = 4


@dataclass
class Snip:
    image: np.ndarray  # the cropped area, BGR
    rect: Tuple[int, int, int, int]  # client-relative pixels
    frac: FracRect
    client_size: Tuple[int, int]
    frame: np.ndarray  # the whole frozen client area


def capture_region(
    parent: tk.Misc,
    hwnd: Optional[int],
    instruction: str,
    initial: Optional[FracRect] = None,
) -> Optional[Snip]:
    """Bring the game forward, freeze it, and let the user drag out a rectangle."""
    if not hwnd or not winutil.window_exists(hwnd):
        raise FlyfouError(
            "The game window isn't there any more.",
            "Start the game, then go back to the Game window step and pick it again.",
        )
    if winutil.is_minimised(hwnd):
        raise FlyfouError(
            errors.window_minimised(winutil.window_title(hwnd)),
            "Restore the game window and try again.",
        )

    root = parent.winfo_toplevel()
    root.withdraw()
    root.update()
    try:
        winutil.bring_to_front(hwnd)
        time.sleep(0.45)
        grabbed = capture.grab_client(hwnd)
        was_foreground = winutil.is_foreground(hwnd)
        if grabbed is None:
            raise FlyfouError(
                "Couldn't take a screenshot of the game window.",
                "Make sure it's on a connected monitor and not minimised.",
            )
        frame, (_, _, width, height) = grabbed
        if not was_foreground:
            raise FlyfouError(errors.capture_not_foreground(), "")
        if vision.frame_is_blank(frame):
            raise FlyfouError(errors.blank_capture(), "")

        overlay = _Overlay(root, frame, winutil.client_rect(hwnd), instruction, initial)
        root.wait_window(overlay)
        selection = overlay.result
    finally:
        root.deiconify()
        root.lift()
        root.focus_force()

    if selection is None:
        return None
    x, y, w, h = selection
    return Snip(
        image=frame[y:y + h, x:x + w].copy(),
        rect=(x, y, w, h),
        frac=FracRect.from_pixels((x, y, w, h), width, height),
        client_size=(width, height),
        frame=frame,
    )


class _Overlay(tk.Toplevel):
    def __init__(self, master, frame, client_rect, instruction, initial):
        super().__init__(master)
        self.frame = frame
        self.height, self.width = frame.shape[:2]
        self.instruction = instruction
        self.result: Optional[Tuple[int, int, int, int]] = None
        self._images = imaging.ImageHolder()
        self._origin: Optional[Tuple[int, int]] = None
        self._sel: Optional[Tuple[int, int, int, int]] = None  # x0, y0, x1, y1
        self._last_loupe = 0.0

        x, y, _, _ = client_rect
        self.overrideredirect(True)
        self.geometry(f"{self.width}x{self.height}+{x}+{y}")
        self.attributes("-topmost", True)
        self.configure(bg="black")

        self.canvas = tk.Canvas(self, width=self.width, height=self.height,
                                highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(
            0, 0, anchor="nw",
            image=self._images.set("frame", imaging.fitted_photo(frame, (self.width, self.height))[0]),
        )

        self._dim = [self.canvas.create_rectangle(0, 0, 0, 0, fill="black",
                                                  stipple="gray50", outline="") for _ in range(4)]
        self._rect = self.canvas.create_rectangle(0, 0, 0, 0, outline=_SELECT, width=2, state="hidden")
        self._vline = self.canvas.create_line(0, 0, 0, self.height, fill=_SELECT, dash=(3, 3))
        self._hline = self.canvas.create_line(0, 0, self.width, 0, fill=_SELECT, dash=(3, 3))

        self._readout_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill="#0d1117", outline=_SELECT)
        self._readout = self.canvas.create_text(0, 0, anchor="nw", fill=theme.FG,
                                                font=theme.FONT_SMALL, text="")
        self._build_banner()

        self.loupe = tk.Canvas(self, width=_LOUPE_HALF * 2 * _LOUPE_ZOOM,
                               height=_LOUPE_HALF * 2 * _LOUPE_ZOOM,
                               highlightthickness=1, highlightbackground=_SELECT, bd=0)
        self._loupe_corner = None

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_move)
        # Bound on the toplevel only: it is already in the canvas's bindtags, so
        # binding both would run every handler twice and nudge two pixels.
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._accept())
        for key, delta in (("Left", (-1, 0)), ("Right", (1, 0)), ("Up", (0, -1)), ("Down", (0, 1))):
            self.bind(f"<{key}>", lambda _e, d=delta: self._nudge(d, resize=False))
            self.bind(f"<Shift-{key}>", lambda _e, d=delta: self._nudge(d, resize=True))

        if initial is not None and not initial.is_empty():
            ix, iy, iw, ih = initial.to_pixels(self.width, self.height)
            self._sel = (ix, iy, ix + iw, iy + ih)
        self._redraw()

        self.update_idletasks()
        self.lift()
        self.focus_force()
        self.canvas.focus_set()
        self.grab_set()

    # ---- chrome ----------------------------------------------------------- #

    def _build_banner(self) -> None:
        lines = [
            self.instruction,
            "Drag a box around it, then press Enter   ·   arrow keys nudge, "
            "Shift+arrows resize   ·   Esc cancels",
        ]
        cx = self.width // 2
        text = self.canvas.create_text(cx, 34, text="\n".join(lines), fill=theme.FG,
                                       font=theme.FONT, justify="center")
        bounds = self.canvas.bbox(text)
        pad = 16
        box = self.canvas.create_rectangle(
            bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad,
            fill="#0d1117", outline=_SELECT,
        )
        self.canvas.tag_lower(box, text)

    def _redraw(self) -> None:
        if self._sel is None:
            for item in self._dim:
                self.canvas.coords(item, 0, 0, 0, 0)
            self.canvas.itemconfigure(self._rect, state="hidden")
            self.canvas.itemconfigure(self._readout_bg, state="hidden")
            self.canvas.itemconfigure(self._readout, state="hidden")
            return

        x0, y0, x1, y1 = self._normalised()
        self.canvas.itemconfigure(self._rect, state="normal")
        self.canvas.coords(self._rect, x0, y0, x1, y1)
        self.canvas.coords(self._dim[0], 0, 0, self.width, y0)
        self.canvas.coords(self._dim[1], 0, y1, self.width, self.height)
        self.canvas.coords(self._dim[2], 0, y0, x0, y1)
        self.canvas.coords(self._dim[3], x1, y0, self.width, y1)
        for item in self._dim:
            self.canvas.tag_raise(item)
        self.canvas.tag_raise(self._rect)

        label = f"{x1 - x0} × {y1 - y0} px    at {x0}, {y0}"
        tx, ty = x0, (y1 + 8 if y1 + 30 < self.height else y0 - 26)
        self.canvas.itemconfigure(self._readout, state="normal", text=label)
        self.canvas.coords(self._readout, tx + 6, ty + 4)
        bounds = self.canvas.bbox(self._readout)
        self.canvas.itemconfigure(self._readout_bg, state="normal")
        self.canvas.coords(self._readout_bg, bounds[0] - 6, bounds[1] - 4, bounds[2] + 6, bounds[3] + 4)
        self.canvas.tag_raise(self._readout_bg)
        self.canvas.tag_raise(self._readout)

    def _normalised(self) -> Tuple[int, int, int, int]:
        x0, y0, x1, y1 = self._sel
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    # ---- pointer ---------------------------------------------------------- #

    def _clamp(self, x: int, y: int) -> Tuple[int, int]:
        return max(0, min(x, self.width)), max(0, min(y, self.height))

    def _on_press(self, event) -> None:
        self._origin = self._clamp(event.x, event.y)
        self._sel = (*self._origin, *self._origin)
        self._redraw()

    def _on_drag(self, event) -> None:
        if self._origin is None:
            return
        self._sel = (*self._origin, *self._clamp(event.x, event.y))
        self._redraw()
        self._update_loupe(event.x, event.y)

    def _on_release(self, event) -> None:
        if self._origin is None:
            return
        self._sel = (*self._origin, *self._clamp(event.x, event.y))
        self._origin = None
        x0, y0, x1, y1 = self._normalised()
        if x1 - x0 < _MIN_SIZE or y1 - y0 < _MIN_SIZE:
            self._sel = None
        self._redraw()

    def _on_move(self, event) -> None:
        self.canvas.coords(self._vline, event.x, 0, event.x, self.height)
        self.canvas.coords(self._hline, 0, event.y, self.width, event.y)
        self._update_loupe(event.x, event.y)

    def _update_loupe(self, x: int, y: int) -> None:
        now = time.perf_counter()
        if now - self._last_loupe < 0.04:
            return
        self._last_loupe = now

        x0, y0 = max(0, x - _LOUPE_HALF), max(0, y - _LOUPE_HALF)
        patch = self.frame[y0:y0 + _LOUPE_HALF * 2, x0:x0 + _LOUPE_HALF * 2]
        if patch.size == 0:
            return
        size = _LOUPE_HALF * 2 * _LOUPE_ZOOM
        self.loupe.delete("all")
        self.loupe.create_image(
            0, 0, anchor="nw", image=self._images.set("loupe", imaging.zoomed_photo(patch, _LOUPE_ZOOM))
        )
        mid_x = (x - x0) * _LOUPE_ZOOM
        mid_y = (y - y0) * _LOUPE_ZOOM
        self.loupe.create_line(mid_x, 0, mid_x, size, fill=_SELECT)
        self.loupe.create_line(0, mid_y, size, mid_y, fill=_SELECT)

        corner = ("e" if x < self.width // 2 else "w", "s" if y < self.height // 2 else "n")
        if corner != self._loupe_corner:
            self._loupe_corner = corner
            margin = 18
            self.loupe.place(
                x=(self.width - size - margin) if corner[0] == "e" else margin,
                y=(self.height - size - margin) if corner[1] == "s" else margin,
            )

    # ---- keyboard --------------------------------------------------------- #

    def _nudge(self, delta: Tuple[int, int], resize: bool) -> None:
        if self._sel is None:
            return
        dx, dy = delta
        x0, y0, x1, y1 = self._normalised()
        if resize:
            x1, y1 = max(x0 + _MIN_SIZE, x1 + dx), max(y0 + _MIN_SIZE, y1 + dy)
        else:
            x0, y0, x1, y1 = x0 + dx, y0 + dy, x1 + dx, y1 + dy
        if x0 < 0 or y0 < 0 or x1 > self.width or y1 > self.height:
            return
        self._sel = (x0, y0, x1, y1)
        self._redraw()

    def _accept(self) -> None:
        if self._sel is None:
            return
        x0, y0, x1, y1 = self._normalised()
        if x1 - x0 < _MIN_SIZE or y1 - y0 < _MIN_SIZE:
            return
        self.result = (x0, y0, x1 - x0, y1 - y0)
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self._images.clear()
        self.destroy()
