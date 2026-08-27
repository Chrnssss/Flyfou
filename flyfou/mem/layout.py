"""What the client's structures look like, written down and checked.

An offset is only true of one build. The bot therefore never ships offsets; it
finds them once against the running client and remembers them under a key made
from the executable's own link timestamp and size. Patch the client and the key
changes, the cache misses, and the finder runs again - which is the whole point
of having a finder.

A cache that is trusted blindly is worse than none, because a wrong offset does
not fail, it reads a plausible number from the wrong place. So every layout is
verified before use: resolve the player through it, confirm the object carries
the class pointer the layout claims, and confirm the name reads as a name. That
takes a millisecond and turns a silent misread into a rediscovery.

Offsets are stored as hex strings. They exist to be compared against a debugger
by a human, and 9885216 is not comparable to anything.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .process import Module, Process, ascii_at, link_stamp

LAYOUT_FILE = "layouts.yaml"

#: Offsets without which nothing else can be attempted.
ESSENTIAL = ("mover_vtable", "name", "player_ptrs")

#: Offsets each behaviour needs, for saying what is missing in plain words.
#:
#: Two fields that were once on this list are not on it any more, and their
#: absence is not a gap. A target field would say who is fighting what, and this
#: build has none - but who is fighting what can be watched instead, because a
#: monster losing health that we are not hitting is in somebody else's fight. A
#: destination field would say where the character is walking, and this build
#: has none of those either - and it would be useless if it did, because writing
#: to this client never reaches its server. Both are done another way now, so
#: neither belongs in a list of things that stop the bot working.
NEEDED_FOR = {
    "position": "moving anywhere, or measuring a farming radius",
    "entity_id": "telling one monster from another",
    "level": "targeting by level range",
    "hp": "knowing when something has died, and who is fighting whom",
}

#: Fields that cannot be found from a snapshot of a quiet map, and why. Kept
#: for the ones still worth having; the bot no longer waits on any of them.
NEEDS_COMBAT = {
    "target_id": "no monster on the map was fighting anything, and a target "
                 "field only holds a value while its owner is in a fight",
    "max_hp": "no offset in the object ever held a mover own health except "
              "the current one, across 289 movers - this build does not store "
              "a maximum, so the reader remembers the best reading instead",
    "dest": "nobody walked, and a destination is the same as a position until "
            "somebody does",
}


def _hex(value: Optional[int]) -> Optional[str]:
    return None if value is None else f"0x{value:X}"


def _int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    return int(str(value), 16)


@dataclass
class Layout:
    """Where things are in one build of the client.

    `mover_vtable` and `player_ptrs` are relative to the module, because ASLR
    moves it every launch. Everything else is relative to a mover object.
    """

    build: str
    mover_vtable: int
    name: int
    player_ptrs: List[int] = field(default_factory=list)
    size: Optional[int] = None          # bytes from the class pointer to the next
    position: Optional[int] = None
    entity_id: Optional[int] = None
    level: Optional[int] = None
    hp: Optional[int] = None
    max_hp: Optional[int] = None
    mp: Optional[int] = None
    target_id: Optional[int] = None
    dest: Optional[int] = None      # where the mover is walking to
    # A mover keeps its behaviour in a satellite object rather than inside
    # itself, so these two are reached by following a pointer first. When a
    # `_via` is set, the field is that many bytes into whatever the pointer at
    # `player + via` leads to; when it is None, the field is in the mover.
    target_via: Optional[int] = None
    dest_via: Optional[int] = None
    #: Whether the target field holds the monster's address rather than its id.
    #: Both shapes exist in the wild and the recording tests for both, so which
    #: one this build chose is a fact to store, not one to assume.
    target_is_pointer: bool = False
    kind: Optional[int] = None
    kind_mask: Optional[int] = None     # bits of `kind` that mean the same to
                                        # every client watching the same world
    found: str = ""
    note: str = ""

    @property
    def usable(self) -> bool:
        """Enough to enumerate the world and find ourselves in it."""
        return bool(self.mover_vtable and self.player_ptrs) and self.name is not None

    def missing(self) -> List[str]:
        """Which optional offsets were never found, worst first."""
        return [field_name for field_name in NEEDED_FOR
                if getattr(self, field_name) is None]

    def why_incomplete(self) -> str:
        gaps = self.missing()
        if not gaps:
            return ""
        said = []
        for name in gaps:
            because = NEEDS_COMBAT.get(name)
            said.append(f"no {name} offset, so {NEEDED_FOR[name]} will not work"
                        + (f" - {because}" if because else ""))
        return "; ".join(said)

    def to_dict(self) -> Dict:
        return {
            "build": self.build,
            "found": self.found,
            "note": self.note,
            "mover_vtable": _hex(self.mover_vtable),
            "player_ptrs": [_hex(p) for p in self.player_ptrs],
            "size": _hex(self.size),
            "kind_mask": _hex(self.kind_mask),
            "fields": {
                "name": _hex(self.name),
                "position": _hex(self.position),
                "entity_id": _hex(self.entity_id),
                "level": _hex(self.level),
                "hp": _hex(self.hp),
                "max_hp": _hex(self.max_hp),
                "mp": _hex(self.mp),
                "target_id": _hex(self.target_id),
                "dest": _hex(self.dest),
                "target_via": _hex(self.target_via),
                "dest_via": _hex(self.dest_via),
                "target_is_pointer": bool(self.target_is_pointer),
                "kind": _hex(self.kind),
            },
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Layout":
        fields = data.get("fields") or {}
        return cls(
            build=str(data.get("build", "")),
            mover_vtable=_int(data.get("mover_vtable")) or 0,
            name=_int(fields.get("name")) or 0,
            player_ptrs=[p for p in (_int(v) for v in data.get("player_ptrs") or []) if p],
            size=_int(data.get("size")),
            position=_int(fields.get("position")),
            entity_id=_int(fields.get("entity_id")),
            level=_int(fields.get("level")),
            hp=_int(fields.get("hp")),
            max_hp=_int(fields.get("max_hp")),
            mp=_int(fields.get("mp")),
            target_id=_int(fields.get("target_id")),
            dest=_int(fields.get("dest")),
            target_via=_int(fields.get("target_via")),
            dest_via=_int(fields.get("dest_via")),
            target_is_pointer=bool(fields.get("target_is_pointer") or False),
            kind=_int(fields.get("kind")),
            kind_mask=_int(data.get("kind_mask")),
            found=str(data.get("found", "")),
            note=str(data.get("note", "")),
        )


def build_key(process: Process, module: Module) -> str:
    """A name for one build of the client, stable across launches and machines."""
    stamp = link_stamp(process, module)
    return f"{module.name.lower()}-{stamp or 0:08X}-{module.size:X}"


def resolve_player(process: Process, module: Module,
                   layout: Layout) -> Optional[int]:
    """The local player's object, through whichever static pointer still works.

    Discovery finds several statics holding the player's address. They are not
    equally good - one of them lives in an array whose index moves between
    clients - so they are all kept and tried in turn, and the first that yields
    an object of the right class wins.
    """
    wanted = module.base + layout.mover_vtable
    for offset in layout.player_ptrs:
        candidate = process.pointer(module.base + offset)
        if candidate and process.u64(candidate) == wanted:
            return candidate
    return None


def verify(process: Process, module: Module, layout: Layout) -> str:
    """Empty if the layout still describes this client, else what went wrong."""
    if not layout.usable:
        return "the stored layout is missing the class pointer or the player pointer"
    if layout.build != build_key(process, module):
        return f"stored for build {layout.build}, this client is {build_key(process, module)}"

    me = resolve_player(process, module, layout)
    if not me:
        return "no static pointer leads to an object of the expected class"

    body = process.read(me + layout.name, 32)
    if not body:
        return f"the name field at +0x{layout.name:X} is not readable"
    if len(ascii_at(body, 0)) < 3:
        return f"there is no name at +0x{layout.name:X}"

    if layout.position is not None:
        spot = process.vec3(me + layout.position)
        if not spot or not all(abs(v) < 1e7 for v in spot):
            return f"the position at +0x{layout.position:X} is not a world coordinate"
    return ""


class LayoutStore:
    """One file, one entry per client build."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_path()

    def _all(self) -> Dict[str, Dict]:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return (yaml.safe_load(handle) or {}).get("builds") or {}
        except Exception:
            return {}

    def load(self, build: str) -> Optional[Layout]:
        data = self._all().get(build)
        return Layout.from_dict(data) if data else None

    def save(self, layout: Layout) -> Path:
        layout.found = layout.found or datetime.datetime.now().isoformat(timespec="seconds")
        builds = self._all()
        builds[layout.build] = layout.to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(_HEADER)
            yaml.safe_dump({"builds": builds}, handle, sort_keys=True, allow_unicode=True)
        return self.path

    def forget(self, build: str) -> None:
        builds = self._all()
        if builds.pop(build, None) is None:
            return
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(_HEADER)
            yaml.safe_dump({"builds": builds}, handle, sort_keys=True, allow_unicode=True)

    def builds(self) -> List[str]:
        return sorted(self._all())


def default_path() -> Path:
    override = os.environ.get("FLYFOU_HOME")
    root = Path(override) if override else Path(
        os.environ.get("APPDATA") or str(Path.home())) / "Flyfou"
    return root / LAYOUT_FILE


_HEADER = """\
# Flyfou memory layouts - found by the offset finder, not written by hand.
#
# One entry per build of the client, keyed by the executable's link timestamp
# and size. Offsets are relative to the module for statics and to a mover
# object for fields. Delete an entry to have it found again.
"""
