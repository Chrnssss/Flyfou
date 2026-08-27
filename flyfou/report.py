"""What the bot says about itself, and what state it is in.

Kept apart from the loop because three different things need it and none of
them should have to import a farming state machine to get a log line: the panel
polls it, headless mode echoes it, and the loop writes to it from its own
thread. It is the only place in the bot with a lock.

The state names are here rather than in the loop for the same reason - the panel
colours its status chip by them, and a panel that has to import the whole bot to
know the word "Searching" is a panel wired to the wrong thing.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, List, Optional, Tuple


def log_path() -> Path:
    """Where the run writes itself down.

    A bot that only reports into a panel is a bot nobody can debug: when it goes
    wrong the person watching it says "it did not work", which is true and says
    nothing. Every line the panel shows is also appended here, so afterwards
    there is something to read.
    """
    home = os.environ.get("FLYFOU_HOME")
    base = Path(home) if home else Path(
        os.environ.get("APPDATA", Path.home())) / "Flyfou"
    return base / "flyfou.log"


SEARCHING = "Searching"
FIGHTING = "Fighting"
RETURNING = "Returning"
RESTING = "Resting"
PAUSED = "Paused"
WAITING = "Waiting for game"
STOPPED = "Stopped"


@dataclass
class LogLine:
    index: int
    at: float
    level: str  # info | good | warn
    text: str

    @property
    def clock(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.at))


class Reporter:
    """Thread-safe log the GUI polls and headless mode echoes."""

    def __init__(self, echo: bool = False, maxlen: int = 400,
                 path: Optional[Path] = None):
        self.echo = echo
        self.path = path
        self._lines: Deque[LogLine] = deque(maxlen=maxlen)
        self._next = 0
        self._last_hint: dict = {}
        self._lock = threading.Lock()
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as sink:
                    sink.write("\n--- %s ---\n"
                               % time.strftime("%Y-%m-%d %H:%M:%S"))
            except OSError:
                self.path = None       # a log that cannot be written is not a
                                       # reason to refuse to run

    def log(self, text: str, level: str = "info") -> None:
        with self._lock:
            line = LogLine(self._next, time.time(), level, text)
            self._next += 1
            self._lines.append(line)
        marker = {"warn": "!", "good": "+"}.get(level, " ")
        if self.echo:
            print(f"[{line.clock}] {marker} {text}", flush=True)
        if self.path:
            try:
                with open(self.path, "a", encoding="utf-8") as sink:
                    sink.write(f"[{line.clock}] {marker} {text}\n")
            except OSError:
                pass

    def hint(self, key: str, text: str, cooldown: float = 25.0) -> None:
        """A diagnostic that would otherwise repeat every tick."""
        now = time.time()
        with self._lock:
            if now - self._last_hint.get(key, 0.0) < cooldown:
                return
            self._last_hint[key] = now
        self.log(text, level="warn")

    def forget(self, key: str) -> None:
        with self._lock:
            self._last_hint.pop(key, None)

    def read(self, cursor: int = 0) -> Tuple[List[LogLine], int]:
        with self._lock:
            fresh = [line for line in self._lines if line.index >= cursor]
            return fresh, self._next


@dataclass
class Status:
    """One glance at the run, as the panel draws it."""

    state: str = STOPPED
    running: bool = False
    paused: bool = True
    kills: int = 0
    runtime: float = 0.0
    active_seconds: float = 0.0
    player_hp: Optional[float] = None
    target_hp: Optional[float] = None
    target_name: str = ""
    movers: int = 0
    window_found: bool = False
    window_focused: bool = False
    client_size: Tuple[int, int] = (0, 0)

    @property
    def kills_per_hour(self) -> float:
        if self.active_seconds <= 60:
            return 0.0
        return self.kills * 3600.0 / self.active_seconds
