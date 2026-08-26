"""Background source of game frames for the wizard's live previews.

Two ways to see the game: ask the window to draw itself (works while the wizard
covers it, but DirectX often answers with a black frame) or grab that patch of
screen (always renders, but shows whatever is on top). We try the first, check
it, and fall back — then tell the user which one they're looking at, because a
preview of the wrong pixels is worse than no preview.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import numpy as np

from .. import capture, vision, winutil

SOURCE_WINDOW = "window"
SOURCE_SCREEN = "screen"


@dataclass
class FeedFrame:
    image: np.ndarray
    client_size: Tuple[int, int]
    focused: bool
    source: str
    at: float

    @property
    def trusted(self) -> bool:
        """Whether these pixels are certainly the game's own."""
        return self.source == SOURCE_WINDOW or self.focused

    @property
    def caption(self) -> Tuple[str, str]:
        if self.source == SOURCE_WINDOW:
            return "Live from the game window", "good"
        if self.focused:
            return "Live from the screen", "good"
        return "This window may be covering the game — press Check now for a clean look", "warn"


class GameFeed:
    """Grabs the game's client area on a background thread; widgets poll latest()."""

    def __init__(self, get_hwnd: Callable[[], Optional[int]], fps: float = 6.0):
        self._get_hwnd = get_hwnd
        self._period = 1.0 / fps
        self._frame: Optional[FeedFrame] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._window_source_works: Optional[bool] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="flyfou-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def latest(self) -> Optional[FeedFrame]:
        with self._lock:
            return self._frame

    def grab_now(self, prefer_screen: bool = False) -> Optional[FeedFrame]:
        """Synchronous grab, used by 'Check now' once the wizard is out of the way."""
        return self._grab(prefer_screen=prefer_screen)

    def _loop(self) -> None:
        while not self._stop.is_set():
            began = time.perf_counter()
            try:
                frame = self._grab()
            except Exception:
                frame = None
            if frame is not None:
                with self._lock:
                    self._frame = frame
            time.sleep(max(0.0, self._period - (time.perf_counter() - began)))

    def _grab(self, prefer_screen: bool = False) -> Optional[FeedFrame]:
        hwnd = self._get_hwnd()
        if not hwnd or not winutil.window_exists(hwnd) or winutil.is_minimised(hwnd):
            return None
        focused = winutil.is_foreground(hwnd)

        if not prefer_screen and self._window_source_works is not False:
            image = winutil.print_window(hwnd)
            usable = image is not None and not vision.frame_is_blank(image)
            if self._window_source_works is None:
                self._window_source_works = usable
            if usable:
                h, w = image.shape[:2]
                return FeedFrame(image, (w, h), focused, SOURCE_WINDOW, time.time())

        grabbed = capture.grab_client(hwnd)
        if grabbed is None:
            return None
        image, (_, _, w, h) = grabbed
        return FeedFrame(image, (w, h), focused, SOURCE_SCREEN, time.time())
