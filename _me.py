"""Is our own character in the candidate list, and how close does it come?

_check.py reported that nothing was skipped as "us" on the real frames, which
either means the character isn't being detected at all - fine, but only by luck -
or that it is being detected and is sitting in the queue as candidate number one.
The difference matters: probing ourselves wastes an engage, and clicking
ourselves drops the target.
"""
import sys

sys.path.insert(0, ".")

import numpy as np

from flyfou import vision
from flyfou.profile import ProfileStore

frames = np.load("_frames.npy")
store = ProfileStore()
profile = store.load(sys.argv[1] if len(sys.argv) > 1 else store.names()[0])
CH, CW = frames.shape[1:3]
ax, ay, aw, ah = profile.play_area.to_pixels(CW, CH)
ME = (CW // 2 - ax, CH // 2 - ay)
radius = vision.self_radius(CH)
print("character sits at %s in the hunting ground; 'that's us' radius is %d px\n"
      % (ME, radius))

for index, frame in enumerate(frames):
    region = frame[ay:ay + ah, ax:ax + aw]
    blobs = vision.find_blobs(region)
    ranked = sorted(blobs, key=lambda b: (b.center[0] - ME[0]) ** 2 + (b.center[1] - ME[1]) ** 2)
    print("  frame %d" % index)
    for blob in ranked[:4]:
        dist = ((blob.center[0] - ME[0]) ** 2 + (blob.center[1] - ME[1]) ** 2) ** 0.5
        covers = (blob.x <= ME[0] <= blob.x + blob.w and blob.y <= ME[1] <= blob.y + blob.h)
        print("     %3dx%-4d at %4d,%-4d  centre %4.0f px away%s"
              % (blob.w, blob.h, blob.x, blob.y, dist,
                 "   <- its box covers the character" if covers else ""))

    # Unfiltered, to see whether the character exists as a shape at all.
    loose = vision.find_blobs(region, min_w=4, min_h=4, max_w=9999, max_h=9999,
                              min_area=40, min_fill=0.0, drop_clipped=False)
    covering = [b for b in loose
                if b.x <= ME[0] <= b.x + b.w and b.y <= ME[1] <= b.y + b.h]
    for blob in covering:
        print("     with no size limits, the shape over the character is %dx%d (area %d)"
              % (blob.w, blob.h, blob.area))
