"""Where the bot is allowed to click, and where it must refuse.

A wrong decision costs a wasted second. A wrong click costs something else
entirely: the same pixel that selects a monster is, a hundred pixels lower, the
chat box, the action bar, or a button that sells something. And if the window is
not in front, that pixel belongs to whatever the user alt-tabbed to.

None of that needs a game to test. A click is refused or it is not, and the
reasons are arithmetic on a rectangle.

    .venv\\Scripts\\python tests\\test_control.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flyfou.control import MARGIN, Control
from flyfou.mem.layout import Layout
from flyfou.mem.world import Entity

failures = []
checked = 0


def check(name, got, want):
    global checked
    checked += 1
    ok = got == want
    print("  %-58s %s" % (name, "ok" if ok else "FAILED  got %r want %r"
                          % (got, want)))
    if not ok:
        failures.append(name)


WIDE, HIGH = 1600, 900


class Nothing:
    """Stands in for a process nobody is going to read."""

    writable = False

    def vec3(self, _address):
        return None

    def close(self):
        pass


class Eye:
    """A projector that puts things exactly where the test says to."""

    def __init__(self, places):
        self.places = places          # world x -> pixel, or None

    def on_screen(self, spot, margin=0, lift=0.0):
        return self.places.get(round(spot[0], 3))


def a_control(places=None, focused=True, dry=True):
    control = Control(Nothing(), None, Layout(build="b", mover_vtable=1,
                                              name=2, position=0x60),
                      hwnd=1, dry=dry)
    control.area = lambda: (0, 0, WIDE, HIGH)
    control.projector = lambda: Eye(places or {})
    control.focused = lambda: focused
    return control


def being(x, name="Aibatt"):
    return Entity(address=1, id=7, name=name, kind=4, hp=100, x=x, y=0.0, z=0.0)


# --------------------------------------------------------------------------- #
print("it refuses to click the parts of the window that are not the world")

middle = (WIDE // 2, HIGH // 2)
control = a_control({0.0: middle})
check("a monster in open ground is clickable", control.target(being(0.0)), True)
check("and it says it would have clicked",
      "would click" in control.last_refusal.because, True)

control = a_control({0.0: middle})
check("attacking is a double click, not a click", control.attack(being(0.0)), True)
check("  and it says which gesture it would send",
      "would double click" in control.last_refusal.because, True)

for label, point in (("the chat box", (200, 800)),
                     ("the action bar", (800, 880)),
                     ("the inventory panel", (100, 200)),
                     ("the minimap", (1500, 80))):
    control = a_control({0.0: point})
    check("a click in %s is refused" % label, control.target(being(0.0)), False)
    check("  and it says why", "interface" in control.last_refusal.because, True)


# --------------------------------------------------------------------------- #
print("\nit refuses to click outside the window, rather than clamping to it")

for label, point in (("past the right edge", (WIDE + 40, 400)),
                     ("above the top", (700, -20)),
                     ("exactly on the margin", (MARGIN - 1, 400))):
    control = a_control({0.0: point})
    check("%s is refused" % label, control.target(being(0.0)), False)
    check("  and it says so", "edge" in control.last_refusal.because, True)

control = a_control({0.0: None})
check("something off screen is not clicked", control.target(being(0.0)), False)
check("  and it says it cannot see it",
      "not on screen" in control.last_refusal.because, True)


# --------------------------------------------------------------------------- #
print("\nit will not touch the mouse while the game is behind another window")

control = a_control({0.0: middle}, focused=False)
check("a click is refused when we are not in front",
      control.target(being(0.0)), False)
check("  and it says which rule stopped it",
      "not in front" in control.last_refusal.because, True)
check("a key is refused too", control.press("1"), False)


# --------------------------------------------------------------------------- #
print("\na walk too far to see is shortened, not clamped to the screen edge")

# Only the nearer parts of the way are on screen; the rest is out of view.
here, far = (0.0, 0.0, 0.0), (100.0, 0.0, 0.0)
control = a_control({100.0: None, 80.0: None, 60.0: (900, 500), 45.0: (870, 490)})
check("it walks as far as it can see", control.walk_to(far, here), True)
check("  and takes the longest step that is visible",
      "(900, 500)" in control.last_refusal.because, True)

control = a_control({})
check("with none of the way visible it refuses", control.walk_to(far, here), False)
check("  and says the way is not on screen",
      "visible on screen" in control.last_refusal.because, True)

control = a_control({100.0: (200, 800)})     # the whole way lands in the chat
check("a walk that would land in the chat box is refused",
      control.walk_to(far, here), False)

# The ground is not empty. A click meant for the floor that lands on a monster
# starts a fight; one that lands on dropped loot picks it up instead of walking.
control = a_control({100.0: (900, 500), 80.0: (880, 495), 60.0: (700, 450),
                     45.0: (650, 430), 30.0: (600, 420), 20.0: (580, 410)})
check("it steps past anything the world has drawn",
      control.walk_to(far, here, avoid=[(900, 500), (880, 495)]), True)
check("  and clicks the first clear pixel along the way",
      "(700, 450)" in control.last_refusal.because, True)

control = a_control({100.0: (900, 500), 80.0: (900, 500), 60.0: (900, 500),
                     45.0: (900, 500), 30.0: (900, 500), 20.0: (900, 500)})
check("with every step under something it refuses",
      control.walk_to(far, here, avoid=[(900, 500)]), False)
check("  and says the way is covered",
      "under something else" in control.last_refusal.because, True)

check("walking nowhere is refused rather than crashing",
      control.walk_to(None, here), False)


# --------------------------------------------------------------------------- #
print("\nnothing at all happens without a window to click into")

control = a_control({0.0: middle})
control.area = lambda: None
check("no window means no target", control.target(being(0.0)), False)
check("no window means no walk", control.walk_to((1.0, 0.0, 0.0), here), False)
check("  and it says the client is not drawing",
      "not drawing" in control.last_refusal.because, True)


# --------------------------------------------------------------------------- #
print("\n%d checks, %d failed" % (checked, len(failures)))
if failures:
    print("\nFAILED:")
    for name in failures:
        print("  " + name)
sys.exit(1 if failures else 0)
