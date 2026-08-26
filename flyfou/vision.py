"""Everything that looks at pixels: template matching, HP bars, colour picking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

_COARSE_WIDTH = 640
_MIN_COARSE_TEMPLATE = 16  # below this a shrunken template is too mushy to locate


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
    scene_h, scene_w = scene.shape[:2]
    factor = _COARSE_WIDTH / scene_w if scene_w > _COARSE_WIDTH else 1.0
    coarse: Optional[np.ndarray] = None

    for template in templates:
        if template is None or template.size == 0:
            continue
        th, tw = template.shape[:2]
        if th > scene_h or tw > scene_w:
            continue

        match = None
        if factor < 1.0 and min(th, tw) * factor >= _MIN_COARSE_TEMPLATE:
            if coarse is None:
                coarse = _shrink(scene, factor)
            match = _coarse_then_exact(scene, coarse, template, factor)
        if match is None:
            result = cv2.matchTemplate(scene, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(result)
            match = Match(float(score), int(loc[0]), int(loc[1]), tw, th)

        if best is None or match.score > best.score:
            best = match
    return best


def _shrink(image: np.ndarray, factor: float) -> np.ndarray:
    size = (max(4, int(round(image.shape[1] * factor))), max(4, int(round(image.shape[0] * factor))))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def _coarse_then_exact(scene, coarse, template, factor: float) -> Optional[Match]:
    """Locate the template in a shrunken copy of the scene, then score it properly
    at full resolution around that spot.

    Matching a 1080p frame outright costs 100-200ms per template, which is more
    than the whole loop budget; this gives the same score and position for about
    a tenth of the time. The refine pass is what keeps the score honest — the
    coarse pass only has to get close.
    """
    th, tw = template.shape[:2]
    small = _shrink(template, factor)
    if small.shape[0] > coarse.shape[0] or small.shape[1] > coarse.shape[1]:
        return None

    result = cv2.matchTemplate(coarse, small, cv2.TM_CCOEFF_NORMED)
    _, _, _, loc = cv2.minMaxLoc(result)

    pad = int(round(2 / factor)) + 6  # one coarse pixel is worth 1/factor real ones
    x, y = int(round(loc[0] / factor)), int(round(loc[1] / factor))
    scene_h, scene_w = scene.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(scene_w, x + tw + pad), min(scene_h, y + th + pad)
    roi = scene[y0:y1, x0:x1]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return None

    result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(result)
    return Match(float(score), x0 + int(loc[0]), y0 + int(loc[1]), tw, th)


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
