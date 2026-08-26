"""Build every wizard screen for real and drive it with a synthetic frame.

Most of the wizard only runs when a widget is drawn, so a bad argument or a
canvas call with the wrong arity hides until someone clicks through to that
screen. Building every step against a hidden root and ticking it finds those
without the game running.

    .venv\\Scripts\\python tests\\test_wizard.py
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

from flyfou.gui import theme
from flyfou.gui.feed import FeedFrame
from flyfou.gui.wizard import SetupWizard
from flyfou.profile import Profile, ProfileStore

CW, CH = 1600, 900

root = tk.Tk()
root.withdraw()
theme.apply(root)

scene = np.full((CH, CW, 3), (90, 140, 180), dtype=np.uint8)
for x, y in ((520, 300), (900, 560), (1100, 240)):
    scene[y - 25:y + 25, x - 20:x + 20] = (40, 40, 230)
scene[CH // 2 - 48:CH // 2 + 48, CW // 2 - 40:CW // 2 + 40] = (60, 200, 90)  # the character
frame = FeedFrame(scene, (CW, CH), True, "window", time.time())

profile = Profile(name="synthetic", window_title="x", client_size=(CW, CH))
wizard = SetupWizard(root, ProfileStore(), None)
wizard.withdraw()
wizard.profile = profile
for step in wizard.steps:
    step.profile = profile
wizard.frame_now = lambda: frame

failures = []
for index, step in enumerate(wizard.steps):
    name = type(step).__name__
    try:
        wizard._show(index)
        for _ in range(3):
            step.tick()
            wizard.update()
        print("  %-22s built and ticked, validate: %s"
              % (name, step.validate() or "passes"))
    except Exception as exc:
        failures.append(name)
        print("  %-22s FAILED  %s: %s" % (name, type(exc).__name__, exc))

# The hunting-ground preview should see three things and skip the character.
ground = wizard.steps[2]
found = ground.preview.update_view(frame, profile.play_area)
if found != 3:
    failures.append("blob count")
print("\n  boxed %d candidates besides the character (want 3)" % found)

wizard.feed.stop()
root.destroy()
print("\n%s" % ("every screen builds" if not failures else "FAILED: %s" % ", ".join(failures)))
sys.exit(1 if failures else 0)
