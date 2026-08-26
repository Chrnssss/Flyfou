"""Everything that looks at pixels: template matching, HP bars, colour picking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None


@dataclass
class Match:
    score: float
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> Tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


@dataclass
class ColorGuess:
    rgb: Tuple[int, int, int]
    tolerance: int
    coverage: float  # share of the region that matched, 0-1


def scale_template(template: np.ndarray, factor: float) -> np.ndarray:
    if abs(factor - 1.0) < 0.02:
        return template
    h, w = template.shape[:2]
    new = (max(4, int(round(w * factor))), max(4, int(round(h * factor))))
    interp = cv2.INTER_AREA if factor < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(template, new, interpolation=interp)


def best_match(scene: np.ndarray, templates: Sequence[np.ndarray]) -> Optional[Match]:
    """Highest-scoring template anywhere in the scene. Score is always returned,
    even when it's poor — callers threshold it themselves so they can report the
    near-miss."""
    best: Optional[Match] = None
    for template in templates:
        if template is None or template.size == 0:
            continue
        th, tw = template.shape[:2]
        if th > scene.shape[0] or tw > scene.shape[1]:
            continue
        result = cv2.matchTemplate(scene, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        if best is None or score > best.score:
            best = Match(float(score), int(loc[0]), int(loc[1]), tw, th)
    return best


def bar_fill_fraction(region: np.ndarray, rgb: Sequence[int], tolerance: int) -> float:
    """How full a bar is, 0-1.

    Counts matching *lines* across the bar rather than tracking the filled edge:
    that works whether the bar drains left-to-right or right-to-left, and a single
    stray matching pixel near the end can't read as a nearly-full bar.
    """
    if region is None or region.size == 0:
        return 0.0
    h, w = region.shape[:2]
    target = np.array([rgb[2], rgb[1], rgb[0]], dtype=np.int16)
    diff = np.abs(region.astype(np.int16) - target)
    mask = np.all(diff <= tolerance, axis=2)
    axis, length = (1, h) if h > w * 1.5 else (0, w)
    hits = mask.mean(axis=axis) >= 0.34
    return float(hits.sum()) / float(length)


def dominant_bar_color(region: np.ndarray) -> Optional[ColorGuess]:
    """Pick the strongest saturated colour in a region — i.e. the filled part of a
    bar, ignoring its grey backing, dark border and any text drawn over it."""
    if region is None or region.size == 0:
        return None
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0].astype(np.int32), hsv[..., 1], hsv[..., 2]

    mask = None
    for sat_min, val_min in ((90, 70), (60, 50), (35, 40)):
        candidate = (sat >= sat_min) & (val >= val_min)
        if candidate.sum() >= max(12, 0.06 * candidate.size):
            mask = candidate
            break
    if mask is None:
        return None

    histogram = np.bincount(hue[mask], minlength=180).astype(np.float32)
    wrapped = np.concatenate([histogram[-8:], histogram, histogram[:8]])
    smoothed = np.convolve(wrapped, np.ones(9, np.float32), mode="same")[8:188]
    peak = int(np.argmax(smoothed))

    delta = np.abs(hue - peak)
    selected = mask & (np.minimum(delta, 180 - delta) <= 12)
    if selected.sum() < 8:
        return None

    pixels = region[selected].astype(np.float32)
    median = np.median(pixels, axis=0)
    spread = float(np.percentile(np.abs(pixels - median), 85, axis=0).max())
    b, g, r = median
    return ColorGuess(
        rgb=(int(round(r)), int(round(g)), int(round(b))),
        tolerance=int(np.clip(spread * 1.6 + 12, 18, 70)),
        coverage=float(selected.sum()) / float(selected.size),
    )


def frame_is_blank(frame: Optional[np.ndarray]) -> bool:
    """A flat frame means we're capturing nothing — usually fullscreen-exclusive mode."""
    if frame is None or frame.size == 0:
        return True
    small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
    return float(small.std()) < 1.5


def color_mask_preview(region: np.ndarray, rgb: Sequence[int], tolerance: int) -> np.ndarray:
    """Region with matched pixels kept and everything else dimmed — shown in the
    wizard so a bad colour/tolerance is obvious at a glance."""
    target = np.array([rgb[2], rgb[1], rgb[0]], dtype=np.int16)
    mask = np.all(np.abs(region.astype(np.int16) - target) <= tolerance, axis=2)
    out = (region.astype(np.float32) * 0.25).astype(np.uint8)
    out[mask] = region[mask]
    return out


def to_rgb(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def encode_png(frame: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError("failed to encode PNG")
    return buf.tobytes()


def imread(path: str) -> Optional[np.ndarray]:
    """cv2.imread can't handle non-ASCII paths on Windows; profiles live under the
    user's home directory, which frequently has one."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite(path: str, frame: np.ndarray) -> None:
    with open(path, "wb") as handle:
        handle.write(encode_png(frame))
