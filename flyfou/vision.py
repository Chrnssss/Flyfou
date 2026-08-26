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

_BLOB_SCALE = 0.25  # the terrain estimate is made on a quarter-size copy
_BLOB_BLUR = 21  # median window at that scale — about 84 px of the real frame
_BLOB_DELTA = 26  # how far off the local background a pixel has to be to count
_BLOB_EDGE = 2  # px of the region's own border that count as "clipped by it"


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
class Blob:
    """Something in the scene that isn't the ground — position only, no identity."""

    x: int
    y: int
    w: int
    h: int
    area: int

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


def _terrain(region: np.ndarray) -> np.ndarray:
    """What the ground would look like with nothing standing on it.

    A median ignores anything narrower than half its window, so a wide one keeps
    the terrain and discards the sprites sitting on it. Taken at full size that
    costs 57 ms of a 100 ms tick — most of the loop, spent every frame.

    Shrinking first buys far more than it costs. Terrain is smooth by definition,
    so it survives being sampled at a quarter size, while the median gets
    sixteen times fewer pixels to sort. Measured against a colour oracle on this
    map it finds slightly *more* monsters than the full-size version did, at a
    third of the price — the sprites are small enough that dropping to a quarter
    size removes them from the estimate rather than blurring them into it.
    """
    h, w = region.shape[:2]
    small = cv2.resize(region, (max(8, int(w * _BLOB_SCALE)), max(8, int(h * _BLOB_SCALE))),
                       interpolation=cv2.INTER_AREA)
    # medianBlur wants an odd window, and one that fits inside what it's given.
    window = min(_BLOB_BLUR, (min(small.shape[:2]) - 1) | 1)
    if window >= 3:
        small = cv2.medianBlur(small, window)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def find_blobs(
    region: np.ndarray,
    min_w: int = 10,
    max_w: int = 120,
    min_h: int = 12,
    max_h: int = 140,
    min_area: int = 90,
    min_fill: float = 0.14,
    drop_clipped: bool = True,
) -> List[Blob]:
    """Everything in the region that isn't the terrain it's standing on.

    Recognising a creature by appearance doesn't work: it turns, it animates, and
    it shrinks with distance, so neither templates nor colour histograms can tell
    one from a cactus — measured on this game, both overlap rather than separate.
    Separating *objects* from *terrain* is easy by comparison, because terrain is
    smooth and low-contrast: a wide median blur estimates it, and anything that
    differs from that estimate is a thing standing on it.

    This deliberately says only where things are. Deciding which of them are worth
    clicking is somebody else's job.

    Blobs flush against the region's own edge are dropped by default. The
    interface panels sit outside the hunting ground and get sliced by it, and
    what survives is a rectangle of the right size and fill that passes every
    other test — measured, a dozen of them per frame, each costing a hover.
    """
    if region is None or region.size == 0:
        return []
    height, width = region.shape[:2]
    delta = cv2.absdiff(region, _terrain(region))
    if delta.ndim == 3:
        delta = delta.max(axis=2)

    mask = cv2.threshold(delta, _BLOB_DELTA, 255, cv2.THRESH_BINARY)[1]
    # Close first to pull a sprite's separately-lit parts into one shape, then open
    # to drop the speckle that survives from terrain detail.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    blobs = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index][:5])
        if not (min_w <= w <= max_w and min_h <= h <= max_h and area >= min_area):
            continue
        if area / float(w * h) < min_fill:
            continue  # a sparse box is a scatter of terrain detail, not one object
        if drop_clipped and (x <= _BLOB_EDGE or y <= _BLOB_EDGE
                             or x + w >= width - _BLOB_EDGE
                             or y + h >= height - _BLOB_EDGE):
            continue  # cut off by the edge, so its real shape is anyone's guess
        blobs.append(Blob(x, y, w, h, area))
    return blobs


def self_radius(client_h: int) -> int:
    """How far from the middle of the view still counts as our own character.

    Half a sprite: measured about 90 px tall in a 900 px client, and it scales
    with the window because the game draws the scene to fit.
    """
    return max(12, int(client_h * 0.055))


def split_self(blobs: List[Blob], centre: Tuple[int, int],
               radius: int) -> Tuple[List[Blob], List[Blob]]:
    """Separate the player's own character from everything else it found.

    The camera rides on the character, so it is always drawn at the middle of the
    view. That makes it the nearest blob of all and therefore the first one any
    nearest-first search would reach — the very last thing you want clicked.

    The test is proximity rather than "the blob containing the middle", because
    what the character detects as is not stable. When the terrain estimate used a
    window narrower than the sprite, a large flat-coloured character was judged
    to be its own background and came back as four corner fragments with a hole
    where the middle was — not one of them containing the centre point, and all
    four sorting ahead of any real monster. Anything centred within a sprite's
    reach of the middle is us, which holds however the sprite happens to break up.
    """
    mine, others = [], []
    cx, cy = centre
    for blob in blobs:
        near = (blob.center[0] - cx) ** 2 + (blob.center[1] - cy) ** 2 <= radius ** 2
        (mine if near else others).append(blob)
    return others, mine


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
