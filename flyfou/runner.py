"""The loop: read the world, decide, act, repeat.

The three hard parts of the old bot are gone, because they were all the same
part. Finding a monster, telling whether it was still alive, and telling whether
somebody else had already claimed it were guesses about pixels; they are now
three fields. What is left here is genuinely just a loop, and almost everything
in it is about safety rather than about farming:

  It starts paused. Nothing happens until somebody says so.

  It re-resolves the player every tick rather than remembering an address. A
  remembered one survives a map change and a relog and then points at freed
  memory that something else has since been given.

  Keys go out only while the game is in front. Memory writes do not need that -
  they do not care what has focus - but an attack key sent to whatever the user
  alt-tabbed to is worse than a missed swing.

  Walking is re-aimed every tick instead of being fired once. That is what makes
  a wall survivable: if the position has not changed in a couple of seconds
  while we are supposed to be walking, the destination is nudged sideways rather
  than being written again, and after a few of those the target is abandoned.
  It is not pathfinding. It is enough to get off a rock.

A kill is counted when something we were fighting stops being alive, not when a
number on a screen goes away, so the count is the real one.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from . import inputs, winutil
from .brain import (ATTACK, IDLE, REST, WALK, Memory, Plan, Rules, choose)
from .control import Control
from .mem import LayoutStore, layout_for, open_client
from .mem.world import WorldReader
from .report import (FIGHTING, PAUSED, RESTING, RETURNING, SEARCHING, STOPPED,
                     WAITING, LogLine, Reporter, Status, log_path)

#: How often the loop runs.
#:
#: The reading underneath costs fifteen to twenty milliseconds most ticks, so
#: this is what decides how long the bot stands about after killing something.
#: It used to be a third of a second, which with a sweep on top came to nearly a
#: second between one monster and the next.
TICK = 0.12

#: No wait at all when something just changed. Killing a monster is exactly the
#: moment to look again rather than to sleep.
QUICK = 0.01

#: A walk that has not moved us in this long has hit something.
STUCK = 2.2

#: How far to sidestep when stuck, and how many tries before giving up.
SIDESTEP = 12.0
SIDESTEPS = 4

#: An attack key is not spammed faster than this.
SWING = 0.8

#: How often food or a heal is pressed while sitting still.
HEAL_EVERY = 2.0

#: A fight that has drawn no blood in this long gets clicked again.
#:
#: A double click that misses leaves the bot certain it is fighting something,
#: standing there pressing a key at nothing. It used to wait for the brain to
#: give up, which takes ten seconds, and from the outside that is the bot
#: stopping for a quarter of a minute every few kills. Clicking again is cheap
#: and it is what a person would do.
RETRY = 2.5


@dataclass
class Walking:
    """The state of one journey, so that being stuck is detectable."""

    spot: Optional[Tuple[float, float, float]] = None
    since: float = 0.0
    was: Optional[Tuple[float, float, float]] = None
    moved_at: float = 0.0
    nudges: int = 0

    def restart(self, spot, where, now: float) -> None:
        self.spot, self.since, self.was = spot, now, where
        self.moved_at, self.nudges = now, 0


class Bot:
    """One client, farmed. Same surface as the old one, so the panel still fits."""

    def __init__(self, profile, echo: bool = False, hwnd: Optional[int] = None,
                 pid: Optional[int] = None, dry: bool = False):
        self.profile = profile
        self.reporter = Reporter(echo=echo, path=log_path())
        self.hwnd = hwnd
        self.pid = pid
        self.dry = dry

        self.rules: Rules = profile.rules() if hasattr(profile, "rules") else Rules()
        self.memory = Memory()
        self.walking = Walking()

        self._status = Status(state=STOPPED)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._go = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_swing = 0.0
        #: What we last clicked on. There is no target field in this client to
        #: read back, so what the bot believes it selected is what it clicked -
        #: and a click that missed shows up as a monster whose health never
        #: moves, which the brain already gives up on.
        self._selected = 0
        self._selected_hp = 0
        self._clicked_at = 0.0
        self._healed_at = 0.0
        self._was_after = 0
        self._process = None
        self._module = None
        self._reader: Optional[WorldReader] = None
        self._control: Optional[Control] = None
        self.layout = None

    # ------------------------------------------------------------- the outside
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._go.clear()
        self.memory = Memory()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, reason: str = "") -> None:
        self._stop.set()
        self._go.set()
        if self._control:
            self._control.release_all()
        if reason:
            self.reporter.log(reason, "warn")
        self._set(state=STOPPED, running=False, paused=True)

    def pause(self) -> None:
        self._go.clear()
        if self._control:
            self._control.release_all()
        self._set(state=PAUSED, paused=True)
        self.reporter.log("Paused.", "warn")

    def resume(self) -> None:
        self._go.set()
        self._set(paused=False)
        self.reporter.log("Running.", "good")

    def toggle(self) -> None:
        self.pause() if self._go.is_set() else self.resume()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> Status:
        with self._lock:
            return Status(**vars(self._status))

    def _set(self, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._status, key, value)

    # ------------------------------------------------------------- attaching
    def _attach(self) -> bool:
        """Open the client and get its layout. Cheap after the first time."""
        if self._reader is not None:
            return True
        pid = self.pid
        if pid is None and self.hwnd:
            import win32process
            _tid, pid = win32process.GetWindowThreadProcessId(self.hwnd)
        if not pid:
            self.reporter.hint("nopid", "No game window has been chosen yet.")
            return False

        who = winutil.window_title(self.hwnd) if self.hwnd else ""
        who = who.split(" - ")[-1].strip() if " - " in who else who
        try:
            process, module = open_client(pid, writable=not self.dry)
        except Exception as bad:                       # noqa: BLE001
            self.reporter.hint("open", f"Could not open the client: {bad}")
            return False

        layout = layout_for(process, module, who, store=LayoutStore(),
                            report=lambda m: self.reporter.log(m))
        if not layout.usable:
            self.reporter.log("The offsets for this build are unusable.", "warn")
            return False
        if layout.missing():
            self.reporter.log(layout.why_incomplete(), "warn")

        self._process, self._module = process, module
        self.layout = layout
        self._reader = WorldReader(process, module, layout)
        self._control = Control(process, module, layout, hwnd=self.hwnd,
                                dry=self.dry)
        self.reporter.log(
            f"Attached to {who or pid}"
            + (" (dry run - nothing will be clicked)" if self._control.dry else ""),
            "good")
        rect = self._control.area()
        self.reporter.log(
            "Window is %s; mouse input is %s."
            % ("%dx%d" % (rect[2], rect[3]) if rect else "not measurable",
               "available" if inputs.available() else
               "NOT AVAILABLE - pydirectinput is missing, so nothing can be "
               "clicked"),
            "info" if rect and inputs.available() else "warn")
        return True

    # ------------------------------------------------------------------ acting
    def _do(self, plan: Plan, world, now: float) -> None:
        """Carry out one decision.

        Anything aimed at a creature goes through `_engage`, whether the brain
        called it attacking or closing the distance, because to this client they
        are the same gesture: a double click both walks and swings. Only a plan
        with nowhere to be - going home, following a route - clicks the ground.
        """
        control = self._control
        if plan.entity is not None and plan.do in (ATTACK, WALK):
            self._engage(plan, world, now)

        elif plan.do == WALK:
            self._walk(plan, world, now)
            self._set(state=RETURNING)

        elif plan.do == REST:
            control.stop()
            self._selected = 0
            if self.rules.heal_key and now - self._healed_at >= HEAL_EVERY:
                self._healed_at = now
                control.press(self.rules.heal_key)
            self._set(state=RESTING)

        else:
            self._selected = 0
            self._set(state=SEARCHING)

    def _engage(self, plan: Plan, world, now: float) -> None:
        """Go and hit something, by asking the client to do the walking.

        A double click on a monster is not just "attack": the character walks to
        it first, on its own, and starts swinging when it arrives. That is worth
        more than it sounds, because the alternative was clicking the ground to
        close the distance - and the ground is covered in dropped loot, so half
        those clicks picked something up instead of going anywhere.

        So distance is not this loop's problem. If the monster can be seen, it
        gets clicked, however far away it is. Walking is only for when it cannot
        be seen at all.
        """
        control, entity = self._control, plan.entity

        if entity.hp < self._selected_hp:
            # It is bleeding, so the click landed and the client is doing its
            # job. Keep the clock running from the last thing that actually
            # happened rather than from the last thing we tried.
            self._selected_hp = entity.hp
            self._clicked_at = now

        fresh = self._selected != entity.id
        stale = now - self._clicked_at > RETRY
        if fresh or stale:
            if control.attack(entity):
                if not fresh:
                    self.reporter.hint(
                        "again", "%s has taken no damage in %.0fs; clicking it "
                                 "again." % (entity.name, RETRY), cooldown=8.0)
                self._selected = entity.id
                self._selected_hp = entity.hp
                self._clicked_at = now
                self._last_swing = now
                self.walking.spot = None
                self._set(state=FIGHTING)
                return
            self.reporter.hint("noclick", "Could not click %s: %s"
                               % (entity.name, self._why(control)))
            self._walk(Plan(WALK, entity=entity, spot=entity.pos,
                            why=plan.why), world, now)
        elif self.rules.attack_key and now - self._last_swing >= SWING:
            # It is already engaged and closing under its own steam; the key is
            # only there for whatever skill the character leads with.
            self._last_swing = now
            control.press(self.rules.attack_key)
        self._set(state=FIGHTING)

    @staticmethod
    def _why(control) -> str:
        """The last refusal, in words.

        `Refused` is deliberately falsy, so that `return bool(self._refuse(...))`
        reads as a failure - which means testing it with `if` is always false
        and once turned every reason in the log into a question mark.
        """
        refusal = control.last_refusal
        return refusal.because if refusal is not None else "no reason recorded"

    def _crowd(self, world) -> list:
        """Pixels a walking click must keep away from.

        Only things memory knows about: monsters, players and pets. Dropped
        items are not movers and do not appear in any sweep, so a click can
        still land on loot - that is a real gap, not an oversight.
        """
        eye = self._control.projector()
        if eye is None or world.me is None:
            return []
        out = []
        for entity in world.others():
            if entity.apart_from(world.me) > 60:
                continue
            point = eye.on_screen(entity.pos)
            if point:
                out.append(point)
        return out

    def _walk(self, plan: Plan, world, now: float) -> None:
        """Head for a spot, and get off whatever we are stuck on."""
        control, me = self._control, world.me
        if plan.spot is None:
            return
        here = me.pos
        walk = self.walking

        if walk.spot is None or _apart(walk.spot, plan.spot) > 3.0:
            walk.restart(plan.spot, here, now)
            control.walk_to(plan.spot, here, self._crowd(world))
            return

        if walk.was is None or _apart(here, walk.was) > 0.6:
            walk.was, walk.moved_at, walk.nudges = here, now, 0
            control.walk_to(plan.spot, here, self._crowd(world))
            return

        if now - walk.moved_at < STUCK:
            return

        # We have not moved and we are supposed to be walking.
        walk.nudges += 1
        walk.moved_at = now
        if walk.nudges > SIDESTEPS:
            if plan.entity is not None:
                self.memory.abandon(plan.entity.id, now)
                self.reporter.log(
                    f"Could not reach {plan.entity.name}; leaving it.", "warn")
            walk.spot = None
            return

        angle = math.atan2(plan.spot[2] - here[2], plan.spot[0] - here[0])
        away = angle + random.choice((math.pi / 2, -math.pi / 2))
        control.walk_to((here[0] + SIDESTEP * math.cos(away), here[1],
                         here[2] + SIDESTEP * math.sin(away)), here,
                        self._crowd(world))
        self.reporter.hint("stuck", "Something is in the way; stepping around it.")

    # ------------------------------------------------------------------- kills
    def _count_kills(self, world, was: int) -> bool:
        """Count a kill, and say whether one happened so the loop can hurry."""
        if not was:
            return False
        gone = world.find(was)
        if gone is None or not gone.alive:
            self.memory.kills += 1
            self.memory.target_id = 0
            self.reporter.log(
                f"Killed {gone.name if gone else 'it'} "
                f"({self.memory.kills} so far).", "good")
            return True
        return False

    # -------------------------------------------------------------------- loop
    def _run(self) -> None:
        self._set(running=True, paused=True, state=PAUSED)
        self.reporter.log("Ready. Press the start hotkey to begin.", "info")
        started = time.time()
        active = 0.0

        while not self._stop.is_set():
            if not self._go.wait(timeout=0.2):
                continue
            now = time.time()

            hurry = False
            if not self._attach():
                self._set(state=WAITING)
                time.sleep(1.0)
                continue

            try:
                world = self._reader.read()
            except Exception as bad:                   # noqa: BLE001
                self.reporter.hint("read", f"Lost the client: {bad}")
                self._reader = None
                time.sleep(1.0)
                continue

            if world.me is None:
                self.reporter.hint("me", "The character is not in the world yet.")
                self._set(state=WAITING)
                time.sleep(0.5)
                continue

            if self.rules.origin is None:
                self.rules.origin = world.me.pos
                self.reporter.log("Origin set to where we are standing.", "info")

            was = self.memory.target_id
            try:
                # Watch health before deciding: who is bleeding is how this bot
                # knows who is already in a fight, who is hitting us, and who is
                # hitting the leech.
                self.memory.observe(world, now, self.rules.protect)
                plan = choose(world, self.rules, self.memory, now=now)
                killed = self._count_kills(
                    world, was if was != self.memory.target_id else 0)
                self._do(plan, world, now)
                hurry = killed or plan.entity is not None and (
                    plan.entity.id != self._was_after)
                self._was_after = plan.entity.id if plan.entity else 0
            except Exception as bad:                   # noqa: BLE001
                # One bad tick used to end the run in silence: the thread died,
                # the panel kept showing whatever it last saw, and nothing said
                # why. A tick is cheap - report it and take the next one, with
                # the traceback in the log so it can be fixed rather than
                # guessed at.
                import traceback
                self.reporter.hint("tick", "That tick went wrong: %r" % (bad,),
                                   cooldown=10.0)
                self.reporter.hint("trace", traceback.format_exc(),
                                   cooldown=10.0)

            target = world.find(self.memory.target_id)
            self._set(kills=self.memory.kills,
                      runtime=now - started,
                      active_seconds=active,
                      movers=len(world),
                      target_name=target.name if target else "",
                      player_hp=(world.me.hp / world.me.max_hp
                                 if world.me.max_hp else None),
                      target_hp=(target.hp / target.max_hp
                                 if target and target.max_hp else None),
                      window_found=winutil.window_exists(self.hwnd),
                      window_focused=self._control.focused())
            active += TICK
            # A kill or a change of quarry is the moment to look again, not the
            # moment to sleep: waiting a full tick there is most of the pause a
            # person notices between one monster and the next.
            time.sleep(QUICK if hurry else TICK)

        if self._control:
            self._control.release_all()
        if self._process:
            self._process.close()
        self._set(running=False, state=STOPPED)


def _apart(a, b) -> float:
    if a is None or b is None:
        return 1e9
    return math.hypot(a[0] - b[0], a[2] - b[2])


def run_headless(profile) -> int:
    """Console mode: same loop, same hotkeys, no window.

    The window is found by title rather than handed in, because without a panel
    there is nobody to pick one, and a profile that names a character already
    says which client it means.
    """
    from . import errors, inputs
    from .hotkeys import HotkeyManager

    found = winutil.find_window(profile.window_title, profile.window_process)
    bot = Bot(profile, echo=True, hwnd=found.hwnd if found else None)
    if not found:
        bot.reporter.log(
            "No game window matches %r yet; waiting for one."
            % (profile.window_title or "the profile"), "warn")

    keys = profile.hotkeys
    manager = HotkeyManager()
    bot.start()

    failure = manager.register({
        keys.pause: bot.pause,
        keys.resume: bot.resume,
        keys.stop: lambda: bot.stop("Kill switch pressed."),
    })
    if failure:
        bot.reporter.log(errors.hotkeys_unavailable(failure), level="warn")
        bot.reporter.log("Without hotkeys the only way out is Ctrl+C in this "
                         "window.", level="warn")
    else:
        bot.reporter.log(
            f"Hotkeys: {keys.pause.upper()} pause - {keys.resume.upper()} "
            f"resume - {keys.stop.upper()} stop.")

    try:
        while bot.is_running():
            time.sleep(0.2)
    except KeyboardInterrupt:
        bot.stop("Interrupted at the console.")
    finally:
        manager.clear()
        inputs.release_all()
    return 0
