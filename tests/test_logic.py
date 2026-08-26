"""Search and engagement rules, checked against synthetic frames.

Every behaviour here was wrong at some point, and each one cost a live run to
find. They are all decidable from a drawn frame and a hand-driven clock, so they
are pinned down here rather than re-discovered in front of a monster.

    .venv\\Scripts\\python tests\\test_logic.py
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flyfou import bot as bot_module
from flyfou import vision
from flyfou.bot import Bot
from flyfou.profile import BarConfig, FracRect, Profile

CW, CH = 1600, 900
SAND = (90, 140, 180)
failures = []


def check(name, got, want):
    ok = got == want
    print("  %-52s %s" % (name, "ok" if ok else "FAILED  got %r want %r" % (got, want)))
    if not ok:
        failures.append(name)


def make_profile():
    profile = Profile(name="synthetic", window_title="x", client_size=(CW, CH))
    profile.play_area = FracRect(0.13, 0.05, 0.74, 0.68)
    profile.player_hp = BarConfig(rect=FracRect(0.06, 0.02, 0.07, 0.02))
    profile.target_hp = BarConfig(rect=FracRect(0.4375, 0.03333, 0.125, 0.01333))
    profile.attack_key = "f1"
    profile.target_lost_timeout = 1.0
    profile.post_click_delay = (0.0, 0.0)
    return profile


def frame_with(boxes):
    """Sand, with a filled square per (x, y, size)."""
    canvas = np.full((CH, CW, 3), SAND, dtype=np.uint8)
    for x, y, size in boxes:
        canvas[y - size // 2:y + size // 2, x - size // 2:x + size // 2] = (40, 40, 230)
    return canvas


# --------------------------------------------------------------------------- #
print("the character is never the target")

profile = make_profile()
bot = Bot(profile, echo=False, hwnd=1)
bot._paused = False

CHARACTER = (CW // 2, CH // 2, 90)      # dead centre, as the game always draws it
NEAR = (CW // 2 + 150, CH // 2 + 60, 46)
FAR = (CW // 2 - 380, CH // 2 - 170, 46)
frame = frame_with([CHARACTER, NEAR, FAR])

clicks = []
bot._click = lambda xy, button="left": (clicks.append(xy), True)[1]
bot._wander = lambda w, h: clicks.append("wander")
bot_module.capture.grab_client = lambda hwnd: (frame, (0, 0, CW, CH))


def fake_hotspot(point):
    px, py = point
    for x, y, size in (CHARACTER, NEAR, FAR):
        if abs(px - x) <= size // 2 and abs(py - y) <= size // 2:
            return (15, 9) if (x, y, size) == CHARACTER else (0, 0)
    return (1, 1)


bot._hotspot_at = fake_hotspot
bot._searching(frame, CW, CH)

check("learnt the bare-ground cursor", bot._idle_hotspot, (1, 1))
check("learnt its own cursor", bot._self_hotspot, (15, 9))
check("clicked twice (select, then attack)", len(clicks), 2)
if clicks:
    px, py = clicks[0]
    on_character = abs(px - CHARACTER[0]) <= 45 and abs(py - CHARACTER[1]) <= 45
    check("did not click the character", on_character, False)
    check("picked the nearer monster", (abs(px - NEAR[0]) <= 30 and abs(py - NEAR[1]) <= 30), True)

# --------------------------------------------------------------------------- #
print("\nthe character is skipped however it happens to detect")

# The character is always drawn dead centre, so it is always the nearest blob and
# would always be probed first. What it detects *as* has changed once already:
# a terrain estimate narrower than the sprite used to judge it to be its own
# background and leave only corner fragments, none containing the centre point.
# So the rule can't be "skip the blob under the middle" — it's "skip whatever is
# centred within a sprite's reach of the middle", which holds either way.
area_px = profile.play_area.to_pixels(CW, CH)
apx, apy, apw, aph = area_px
pieces = vision.find_blobs(frame[apy:apy + aph, apx:apx + apw])
centre = (CW // 2 - apx, CH // 2 - apy)
others, mine = vision.split_self(pieces, centre, vision.self_radius(CH))
check("the character is recognised as us", len(mine) >= 1, True)
check("nothing we kept is inside the character",
      [b for b in others
       if abs(b.center[0] - centre[0]) <= 45 and abs(b.center[1] - centre[1]) <= 45], [])
check("the two monsters survive", len(others), 2)

# --------------------------------------------------------------------------- #
print("\nnothing attackable is reported, not clicked")

quiet = frame_with([CHARACTER])
clicks.clear()
bot._clicked_at = 0.0  # the click above is still inside its grace period otherwise
bot._hotspot_at = lambda point: (15, 9) if abs(point[0] - CW // 2) <= 45 and abs(point[1] - CH // 2) <= 45 else (1, 1)
bot_module.capture.grab_client = lambda hwnd: (quiet, (0, 0, CW, CH))
bot._searching(quiet, CW, CH)
check("wandered instead of clicking scenery", clicks, ["wander"])

# --------------------------------------------------------------------------- #
print("\nthe attack click follows the monster, but only a short hop")

area = profile.play_area.to_pixels(CW, CH)
ax, ay, _, _ = area
aimed = (NEAR[0], NEAR[1])

drifted = frame_with([CHARACTER, (NEAR[0] + 12, NEAR[1] + 8, 46)])
bot_module.capture.grab_client = lambda hwnd: (drifted, (0, 0, CW, CH))
moved = bot._reaim(aimed, area)
check("followed a small drift", (abs(moved[0] - (NEAR[0] + 12)) <= 6 and
                                 abs(moved[1] - (NEAR[1] + 8)) <= 6), True)

jumped = frame_with([CHARACTER, (NEAR[0] + 200, NEAR[1] + 200, 46)])
bot_module.capture.grab_client = lambda hwnd: (jumped, (0, 0, CW, CH))
check("ignored a far blob and kept the original aim", bot._reaim(aimed, area), aimed)

# --------------------------------------------------------------------------- #
print("\na target that never takes damage gets dropped")

fresh = Bot(make_profile(), echo=False, hwnd=1)
fresh._paused = False
fresh._update_engagement(0.80, 100.0)
check("engaged", fresh._engaged, True)
fresh._update_engagement(0.80, 104.0)
check("still engaged after 4s of no damage", fresh._engaged, True)
fresh._update_engagement(0.80, 100.0 + bot_module._STUCK_FIGHT + 0.5)
check("dropped it once the bar had stalled", fresh._engaged, False)
check("counted no kill for it", fresh._kills, 0)

fresh._update_engagement(0.80, 100.0 + bot_module._STUCK_FIGHT + 1.0)
check("does not re-grab the same one during the grace", fresh._engaged, False)
later = 100.0 + bot_module._STUCK_FIGHT + bot_module._ABANDON_GRACE + 1.0
fresh._update_engagement(0.80, later)
check("engages again once the grace is over", fresh._engaged, True)

# --------------------------------------------------------------------------- #
print("\na real kill still counts")

killer = Bot(make_profile(), echo=False, hwnd=1)
killer._paused = False
killer._update_engagement(0.80, 200.0)
killer._update_engagement(0.42, 201.0)
killer._update_engagement(0.00, 202.0)
check("no kill the instant the bar empties", killer._kills, 0)
killer._update_engagement(0.00, 203.5)
check("kill counted once it stays empty", killer._kills, 1)
check("back to unengaged", killer._engaged, False)

print("\n%s" % ("all checks passed" if not failures else "FAILED: %s" % ", ".join(failures)))
sys.exit(1 if failures else 0)
