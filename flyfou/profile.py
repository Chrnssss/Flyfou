"""Named farm profiles: YAML on disk, but nobody has to look at it.

All geometry is stored as a fraction of the game's client area rather than as
pixels, so a profile made at 1280x720 still points at the right things when the
user resizes the window.
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:
    yaml = None

import numpy as np

from . import vision
from .errors import FlyfouError

SCHEMA_VERSION = 2
PROFILE_FILE = "profile.yaml"


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #

@dataclass
class FracRect:
    """A rectangle as proportions (0-1) of the client area."""

    x: float
    y: float
    w: float
    h: float

    def to_pixels(self, client_w: int, client_h: int) -> Tuple[int, int, int, int]:
        x = int(round(self.x * client_w))
        y = int(round(self.y * client_h))
        w = max(1, int(round(self.w * client_w)))
        h = max(1, int(round(self.h * client_h)))
        x = max(0, min(x, max(0, client_w - 1)))
        y = max(0, min(y, max(0, client_h - 1)))
        return x, y, min(w, client_w - x), min(h, client_h - y)

    @classmethod
    def from_pixels(cls, rect: Sequence[int], client_w: int, client_h: int) -> "FracRect":
        x, y, w, h = rect
        return cls(
            round(x / client_w, 6),
            round(y / client_h, 6),
            round(w / client_w, 6),
            round(h / client_h, 6),
        )

    def as_list(self) -> List[float]:
        return [self.x, self.y, self.w, self.h]

    @classmethod
    def from_list(cls, values) -> "FracRect":
        x, y, w, h = values
        return cls(float(x), float(y), float(w), float(h))

    def is_empty(self) -> bool:
        return self.w <= 0 or self.h <= 0


# --------------------------------------------------------------------------- #
# pieces of a profile
# --------------------------------------------------------------------------- #

@dataclass
class BarConfig:
    rect: FracRect = field(default_factory=lambda: FracRect(0, 0, 0, 0))
    filled_color: Tuple[int, int, int] = (200, 40, 40)
    tolerance: int = 30

    def configured(self) -> bool:
        return not self.rect.is_empty()

    def to_dict(self) -> Dict:
        return {
            "rect": self.rect.as_list(),
            "filled_color": list(self.filled_color),
            "tolerance": int(self.tolerance),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "BarConfig":
        data = data or {}
        colour = data.get("filled_color") or [200, 40, 40]
        return cls(
            rect=FracRect.from_list(data.get("rect") or [0, 0, 0, 0]),
            filled_color=(int(colour[0]), int(colour[1]), int(colour[2])),
            tolerance=int(data.get("tolerance", 30)),
        )


@dataclass
class Skill:
    key: str
    cooldown: float = 3.0

    def to_dict(self) -> Dict:
        return {"key": self.key, "cooldown": float(self.cooldown)}

    @classmethod
    def from_dict(cls, data: Dict) -> "Skill":
        return cls(str(data.get("key", "")), float(data.get("cooldown", 3.0)))


@dataclass
class TemplateRef:
    """A captured image plus the client size it was captured at, so it can be
    rescaled if the window size changes."""

    file: str
    client_size: Tuple[int, int]

    def to_dict(self) -> Dict:
        return {"file": self.file, "client_size": list(self.client_size)}

    @classmethod
    def from_dict(cls, data: Dict) -> "TemplateRef":
        size = data.get("client_size") or [0, 0]
        return cls(str(data["file"]), (int(size[0]), int(size[1])))


@dataclass
class Hotkeys:
    pause: str = "f9"
    resume: str = "f10"
    stop: str = "f12"

    def to_dict(self) -> Dict:
        return {"pause": self.pause, "resume": self.resume, "stop": self.stop}

    @classmethod
    def from_dict(cls, data: Dict) -> "Hotkeys":
        data = data or {}
        return cls(
            pause=str(data.get("pause", "f9")),
            resume=str(data.get("resume", "f10")),
            stop=str(data.get("stop", data.get("quit", "f12"))),
        )


# --------------------------------------------------------------------------- #
# the profile
# --------------------------------------------------------------------------- #

@dataclass
class Profile:
    name: str = "New profile"
    window_title: str = ""
    window_process: str = ""
    client_size: Tuple[int, int] = (0, 0)

    # Monsters aren't recognised by appearance — see vision.find_blobs for why —
    # so the only picture a profile keeps is the landmark it walks home to.
    home: Optional[TemplateRef] = None
    match_threshold: float = 0.80

    # The part of the window that is scenery rather than interface. Hovering is
    # harmless but clicking isn't, so the search never leaves this box — the
    # default keeps clear of the side panels, the minimap and the skill bar.
    play_area: FracRect = field(default_factory=lambda: FracRect(0.13, 0.05, 0.74, 0.68))

    player_hp: BarConfig = field(default_factory=BarConfig)
    critical_fraction: float = 0.25
    target_hp: BarConfig = field(default_factory=BarConfig)

    attack_key: Optional[str] = None
    skills: List[Skill] = field(default_factory=list)
    target_lost_timeout: float = 4.0

    search_radius_frac: float = 0.17
    return_step_frac: float = 0.11
    home_tolerance_frac: float = 0.047
    kills_before_home_check: int = 5

    loop_hz: int = 8
    post_click_delay: Tuple[float, float] = (0.15, 0.35)
    post_key_delay: Tuple[float, float] = (0.05, 0.15)

    hotkeys: Hotkeys = field(default_factory=Hotkeys)
    created: str = ""
    directory: Optional[Path] = None  # not serialised

    # ---- serialisation --------------------------------------------------- #

    def to_dict(self) -> Dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "created": self.created,
            "window": {
                "title_contains": self.window_title,
                "process": self.window_process,
                "client_size": list(self.client_size),
            },
            "templates": {
                "home": self.home.to_dict() if self.home else None,
                "match_threshold": float(self.match_threshold),
            },
            "play_area": self.play_area.as_list(),
            "player_hp": dict(self.player_hp.to_dict(), critical_fraction=float(self.critical_fraction)),
            "target_hp": self.target_hp.to_dict(),
            "combat": {
                "attack_key": self.attack_key,
                "skills": [s.to_dict() for s in self.skills],
                "target_lost_timeout": float(self.target_lost_timeout),
            },
            "movement": {
                "search_radius_frac": float(self.search_radius_frac),
                "return_step_frac": float(self.return_step_frac),
                "home_tolerance_frac": float(self.home_tolerance_frac),
                "kills_before_home_check": int(self.kills_before_home_check),
            },
            "timing": {
                "loop_hz": int(self.loop_hz),
                "post_click_delay": list(self.post_click_delay),
                "post_key_delay": list(self.post_key_delay),
            },
            "hotkeys": self.hotkeys.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict, directory: Optional[Path] = None) -> "Profile":
        window = data.get("window") or {}
        templates = data.get("templates") or {}
        player = data.get("player_hp") or {}
        combat = data.get("combat") or {}
        movement = data.get("movement") or {}
        timing = data.get("timing") or {}
        size = window.get("client_size") or [0, 0]
        home = templates.get("home")

        return cls(
            name=str(data.get("name", "Unnamed")),
            window_title=str(window.get("title_contains", "")),
            window_process=str(window.get("process", "")),
            client_size=(int(size[0]), int(size[1])),
            home=TemplateRef.from_dict(home) if home else None,
            match_threshold=float(templates.get("match_threshold", 0.80)),
            play_area=FracRect.from_list(data.get("play_area") or [0.13, 0.05, 0.74, 0.68]),
            player_hp=BarConfig.from_dict(player),
            critical_fraction=float(player.get("critical_fraction", 0.25)),
            target_hp=BarConfig.from_dict(data.get("target_hp")),
            attack_key=combat.get("attack_key") or None,
            skills=[Skill.from_dict(s) for s in (combat.get("skills") or [])],
            target_lost_timeout=float(combat.get("target_lost_timeout", 4.0)),
            search_radius_frac=float(movement.get("search_radius_frac", 0.17)),
            return_step_frac=float(movement.get("return_step_frac", 0.11)),
            home_tolerance_frac=float(movement.get("home_tolerance_frac", 0.047)),
            kills_before_home_check=int(movement.get("kills_before_home_check", 5)),
            loop_hz=int(timing.get("loop_hz", 8)),
            post_click_delay=tuple(timing.get("post_click_delay", (0.15, 0.35))),
            post_key_delay=tuple(timing.get("post_key_delay", (0.05, 0.15))),
            hotkeys=Hotkeys.from_dict(data.get("hotkeys")),
            created=str(data.get("created", "")),
            directory=directory,
        )

    # ---- templates ------------------------------------------------------- #

    def template_path(self, ref: TemplateRef) -> Path:
        base = self.directory or Path(".")
        return base / "templates" / ref.file

    def load_template(self, ref: TemplateRef, scale: float = 1.0) -> np.ndarray:
        path = self.template_path(ref)
        image = vision.imread(str(path))
        if image is None:
            raise FlyfouError(
                f"The template image '{ref.file}' is missing from profile '{self.name}'.",
                "Press Set up for this profile and capture it again.",
            )
        return vision.scale_template(image, scale) if scale != 1.0 else image

    def load_home_template(self, scale: float = 1.0) -> Optional[np.ndarray]:
        return self.load_template(self.home, scale) if self.home else None

    # ---- validation ------------------------------------------------------ #

    def problems(self) -> List[str]:
        """Reasons this profile can't farm yet, in plain language."""
        issues = []
        if not self.window_title and not self.window_process:
            issues.append("No game window picked.")
        if self.play_area.is_empty():
            issues.append("No hunting ground marked out, so there's nowhere to look.")
        if not self.player_hp.configured():
            issues.append("Your own HP bar hasn't been marked out.")
        if not self.target_hp.configured():
            issues.append("The target HP bar hasn't been marked out — kills can't be detected without it.")
        if not self.skills and not self.attack_key:
            issues.append("No attack key or skills recorded, so the bot would never attack.")
        if self.home and not self.template_path(self.home).exists():
            issues.append(f"The captured image '{self.home.file}' is missing from disk.")
        return issues

    def scale_for(self, client_w: int, client_h: int) -> float:
        """How much to grow/shrink templates for the current window size."""
        saved_w = self.client_size[0]
        if not saved_w or not client_w:
            return 1.0
        return client_w / saved_w

    def size_changed(self, client_w: int, client_h: int) -> bool:
        return bool(self.client_size[0]) and (client_w, client_h) != tuple(self.client_size)


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #

