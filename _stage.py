"""Where in the pipeline each monster is lost.

_tune.py says half the monsters are never clicked, and that relaxing the size
caps hardly helps. So the loss is somewhere else. There are only three places a
monster can go missing:

  1. the mask never lit it up            - the object/terrain trick failed
  2. the mask had it, a filter cut it    - our limits are wrong
  3. it survived, but the point we aim   - the blob is a merged clump and its
     at isn't on it                        centre falls between two monsters

This walks a monster through all three and reports which one ate it.
"""
import sys
from collections import Counter

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
    mask = (((b + g + r) < 430) & (b > r + 12)) * 255
    mask = mask.astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))


def truth_blobs(mask):
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    return [tuple(int(v) for v in stats[i][:4])
            for i in range(1, count)
            if stats[i][4] >= 400 and stats[i][2] >= 14 and stats[i][3] >= 18]


def raw_components(region):
    background = cv2.medianBlur(region, vision._BLOB_BLUR)
    delta = cv2.absdiff(region, background).max(axis=2)
    mask = cv2.threshold(delta, vision._BLOB_DELTA, 255, cv2.THRESH_BINARY)[1]
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    return [tuple(int(v) for v in stats[i][:5]) for i in range(1, count)], labels


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


fate = Counter()
clump_sizes = []
for frame in frames:
    region = frame[ay:ay + ah, ax:ax + aw]
    truth = oracle(region)
    monsters = truth_blobs(truth)
    comps, labels = raw_components(region)

    for mx, my, mw, mh in monsters:
        # Which component covers the middle of this monster?
        cy, cx = my + mh // 2, mx + mw // 2
        label = labels[cy, cx]
        if label == 0:
            fate["1. mask never saw it"] += 1
            continue
        w, h, area = comps[label - 1][2], comps[label - 1][3], comps[label - 1][4]
        why = verdict(w, h, area)
        if why != "kept":
            fate["2. filtered out: %s" % why] += 1
            continue
        # It survived. Does the point we would click land inside this monster?
        bx, by = comps[label - 1][0], comps[label - 1][1]
        px, py = bx + w // 2, by + h // 2
        if mx <= px <= mx + mw and my <= py <= my + mh:
            fate["3. clicked correctly"] += 1
        else:
            fate["3. blob kept, centre misses"] += 1
            clump_sizes.append((w, h))

total = sum(fate.values())
print("%d monsters across %d frames\n" % (total, len(frames)))
for reason, n in sorted(fate.items()):
    print("  %-34s %3d  (%2.0f%%)" % (reason, n, 100.0 * n / total))

if clump_sizes:
    print("\nthe blobs whose centre missed, by size:")
    for size, n in Counter(clump_sizes).most_common(12):
        print("   %3dx%-4d  %d times" % (size[0], size[1], n))

# How many distinct monsters does one surviving blob typically swallow?
region = frames[0][ay:ay + ah, ax:ax + aw]
monsters = truth_blobs(oracle(region))
comps, labels = raw_components(region)
swallowed = Counter()
for mx, my, mw, mh in monsters:
    label = labels[my + mh // 2, mx + mw // 2]
    if label:
        swallowed[label] += 1
print("\nframe 0: monsters per connected component")
for n, times in sorted(Counter(swallowed.values()).items()):
    print("   %d component(s) hold %d monster(s)" % (times, n))
