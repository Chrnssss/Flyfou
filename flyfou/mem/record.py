"""Keep a play session, so the rules can be argued with afterwards.

Watching live had a cost that was easy to miss until it had been paid three
times: every attempt to find an offset needed somebody to stop what they were
doing and go and play, and each attempt tested exactly one version of one rule.
A rule that was too strict, or that accepted a linked list, cost another session
to discover. So the session is written down and the arguing happens afterwards,
against a file, as many times as it takes.

What gets kept, and why each part is needed:

  Our own whole object every tick. Both unknowns belong to the local player.

  The objects our object points at, one hop out, because they turned out not to
  be in the player at all. No float triple in the player was ever more than a
  unit from where we stood over a hundred and forty-seven seconds and four
  hundred units of walking - they are all copies of the position - and no dword
  in it ever held another entity's id. What does hold a place is a satellite:
  player +0x380 leads to an object whose +0xC8 was our position exactly. A mover
  keeps its behaviour beside itself rather than inside itself.

  For everything else, only what is needed to judge ours against it: id,
  address, health and where it stood. The address matters because a target may
  be a pointer to a monster rather than a number naming one, and without it that
  question cannot be asked of a recording at all.

The satellite offsets are chosen once, on the first tick, and then re-read every
tick. Fixing them keeps the recording rectangular; re-reading them means a
pointer swung at a new object mid-session is followed rather than frozen, which
is exactly what a target pointer would do.

Read-only.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .layout import Layout
from .process import Module, Process
from .world import WorldReader

#: How much of each satellite object to keep per tick.
SATELLITE = 0x400

#: Sanity bounds for something that might be a heap address.
LOW, HIGH = 0x10000, 1 << 47


@dataclass
class Recording:
    """A play session, as arrays. Ragged per-tick data is flat plus offsets."""

    span: int
    me: np.ndarray            # (ticks, span) uint8   - our object each tick
    me_pos: np.ndarray        # (ticks, 3)   float32
    starts: np.ndarray        # (ticks + 1,) int64    - slices of the flat arrays
    ids: np.ndarray           # flat uint32
    hp: np.ndarray            # flat uint32
    xs: np.ndarray            # flat float32
    zs: np.ndarray            # flat float32
    at: np.ndarray            # (ticks,) float64      - wall clock
    names: Dict[int, str]     # id -> the last name seen for it
    me_id: Optional[np.ndarray] = None    # (ticks,) uint32 - our own id
    addrs: Optional[np.ndarray] = None    # flat uint64 - where each mover lives
    sat_offsets: Optional[np.ndarray] = None   # (n,) uint32 - offsets in us
    sat: Optional[np.ndarray] = None      # (ticks, n, SATELLITE) uint8
    sat_ptr: Optional[np.ndarray] = None  # (ticks, n) uint64
    who: str = ""
    build: str = ""

    @property
    def ticks(self) -> int:
        return int(self.me.shape[0])

    def tick(self, t: int):
        """One tick's world, as (ids, hp, x, z)."""
        a, b = int(self.starts[t]), int(self.starts[t + 1])
        return self.ids[a:b], self.hp[a:b], self.xs[a:b], self.zs[a:b]

    def addresses(self, t: int) -> np.ndarray:
        a, b = int(self.starts[t]), int(self.starts[t + 1])
        if self.addrs is None:
            return np.zeros(b - a, dtype=np.uint64)
        return self.addrs[a:b]

    def dwords(self) -> np.ndarray:
        """Our object over time, as (ticks, span // 4) uint32."""
        return self.me.view(np.uint32)

    def qwords(self) -> np.ndarray:
        return self.me.view(np.uint64)

    @property
    def satellites(self) -> int:
        return 0 if self.sat is None else int(self.sat.shape[1])

    def sat_dwords(self, n: int) -> np.ndarray:
        """One satellite over time, as (ticks, SATELLITE // 4) uint32."""
        return np.ascontiguousarray(self.sat[:, n, :]).view(np.uint32)

    def sat_floats(self, n: int) -> np.ndarray:
        return np.ascontiguousarray(self.sat[:, n, :]).view(np.float32)

    def save(self, path: str) -> None:
        np.savez_compressed(
            path, span=self.span, me=self.me, me_pos=self.me_pos,
            starts=self.starts, ids=self.ids, hp=self.hp, xs=self.xs,
            zs=self.zs, at=self.at,
            names=json.dumps({str(k): v for k, v in self.names.items()}),
            who=self.who, build=self.build,
            me_id=(self.me_id if self.me_id is not None
                   else np.zeros(self.ticks, np.uint32)),
            addrs=(self.addrs if self.addrs is not None
                   else np.zeros(0, np.uint64)),
            sat_offsets=(self.sat_offsets if self.sat_offsets is not None
                         else np.zeros(0, np.uint32)),
            sat=(self.sat if self.sat is not None
                 else np.zeros((0, 0, 0), np.uint8)),
            sat_ptr=(self.sat_ptr if self.sat_ptr is not None
                     else np.zeros((0, 0), np.uint64)))

    @classmethod
    def load(cls, path: str) -> "Recording":
        blob = np.load(path, allow_pickle=False)

        def maybe(key):
            if key not in blob.files:
                return None
            value = blob[key]
            return value if value.size else None

        names = {int(k): v for k, v in json.loads(str(blob["names"])).items()}
        return cls(span=int(blob["span"]), me=blob["me"], me_pos=blob["me_pos"],
                   starts=blob["starts"], ids=blob["ids"], hp=blob["hp"],
                   xs=blob["xs"], zs=blob["zs"], at=blob["at"], names=names,
                   who=str(blob["who"]), build=str(blob["build"]),
                   me_id=maybe("me_id"), addrs=maybe("addrs"),
                   sat_offsets=maybe("sat_offsets"), sat=maybe("sat"),
                   sat_ptr=maybe("sat_ptr"))

    def summary(self) -> str:
        step = np.hypot(np.diff(self.me_pos[:, 0]), np.diff(self.me_pos[:, 2]))
        counts = np.diff(self.starts)
        gone = 0
        for t in range(self.ticks - 1):
            now_ids, _hp, _x, _z = self.tick(t)
            later = np.sort(self.tick(t + 1)[0])
            if not later.size:
                continue
            keep = np.clip(np.searchsorted(later, now_ids), 0, later.size - 1)
            gone += int((later[keep] != now_ids).sum())
        return ("%d ticks over %.0fs, %d-%d movers each, %d moving ticks "
                "covering %.0f units, %d disappearances, %d satellites"
                % (self.ticks,
                   self.at[-1] - self.at[0] if self.ticks > 1 else 0,
                   counts.min() if counts.size else 0,
                   counts.max() if counts.size else 0,
                   int((step > 0.05).sum()), step.sum(), gone,
                   self.satellites))


