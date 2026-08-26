"""Read-only snapshot of what the bot would see right now. Sends no input."""
import sys

sys.path.insert(0, ".")

import cv2

from flyfou import capture, vision, winutil
from flyfou.profile import ProfileStore

store = ProfileStore()
name = sys.argv[1] if len(sys.argv) > 1 else store.names()[0]
profile = store.load(name)
info = winutil.find_window(profile.window_title, profile.window_process)
if info is None:
    raise SystemExit("the game window isn't open")

frame, (_, _, cw, ch) = capture.grab_client(info.hwnd)
ax, ay, aw, ah = profile.play_area.to_pixels(cw, ch)
others, mine = vision.split_self(
    vision.find_blobs(frame[ay:ay + ah, ax:ax + aw]),
    (cw // 2 - ax, ch // 2 - ay), vision.self_radius(ch))

bar = profile.target_hp
level = vision.bar_fill_fraction(capture.crop_fraction(frame, bar.rect),
                                 bar.filled_color, bar.tolerance)
own = vision.bar_fill_fraction(capture.crop_fraction(frame, profile.player_hp.rect),
                               profile.player_hp.filled_color, profile.player_hp.tolerance)

print("profile     %s" % name)
print("window      %s  (%dx%d, foreground %s)" % (info.title, cw, ch,
                                                  winutil.is_foreground(info.hwnd)))
print("hunting     %dx%d at %d,%d" % (aw, ah, ax, ay))
print("candidates  %d, plus %d piece(s) of the character" % (len(others), len(mine)))
print("target bar  %.0f%%     my hp %.0f%%" % (level * 100, own * 100))

shot = frame.copy()
cv2.rectangle(shot, (ax, ay), (ax + aw, ay + ah), (255, 160, 60), 2)
me = (cw // 2 - ax, ch // 2 - ay)
order = sorted(others, key=lambda b: (b.center[0] - me[0]) ** 2 + (b.center[1] - me[1]) ** 2)
for rank, blob in enumerate(order):
    cv2.rectangle(shot, (ax + blob.x, ay + blob.y),
                  (ax + blob.x + blob.w, ay + blob.y + blob.h), (100, 220, 100), 2)
    cv2.putText(shot, str(rank), (ax + blob.x, ay + blob.y - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 220, 100), 1, cv2.LINE_AA)
for blob in mine:
    cv2.rectangle(shot, (ax + blob.x, ay + blob.y),
                  (ax + blob.x + blob.w, ay + blob.y + blob.h), (60, 180, 240), 2)
cv2.circle(shot, (cw // 2, ch // 2), vision.self_radius(ch), (60, 180, 240), 1)
vision.imwrite("_look.png", shot)
print("wrote _look.png  (green = would be probed, numbered nearest-first)")
