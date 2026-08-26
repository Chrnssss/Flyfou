"""How long each part of a tick takes, and what a kill costs in wall clock.

Detection turned out to be fine - a monster is three hovers away - so the ~17s
per kill is being spent somewhere else. Some of it is measurable offline: the
terrain estimate is a 61-pixel median over most of the screen, and it runs once
in _searching and again in _reaim.
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
region = frames[0][ay:ay + ah, ax:ax + aw]
print("hunting ground %dx%d px\n" % (aw, ah))


def timed(label, fn, rounds=5):
    fn()
    best = min(_run(fn) for _ in range(rounds))
    print("  %-44s %6.1f ms" % (label, best * 1000))
    return best


def _run(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


print("one pass of the detector, broken down")
blur = timed("medianBlur, ksize %d" % vision._BLOB_BLUR,
             lambda: cv2.medianBlur(region, vision._BLOB_BLUR))
background = cv2.medianBlur(region, vision._BLOB_BLUR)
timed("absdiff + max over colour",
      lambda: cv2.absdiff(region, background).max(axis=2))
delta = cv2.absdiff(region, background).max(axis=2)
mask = cv2.threshold(delta, vision._BLOB_DELTA, 255, cv2.THRESH_BINARY)[1]
timed("threshold + two morphology passes",
      lambda: cv2.morphologyEx(
          cv2.morphologyEx(cv2.threshold(delta, vision._BLOB_DELTA, 255, cv2.THRESH_BINARY)[1],
                           cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)),
          cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)))
timed("connected components", lambda: cv2.connectedComponentsWithStats(mask, 8))
whole = timed("find_blobs, all of it", lambda: vision.find_blobs(region))

print("\ncheaper ways to estimate the terrain")
for k in (5, 9, 15, 21, 31, 41, 61):
    timed("medianBlur ksize %d" % k, lambda k=k: cv2.medianBlur(region, k))
for k in (31, 61, 121):
    timed("blur (box) ksize %d" % k, lambda k=k: cv2.blur(region, (k, k)))
small = cv2.resize(region, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
timed("half size, medianBlur 31, back up",
      lambda: cv2.resize(cv2.medianBlur(small, 31), (aw, ah), interpolation=cv2.INTER_LINEAR))

print("\nwhat that means for the loop")
print("  a tick at %d Hz has %.0f ms to play with" % (profile.loop_hz, 1000.0 / profile.loop_hz))
print("  _searching runs find_blobs once          %6.1f ms" % (whole * 1000))
print("  _reaim runs grab_client + find_blobs      %6.1f ms + capture" % (whole * 1000))

print("\nfixed costs per engage, from the constants in bot.py")
rows = [
    ("hover bare ground, to learn the idle cursor", 50),
    ("hover our own character", 50),
    ("hover candidates until one is a monster (3)", 150),
    ("find_blobs in _searching", whole * 1000),
    ("click 1, then post_click_delay", 250),
    ("_reaim: grab + find_blobs", whole * 1000),
    ("click 2, then post_click_delay", 250),
]
total = sum(r[1] for r in rows)
for label, ms in rows:
    print("  %-44s %6.1f ms" % (label, ms))
print("  %-44s %6.1f ms" % ("-> one engage attempt", total))
print("\n  target_lost_timeout adds %.1fs after each kill before the next counts"
      % profile.target_lost_timeout)
