"""Try to stop monsters welding into clumps, and measure whether it worked.

_stage.py showed the loss: a third of the monsters end up inside a component so
wide it gets discarded, and those components hold two or three monsters each.
Raising the cap just means clicking the gap between them. The component has to
come apart instead.

Three ways to do that, cheapest first:
  close    - the CLOSE step bridges gaps; use a smaller kernel or none
  erode    - shrink everything until the necks between monsters snap
  peaks    - distance transform, then one blob per local maximum

Scored the same way as _tune.py: of the points we would click how many are on a
monster, and of the monsters on screen how many we could ever reach.
"""
import sys

sys.path.insert(0, ".")

import cv2
import numpy as np

from flyfou import vision
from flyfou.profile import ProfileStore

frames = np.load("_frames.npy")
store = ProfileStore()
profile = store.load(sys.argv[1] if len(sys.argv) > 1 else store.names()[0])
CH, CW = frames.shape[1:3]
ax, ay, aw, ah = profile.play_area.to_pixels(CW, CH)

MIN_W, MAX_W, MIN_H, MAX_H, MIN_AREA, MIN_FILL = 10, 120, 12, 140, 90, 0.14


def oracle(region):
    b, g, r = region[:, :, 0].astype(int), region[:, :, 1].astype(int), region[:, :, 2].astype(int)
    mask = ((((b + g + r) < 430) & (b > r + 12)) * 255).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))


def truth_blobs(mask):
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    return [tuple(int(v) for v in stats[i][:4])
            for i in range(1, count)
            if stats[i][4] >= 400 and stats[i][2] >= 14 and stats[i][3] >= 18]


def raw_mask(region, close=5, open_=3, delta=None):
    background = cv2.medianBlur(region, vision._BLOB_BLUR)
    diff = cv2.absdiff(region, background).max(axis=2)
    mask = cv2.threshold(diff, vision._BLOB_DELTA if delta is None else delta,
                         255, cv2.THRESH_BINARY)[1]
    if close:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((close, close), np.uint8))
    if open_:
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((open_, open_), np.uint8))
    return mask


def keep(w, h, area):
    return (MIN_W <= w <= MAX_W and MIN_H <= h <= MAX_H
            and area >= MIN_AREA and area / float(w * h) >= MIN_FILL)


def points_plain(mask):
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i][:5])
        if keep(w, h, area):
            out.append((x + w // 2, y + h // 2))
    return out


def points_peaks(mask, min_distance=9):
    """One point per local maximum of the distance transform.

    Two monsters that touch still each have a fat middle with a thin neck
    between; the distance to the nearest background pixel peaks separately in
    each body. Thresholding that at a fraction of the local peak separates them
    without needing to know how many there are.
    """
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    dilated = cv2.dilate(dist, np.ones((min_distance, min_distance), np.uint8))
    peaks = ((dist >= dilated - 1e-6) & (dist > 3.0)).astype(np.uint8)
    count, _, _, centroids = cv2.connectedComponentsWithStats(peaks, 8)
    return [(int(centroids[i][0]), int(centroids[i][1])) for i in range(1, count)]


def points_watershed(mask, min_distance=9):
    """Peaks as seeds, then flood each body back out and take its own centre."""
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    dilated = cv2.dilate(dist, np.ones((min_distance, min_distance), np.uint8))
    peaks = ((dist >= dilated - 1e-6) & (dist > 3.0)).astype(np.uint8)
    count, seeds, _, _ = cv2.connectedComponentsWithStats(peaks, 8)
    if count < 2:
        return []
    markers = seeds.astype(np.int32) + 1
    markers[mask == 0] = 1
    bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    cv2.watershed(bgr, markers)
    out = []
    for label in range(2, count + 1):
        ys, xs = np.where(markers == label)
        if len(xs) < MIN_AREA:
            continue
        w = int(xs.max() - xs.min()) + 1
        h = int(ys.max() - ys.min()) + 1
        if MIN_W <= w <= MAX_W and MIN_H <= h <= MAX_H:
            out.append((int(xs.min()) + w // 2, int(ys.min()) + h // 2))
    return out


def score(name, points_for):
    hits = misses = covered = total = 0
    for frame in frames:
        region = frame[ay:ay + ah, ax:ax + aw]
        truth = oracle(region)
        monsters = truth_blobs(truth)
        total += len(monsters)
        reached = set()
        for px, py in points_for(region):
            if not (0 <= px < aw and 0 <= py < ah):
                continue
            if truth[py, px]:
                hits += 1
                for m, (mx, my, mw, mh) in enumerate(monsters):
                    if mx <= px <= mx + mw and my <= py <= my + mh:
                        reached.add(m)
            else:
                misses += 1
        covered += len(reached)
    print("  %-40s %3d/%-3d on a monster (%3.0f%%)   reached %3d of %3d (%3.0f%%)"
          % (name, hits, hits + misses, 100.0 * hits / max(1, hits + misses),
             covered, total, 100.0 * covered / max(1, total)))


print("where we are")
score("current", lambda r: points_plain(raw_mask(r)))

print("\nweaker CLOSE, so neighbours stop welding")
for close in (0, 3, 5):
    for open_ in (3, 5):
        score("close %d, open %d" % (close, open_),
              lambda r, c=close, o=open_: points_plain(raw_mask(r, c, o)))

print("\none point per distance-transform peak")
for d in (7, 9, 13, 17):
    score("peaks, %d px apart" % d,
          lambda r, d=d: points_peaks(raw_mask(r), d))

print("\nsame peaks, but flooded back out and re-centred")
for d in (9, 13, 17):
    score("watershed, seeds %d px apart" % d,
          lambda r, d=d: points_watershed(raw_mask(r), d))

print("\npeaks on a mask that was never closed")
for d in (9, 13):
    score("peaks %d px, close 0 open 3" % d,
          lambda r, d=d: points_peaks(raw_mask(r, 0, 3), d))
