"""Mouse and keyboard output.

pydirectinput is used rather than pyautogui because DirectX games ignore the
synthetic events pyautogui sends. Nothing here checks whether the game is
focused — that guard lives in the bot, which owns the decision to act.
"""

from __future__ import annotations

import re
from typing import Optional

try:
    import pydirectinput

    pydirectinput.FAILSAFE = False
    pydirectinput.PAUSE = 0.0
except ImportError:
    pydirectinput = None


def available() -> bool:
    return pydirectinput is not None


def click(x: int, y: int, button: str = "left") -> None:
    pydirectinput.moveTo(x, y)
    pydirectinput.click(x=x, y=y, button=button)


def press(key: str) -> None:
    pydirectinput.press(key)


def release_all() -> None:
    """Called on stop and on the kill-switch, so nothing is left held down."""
    if pydirectinput is None:
        return
    for button in ("left", "right", "middle"):
        try:
            pydirectinput.mouseUp(button=button)
        except Exception:
            pass
    for key in ("shift", "ctrl", "alt"):
        try:
            pydirectinput.keyUp(key)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# key names
# --------------------------------------------------------------------------- #

_TK_KEYSYMS = {
    "Return": "enter",
    "KP_Enter": "enter",
    "Escape": "esc",
    "BackSpace": "backspace",
    "Prior": "pageup",
    "Next": "pagedown",
    "Delete": "delete",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "space": "space",
    "Tab": "tab",
    "Control_L": "ctrl",
    "Control_R": "ctrl",
    "Shift_L": "shift",
    "Shift_R": "shift",
    "Alt_L": "alt",
    "Alt_R": "alt",
    "minus": "-",
    "plus": "+",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backslash": "\\",
    "bracketleft": "[",
    "bracketright": "]",
    "semicolon": ";",
    "apostrophe": "'",
    "grave": "`",
}

_IGNORED_KEYSYMS = {"Caps_Lock", "Num_Lock", "Scroll_Lock", "Super_L", "Super_R", "Menu"}


def from_keysym(keysym: str) -> Optional[str]:
    """Turn a Tk key event into a pydirectinput key name, or None if unusable."""
    if not keysym or keysym in _IGNORED_KEYSYMS:
        return None
    if keysym in _TK_KEYSYMS:
        return _TK_KEYSYMS[keysym]
    if re.fullmatch(r"F\d{1,2}", keysym):
        return keysym.lower()
    if re.fullmatch(r"KP_\d", keysym):
        return f"num{keysym[-1]}"
    if len(keysym) == 1 and keysym.isprintable():
        return keysym.lower()
    return None


def display_name(key: str) -> str:
    if len(key) == 1:
        return key.upper()
    return key.upper() if key.startswith("f") and key[1:].isdigit() else key.capitalize()
