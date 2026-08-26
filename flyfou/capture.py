"""Screen capture. mss instances are not thread-safe, so each thread gets one."""

from __future__ import annotations

import threading
from typing import Optional, Tuple

import numpy as np

try:
    import mss
except ImportError:
    mss = None

from . import winutil

_local = threading.local()


def _sct():
    inst = getattr(_local, "sct", None)
    if inst is None:
        inst = mss.mss()
        _local.sct = inst
    return inst


def _clamp_to_screen(x: int, y: int, w: int, h: int) -> Optional[Tuple[int, int, int, int]]:
    vx, vy, vw, vh = winutil.virtual_screen()
    left, top = max(x, vx), max(y, vy)
    right, bottom = min(x + w, vx + vw), min(y + h, vy + vh)
    if right - left < 1 or bottom - top < 1:
        return None
    return left, top, right - left, bottom - top


def grab(x: int, y: int, w: int, h: int) -> Optional[np.ndarray]:
    """Screen region as a BGR array, or None if it's entirely off-screen."""
    box = _clamp_to_screen(x, y, w, h)
    if box is None:
        return None
    left, top, width, height = box
    shot = _sct().grab({"left": left, "top": top, "width": width, "height": height})
    return np.array(shot)[:, :, :3]


def grab_client(hwnd: int) -> Optional[Tuple[np.ndarray, Tuple[int, int, int, int]]]:
    """Whole client area of a window, plus its screen rect."""
    rect = winutil.client_rect(hwnd)
    frame = grab(*rect)
    return (frame, rect) if frame is not None else None


def crop_fraction(frame: np.ndarray, frac) -> np.ndarray:
    """Crop a (fx, fy, fw, fh) proportional rect out of an already-captured frame."""
    h, w = frame.shape[:2]
    x, y, rw, rh = frac.to_pixels(w, h)
    return frame[y:y + rh, x:x + rw]
