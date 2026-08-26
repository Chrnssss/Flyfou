"""Save the game's own pixels to disk so the detector can be tuned offline.

PrintWindow renders the client even while another window covers it, so this
takes nothing away from whoever is using the machine. Several frames a second
apart, because one frame of a scene with walking monsters proves little.
"""
import sys
import time

sys.path.insert(0, ".")

import numpy as np

from flyfou import vision, winutil
from flyfou.profile import ProfileStore

SHOTS = 4

store = ProfileStore()
profile = store.load(sys.argv[1] if len(sys.argv) > 1 else store.names()[0])
info = winutil.find_window(profile.window_title, profile.window_process)
if info is None:
    raise SystemExit("the game window isn't open")

frames = []
for shot in range(SHOTS):
    image = winutil.print_window(info.hwnd)
    if image is None or vision.frame_is_blank(image):
        print("  frame %d came back blank — skipped" % shot)
    else:
        frames.append(image)
        print("  frame %d  %dx%d" % (shot, image.shape[1], image.shape[0]))
    time.sleep(1.2)

if not frames:
    raise SystemExit("every frame was blank")
np.save("_frames.npy", np.stack(frames))
vision.imwrite("_frame0.png", frames[0])
print("saved %d frames to _frames.npy" % len(frames))
