"""The only number that predicts farming speed: probes burnt before a hit.

The bot never clicks a candidate blind. It moves the mouse there, waits for the
cursor to change, and clicks only if the cursor says "attackable". So a candidate
that isn't a monster costs one hover - about 50 ms - not a wasted click that
would walk the character somewhere stupid.

That makes raw hit rate misleading. What matters is whether a monster turns up
inside the probe budget, and how much of the budget it eats. A detector that
offers 200 candidates at 40% is better than one offering 12 at 58%, as long as
the good ones sort early.

Candidates are ordered the way the bot orders them: nearest the character first.
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
ME = (CW // 2 - ax, CH // 2 - ay)
BUDGET = 12

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


def raw_mask(region, close=5, open_=3):
    background = cv2.medianBlur(region, vision._BLOB_BLUR)
    diff = cv2.absdiff(region, background).max(axis=2)
    mask = cv2.threshold(diff, vision._BLOB_DELTA, 255, cv2.THRESH_BINARY)[1]
    if close:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((close, close), np.uint8))
    if open_:
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((open_, open_), np.uint8))
    return mask


def on_border(x, y, w, h, margin=2):
    """Clipped by the edge of the hunting ground, so its real shape is unknown.

    The interface panels sit just outside the box and get sliced by it; what
    survives is a rectangle flush against the edge that passes every size test.
    """
    return x <= margin or y <= margin or x + w >= aw - margin or y + h >= ah - margin


def points_plain(region, close=5, open_=3, border=False):
    count, _, stats, _ = cv2.connectedComponentsWithStats(raw_mask(region, close, open_), 8)
    out = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i][:5])
        if not (MIN_W <= w <= MAX_W and MIN_H <= h <= MAX_H):
            continue
        if area < MIN_AREA or area / float(w * h) < MIN_FILL:
            continue
        if border and on_border(x, y, w, h):
            continue
        out.append((x + w // 2, y + h // 2))
    return out


def points_peaks(region, close=5, open_=3, spacing=9, border=False):
    mask = raw_mask(region, close, open_)
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    dilated = cv2.dilate(dist, np.ones((spacing, spacing), np.uint8))
    peaks = ((dist >= dilated - 1e-6) & (dist > 3.0)).astype(np.uint8)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(peaks, 8)
    out = []
    for i in range(1, count):
        px, py = int(centroids[i][0]), int(centroids[i][1])
        if border and (px <= 2 or py <= 2 or px >= aw - 3 or py >= ah - 3):
            continue
        out.append((px, py))
    return out


def drop_self(points, radius):
    return [p for p in points
            if (p[0] - ME[0]) ** 2 + (p[1] - ME[1]) ** 2 > radius ** 2]


def evaluate(name, points_for):
    radius = vision.self_radius(CH)
    first_hit, found_in_budget, offered = [], 0, []
    distinct = 0
    for frame in frames:
        region = frame[ay:ay + ah, ax:ax + aw]
        truth = oracle(region)
        monsters = truth_blobs(truth)
        points = drop_self(points_for(region), radius)
        points = [p for p in points if 0 <= p[0] < aw and 0 <= p[1] < ah]
        points.sort(key=lambda p: (p[0] - ME[0]) ** 2 + (p[1] - ME[1]) ** 2)
        offered.append(len(points))

        rank = None
        reached = set()
        for i, (px, py) in enumerate(points[:BUDGET]):
            if truth[py, px]:
                if rank is None:
                    rank = i + 1
                for m, (mx, my, mw, mh) in enumerate(monsters):
                    if mx <= px <= mx + mw and my <= py <= my + mh:
                        reached.add(m)
        distinct += len(reached)
        if rank is not None:
            found_in_budget += 1
            first_hit.append(rank)
    hit = "%.1f" % (sum(first_hit) / float(len(first_hit))) if first_hit else " - "
    print("  %-38s  %s/%d frames find one   %5s probes to first hit   %4.1f monsters in budget   %3.0f offered"
          % (name, found_in_budget, len(frames), hit,
             distinct / float(len(frames)), sum(offered) / float(len(offered))))


print("budget is %d probes per tick, ordered nearest the character first\n" % BUDGET)

print("where we are")
evaluate("current", lambda r: points_plain(r))
evaluate("current + drop border-clipped", lambda r: points_plain(r, border=True))

print("\nno CLOSE, so neighbours stop welding")
for close, open_ in ((0, 3), (0, 5), (3, 5)):
    evaluate("close %d open %d, drop border" % (close, open_),
             lambda r, c=close, o=open_: points_plain(r, c, o, border=True))

print("\ndistance-transform peaks")
for close, open_ in ((5, 3), (0, 5)):
    for spacing in (9, 13, 17):
        evaluate("peaks %d px, close %d open %d" % (spacing, close, open_),
                 lambda r, c=close, o=open_, s=spacing: points_peaks(r, c, o, s, border=True))
