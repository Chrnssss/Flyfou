"""Does a cheaper terrain estimate still find the monsters?

medianBlur at ksize 61 costs 57 ms of the 97 ms a detection pass takes, and the
pass runs twice per engage. A box blur of the same width is 2.6 ms - but a mean
is dragged toward whatever sits in the window, so a monster partly erases itself
from its own background and the contrast that reveals it shrinks. A median just
ignores it. Whether that matters at this sprite size is a question for the
oracle, not for taste.

Scored both ways that count: how many of the monsters we could ever reach, and
how many hovers are burnt before one turns up.
"""
import sys
import time

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


# ---- terrain estimators -------------------------------------------------- #

def median61(region):
    return cv2.medianBlur(region, 61)


def box61(region):
    return cv2.blur(region, (61, 61))


def box91(region):
    return cv2.blur(region, (91, 91))


def half_median31(region):
    small = cv2.resize(region, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    return cv2.resize(cv2.medianBlur(small, 31), (region.shape[1], region.shape[0]),
                      interpolation=cv2.INTER_LINEAR)


def quarter_median21(region):
    small = cv2.resize(region, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    return cv2.resize(cv2.medianBlur(small, 21), (region.shape[1], region.shape[0]),
                      interpolation=cv2.INTER_LINEAR)


ESTIMATORS = [("medianBlur 61 (current)", median61),
              ("box 61", box61),
              ("box 91", box91),
              ("half size, median 31", half_median31),
              ("quarter size, median 21", quarter_median21)]


def points(region, terrain, delta, close, open_, border=True):
    background = terrain(region)
    diff = cv2.absdiff(region, background).max(axis=2)
    mask = cv2.threshold(diff, delta, 255, cv2.THRESH_BINARY)[1]
    if close:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((close, close), np.uint8))
    if open_:
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((open_, open_), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i][:5])
        if not (MIN_W <= w <= MAX_W and MIN_H <= h <= MAX_H):
            continue
        if area < MIN_AREA or area / float(w * h) < MIN_FILL:
            continue
        if border and (x <= 2 or y <= 2 or x + w >= aw - 2 or y + h >= ah - 2):
            continue
        out.append((x + w // 2, y + h // 2))
    return out


def evaluate(name, terrain, delta, close, open_):
    radius = vision.self_radius(CH)
    covered = total = found = 0
    ranks, offered = [], []
    for frame in frames:
        region = frame[ay:ay + ah, ax:ax + aw]
        truth = oracle(region)
        monsters = truth_blobs(truth)
        total += len(monsters)
        pts = [p for p in points(region, terrain, delta, close, open_)
               if (p[0] - ME[0]) ** 2 + (p[1] - ME[1]) ** 2 > radius ** 2]
        pts.sort(key=lambda p: (p[0] - ME[0]) ** 2 + (p[1] - ME[1]) ** 2)
        offered.append(len(pts))
        reached, rank = set(), None
        for i, (px, py) in enumerate(pts[:BUDGET]):
            if truth[py, px]:
                rank = i + 1 if rank is None else rank
                for m, (mx, my, mw, mh) in enumerate(monsters):
                    if mx <= px <= mx + mw and my <= py <= my + mh:
                        reached.add(m)
        covered += len(reached)
        if rank:
            found += 1
            ranks.append(rank)

    region = frames[0][ay:ay + ah, ax:ax + aw]
    terrain(region)
    ms = min(_time(lambda: points(region, terrain, delta, close, open_)) for _ in range(3)) * 1000
    print("  %-32s %2d/%d frames  %4s probes  %4.1f reached  %6.1f ms"
          % (name, found, len(frames),
             "%.1f" % (sum(ranks) / float(len(ranks))) if ranks else "-",
             covered / float(len(frames)), ms))


def _time(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


print("close 5, open 3 - the current morphology\n")
for name, fn in ESTIMATORS:
    evaluate(name, fn, vision._BLOB_DELTA, 5, 3)

print("\nclose 0, open 5 - the one that stopped monsters welding\n")
for name, fn in ESTIMATORS:
    evaluate(name, fn, vision._BLOB_DELTA, 0, 5)

print("\nbox 61, close 0 open 5, sweeping the threshold\n")
for delta in (14, 18, 22, 26, 32, 40):
    evaluate("delta %d" % delta, box61, delta, 0, 5)

print("\nhalf-size median, close 0 open 5, sweeping the threshold\n")
for delta in (14, 18, 22, 26, 32, 40):
    evaluate("delta %d" % delta, half_median31, delta, 0, 5)
