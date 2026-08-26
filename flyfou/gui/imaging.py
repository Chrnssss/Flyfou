"""Numpy BGR frames -> tkinter images."""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageTk

try:
    import cv2
except ImportError:
    cv2 = None


def to_pil(frame: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def fit_scale(size: Tuple[int, int], box: Tuple[int, int]) -> float:
    w, h = size
    bw, bh = box
    if w <= 0 or h <= 0:
        return 1.0
    return min(bw / w, bh / h)


def fitted_photo(frame: np.ndarray, box: Tuple[int, int], allow_upscale: bool = False):
    """Return (PhotoImage, scale, (width, height)) sized to fit inside `box`."""
    h, w = frame.shape[:2]
    scale = fit_scale((w, h), box)
    if not allow_upscale:
        scale = min(scale, 1.0)
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    image = to_pil(frame)
    if (new_w, new_h) != (w, h):
        resample = Image.NEAREST if scale > 2 else Image.BILINEAR
        image = image.resize((new_w, new_h), resample)
    return ImageTk.PhotoImage(image), scale, (new_w, new_h)


def zoomed_photo(frame: np.ndarray, factor: int):
    image = to_pil(frame)
    h, w = frame.shape[:2]
    return ImageTk.PhotoImage(image.resize((max(1, w * factor), max(1, h * factor)), Image.NEAREST))


def placeholder_photo(size: Tuple[int, int], color: str = "#23252e"):
    return ImageTk.PhotoImage(Image.new("RGB", size, color))


class ImageHolder:
    """Tk drops images that nothing references; canvases only keep the id."""

    def __init__(self):
        self._keep = {}

    def set(self, key: str, photo) -> object:
        self._keep[key] = photo
        return photo

    def clear(self) -> None:
        self._keep.clear()
