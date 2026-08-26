"""The farming loop. No GUI imports live here — this runs headless as-is.

Safety rules, unchanged from the original CLI version:
  * starts paused, so nothing happens until the user says so;
  * input only goes out while the game window is genuinely in the foreground;
  * critically low HP pauses the run instead of fighting on;
  * the stop hotkey halts the loop and releases every button immediately.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Tuple

import numpy as np

from . import capture, errors, inputs, vision, winutil
from .errors import FlyfouError
from .profile import Profile

SEARCHING = "Searching"
FIGHTING = "Fighting"
RETURNING = "Returning home"
PAUSED = "Paused"
WAITING = "Waiting for game"
STOPPED = "Stopped"

_MISSES_BEFORE_HINT = 12
_FAILED_ENGAGES_BEFORE_HINT = 3
_UNREADABLE_HP_SECONDS = 8.0


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

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

    def __init__(self, echo: bool = False, maxlen: int = 400):
        self.echo = echo
        self._lines: Deque[LogLine] = deque(maxlen=maxlen)
        self._next = 0
        self._last_hint: dict = {}
        self._lock = threading.Lock()

    def log(self, text: str, level: str = "info") -> None:
        with self._lock:
            line = LogLine(self._next, time.time(), level, text)
            self._next += 1
            self._lines.append(line)
        if self.echo:
            marker = {"warn": "!", "good": "+"}.get(level, " ")
            print(f"[{line.clock}] {marker} {text}", flush=True)

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
    state: str = STOPPED
    running: bool = False
    paused: bool = True
    kills: int = 0
    runtime: float = 0.0
    active_seconds: float = 0.0
    player_hp: Optional[float] = None
    target_hp: Optional[float] = None
    match_score: float = 0.0
    window_found: bool = False
    window_focused: bool = False
    client_size: Tuple[int, int] = (0, 0)

    @property
    def kills_per_hour(self) -> float:
        return self.kills * 3600.0 / self.active_seconds if self.active_seconds > 60 else 0.0


@dataclass
class _Cooldown:
    key: str
    seconds: float
    last_used: float = 0.0

    def ready(self, now: float) -> bool:
        return now - self.last_used >= self.seconds


# --------------------------------------------------------------------------- #
# the bot
# --------------------------------------------------------------------------- #

class Bot:
    def __init__(self, profile: Profile, echo: bool = False):
        self.profile = profile
        self.reporter = Reporter(echo=echo)

        self._status = Status()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = True
        self._thread: Optional[threading.Thread] = None

        self._hwnd: Optional[int] = None
        self._monsters: List[np.ndarray] = []
        self._home: Optional[np.ndarray] = None
        self._loaded_for_size: Optional[Tuple[int, int]] = None

        self._skills = [_Cooldown(s.key, s.cooldown) for s in profile.skills]
        self._engaged = False
        self._target_empty_since = 0.0
        self._kills = 0
        self._kills_since_home = 0
        self._last_wander = 0.0
        self._started_at = 0.0
        self._active_seconds = 0.0
        self._last_tick = 0.0

        self._consecutive_misses = 0
        self._best_miss_score = 0.0
        self._failed_engages = 0
        self._player_hp_zero_since = 0.0

    # ---- lifecycle -------------------------------------------------------- #

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        problems = self.profile.problems()
        if problems:
            raise FlyfouError(
                f"Profile '{self.profile.name}' isn't finished yet:\n  • "
                + "\n  • ".join(problems),
                "Open Set up for this profile and complete the missing steps.",
            )
        self._stop.clear()
        self._paused = True
        self._started_at = time.time()
        self._last_tick = self._started_at
        self._thread = threading.Thread(target=self._run, name="flyfou-bot", daemon=True)
        self._thread.start()

    def stop(self, reason: str = "") -> None:
        if not (self._thread and self._thread.is_alive()):
            return
        self._stop.set()
        self._paused = True
        inputs.release_all()
        self.reporter.log(reason or "Stopped.", level="warn" if reason else "info")

    def pause(self) -> None:
        if not self._paused:
            self._paused = True
            inputs.release_all()
            self.reporter.log("Paused — no input will be sent.")

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            self.reporter.log("Running. Bring the game to the front to let it act.", level="good")

    def toggle(self) -> None:
        self.resume() if self._paused else self.pause()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> Status:
        with self._lock:
            return Status(**vars(self._status))

    def _set(self, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._status, key, value)

    # ---- window / frame --------------------------------------------------- #

    def _resolve_window(self) -> Optional[int]:
        if winutil.window_exists(self._hwnd):
            return self._hwnd
        if self._hwnd is not None:
            self.reporter.hint("window_gone", errors.window_gone(self.profile.window_title))
            self._hwnd = None
        info = winutil.find_window(self.profile.window_title, self.profile.window_process)
        if info is None:
            self.reporter.hint("no_window", errors.no_game_window(self.profile.window_title), cooldown=15.0)
            return None
        self._hwnd = info.hwnd
        self.reporter.forget("no_window")
        self.reporter.forget("window_gone")
        self.reporter.log(f"Found the game window: {info.title}")
        return self._hwnd

    def _ensure_templates(self, client_w: int, client_h: int) -> None:
        if self._loaded_for_size == (client_w, client_h):
            return
        scale = self.profile.scale_for(client_w, client_h)
        self._monsters = self.profile.load_monster_templates(scale)
        self._home = self.profile.load_home_template(scale)
        self._loaded_for_size = (client_w, client_h)
        if self.profile.size_changed(client_w, client_h):
            self.reporter.hint(
                "resolution",
                errors.resolution_changed(self.profile.client_size, (client_w, client_h)),
                cooldown=300.0,
            )

    # ---- perception ------------------------------------------------------- #

    def _read_bar(self, frame: np.ndarray, bar) -> float:
        if not bar.configured():
            return 0.0
        region = capture.crop_fraction(frame, bar.rect)
        return vision.bar_fill_fraction(region, bar.filled_color, bar.tolerance)

    def _find(self, frame: np.ndarray, templates) -> Optional[vision.Match]:
        match = vision.best_match(frame, templates)
        if match is None:
            return None
        return match if match.score >= self.profile.match_threshold else None

    # ---- actions ---------------------------------------------------------- #

    def _click(self, client_xy: Tuple[int, int], button: str = "left") -> bool:
        if not winutil.is_foreground(self._hwnd):
            return False
        cx, cy, _, _ = winutil.client_rect(self._hwnd)
        inputs.click(cx + client_xy[0], cy + client_xy[1], button=button)
        low, high = self.profile.post_click_delay
        time.sleep(random.uniform(low, high))
        return True

    def _press(self, key: str) -> bool:
        if not winutil.is_foreground(self._hwnd):
            return False
        inputs.press(key)
        low, high = self.profile.post_key_delay
        time.sleep(random.uniform(low, high))
        return True

    def _wander(self, client_w: int, client_h: int) -> None:
        now = time.time()
        if now - self._last_wander < 1.5:
            return
        self._last_wander = now
        radius = max(20, int(self.profile.search_radius_frac * client_w))
        target = (
            max(0, min(client_w - 1, client_w // 2 + random.randint(-radius, radius))),
            max(0, min(client_h - 1, client_h // 2 + random.randint(-radius, radius))),
        )
        self._click(target, button="right")

    def _attack(self) -> None:
        now = time.time()
        for skill in self._skills:
            if skill.ready(now):
                if self._press(skill.key):
                    skill.last_used = now
                return
        if self.profile.attack_key:
            self._press(self.profile.attack_key)

    # ---- states ----------------------------------------------------------- #

    def _searching(self, frame: np.ndarray, client_w: int, client_h: int) -> None:
        match = vision.best_match(frame, self._monsters)
        score = match.score if match else 0.0
        self._set(match_score=score)

        if match is None or score < self.profile.match_threshold:
            self._consecutive_misses += 1
            self._best_miss_score = max(self._best_miss_score, score)
            if self._consecutive_misses >= _MISSES_BEFORE_HINT:
                self.reporter.hint(
                    "no_monster",
                    errors.no_monster_match(
                        self._best_miss_score, self.profile.match_threshold, len(self._monsters)
                    ),
                )
                self._consecutive_misses = 0
                self._best_miss_score = 0.0
            self._wander(client_w, client_h)
            return

        self._consecutive_misses = 0
        self._best_miss_score = 0.0
        self.reporter.forget("no_monster")

        if not self._click(match.center, button="left"):
            return
        time.sleep(0.4)

        fresh = capture.grab_client(self._hwnd)
        target_hp = self._read_bar(fresh[0], self.profile.target_hp) if fresh else 0.0
        self._set(target_hp=target_hp)
        if target_hp > 0.02:
            self._engaged = True
            self._target_empty_since = 0.0
            self._failed_engages = 0
            self.reporter.forget("target_hp")
            self.reporter.log(f"Engaged a target (match {score:.2f}).", level="good")
        else:
            self._failed_engages += 1
            if self._failed_engages >= _FAILED_ENGAGES_BEFORE_HINT:
                self.reporter.hint(
                    "target_hp",
                    errors.target_hp_never_reads(
                        self.profile.target_hp.filled_color, self.profile.target_hp.tolerance
                    ),
                )
                self._failed_engages = 0

    def _fighting(self, target_hp: float) -> None:
        now = time.time()
        if target_hp <= 0.02:
            if self._target_empty_since == 0.0:
                self._target_empty_since = now
            elif now - self._target_empty_since >= self.profile.target_lost_timeout:
                self._engaged = False
                self._target_empty_since = 0.0
                self._kills += 1
                self._kills_since_home += 1
                self._set(kills=self._kills)
                self.reporter.log(f"Target down — {self._kills} killed this session.", level="good")
            return
        self._target_empty_since = 0.0
        self._attack()

    def _returning(self, frame: np.ndarray, client_w: int, client_h: int) -> None:
        match = vision.best_match(frame, [self._home]) if self._home is not None else None
        if match is None or match.score < self.profile.match_threshold:
            self.reporter.hint("no_home", errors.no_home_match(match.score if match else 0.0,
                                                              self.profile.match_threshold))
            self._wander(client_w, client_h)
            return
        self.reporter.forget("no_home")

        dx = match.center[0] - client_w // 2
        dy = match.center[1] - client_h // 2
        distance = max(1.0, (dx * dx + dy * dy) ** 0.5)
        if distance <= self.profile.home_tolerance_frac * client_w:
            self._kills_since_home = 0
            self.reporter.log("Back at the home landmark.")
            return
        step = max(20, int(self.profile.return_step_frac * client_w))
        scale = min(1.0, step / distance)
        target = (
            max(0, min(client_w - 1, int(client_w // 2 + dx * scale))),
            max(0, min(client_h - 1, int(client_h // 2 + dy * scale))),
        )
        self._click(target, button="right")

    # ---- the loop --------------------------------------------------------- #

    def _cycle(self) -> None:
        hwnd = self._resolve_window()
        if hwnd is None:
            self._set(state=WAITING, window_found=False, window_focused=False)
            time.sleep(0.5)
            return

        if winutil.is_minimised(hwnd):
            self.reporter.hint("minimised", errors.window_minimised(self.profile.window_title))
            self._set(state=WAITING, window_found=True, window_focused=False)
            time.sleep(0.5)
            return
        self.reporter.forget("minimised")

        grabbed = capture.grab_client(hwnd)
        if grabbed is None:
            self._set(state=WAITING, window_found=True)
            time.sleep(0.5)
            return
        frame, (_, _, client_w, client_h) = grabbed

        if vision.frame_is_blank(frame):
            self.reporter.hint("blank", errors.blank_capture(), cooldown=30.0)
            self._set(state=WAITING, window_found=True, client_size=(client_w, client_h))
            time.sleep(0.5)
            return
        self.reporter.forget("blank")

        self._ensure_templates(client_w, client_h)

        player_hp = self._read_bar(frame, self.profile.player_hp)
        target_hp = self._read_bar(frame, self.profile.target_hp)
        focused = winutil.is_foreground(hwnd)
        self._set(
            player_hp=player_hp,
            target_hp=target_hp,
            window_found=True,
            window_focused=focused,
            client_size=(client_w, client_h),
        )

        if self._paused:
            self._set(state=PAUSED)
            return
        if not focused:
            self.reporter.hint(
                "focus",
                "The game window isn't in front, so no clicks or keys are being sent. "
                "Click the game to carry on.",
                cooldown=20.0,
            )
            self._set(state=WAITING)
            return
        self.reporter.forget("focus")

        if not self._check_player_hp(player_hp):
            return

        if self._engaged:
            self._set(state=FIGHTING)
            self._fighting(target_hp)
        elif self._home is not None and self._kills_since_home >= self.profile.kills_before_home_check:
            self._set(state=RETURNING)
            self._returning(frame, client_w, client_h)
        else:
            self._set(state=SEARCHING)
            self._searching(frame, client_w, client_h)

    def _check_player_hp(self, player_hp: float) -> bool:
        """False means the loop should stop acting this cycle."""
        now = time.time()
        if player_hp <= 0.005:
            # A real character at 0% is already dead, so this reads as a bad region
            # rather than an emergency — pausing forever on it would be worse.
            if self._player_hp_zero_since == 0.0:
                self._player_hp_zero_since = now
            elif now - self._player_hp_zero_since > _UNREADABLE_HP_SECONDS:
                self.reporter.hint(
                    "player_hp",
                    errors.player_hp_unreadable(
                        self.profile.player_hp.filled_color, self.profile.player_hp.tolerance
                    ),
                    cooldown=60.0,
                )
            return True

        self._player_hp_zero_since = 0.0
        self.reporter.forget("player_hp")
        if player_hp < self.profile.critical_fraction:
            self.reporter.log(
                f"HP is down to {player_hp:.0%} — pausing instead of fighting on. "
                f"Heal up, then press Resume.",
                level="warn",
            )
            self.pause()
            self._set(state=PAUSED)
            return False
        return True

    def _run(self) -> None:
        self.reporter.log(
            f"Loaded profile '{self.profile.name}'. Starting paused — press Resume "
            f"(or {self.profile.hotkeys.resume.upper()}) when you're at the farm spot."
        )
        self._set(running=True, state=PAUSED, paused=True, kills=self._kills)
        period = 1.0 / max(1, self.profile.loop_hz)

        while not self._stop.is_set():
            began = time.perf_counter()
            now = time.time()
            if not self._paused:
                self._active_seconds += min(1.0, now - self._last_tick)
            self._last_tick = now
            self._set(
                paused=self._paused,
                runtime=now - self._started_at,
                active_seconds=self._active_seconds,
            )
            try:
                self._cycle()
            except FlyfouError as exc:
                self.reporter.hint(f"err:{exc.message[:40]}", exc.full())
            except Exception as exc:  # a bad frame must not kill the session
                self.reporter.hint(
                    f"err:{exc.__class__.__name__}",
                    f"Hit an unexpected problem and skipped this frame "
                    f"({exc.__class__.__name__}: {exc}). The run is still going.",
                )
            time.sleep(max(0.0, period - (time.perf_counter() - began)))

        inputs.release_all()
        self._set(running=False, state=STOPPED, paused=True)
        self.reporter.log("Run finished.")


def run_headless(profile: Profile) -> int:
    """Console mode: same loop, same hotkeys, no window."""
    from .hotkeys import HotkeyManager

    bot = Bot(profile, echo=True)
    keys = profile.hotkeys
    manager = HotkeyManager()
    bot.start()

    failure = manager.register(
        {keys.pause: bot.pause, keys.resume: bot.resume, keys.stop: lambda: bot.stop("Kill switch pressed.")}
    )
    if failure:
        bot.reporter.log(errors.hotkeys_unavailable(failure), level="warn")
        bot.reporter.log("Without hotkeys the only way out is Ctrl+C in this window.", level="warn")
    else:
        bot.reporter.log(
            f"Hotkeys: {keys.pause.upper()} pause · {keys.resume.upper()} resume · "
            f"{keys.stop.upper()} stop."
        )

    try:
        while bot.is_running():
            time.sleep(0.2)
    except KeyboardInterrupt:
        bot.stop("Interrupted at the console.")
    finally:
        manager.clear()
        inputs.release_all()
    return 0
