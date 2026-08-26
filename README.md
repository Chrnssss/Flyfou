# Flyfou

A screen-capture auto-farm bot for Flyff (built and tested against a private
server client). You show it a monster by dragging a box around it, mark your
HP bars the same way, press the keys you attack with — and it farms. No config
files, no terminal, no pixel coordinates.

It does **not** read game memory, modify game files, or touch the network
protocol. It only looks at pixels on screen and sends mouse/keyboard input the
same way a human would. That keeps it simple and client-agnostic, but it also
means it's only as good as the pictures and regions you gave it.

**Before you run this unattended: botting is against the rules on most Flyff
servers, official or private.** Flyfou doesn't know or enforce your server's
rules — that's on you to check. Test it supervised first. There's an
auto-pause-on-low-HP safety net, but no auto-potion, no death recovery, and no
defence against another player or a GM interacting with your character.

## Getting started

1. Download `Flyfou.exe` and double-click it. Nothing to install.
2. Start the game in **windowed or borderless-windowed** mode. (Fullscreen
   exclusive breaks screen capture and focus detection on Windows.)
3. Press **New…** and follow the setup wizard.
4. Press **Start farming**, get your character to the spot, and press
   **Start** in the little panel that appears.

The whole thing takes a few minutes the first time and about ten seconds every
time after that, because profiles are saved.

## What the wizard asks

Nine screens, one thing each. Every screen previews what Flyfou currently sees,
so a bad threshold or a misplaced region is visible immediately rather than an
hour into a run.

| Step | What you do |
| --- | --- |
| **Name this farm spot** | Something you'll recognise later, e.g. `aibatt-lv30`. |
| **Pick the game window** | Live thumbnails of every window on screen — click the one showing the game. No typing window titles. The title is remembered, so if you run two clients of the same game it farms in the one you picked. |
| **Show it the monster** | Freezes a screenshot; drag a box around the monster. Capture two or three facings for better matching. A green box appears over whatever it currently matches, with the score. |
| **Mark your home landmark** | Optional. A rock, a statue, a building corner — something that doesn't move. Flyfou walks back to it so your character doesn't drift off the spot. |
| **Mark your own HP bar** | Drag a box around the filled part. The colour is detected automatically from the pixels inside the box; you can override it if it guessed wrong. The percentage updates live while you adjust. |
| **Mark the target's HP bar** | Click a monster in-game first so the bar exists, then mark it. This is how kills are detected. |
| **Record your attack rotation** | Press Record, then press your keys in order. Each gets a cooldown spinner. |
| **Movement and hotkeys** | Search radius, how far it steps home, pause/resume/stop hotkeys. Defaults are sensible. |
| **Ready to farm** | A summary. Save. |

Drag the box with the mouse, then press Enter to accept it. Arrow keys nudge it
a pixel at a time and Shift+arrows resize it, which matters for HP bars only a
few pixels tall; a magnifier follows the cursor so you can land on the exact
pixel. The note at the top moves out of the way when you approach it, and **H**
hides it outright — the target's HP bar is usually drawn right underneath it.
Esc cancels.

## The control panel

Starting a run opens a small always-on-top window:

- **Start / Pause** and **Stop**.
- Current state — *Searching*, *Fighting*, *Returning home*, *Paused*,
  *Waiting for game*.
- Kill count, session runtime and kills/hour.
- Your HP and the target's HP, live.
- The last few log lines, including any diagnostics.

The global hotkeys keep working from inside the game — **F9** pause, **F10**
resume, **F12** stop, changeable per profile.

## Safety behaviour

Unchanged from the original command-line version:

- Runs start **paused**. Nothing happens until you say so.
- Input only goes out while the game window is genuinely in the foreground. If
  you alt-tab away, it stops clicking and typing until you come back.
- HP below your critical threshold (25% by default) pauses the run instead of
  fighting on.
- The stop hotkey halts the loop and releases every held button immediately.

## When something doesn't work

Flyfou tries to say what to do rather than what went wrong. For example, if it
can't find the monster it will tell you the best score it actually got, and
suggest either re-capturing at your current camera zoom or the specific
threshold that would have matched. The same goes for an HP region that reads
0% forever, a game window that went fullscreen-exclusive, a window that
disappeared, and a missing component in the download.

## Profiles

Each farm spot is a profile — a monster, a place, a rotation. Switch between
them from the dropdown in the main window, and use **Duplicate** to make a
variant without redoing the whole wizard.

They're stored as YAML plus PNGs under `%APPDATA%\Flyfou\profiles\`, one
folder each. You never need to open them, but they're plain text if you want
to, and they're easy to back up or share. Set `FLYFOU_HOME` to keep them
somewhere else.

**Regions are stored as fractions of the game's client area, not as pixels.**
Change your resolution and everything still lands in the right place; captured
monster images get rescaled to match. Flyfou warns you when the window size
differs from when the profile was made, since template matching gets less
reliable across big jumps and a re-capture is usually worth it.

## Running from source

Windows only — `pywin32` for window handling, `pydirectinput` for input the
game accepts. Python 3.9 or newer.

```
python -m pip install -r requirements.txt
python -m flyfou
```

Headless, for running without any windows at all:

```
python -m flyfou --headless --profile "aibatt-lv30"
python -m flyfou --list
```

The bot loop in `flyfou/bot.py` imports nothing from `flyfou/gui/`, so headless
mode is the same code path with a console instead of a panel.

## Building the exe

```
python -m pip install -r requirements.txt -r requirements-build.txt
python build.py
```

That produces `dist/Flyfou.exe` — a single file you can hand to someone who
has neither Python nor pip. It's built with a console attached so `--headless`
has somewhere to print; the GUI hides that console on the way up.

## Why tkinter and not PySide6

tkinter ships with Python, so there's no extra install for people running from
source, and it adds roughly nothing to the packaged exe — PySide6 would have
added 60+ MB of Qt to a download aimed at non-technical users, and Qt's LGPL
terms are a complication for redistributing a single static binary. The UI here
is a wizard, a list and a status panel; none of that needs Qt's widget set. The
one genuinely demanding piece is the drag-to-select overlay, and a borderless
topmost `Toplevel` with a `Canvas` handles it fine.

## Project layout

```
flyfou/
  bot.py          the farming loop and its state machine — no GUI imports
  profile.py      profiles, fractional geometry, load/save
  vision.py       template matching, HP bar reading, colour detection
  capture.py      screen grabs
  winutil.py      window enumeration, client rects, focus, DPI
  inputs.py       clicks and keys the game will accept
  hotkeys.py      global pause/resume/stop
  errors.py       every user-facing message, in one place
  cli.py          argument handling for both modes
  gui/
    launcher.py   the main window
    wizard.py     the nine setup screens
    snip.py       the drag-a-box overlay
    panel.py      the always-on-top run panel
    previews.py   live match and HP previews
    feed.py       background frame grabber for the wizard
    widgets.py, imaging.py, theme.py
build.py          produces dist/Flyfou.exe
```

## Extending

The state machine is `Bot._searching` / `_attack` / `_returning` in
`flyfou/bot.py`, picked between by `Bot._update_engagement`, which reads the
target's HP bar to decide whether a fight is on — so a monster you target by
hand counts the same as one the bot clicked. Auto-loot, auto-potion, buff upkeep and multi-spot rotation
are all reasonable additions and none are included. Anything that needs to act
goes through `Bot._click` and `Bot._press`, which refuse to do anything unless
the game window is in the foreground — keep new behaviour behind those and the
safety guarantees above still hold.
