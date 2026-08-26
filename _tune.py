"""Score the detector against frames where the monsters can be found another way.

On this map the monsters are dark blue and the ground is snow, so a colour rule
finds them almost perfectly. That rule is useless in general - it would fail the
moment the map changes, which is the whole reason the shipped detector doesn't
use colour - but it makes a good ruler. It tells us, for each candidate the real
detector returns, whether the point we would click actually lands on a monster.

Two numbers matter:
  hit rate  - of the points we would click, how many are on a monster
  coverage  - of the monsters on screen, how many we would ever click
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


def oracle(region):
    """Monster pixels, by colour. Only valid on this snow map."""
    b, g, r = region[:, :, 0].astype(int), region[:, :, 1].astype(int), region[:, :, 2].astype(int)
    dark = (b + g + r) < 430          # snow is bright, the sprites are not
    blueish = b > r + 12              # and they are blue rather than grey shadow
    mask = ((dark & blueish) * 255).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return mask


def truth_blobs(mask):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i][:5])
        if area >= 400 and w >= 14 and h >= 18:
            out.append((x, y, w, h))
    return out


def score(name, points_for, frame_index=None):
    hits = misses = 0
    covered = total = 0
    for index, frame in enumerate(frames):
        if frame_index is not None and index != frame_index:
            continue
        region = frame[ay:ay + ah, ax:ax + aw]
        truth = oracle(region)
        monsters = truth_blobs(truth)
        total += len(monsters)
        reached = set()
        for point in points_for(region):
            px, py = point
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
    shots = len(frames) if frame_index is None else 1
    print("  %-42s %3d/%-3d points on a monster (%3.0f%%)   reached %2d of %2d monsters (%3.0f%%)"
          % (name, hits, hits + misses, 100.0 * hits / max(1, hits + misses),
             covered, total, 100.0 * covered / max(1, total)))


def centres(region, **kw):
    return [b.center for b in vision.find_blobs(region, **kw)]


print("what the detector does now")
score("centre of each blob, current limits", centres)

print("\nletting merged shapes through instead of dropping them")
for cap in (160, 200, 260, 400):
    score("max %d px wide/tall" % cap,
          lambda r, c=cap: centres(r, max_w=c, max_h=c))

print("\nmonsters actually on screen, per frame")
for index, frame in enumerate(frames):
    m = truth_blobs(oracle(frame[ay:ay + ah, ax:ax + aw]))
    print("  frame %d: %d monsters, sizes %s"
          % (index, len(m), sorted(set((w, h) for _, _, w, h in m))[:8]))

vision.imwrite("_truth.png", cv2.cvtColor(oracle(frames[0][ay:ay + ah, ax:ax + aw]),
                                          cv2.COLOR_GRAY2BGR))
print("\nwrote _truth.png — the ruler, so you can check it is not lying")
