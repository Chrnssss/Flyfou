"""Score the shipped detector, not a copy of it.

Every measurement so far reimplemented the pipeline inside the test script, so
none of them prove the code that actually runs got better. This one calls
vision.find_blobs itself and applies the bot's own ordering rules.

Baseline recorded before the change, on these same four frames:
    3.0 probes to the first monster, 7.0 monsters reached, 84 ms a pass.
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


ranks, reached_total, monsters_total, hits, probes = [], 0, 0, 0, 0
found_frames = 0
character_kept = 0

for index, frame in enumerate(frames):
    region = frame[ay:ay + ah, ax:ax + aw]
    truth = oracle(region)
    monsters = truth_blobs(truth)
    monsters_total += len(monsters)

    # Exactly what the bot does: detect, drop ourselves, sort nearest first.
    seen = vision.find_blobs(region)
    others, mine = vision.split_self(seen, ME, vision.self_radius(CH))
    others.sort(key=lambda b: (b.center[0] - ME[0]) ** 2 + (b.center[1] - ME[1]) ** 2)
    character_kept += len(mine)

    rank, reached = None, set()
    for i, blob in enumerate(others[:BUDGET]):
        px, py = blob.center
        probes += 1
        if truth[py, px]:
            hits += 1
            rank = i + 1 if rank is None else rank
            for m, (mx, my, mw, mh) in enumerate(monsters):
                if mx <= px <= mx + mw and my <= py <= my + mh:
                    reached.add(m)
    reached_total += len(reached)
    if rank:
        found_frames += 1
        ranks.append(rank)
    print("  frame %d  %2d candidates, %2d after dropping us | first monster at probe %-3s "
          "reached %2d of %2d"
          % (index, len(seen), len(others), rank or "-", len(reached), len(monsters)))

region = frames[0][ay:ay + ah, ax:ax + aw]
vision.find_blobs(region)


def elapsed(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


ms = min(elapsed(lambda: vision.find_blobs(region)) for _ in range(5)) * 1000

print("\n  %-38s %s / 4" % ("frames where a monster was found", found_frames))
print("  %-38s %.1f   (was 3.0)" % ("probes to the first monster",
                                    sum(ranks) / float(len(ranks)) if ranks else 0))
print("  %-38s %.1f   (was 7.0)" % ("monsters reached inside the budget",
                                    reached_total / float(len(frames))))
print("  %-38s %d of %d" % ("probes that landed on a monster", hits, probes))
print("  %-38s %.1f ms  (was 84 ms)" % ("one detection pass", ms))
print("  %-38s %.1f per frame" % ("character blobs correctly skipped",
                                  character_kept / float(len(frames))))
