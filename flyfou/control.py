"""Everything the bot is allowed to do to the game.

Perception is reading; this is the other half. It clicks, because clicking is
the only thing that works.

That was not the first design. Writing to the client's memory was tried first
and it looked like it worked: write a position and the character moves on
screen, convincingly, and stays moved. It is an illusion. Two other clients
standing beside it watched it not move at all - the write never left the
machine. This client sends packets when it processes input, not when its own
memory changes, so memory is a mirror and the mouse is the lever.

Everything here therefore goes through one pipeline: take a world position, ask
the projector where the client is drawing it, and click that pixel. The rules
that pipeline enforces, in order of how much trouble breaking them causes:

  Nothing happens unless the game is in front. A click sent to whatever the user
  alt-tabbed to lands in their browser, their editor, or their bank. This is the
  rule that matters most and it is checked immediately before every click rather
  than once at the start, because alt-tab is instant.

  Nothing is clicked that is not on screen. Behind the camera, past the edge, or
  under the interface all mean "do not click", and each says so rather than
  clamping to an edge and clicking something else by accident. Clamping was
  considered and rejected: a click at the edge of the screen is a click on
  whatever is at the edge of the screen.

  Nothing is clicked in the parts of the window that are not the world. The
  chat box, the action bar and the inventory are all clickable, and a click that
  lands in them types, drags or uses something.

A Control built with `dry=True` reports what it would have done and touches
nothing, so the loop can be watched deciding without the mouse ever moving.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from . import inputs, screen, winutil
from .mem.layout import Layout, resolve_player
from .mem.process import Module, Process
from .mem.world import Entity

#: Fractions of the window given over to the interface rather than the world.
#: A click here does something other than what was meant, so nothing is clicked
#: here. Measured off the client at 1600x900 with room to spare.
CHAT = (0.0, 0.86, 0.36, 1.0)          # bottom left: chat and its buttons
BARS = (0.0, 0.92, 1.0, 1.0)           # bottom strip: the action bar
PANEL = (0.0, 0.0, 0.16, 0.46)         # top left: portrait and inventory
MINIMAP = (0.86, 0.0, 1.0, 0.22)       # top right: the map
FURNITURE = (CHAT, BARS, PANEL, MINIMAP)

#: How near a walk gets before it counts as arrived, in world units.
ARRIVED = 2.5

#: A click no closer to the edge than this, so the cursor is really inside.
MARGIN = 6

#: How far a walking click keeps away from anything the world has drawn. A
#: click meant for the ground that lands on a monster starts a fight the rules
#: never sanctioned, and one that lands on a dropped item picks it up instead of
#: going anywhere.
CLEAR = 26


@dataclass
class Refused:
    """Why an action did not happen, for the log rather than for an exception."""

    what: str
    because: str

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"<refused {self.what}: {self.because}>"


class Control:
    """The bot's hands. One per client."""

    def __init__(self, process: Process, module: Module, layout: Layout,
                 hwnd: Optional[int] = None, dry: bool = False):
        self.process = process
        self.module = module
        self.layout = layout
        self.hwnd = hwnd
        self.dry = dry
        self.last_refusal: Optional[Refused] = None
        self.clicks = 0

    # ------------------------------------------------------------------ where
    def me(self) -> Optional[int]:
        """Our own object, re-resolved every time rather than remembered."""
        return resolve_player(self.process, self.module, self.layout)

    def area(self) -> Optional[Tuple[int, int, int, int]]:
        if not self.hwnd or not winutil.window_exists(self.hwnd):
            return None
        rect = winutil.client_rect(self.hwnd)
        return rect if rect[2] > 0 and rect[3] > 0 else None

    def projector(self) -> Optional[screen.Projector]:
        """Where the client is drawing the world, right now.

        Rebuilt every time. The camera moves whenever the character does, so a
        projection kept from the last tick aims at where something used to be.
        """
        rect = self.area()
        if rect is None:
            return None
        return screen.project_world(self.process, self.module, rect[2], rect[3])

    def _refuse(self, what: str, because: str) -> Refused:
        self.last_refusal = Refused(what, because)
        return self.last_refusal

    # ------------------------------------------------------------- the rules
    def focused(self) -> bool:
        return self.hwnd is not None and winutil.is_foreground(self.hwnd)

    def _is_furniture(self, point, width: int, height: int) -> bool:
        fx, fy = point[0] / float(width), point[1] / float(height)
        for x0, y0, x1, y1 in FURNITURE:
            if x0 <= fx <= x1 and y0 <= fy <= y1:
                return True
        return False

    def _click_at(self, what: str, point, rect, twice: bool = False) -> bool:
        """The only place a button is ever pressed."""
        x, y, width, height = rect
        if not (MARGIN <= point[0] < width - MARGIN
                and MARGIN <= point[1] < height - MARGIN):
            return bool(self._refuse(what, "that is off the edge of the window"))
        if self._is_furniture(point, width, height):
            return bool(self._refuse(
                what, "that pixel is interface, not world - clicking it would "
                      "press a button instead"))
        if not self.focused():
            return bool(self._refuse(what, "the game window is not in front"))
        if self.dry:
            # A dry run has to take the same branch a real one would, or it is
            # not a rehearsal of anything. It records what it would have done
            # and reports success, because success is what would have happened.
            self._refuse(what, "dry run: would %s (%d, %d)"
                         % ("double click" if twice else "click", point[0],
                            point[1]))
            return True
        if twice:
            inputs.double_click(x + point[0], y + point[1])
        else:
            inputs.click(x + point[0], y + point[1])
        self.clicks += 1
        return True

    # ----------------------------------------------------------------- target
    def _aim(self, what: str, entity: Entity, twice: bool) -> bool:
        rect = self.area()
        eye = self.projector()
        if rect is None or eye is None:
            return bool(self._refuse(what, "the client is not drawing yet"))
        point = eye.on_screen(entity.pos, margin=MARGIN)
        if point is None:
            return bool(self._refuse(
                what, "it is not on screen - behind the camera, or out of view"))
        return self._click_at(what, point, rect, twice=twice)

    def target(self, entity: Entity) -> bool:
        """Select something by clicking where the client has drawn it."""
        return self._aim("target " + (entity.name or "it"), entity, twice=False)

    def attack(self, entity: Entity) -> bool:
        """Select it AND go for it, which is what a double click means here.

        A single click only selects; the second press is what sends the
        character in. Doing both in one gesture also means the bot does not care
        which key this character has its attack bound to.
        """
        return self._aim("attack " + (entity.name or "it"), entity, twice=True)

    def visible(self, entity: Entity) -> bool:
        """Could we click it if we wanted to?"""
        rect, eye = self.area(), self.projector()
        if rect is None or eye is None:
            return False
        point = eye.on_screen(entity.pos, margin=MARGIN)
        return bool(point) and not self._is_furniture(point, rect[2], rect[3])

    # ------------------------------------------------------------------- walk
    def walk_to(self, spot: Sequence[float],
                here: Optional[Sequence[float]] = None,
                avoid: Sequence[Tuple[int, int]] = ()) -> bool:
        """Walk by clicking bare ground, shortening the step if need be.

        Two things make this fussier than it looks. A destination beyond the
        edge of the screen cannot be clicked, and clamping to the edge would
        click whatever is at the edge - so the journey is cut short instead:
        same direction, only as far as is still visible. Walking is re-aimed
        every tick, so a short step costs nothing.

        And the ground is not empty. Everything drawn on it is clickable, and a
        click meant for the floor that lands on something else does that thing
        instead: a monster starts a fight, a dropped item gets picked up. So
        anywhere near something we know about is not bare ground, and the step
        is shortened past it.
        """
        if spot is None:
            return bool(self._refuse("walk", "there is nowhere to walk to"))
        rect, eye = self.area(), self.projector()
        if rect is None or eye is None:
            return bool(self._refuse("walk", "the client is not drawing yet"))

        start = here or self.here()
        if start is None:
            return bool(self._refuse("walk", "we do not know where we are"))

        blocked = False
        for reach in (1.0, 0.8, 0.6, 0.45, 0.3, 0.2):
            goal = (start[0] + (spot[0] - start[0]) * reach,
                    start[1] + (spot[1] - start[1]) * reach,
                    start[2] + (spot[2] - start[2]) * reach)
            point = eye.on_screen(goal, margin=MARGIN, lift=0.0)
            if point is None or self._is_furniture(point, rect[2], rect[3]):
                continue
            if any(abs(point[0] - x) < CLEAR and abs(point[1] - y) < CLEAR
                   for x, y in avoid):
                blocked = True
                continue
            return self._click_at("walk", point, rect)
        return bool(self._refuse(
            "walk", "every step of the way there is under something else"
                    if blocked else
                    "no part of the way there is visible on screen"))

    def stop(self) -> bool:
        """Stand still, by clicking the ground we are already standing on.

        There is no "stop" to send. A character walks because it was told to
        walk somewhere, so the way to stop it is to tell it to walk to where it
        already is.
        """
        here = self.here()
        return self.walk_to(here, here) if here else False

    def here(self) -> Optional[Tuple[float, float, float]]:
        me = self.me()
        if not me or self.layout.position is None:
            return None
        return self.process.vec3(me + self.layout.position)

    def arrived(self, spot: Sequence[float]) -> bool:
        here = self.here()
        if here is None:
            return False
        return math.hypot(here[0] - spot[0], here[2] - spot[2]) <= ARRIVED

    # ------------------------------------------------------------------- keys
    def press(self, key: str) -> bool:
        """Send a key, but only into a window the user is actually looking at."""
        if not key:
            return False
        if not self.focused():
            return bool(self._refuse("press " + key,
                                     "the game window is not in front"))
        if self.dry:
            self._refuse("press " + key, "dry run: would press " + key)
            return True
        inputs.press(key)
        return True

    def release_all(self) -> None:
        inputs.release_all()
