"""Everything the client can see, read out of it once per tick.

The bot's whole perception is one sweep for the mover class pointer plus one
read per object. There is no container to walk and no packet to parse: a class
with virtual methods writes the same vtable pointer at the head of every
instance, so the sweep *is* the entity list. `HotRegions` keeps the cost at a
few milliseconds by looking only where entities were last time.

A snapshot is a value, not a view. Nothing here holds a pointer into the client
and reads it later: by the time the bot acts on what it saw, the object may have
been freed and its memory handed to something else. Read it all, decide, act.

What a mover *is* cannot be settled from offsets alone. `kind` separates players
from everything else on this build, but pets and monsters share a value, so the
honest answer is to expose the raw value and let the profile say what is worth
attacking rather than guess here.

Maximum health is not stored. Two hundred and eighty-nine movers were checked
against every dword in their own object and exactly one offset held a mover own
health - the current one. The client works the maximum out when it draws the
bar. So it is remembered instead: health returns to full out of combat, which
makes the highest reading ever seen the maximum, and a high-water mark can only
be wrong by being too low, only until the character next heals.
"""

from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

from .layout import Layout, resolve_player
from .process import Module, Process, ascii_at
from .scan import HotRegions

#: Names longer than this are not names; the field is inline, not a pointer.
NAME_BYTES = 32

#: Kind values settled by watching two clients look at the same world.
PLAYER, PET = 2, 18


@dataclass
class Entity:
    """One mover, as it was at one instant. Never re-read; take another snapshot."""

    address: int
    id: int = 0
    name: str = ""
    kind: int = 0
    level: int = 0
    hp: int = 0
    max_hp: int = 0
    target_id: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @property
    def pos(self):
        return (self.x, self.y, self.z)

    @property
    def alive(self) -> bool:
        """Zero health is a corpse; the client keeps drawing it for a while."""
        return self.hp > 0

    @property
    def is_player(self) -> bool:
        return self.kind == PLAYER

    @property
    def is_pet(self) -> bool:
        return self.kind == PET

    @property
    def fighting(self) -> bool:
        return bool(self.target_id)

    def apart_from(self, other: "Entity") -> float:
        """Ground distance. Height is not distance: a hill is not a journey."""
        return math.hypot(self.x - other.x, self.z - other.z)

    def apart_from_spot(self, spot: Sequence[float]) -> float:
        return math.hypot(self.x - spot[0], self.z - spot[2])

    def __repr__(self) -> str:
        return (f"<{self.name or '?'} #{self.id} kind {self.kind} "
                f"lv {self.level} hp {self.hp} at "
                f"({self.x:.0f}, {self.y:.0f}, {self.z:.0f})>")


@dataclass
class World:
    """One tick's worth of everything, with us picked out of it."""

    me: Optional[Entity] = None
    entities: List[Entity] = field(default_factory=list)
    at: float = 0.0
    swept: float = 0.0          # seconds the sweep cost

    def __post_init__(self):
        self.by_id: Dict[int, Entity] = {e.id: e for e in self.entities if e.id}

    def __iter__(self) -> Iterator[Entity]:
        return iter(self.entities)

    def __len__(self) -> int:
        return len(self.entities)

    def find(self, entity_id: int) -> Optional[Entity]:
        return self.by_id.get(entity_id)

    def named(self, name: str) -> Optional[Entity]:
        for entity in self.entities:
            if entity.name == name:
                return entity
        return None

    def players(self) -> List[Entity]:
        return [e for e in self.entities if e.is_player]

    def others(self) -> List[Entity]:
        """Every mover that is not us and not our own pet following us."""
        if self.me is None:
            return list(self.entities)
        return [e for e in self.entities if e.address != self.me.address]

    def near(self, spot: Sequence[float], radius: float) -> List[Entity]:
        return [e for e in self.entities if e.apart_from_spot(spot) <= radius]

    def attacking(self, entity_id: int) -> List[Entity]:
        """Whoever is currently targeting that id - the self-defence question."""
        return [e for e in self.entities
                if e.target_id == entity_id and e.id != entity_id]

    def kinds(self) -> Dict[int, int]:
        """How many of each kind value are about, for learning what they mean."""
        tally: Dict[int, int] = {}
        for entity in self.entities:
            tally[entity.kind] = tally.get(entity.kind, 0) + 1
        return tally