def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "profile"


class ProfileStore:
    """Profiles live one-per-directory under the user's app data folder."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else default_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory_for(self, name: str) -> Path:
        return self.root / slugify(name)

    def names(self) -> List[str]:
        found = []
        for child in sorted(self.root.iterdir()) if self.root.exists() else []:
            if (child / PROFILE_FILE).exists():
                try:
                    found.append(self._read_name(child))
                except Exception:
                    continue
        return found

    def _read_name(self, directory: Path) -> str:
        with open(directory / PROFILE_FILE, "r", encoding="utf-8") as handle:
            return str((yaml.safe_load(handle) or {}).get("name", directory.name))

    def exists(self, name: str) -> bool:
        return (self.directory_for(name) / PROFILE_FILE).exists()

    def load(self, name: str) -> Profile:
        directory = self.directory_for(name)
        path = directory / PROFILE_FILE
        if not path.exists():
            raise FlyfouError(
                f"There's no profile called '{name}'.",
                "Pick a different one from the list, or create a new one with Set up.",
            )
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        except Exception as exc:
            raise FlyfouError(
                f"Profile '{name}' is damaged and can't be read ({exc.__class__.__name__}).",
                f"Delete it from the profile list and set it up again, or fix the file at {path}.",
            )
        return Profile.from_dict(data, directory=directory)

    def save(self, profile: Profile) -> Path:
        directory = self.directory_for(profile.name)
        (directory / "templates").mkdir(parents=True, exist_ok=True)
        if not profile.created:
            profile.created = datetime.datetime.now().isoformat(timespec="seconds")
        profile.directory = directory
        path = directory / PROFILE_FILE
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_HEADER)
            yaml.safe_dump(profile.to_dict(), handle, sort_keys=False, allow_unicode=True)
        return path

    def delete(self, name: str) -> None:
        directory = self.directory_for(name)
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)

    def duplicate(self, name: str, new_name: str) -> Profile:
        source = self.directory_for(name)
        target = self.directory_for(new_name)
        if target.exists():
            raise FlyfouError(f"A profile called '{new_name}' already exists.", "Pick another name.")
        shutil.copytree(source, target)
        profile = Profile.from_dict(_read_yaml(target / PROFILE_FILE), directory=target)
        profile.name = new_name
        profile.created = datetime.datetime.now().isoformat(timespec="seconds")
        self.save(profile)
        return profile


def _read_yaml(path: Path) -> Dict:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def default_root() -> Path:
    override = os.environ.get("FLYFOU_HOME")
    if override:
        return Path(override) / "profiles"
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "Flyfou" / "profiles"


_HEADER = """\
# Flyfou profile — written by the setup wizard.
# You can edit this by hand, but you don't have to: everything here is
# reachable from Set up in the app. Rectangles are [x, y, width, height] as
# fractions of the game window's client area, so they survive a resize.
"""
