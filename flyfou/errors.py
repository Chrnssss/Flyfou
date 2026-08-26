"""Actionable failure reporting.

Every message a user can hit is written here rather than at the raise site, so
they can be reviewed as a set and kept in the same voice: say what happened,
say what to do about it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import List, Optional


class FlyfouError(Exception):
    """An error with a message meant to be shown to a non-technical user."""

    def __init__(self, message: str, remedy: str = ""):
        super().__init__(message)
        self.message = message
        self.remedy = remedy

    def full(self) -> str:
        return f"{self.message}\n\n{self.remedy}" if self.remedy else self.message


@dataclass
class MissingDependency:
    module: str
    package: str
    what_breaks: str


DEPENDENCIES = [
    MissingDependency("numpy", "numpy", "all image processing"),
    MissingDependency("cv2", "opencv-python", "finding monsters on screen"),
    MissingDependency("mss", "mss", "capturing the screen"),
    MissingDependency("yaml", "PyYAML", "loading and saving profiles"),
    MissingDependency("win32gui", "pywin32", "finding the game window"),
    MissingDependency("pydirectinput", "pydirectinput", "sending clicks and keys to the game"),
    MissingDependency("keyboard", "keyboard", "the global pause/resume/stop hotkeys"),
    MissingDependency("PIL", "Pillow", "showing screenshots in the setup wizard"),
]


def missing_dependencies() -> List[MissingDependency]:
    missing = []
    for dep in DEPENDENCIES:
        try:
            importlib.import_module(dep.module)
        except Exception:
            missing.append(dep)
    return missing


def dependency_report(missing: List[MissingDependency]) -> str:
    lines = ["Flyfou can't start because some components are missing:", ""]
    for dep in missing:
        lines.append(f"  • {dep.package} — needed for {dep.what_breaks}")
    lines += [
        "",
        "If you downloaded Flyfou.exe, this shouldn't happen — the download is",
        "probably incomplete or was partially removed by antivirus. Re-download it.",
        "",
        "If you're running from source, install them with:",
        "    pip install -r requirements.txt",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# runtime diagnostics
# --------------------------------------------------------------------------- #

def no_monster_match(best_score: float, threshold: float, template_count: int) -> str:
    suggested = max(0.45, round(best_score - 0.03, 2))
    msg = (
        f"No monster match above {threshold:.2f} — best match was {best_score:.2f}. "
    )
    if best_score < 0.35:
        msg += (
            "That's far off, so the template probably doesn't look like anything on "
            "screen right now. Check you're at the right farm spot, then press Set up "
            "and capture the monster again."
        )
    else:
        msg += (
            f"Try re-capturing the monster template at your current camera zoom, or "
            f"lower the match threshold to about {suggested:.2f}. Both are on the "
            f"'Show it the monster' screen under Set up. "
        )
        if template_count == 1:
            msg += "Capturing a second template of the same monster facing another way also helps."
    return msg


def no_home_match(best_score: float, threshold: float) -> str:
    return (
        f"Can't see the home landmark — best match was {best_score:.2f}, below the "
        f"{threshold:.2f} threshold. Either the character has wandered out of sight of "
        f"it, or the landmark template needs re-capturing at your farm camera angle. "
        f"Wandering randomly until it shows up again."
    )


def target_hp_never_reads(color, tolerance: int) -> str:
    r, g, b = color
    return (
        f"Clicked a monster but the target HP bar region still reads empty. The region "
        f"or its colour is probably wrong — it's looking for RGB({r}, {g}, {b}) with a "
        f"tolerance of {tolerance}. Press Set up and re-do 'Mark the target's HP bar' "
        f"with a monster actually targeted, so the bar is on screen when you drag the box."
    )


def player_hp_unreadable(color, tolerance: int) -> str:
    r, g, b = color
    return (
        f"Your own HP bar reads 0% — treating it as unreadable rather than as an "
        f"emergency, so the run continues. It's looking for RGB({r}, {g}, {b}) within "
        f"a tolerance of {tolerance} inside the region you picked. Press Set up and re-do "
        f"'Mark your own HP bar', checking the live percentage there matches the game."
    )


def blank_capture() -> str:
    return (
        "The game window is capturing as a blank frame. This almost always means the "
        "game is in fullscreen-exclusive mode, which can't be screen-captured. In the "
        "game's video options switch to Windowed or Borderless Windowed, then start the "
        "run again."
    )


def window_gone(title: str) -> str:
    return (
        f"The game window ('{title}') closed or disappeared. Run paused. Bring the game "
        f"back up and press Start again — no input will be sent in the meantime."
    )


def window_minimised(title: str) -> str:
    return f"The game window ('{title}') is minimised, so there's nothing to look at. Restore it to continue."


def resolution_changed(saved, current) -> str:
    sw, sh = saved
    cw, ch = current
    saved_ar = sw / sh if sh else 0
    cur_ar = cw / ch if ch else 0
    msg = (
        f"The game window is {cw}×{ch} now, but this profile was set up at {sw}×{sh}. "
        f"Regions are stored as proportions so they'll follow the resize, and monster "
        f"templates are being scaled by {cw / sw:.2f}× to compensate."
    )
    if abs(saved_ar - cur_ar) > 0.02:
        msg += (
            " The aspect ratio changed too, which stretches everything unevenly — "
            "matching will likely be unreliable until you press Set up and capture the "
            "monster again at this size."
        )
    else:
        msg += " If matching gets flaky, press Set up and capture the monster again at this size."
    return msg


def hotkeys_unavailable(detail: str) -> str:
    return (
        f"Global hotkeys couldn't be registered ({detail}). The Start/Stop buttons on "
        f"the control panel still work — you just can't pause from inside the game. "
        f"Running Flyfou as administrator usually fixes this."
    )


def no_game_window(title_hint: Optional[str]) -> str:
    if title_hint:
        return (
            f"Can't find the game window this profile was set up with "
            f"('{title_hint}'). Start the game first, then press Start. If the game is "
            f"running under a different window title now, press Set up and pick it again."
        )
    return "No game window picked yet. Press Set up and choose the game window."


def capture_not_foreground() -> str:
    return (
        "The game wasn't in front when the screenshot was taken, so you'd be cropping "
        "the wrong window. Click Capture again — Flyfou will bring the game forward "
        "itself, but it needs the game to not be minimised."
    )


def region_too_small() -> str:
    return "That selection is too small to be useful. Drag a box at least a few pixels across."


def color_detection_failed() -> str:
    return (
        "Couldn't pick out a bar colour from that region — everything in it is grey or "
        "washed out. Make sure the box covers a part of the bar that's actually filled "
        "in, not just its empty background or border. You can also set the colour by hand below."
    )
