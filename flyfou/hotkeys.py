"""Global hotkeys — they work while the game has focus, which is the point."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

try:
    import keyboard
except ImportError:
    keyboard = None


class HotkeyManager:
    def __init__(self):
        self._handles: List = []

    @staticmethod
    def available() -> bool:
        return keyboard is not None

    def register(self, mapping: Dict[str, Callable[[], None]]) -> Optional[str]:
        """Bind hotkeys; returns a short reason string if it couldn't be done.

        Registering hooks globally needs privileges the user may not have, so
        failure has to be survivable rather than fatal.
        """
        self.clear()
        if keyboard is None:
            return "the 'keyboard' component isn't available"
        try:
            for combo, action in mapping.items():
                if combo:
                    self._handles.append(keyboard.add_hotkey(combo, action, suppress=False))
        except Exception as exc:
            self.clear()
            return f"{exc.__class__.__name__}: {exc}"
        return None

    def clear(self) -> None:
        for handle in self._handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self._handles = []