def _pointer_offsets(body: bytes, base: int, process: Process) -> List[int]:
    """Where in our object we keep a pointer to somebody else's."""
    words = np.frombuffer(body[:len(body) // 8 * 8], dtype=np.uint64)
    out = []
    for i, value in enumerate(words.tolist()):
        if not (LOW < value < HIGH) or base <= value < base + len(body):
            continue
        blob = process.read(value, SATELLITE)
        if blob and len(blob) == SATELLITE:
            out.append(i * 8)
    return out


def record(process: Process, module: Module, layout: Layout,
           seconds: float = 150.0, every: float = 0.35,
           report=None) -> Recording:
    """Play, while this writes down what the client thought was happening."""
    if layout.hp is None:
        raise RuntimeError(
            "this layout has no health offset, so every health in the "
            "recording would be zero and nothing could be learned from it - "
            "run the calibration first")
    reader = WorldReader(process, module, layout, span=layout.size)
    span = reader.span

    # Look before spending two minutes: a recording where our own health reads
    # zero is a recording of nothing, and that has happened once already.
    check = reader.read(force_full=True)
    if check.me is None:
        raise RuntimeError("the character is not in its own world yet")
    if check.me.hp <= 0:
        raise RuntimeError(
            "our own health reads zero at +0x%X, so the offsets do not fit "
            "this client - recording would waste the session"
            % (layout.hp or 0))

    offsets = _pointer_offsets(reader.bodies[check.me.address],
                               check.me.address, process)
    if report:
        report("following %d pointers out of the player object" % len(offsets))

    me_rows: List[bytes] = []
    me_pos: List[Tuple[float, float, float]] = []
    me_ids: List[int] = []
    sat_rows: List[np.ndarray] = []
    sat_ptrs: List[np.ndarray] = []
    ids: List[np.ndarray] = []
    hps: List[np.ndarray] = []
    xs: List[np.ndarray] = []
    zs: List[np.ndarray] = []
    addrs: List[np.ndarray] = []
    at: List[float] = []
    names: Dict[int, str] = {}
    blank = bytes(SATELLITE)

    until = time.monotonic() + seconds
    said = 0.0
    while time.monotonic() < until:
        world = reader.read()
        body = reader.bodies.get(world.me.address) if world.me else None
        if body is not None and len(body) == span:
            me_rows.append(body)
            me_pos.append(world.me.pos)
            me_ids.append(world.me.id)
            at.append(time.time())

            words = np.frombuffer(body[:len(body) // 8 * 8], dtype=np.uint64)
            here, pointed = [], []
            for offset in offsets:
                value = int(words[offset // 8])
                pointed.append(value)
                blob = (process.read(value, SATELLITE)
                        if LOW < value < HIGH else None)
                here.append(blob if blob and len(blob) == SATELLITE else blank)
            sat_rows.append(np.frombuffer(b"".join(here), dtype=np.uint8)
                            .reshape(max(len(offsets), 1), SATELLITE)
                            if offsets else np.zeros((0, SATELLITE), np.uint8))
            sat_ptrs.append(np.array(pointed, dtype=np.uint64))

            live = [e for e in world.entities if e.id]
            ids.append(np.array([e.id for e in live], dtype=np.uint32))
            hps.append(np.array([e.hp for e in live], dtype=np.uint32))
            xs.append(np.array([e.x for e in live], dtype=np.float32))
            zs.append(np.array([e.z for e in live], dtype=np.float32))
            addrs.append(np.array([e.address for e in live], dtype=np.uint64))
            for entity in live:
                if entity.name:
                    names[entity.id] = entity.name

        if report and time.monotonic() - said > 5.0:
            said = time.monotonic()
            report("%d ticks, %d movers, %ds left"
                   % (len(me_rows), len(ids[-1]) if ids else 0,
                      int(until - time.monotonic())))
        time.sleep(every)

    if not me_rows:
        raise RuntimeError("the character never appeared in its own world")

    counts = [len(a) for a in ids]
    starts = np.zeros(len(counts) + 1, dtype=np.int64)
    starts[1:] = np.cumsum(counts)
    return Recording(
        span=span,
        me=np.frombuffer(b"".join(me_rows), dtype=np.uint8).reshape(
            len(me_rows), span).copy(),
        me_pos=np.array(me_pos, dtype=np.float32),
        starts=starts,
        ids=np.concatenate(ids),
        hp=np.concatenate(hps),
        xs=np.concatenate(xs),
        zs=np.concatenate(zs),
        at=np.array(at, dtype=np.float64),
        names=names,
        me_id=np.array(me_ids, dtype=np.uint32),
        addrs=np.concatenate(addrs),
        sat_offsets=np.array(offsets, dtype=np.uint32),
        sat=np.stack(sat_rows),
        sat_ptr=np.stack(sat_ptrs))
