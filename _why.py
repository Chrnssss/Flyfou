"""Every connected component in the hunting ground, and why each was kept or cut.

find_blobs applies size and fill filters before returning. When obvious monsters
don't come back, the question is which filter ate them - so this repeats the
detection with no filtering at all and reports the reason for each rejection.
Read-only; sends no input.
"""
import sys
from collections import Counter

sys.path.insert(0, ".")

import cv2
import numpy as np

from flyfou import capture, vision, winutil
from flyfou.profile import ProfileStore

store = ProfileStore()
profile = store.load(sys.argv[1] if len(sys.argv) > 1 else store.names()[0])
info = winutil.find_window(profile.window_title, profile.window_process)
frame, (_, _, cw, ch) = capture.grab_client(info.hwnd)
ax, ay, aw, ah = profile.play_area.to_pixels(cw, ch)
region = frame[ay:ay + ah, ax:ax + aw]

background = cv2.medianBlur(region, vision._BLOB_BLUR)
delta = cv2.absdiff(region, background).max(axis=2)
mask = cv2.threshold(delta, vision._BLOB_DELTA, 255, cv2.THRESH_BINARY)[1]
mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)

MIN_W, MAX_W, MIN_H, MAX_H, MIN_AREA, MIN_FILL = 10, 120, 12, 140, 90, 0.14


def verdict(w, h, area):
    if w < MIN_W:
        return "too narrow"
    if w > MAX_W:
        return "too wide"
    if h < MIN_H:
        return "too short"
    if h > MAX_H:
        return "too tall"
    if area < MIN_AREA:
        return "too small"
    if area / float(w * h) < MIN_FILL:
        return "too sparse"
    return "kept"


rows = []
for index in range(1, count):
    x, y, w, h, area = (int(v) for v in stats[index][:5])
    rows.append((w, h, area, x, y, verdict(w, h, area)))

tally = Counter(r[5] for r in rows)
print("%d components in the hunting ground" % len(rows))
for reason, n in tally.most_common():
    print("   %-12s %d" % (reason, n))

print("\nthe 25 biggest, by area:")
print("   %-9s %-6s %-11s %s" % ("size", "area", "at", "verdict"))
for w, h, area, x, y, why in sorted(rows, key=lambda r: -r[2])[:25]:
    print("   %3dx%-5d %-6d %4d,%-6d %s" % (w, h, area, ax + x, ay + y, why))

shot = frame.copy()
colours = {"kept": (100, 220, 100), "too wide": (60, 60, 240), "too tall": (60, 130, 240),
           "too sparse": (240, 200, 60), "too small": (200, 200, 200),
           "too narrow": (200, 200, 200), "too short": (200, 200, 200)}
for w, h, area, x, y, why in rows:
    if why in ("too small", "too narrow", "too short") and area < 300:
        continue  # speckle, not worth drawing
    cv2.rectangle(shot, (ax + x, ay + y), (ax + x + w, ay + y + h), colours[why], 2)
cv2.rectangle(shot, (ax, ay), (ax + aw, ay + ah), (255, 160, 60), 2)
vision.imwrite("_why.png", shot)
vision.imwrite("_why_mask.png", cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR))
print("\ngreen kept · red too wide · orange too tall · yellow too sparse")
