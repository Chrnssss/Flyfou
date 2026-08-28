# Flyfou

An auto-farm bot for Flyff, built and tested against a private server client.
It reads the game's world out of the client's memory — every monster's name,
level, health and position — and acts by clicking, the way a person does.

There is no configuration of what things look like. You pick the game window,
say which levels of monster to fight, and press start.

**Read this before running it unattended.** Botting is against the rules on
most Flyff servers, official and private. Flyfou does not know your server's
rules and does not enforce them; that is yours to check.

It also reads another process's memory, which is more intrusive and more
detectable than watching the screen. It never modifies the game, its files or
its traffic — the reading is one-way, and every action goes through the mouse
and keyboard exactly as yours would. But a server that looks for programs
reading its client's memory can find this one.

## Getting started

1. Run `Flyfou.exe`. Nothing to install.
2. Start the game **windowed** or **borderless windowed**. Fullscreen exclusive
   breaks window capture and focus detection on Windows.
3. Press **New…**, choose the game window, and fill in one short form.
4. Press **Start farming**, then **F10** to begin. **F12** stops everything.

The first time it attaches to a build of the client it spends about ten seconds
working out where things live in memory, then remembers. Every launch after
that is instant.

## Setting up a spot

One screen, not a wizard, because there is nothing to point at any more. A
monster's level is a number the bot already has; its name is a string it already
has.

| Field | What it means |
| --- | --- |
| **The game window** | Which client to farm. The title is remembered, so several clients of the same game don't get confused. |
| **Level range** | Which monsters count. **This or a name list is required** — see below. |
| **Only these names** | Optional allowlist, comma separated. Empty means anything in the level range. |
| **Radius** | How far to roam from wherever you press start, in world units. `0` for no limit. |
| **Places to farm** | Optional route. Stand somewhere, press **Add where I am**, repeat. With a route it works each spot in turn; with none it farms around where you started. |
| **Attack key** | Pressed during a fight, for whatever skill you lead with. |
| **Heal key** | Optional. Food or a potion, pressed while resting. |
| **Rest below / fight again above** | Stop fighting under the first, don't resume until the second. |
| **Protect** | A character to defend — your leech or RM. |
| **Hotkeys** | Pause, resume, stop. They work from inside the game. |

A level range or a name list is not optional, and the app will refuse to start
without one. On this client **pets and wild monsters share the same `kind`
value** — `Smilodon Bestial` and `Dragon du rubis` are both 18 — so the client
itself cannot tell you which is which. A level range can: pets are level 1.
Without one, the bot would attack somebody's pet, so it attacks nothing instead.

## How it works

**Perception is memory.** Every entity in the client is an object with the same
class pointer at its head, so sweeping the heap for that one value enumerates
the world with no container to find and nothing to walk. Reading it costs about
thirteen milliseconds, which is why the bot can react in a tenth of a second
rather than a second.

**Action is the mouse.** Writing to the client was tried first and it looked
like it worked — write a position and the character moves on screen,
convincingly. It is an illusion: two other clients standing beside it watched it
not move at all. This client sends packets when it processes input, not when its
own memory changes. Memory is a mirror, not a lever.

So the bot turns a monster's world position into the pixel it is drawn at, and
double-clicks it. That both selects it and sends the character over — the client
does the walking, which is also why the bot almost never clicks the ground and
almost never picks up loot by accident.

**Nothing is hard-coded.** Offsets are found at run time against the running
client and cached under a key made from the executable's own link timestamp and
size. Patch the client and the key changes, the cache misses, and the finder
runs again. A cached layout is verified before every use, because a wrong offset
does not fail — it returns a plausible number from the wrong place.

Two things cannot be found by looking at a quiet town, and they are asked for
rather than guessed:

```
.venv\Scripts\python learn_offsets.py --calibrate Mynuthyj 200 77711 1490
```

Level, health and mana, read off the client's own window. The search is then
exact: the offset holding that number in your object and a believable one in
everybody else's. You should never need this — the finder inherits them across
rediscoveries — but it exists for when a cache is deleted or a client patched.

## What it does

All ten behaviours it was built for:

- fights 1v1, automatically, nearest first
- targets by level range or by name
- leaves monsters somebody else is already fighting
- hits back when something attacks it
- defends a character you name
- stays within a radius of where it started
- returns there when nothing is left to fight
- works a route of saved positions in turn
- steps around whatever it gets stuck on
- counts kills and runtime

The three that sound like they need a target field — killstealing, self-defence,
protecting a leech — do not have one to read. **This client records no targets
anywhere.** They work by watching health instead: a monster losing health that
the bot is not hitting is in somebody else's fight; its own health falling means
something is hitting it; the ward's falling means something is hitting the ward.

