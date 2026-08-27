"""Record a play session, then work the last offsets out of the recording.

    .venv\\Scripts\\python learn_offsets.py [character] [seconds]
    .venv\\Scripts\\python learn_offsets.py --replay <recording.npz>
    .venv\\Scripts\\python learn_offsets.py --calibrate <character> <level> <hp> <mp>

The last form exists because level, health and mana cannot be worked out by
looking - every number in the object is just a number, and only somebody reading
the client's own window knows which one is health. Type in what the window says
and the search is then exact: the offset holding that number in our object, and
a believable number in everybody else's.

Run it if anything ever complains that there is no health offset. That should
not happen now that a rediscovery inherits what it cannot re-derive, but a cache
can still be deleted, and a client can still be patched.

Run the first form and then PLAY for the time it says, on a map with monsters.
Two things have to actually happen, and the live counters show whether they are:

  * fight. Target a monster and KILL it, then target a different one, several
    times over. What is looked for is a field naming something that then comes
    to a bad end, and with one-shot kills the end is the monster vanishing
    rather than its health sliding down - so kills count for more than damage,
    and switching target between them counts for more than either.

  * walk. Click somewhere a good distance off - tens of units, not a step - let
    the character arrive without stopping halfway, then stand still for a
    moment. Three or four of these. Both halves are the test: the gap closing
    while moving, and standing on the spot once stopped.

The session is saved as a .npz next to this file whatever happens, and the
second form re-runs the analysis on it. That matters: if the rules turn out to
be wrong, they can be fixed and re-run against the same session instead of
costing another one. It records the player, every object the player points at,
and the id, address, health and position of everything else - because neither
remaining field turned out to live in the player itself.

Everything found is written into the layout cache, so this is once per client
patch rather than once per launch.
"""

from __future__ import annotations

import sys
import time

import win32gui
import win32process

from flyfou.mem import (LayoutStore, apply_calibration, calibrate, layout_for,
                        open_client, resolve_player)
from flyfou.mem.record import Recording, record
from flyfou.mem.study import study

TITLE = "Airborn - "


def clients():
    found = []

    def visit(hwnd, _extra):
        title = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and title.startswith(TITLE):
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            found.append((pid, title[len(TITLE):].strip()))

    win32gui.EnumWindows(visit, None)
    return sorted(set(found), key=lambda pair: pair[1])


def report(found, layout=None, store=None):
    print("\n" + "=" * 72)
    for note in found.notes:
        print("  " + note)

    if layout is None:
        return 0
    wrote = []
    for name in ("target_id", "dest"):
        value = getattr(found, name)
        if value is None or getattr(layout, name, None) is not None:
            continue
        via = getattr(found, name.replace("target_id", "target") + "_via"
                      if name == "target_id" else "dest_via")
        setattr(layout, name, value)
        setattr(layout, "target_via" if name == "target_id" else "dest_via", via)
        wrote.append("%s %s" % (name, "+0x%X" % value if via is None
                                else "+0x%X -> +0x%X" % (via, value)))
    if wrote:
        layout.note = ((layout.note + "; " if layout.note else "")
                       + "recorded: " + ", ".join(wrote))
        store.save(layout)
        print("\n  written to the layout cache: " + ", ".join(wrote))
    else:
        print("\n  nothing new to write; the reasons are above")
    return 0


def recalibrate(argv) -> int:
    """Pin level, health and mana from numbers read off the client's window."""
    if len(argv) < 4:
        print("--calibrate needs a character and three numbers: "
              "the level, the health and the mana its window is showing.")
        return 1
    who_wanted, level, hp, mp = argv[0], int(argv[1]), int(argv[2]), int(argv[3])

    running = [c for c in clients() if c[1].lower() == who_wanted.lower()]
    if not running:
        print("No client called %r. Open ones: %s"
              % (who_wanted, ", ".join(w for _p, w in clients())))
        return 1
    pid, who = running[0]

    store = LayoutStore()
    process, module = open_client(pid)
    try:
        layout = layout_for(process, module, who, store=store,
                            report=lambda m: print("  " + m))
        me = resolve_player(process, module, layout)
        if not me:
            print("The player object could not be resolved right now; if the "
                  "client is loading, wait a moment and try again.")
            return 1
        # Health is asked for as a pair, and unhurt they are the same number,
        # which no search can split. That is said rather than guessed at.
        result = calibrate(process, module.base + layout.mover_vtable, me,
                           level=level, hp=hp, max_hp=hp, mp=mp)
        for note in result.notes:
            print("  " + note)
        moved = apply_calibration(layout, result)
        if result.anything:
            store.save(layout)
            print("\n  saved: level %s, health %s, mana %s"
                  % (_at(layout.level), _at(layout.hp), _at(layout.mp)))
        else:
            print("\n  nothing was pinned; check the numbers against the window")
        return 0 if result.anything else 1
    finally:
        process.close()


def _at(offset):
    return "+0x%X" % offset if offset is not None else "not found"


def main(argv):
    if argv and argv[0] == "--calibrate":
        return recalibrate(argv[1:])
    if argv and argv[0] == "--replay":
        if len(argv) < 2:
            print("--replay needs the path to a recording")
            return 1
        found = study(Recording.load(argv[1]))
        store = LayoutStore()
        running = clients()
        if not running:
            return report(found)
        process, module = open_client(running[0][0])
        try:
            layout = layout_for(process, module, running[0][1], store=store)
            return report(found, layout, store)
        finally:
            process.close()

    who_wanted = argv[0] if argv else None
    seconds = float(argv[1]) if len(argv) > 1 else 150.0

    running = clients()
    if not running:
        print("No client window starting with %r is open." % TITLE)
        return 1
    if who_wanted:
        running = [c for c in running if c[1].lower() == who_wanted.lower()]
        if not running:
            print("No client called %r. Open ones: %s"
                  % (who_wanted, ", ".join(w for _p, w in clients())))
            return 1
    pid, who = running[0]

    store = LayoutStore()
    process, module = open_client(pid)
    try:
        layout = layout_for(process, module, who, store=store,
                            report=lambda m: print("  " + m))
        print("\nRecording %s for %.0fs. PLAY NOW: kill a monster, target a "
              "different one, and walk somewhere far.\n" % (who, seconds))
        session = record(process, module, layout, seconds=seconds,
                         report=lambda m: print("  " + m))
    finally:
        process.close()

    path = "_session_%s_%s.npz" % (who, time.strftime("%H%M%S"))
    session.who, session.build = who, layout.build
    session.save(path)
    print("\n  saved the session to %s" % path)

    return report(study(session), layout, store)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
