"""Close-up of the components that were rejected, next to their mask.

The rejected blobs are wide and the monsters are not, so the suspicion is that
each monster is being welded to something else - most likely the floating name
label the game draws above it. Seeing the shape settles it.
"""
import sys

sys.path.insert(0, ".")

import cv2
import numpy as np

from flyfou import capture, vision, winutil
from flyfou.profile import ProfileStore

BOXES = [(530, 404, 353, 167), (228, 291, 184, 106), (990, 336, 164, 76)]

store = ProfileStore()
profile = store.load(store.names()[0])
info = winutil.find_window(profile.window_title, profile.window_process)
frame, (_, _, cw, ch) = capture.grab_client(info.hwnd)
ax, ay, aw, ah = profile.play_area.to_pixels(cw, ch)
region = frame[ay:ay + ah, ax:ax + aw]

background = cv2.medianBlur(region, vision._BLOB_BLUR)
delta = cv2.absdiff(region, background).max(axis=2)
mask = cv2.threshold(delta, vision._BLOB_DELTA, 255, cv2.THRESH_BINARY)[1]
mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

panels = []
for x, y, w, h in BOXES:
    pad = 12
    x0, y0 = max(0, x - ax - pad), max(0, y - ay - pad)
    x1, y1 = min(aw, x - ax + w + pad), min(ah, y - ay + h + pad)
    pair = np.hstack([region[y0:y1, x0:x1], mask_bgr[y0:y1, x0:x1]])
    scale = 380.0 / pair.shape[0]
    panels.append(cv2.resize(pair, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_NEAREST))

width = max(p.shape[1] for p in panels)
stacked = np.vstack([
    cv2.copyMakeBorder(p, 4, 4, 0, width - p.shape[1], cv2.BORDER_CONSTANT, value=(20, 20, 20))
    for p in panels])
vision.imwrite("_zoom.png", stacked)
print("wrote _zoom.png — picture on the left, what the detector sees on the right")