That comes with an honest limit. Nobody can say *which* monster swung, so it
goes for the nearest one that plausibly could have. Guessing the wrong
neighbour costs one wasted fight.

## What it does not do

- **See dropped items.** They are not entities and appear in no sweep, so a
  click meant for the ground can still land on loot.
- **Know about floating windows.** It avoids the chat box, action bar,
  inventory panel and minimap. A party or shop window left open over the play
  area will get clicked instead of the monster behind it. Close them.
- **Loot, buff, or repair.**
- **Recover from death.** It notices, says so, and waits.

## Safety

- Runs start **paused**.
- Input only goes out while the game window is genuinely in front, checked
  immediately before every click rather than once at the start.
- Nothing is clicked off the edge of the window or on the interface. A click
  that cannot land safely is refused and says why, rather than being clamped to
  an edge — a click at the edge of the screen is a click on whatever is at the
  edge of the screen.
- Low health stops the fighting, and stays stopped until health has properly
  recovered rather than the instant it crosses back over the line.
- The stop hotkey halts the loop and releases every button immediately.

Every run also writes to `%APPDATA%\Flyfou\flyfou.log`, including the reason
for anything it refused to do.

## Profiles and offsets

Profiles are YAML under `%APPDATA%\Flyfou\profiles\`, one folder each. Offsets
live beside them in `layouts.yaml`, one entry per build of the client, written
as hex so they can be compared against a debugger by a human. Set `FLYFOU_HOME`
to keep both somewhere else.

## Running from source

Windows only — `pywin32` for windows, `pydirectinput` for input the game
accepts. Python 3.9 or newer.

```
python -m pip install -r requirements.txt
python -m flyfou
```

Headless, with no windows at all:

```
python -m flyfou --headless --profile "Fleur 1v1"
python -m flyfou --list
```

`flyfou/runner.py` imports nothing from `flyfou/gui/`, so headless is the same
code path with a console instead of a panel.

## Building the exe

```
python -m pip install -r requirements.txt -r requirements-build.txt
python build.py
```

Produces `dist/Flyfou.exe`, a single file for someone with neither Python nor
pip. Close any running copy first, or the build cannot replace it.

## Project layout

```
flyfou/
  runner.py       the loop: read, decide, act — no GUI imports
  brain.py        what to do next, decided from one snapshot. No I/O at all
  control.py      the only place a button is ever pressed
  screen.py       world position -> the pixel it is drawn at
  report.py       the log and the status the panel draws
  profile.py      profiles, load/save
  winutil.py      windows, client rects, focus, DPI
  inputs.py       clicks and keys the game will accept
  hotkeys.py      global pause/resume/stop
  errors.py       user-facing messages, in one place
  cli.py          argument handling for both modes
  mem/
    process.py    reading another process
    scan.py       finding things in what was read
    world.py      the world as a snapshot of values
    layout.py     where things are in one build, and the cache
    discover.py   working that out from scratch
    learn.py      what only a fight and a walk can reveal
    record.py     keeping a play session to argue with afterwards
    study.py      arguing with it
  gui/
    launcher.py   the main window
    setup.py      the one setup form
    panel.py      the always-on-top run panel
    widgets.py, imaging.py, theme.py
tests/            synthetic checks, no game needed
learn_offsets.py  record a session and work offsets out of it
verify_click.py   one click, once, to prove the whole chain works
build.py          produces dist/Flyfou.exe
```

## Tests

No game needed for any of them. Each is a plain script.

```
python tests\test_brain.py     the ten behaviours, as worlds built by hand
python tests\test_control.py   where clicking is allowed, and where it is refused
python tests\test_layout.py    the finder's rules, and the offset cache
python tests\test_mem.py       reading and scanning another process
python tests\test_profile.py   older profiles still load and re-save
```

Every rule in `mem/discover.py` replaced an earlier rule that looked just as
reasonable and was wrong, so `test_layout.py` writes those mistakes down as
worlds where the right answer is known by construction — a position offset that
must beat a column of constants, an id that must beat the low half of a pointer,
a kind mask that must come from a bit that flickers rather than one that means
something.

## Extending

`brain.choose()` is a pure function of a snapshot: it reads nothing, writes
nothing and sleeps never, so any new rule about which monster to pick is
testable against a world you build in four lines. `Control` is the only place a
click happens, and it refuses everything unless the window is in front — keep
new behaviour behind it and the safety guarantees above still hold.

Auto-loot, buff upkeep and multi-client farming are all reasonable additions and
none are included.
