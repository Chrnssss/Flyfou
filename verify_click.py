"""The first click. One monster, once, and then it stops.

Everything is in place and one link has never been tested: that the pixel the
projector names is really the pixel the client draws the monster at, closely
enough that clicking it selects that monster and not the ground behind it.

There is a way to ask that question which needs no screenshots and no judgement
about what a target frame looks like. Attack it. The monster's health is a
number in memory, and if it goes down then the click landed on the monster, the
client selected it, the attack key reached the client, and the whole chain from
"a monster exists at these coordinates" to "it is being hit" works. If the
health does not move, one of those is broken and the others are unproven.

    .venv\\Scripts\\python verify_click.py [character]        say what it would do
    .venv\\Scripts\\python verify_click.py [character] --go   do it

It brings the window to the front, because a click cannot be sent to a window
that is not there, and puts nothing back afterwards - it does not need to. The
worst it does is hit one monster a few times, which is what the bot is for.
"""

from __future__ import annotations

import sys
import time

import win32gui
import win32process

from flyfou import screen, winutil
from flyfou.control import Control
from flyfou.mem import LayoutStore, build_key, is_client, open_client
from flyfou.mem.world import WorldReader

TITLE = "Airborn - "

#: A monster, as opposed to a pet: pets are level one and share their kind.
LOWEST = 2

#: How many swings to take, and how long to watch the health for.
SWINGS, GAP, WATCH = 5, 0.9, 7.0


def clients():
    found = []

    def visit(hwnd, _extra):
        title = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and title.startswith(TITLE):
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            if is_client(pid):
                found.append((hwnd, pid, title[len(TITLE):].strip()))

    win32gui.EnumWindows(visit, None)
    return sorted(set(found), key=lambda row: row[2])


def main(argv):
    go = "--go" in argv
    argv = [a for a in argv if a != "--go"]
    wanted = argv[0] if argv else None

    running = clients()
    if wanted:
        running = [c for c in running if c[2].lower() == wanted.lower()]
    if not running:
        print("no client called %r is open" % (wanted or "anything"))
        return 1
    hwnd, pid, who = running[0]

    winutil.enable_dpi_awareness()
    process, module = open_client(pid)
    try:
        layout = LayoutStore().load(build_key(process, module))
        reader = WorldReader(process, module, layout)
        control = Control(process, module, layout, hwnd=hwnd, dry=not go)

        world = reader.read(force_full=True)
        me = world.me
        rect = control.area()
        eye = control.projector()
        if me is None or rect is None or eye is None:
            print("the client is not drawing a world right now")
            return 1

        monsters = [e for e in world.others()
                    if e.alive and e.name and e.level >= LOWEST
                    and not e.is_player and control.visible(e)]
        if not monsters:
            print("%s can see %d movers but none of them is a monster that is "
                  "also on screen" % (who, len(world)))
            return 1
        pick = min(monsters, key=lambda e: e.apart_from(me))
        point = eye.on_screen(pick.pos)

        print("%s at (%.0f, %.0f, %.0f), window %dx%d"
              % (who, me.x, me.y, me.z, rect[2], rect[3]))
        print("nearest monster on screen: %s, level %d, %d health, %.1f units away"
              % (pick.name, pick.level, pick.hp, pick.apart_from(me)))
        print("the projector says it is drawn at pixel %s" % (point,))
        if not go:
            print("\ndry run. Nothing was clicked. Add --go to try it.")
            return 0

        print("\nbringing the window to the front...")
        winutil.bring_to_front(hwnd)
        time.sleep(0.8)

        # Re-read at the last moment: the world moved while the window came
        # forward, and clicking where something used to be is how the last
        # attempt clicked bare grass.
        world = reader.read()
        pick = world.find(pick.id) or pick
        point = control.projector().on_screen(pick.pos)
        was = pick.hp
        print("it is now at pixel %s with %d health" % (point, was))

        import cv2
        if not control.attack(pick):
            print("the click was refused: %s" % control.last_refusal)
            return 1
        # Photograph immediately, not at the end: a click on empty ground makes
        # the character walk, so seven seconds later nothing is where it was.
        shot = winutil.print_window(hwnd)
        if shot is not None and point:
            cv2.circle(shot, point, 15, (0, 0, 255), 3)
            cv2.imwrite("_clicked_%s.png" % who, shot)
        print("double clicked. Watching its health...")

        low = was
        gone = False
        until = time.monotonic() + WATCH
        while time.monotonic() < until:
            time.sleep(GAP)
            now = reader.read()
            still = now.find(pick.id)
            if still is None:
                gone = True
                break
            low = min(low, still.hp)

        print()
        print("  health went from %d to %d%s" % (was, low,
                                                 " and then it vanished" if gone else ""))
        if gone or low < was:
            print("  VERDICT: it worked. The projected pixel selected the "
                  "monster and the attack landed, so the whole chain from a "
                  "world position to a hit is proven.")
        else:
            print("  VERDICT: nothing happened to it. _clicked_%s.png was taken "
                  "the instant it clicked, with a ring on the pixel it chose - "
                  "if the ring is not on the monster the projection is off, and "
                  "if it is then the double click is not what attacks here."
                  % who)
    finally:
        process.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