class WorldReader:
    """Turns a process and a layout into snapshots, as cheaply as it can."""

    def __init__(self, process: Process, module: Module, layout: Layout,
                 interval: float = 30.0, span: Optional[int] = None):
        self.process = process
        self.module = module
        self.layout = layout
        self.hot = HotRegions(interval=interval)
        # The bot reads only as far as the fields it knows about. Anything
        # looking for a field it does NOT know about has to pass the whole
        # object, or it is searching a room it never turned the light on in.
        self.span = span or self._span()
        #: The best health ever seen per entity, for builds that do not store a
        #: maximum. Keyed by id, so it survives an object being moved about.
        self.best_hp: Dict[int, int] = {}
        #: The last read's raw object bytes, by address. The learner works on
        #: these; the bot works on the decoded snapshot and ignores them.
        self.bodies: Dict[int, bytes] = {}

    def _span(self) -> int:
        """How much of each object has to be read to cover every known field."""
        offsets = [self.layout.name + NAME_BYTES]
        for name in ("position", "entity_id", "level", "hp", "max_hp",
                     "target_id", "kind"):
            offset = getattr(self.layout, name, None)
            if offset is not None:
                offsets.append(offset + 16)
        want = max(offsets)
        return min(want, self.layout.size) if self.layout.size else want

    def _decode(self, address: int, body: bytes) -> Entity:
        layout = self.layout
        entity = Entity(address=address)

        def u32(offset: Optional[int]) -> int:
            if offset is None or offset + 4 > len(body):
                return 0
            return int.from_bytes(body[offset:offset + 4], "little")

        if layout.position is not None and layout.position + 12 <= len(body):
            entity.x, entity.y, entity.z = struct.unpack_from(
                "<3f", body, layout.position)
        entity.id = u32(layout.entity_id)
        entity.level = u32(layout.level)
        entity.hp = u32(layout.hp)
        entity.max_hp = u32(layout.max_hp)
        if not entity.max_hp and entity.id:
            best = max(self.best_hp.get(entity.id, 0), entity.hp)
            if best:
                self.best_hp[entity.id] = best
            entity.max_hp = best
        if layout.target_via is None:
            entity.target_id = u32(layout.target_id)
        kind = u32(layout.kind)
        entity.kind = kind & layout.kind_mask if layout.kind_mask else kind
        if layout.name is not None:
            entity.name = ascii_at(body, layout.name, NAME_BYTES)
        return entity

    def _follow_targets(self, entities: List[Entity]) -> None:
        """Fill in who everybody is fighting, from one pointer further out.

        A mover keeps its behaviour in a satellite object, so its target is not
        in the object the sweep found - it is that many bytes into whatever the
        object points at. That is one extra read per mover per tick, which on a
        map of three hundred costs a couple of milliseconds and buys the three
        behaviours that cannot exist without it: not stealing a monster somebody
        else is fighting, hitting back, and defending a leech.
        """
        layout = self.layout
        if layout.target_id is None or layout.target_via is None:
            return
        wide = layout.target_is_pointer
        known = {e.address: e.id for e in entities} if wide else {}

        for entity in entities:
            body = self.bodies.get(entity.address)
            if body is None or layout.target_via + 8 > len(body):
                continue
            satellite = int.from_bytes(
                body[layout.target_via:layout.target_via + 8], "little")
            if not (0x10000 < satellite < (1 << 47)):
                continue
            blob = self.process.read(satellite + layout.target_id,
                                     8 if wide else 4)
            if not blob:
                continue
            value = int.from_bytes(blob, "little")
            entity.target_id = known.get(value, 0) if wide else value

    def read(self, force_full: bool = False) -> World:
        started = time.perf_counter()
        addresses = self.hot.sweep(self.process, self._vtable(),
                                   force_full=force_full)
        swept = time.perf_counter() - started

        me_at = resolve_player(self.process, self.module, self.layout)
        if me_at and me_at not in addresses:
            addresses.append(me_at)

        entities: List[Entity] = []
        me: Optional[Entity] = None
        self.bodies = {}
        for address in addresses:
            body = self.process.read(address, self.span)
            if not body or len(body) < self.span:
                continue
            entity = self._decode(address, body)
            entities.append(entity)
            self.bodies[address] = body
            if address == me_at:
                me = entity
        self._follow_targets(entities)
        return World(me=me, entities=entities, at=time.time(), swept=swept)

    def _vtable(self) -> int:
        return self.module.base + self.layout.mover_vtable
