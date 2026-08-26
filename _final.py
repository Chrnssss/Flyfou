"""Confirm the winner before it goes into the shipped detector.

Quarter-size median came out ahead on every axis at once, which is the kind of
result that deserves a second look rather than a celebration. So: sweep the
threshold around it, check the border rule earns its place, and make sure the
gain isn't one lucky frame by printing each frame on its own.
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


def median61(region):
    return cv2.medianBlur(region, 61)


def quarter21(region):
    small = cv2.resize(region, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    return cv2.resize(cv2.medianBlur(small, 21), (region.shape[1], region.shape[0]),
                      interpolation=cv2.INTER_LINEAR)


def points(region, terrain, delta, border):
    diff = cv2.absdiff(region, terrain(region)).max(axis=2)
    mask = cv2.threshold(diff, delta, 255, cv2.THRESH_BINARY)[1]
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
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


def measure(terrain, delta, border):
    radius = vision.self_radius(CH)
    per_frame = []
    for frame in frames:
        region = frame[ay:ay + ah, ax:ax + aw]
        truth = oracle(region)
        monsters = truth_blobs(truth)
        pts = [p for p in points(region, terrain, delta, border)
               if (p[0] - ME[0]) ** 2 + (p[1] - ME[1]) ** 2 > radius ** 2]
        pts.sort(key=lambda p: (p[0] - ME[0]) ** 2 + (p[1] - ME[1]) ** 2)
        reached, rank, hits = set(), None, 0
        for i, (px, py) in enumerate(pts[:BUDGET]):
            if truth[py, px]:
                hits += 1
                rank = i + 1 if rank is None else rank
                for m, (mx, my, mw, mh) in enumerate(monsters):
                    if mx <= px <= mx + mw and my <= py <= my + mh:
                        reached.add(m)
        per_frame.append((rank, len(reached), hits, min(BUDGET, len(pts)), len(monsters)))
    return per_frame


def report(name, terrain, delta, border=True):
    rows = measure(terrain, delta, border)
    ranks = [r[0] for r in rows if r[0]]
    region = frames[0][ay:ay + ah, ax:ax + aw]
    terrain(region)
    ms = min(_t(lambda: points(region, terrain, delta, border)) for _ in range(3)) * 1000
    print("  %-34s %4s probes  %4.1f reached  %2d/%2d probes hit  %6.1f ms"
          % (name, "%.1f" % (sum(ranks) / float(len(ranks))) if ranks else "-",
             sum(r[1] for r in rows) / float(len(rows)),
             sum(r[2] for r in rows), sum(r[3] for r in rows), ms))
    return rows


def _t(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


print("threshold sweep on the quarter-size median\n")
for delta in (18, 22, 26, 30, 34, 40):
    report("delta %d" % delta, quarter21, delta)

print("\ndoes dropping border-clipped blobs earn its place?\n")
report("quarter median, keep border blobs", quarter21, 26, border=False)
report("quarter median, drop border blobs", quarter21, 26, border=True)

print("\nhead to head, per frame\n")
old = measure(median61, vision._BLOB_DELTA, False)
new = measure(quarter21, 26, True)
print("        %-28s %s" % ("medianBlur 61, keep border", "quarter median 21, drop border"))
for i, (a, b) in enumerate(zip(old, new)):
    print("  fr %d  probe %-4s reached %2d of %-4d probe %-4s reached %2d of %d"
          % (i, a[0] or "-", a[1], a[4], b[0] or "-", b[1], b[4]))
