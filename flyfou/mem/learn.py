"""The offsets that only a fight and a walk can reveal.

Four things cannot be found by staring at a quiet town, and every one of them
was left empty by the finder for an honest reason: a target field holds nothing
until somebody is fighting, a maximum health and a current health are the same
number until something is hurt, a destination is the same as a position until
somebody walks, and a monster's kind value cannot be read off a map with no
monsters on it.

None of that needs the user to type anything, and none of it needs a debugger.
It needs somebody to play normally for a minute or two while this watches:

  Target. Every tick knows the id of everything alive. A field that holds a
  target holds either nothing or one of those ids. Two impostors pass that too -
  an entity's own id, and a pet's memory of its owner - so the first is thrown
  out for pointing at itself and the second for never changing.

  Maximum health. Watch anything take a hit. The maximum is the number that did
  not move while the current one did, and that is larger than it.

  Destination. Walk. The destination is the triple that stayed still while the
  position walked into it, and the distance shrank every tick.

  Kinds. What values turn up, and what they were called. One thing filed under
  two kinds settles which bits are the client's own bookkeeping.

Two lessons are baked in here, both learned by this failing on a real map.

The first is that it must read the WHOLE object. It used to borrow the reader
the bot uses, which stops at the last field anybody knows about - so it went
looking for unknown fields in the one part of the object it had not read.

The second is that nothing in a live world holds without exception. A monster
can target a player who walks out of sweep range, and for that tick its target
field holds an id belonging to nobody visible. A rule that demanded perfection
threw away the right answer the first time that happened, which on a busy map is
within seconds. So every rule here is a rate, the rates are reported next to
each other, and a near miss is printed rather than silently discarded - if this
fails again it should say how close it got and to what.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from .layout import Layout
from .process import Module, Process
from .world import Entity, World, WorldReader

#: A health bar no game would draw.
SANE_HEALTH = 100_000_000

#: How far a walk has to close before it counts as walking towards something.
CLOSED = 3.0

#: Ticks a walk must last before its destination is believed.
WALKING = 3

#: How often a target field is allowed to name somebody we cannot see.
MOSTLY = 0.97

#: How often a maximum must sit still above a health bar that moved.
USUALLY = 0.80

#: Times a field must point at somebody other than its owner to be a target.
POINTED = 3


@dataclass
class Learned:
    """What watching the game being played settled, and what it did not."""

    target_id: Optional[int] = None
    max_hp: Optional[int] = None
    dest: Optional[int] = None
    kinds: Dict[int, List[str]] = field(default_factory=dict)
    kind_mask: Optional[int] = None
    ticks: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def anything(self) -> bool:
        return any(v is not None
                   for v in (self.target_id, self.max_hp, self.dest))


@dataclass
class Seen:
    """What the user actually did, so they can be told what is still missing."""

    ticks: int = 0
    entities: int = 0
    hits: int = 0            # times something's health moved
    switches: int = 0        # times a candidate target field changed
    walks: int = 0           # journeys that were long enough to judge
    steps: int = 0           # ticks spent moving

    def __str__(self) -> str:
        return ("%d ticks, %d health changes, %d walks finished"
                % (self.ticks, self.hits, self.walks))


def _matrix(reader: WorldReader, world: World):
    """The tick's objects as one array of dwords, one entity per row."""
    rows, kept = [], []
    for entity in world.entities:
        body = reader.bodies.get(entity.address)
        if body is not None:
            kept.append(entity)
            rows.append(body)
    if not rows:
        return [], None
    flat = np.frombuffer(b"".join(rows), dtype=np.uint8)
    return kept, flat.reshape(len(rows), -1).view(np.uint32)


def _top(scores: np.ndarray, mask: np.ndarray, count: int = 5):
    """The best few offsets by score, for saying how close a failure came."""
    live = np.nonzero(mask)[0]
    if not live.size:
        return []
    best = live[np.argsort(-scores[live])][:count]
    return [(int(k) * 4, float(scores[k])) for k in best]


class Learner:
    """Accumulates evidence across ticks and refuses to guess without it."""

    def __init__(self, layout: Layout):
        self.layout = layout
        self.width = 0
        self.seen = Seen()

        # target: counted per dword offset, over every entity of every tick
        self._sane: Optional[np.ndarray] = None       # times it held 0 or an id
        self._looks: int = 0                          # chances it had to
        self._elsewhere: Optional[np.ndarray] = None  # pointed at someone else
        self._varied: Optional[np.ndarray] = None     # changed for some entity
        self._last: Dict[int, np.ndarray] = {}        # entity id -> its dwords

        # health: what stayed still while a hurt thing's health moved
        self._still: Optional[np.ndarray] = None      # votes, not a veto
        self._first: Dict[int, Tuple[np.ndarray, int]] = {}

        # destination: our own object, over a walk
        self._walk: List[Tuple[np.ndarray, Tuple[float, float, float]]] = []
        self._dest_votes: Dict[int, int] = {}
        self._walk_notes: Dict[str, int] = {}

        self._equal_hp = None         # times an offset equalled health
        self._readings = 0
        self._last_tick = None        # one whole tick, for the structure tests

        self.kinds: Dict[int, Set[str]] = {}
        self._by_name: Dict[str, Set[int]] = {}

    @property
    def ticks(self) -> int:
        return self.seen.ticks

    # ------------------------------------------------------------------ target
    def _see_target(self, rows: Sequence[Entity], mat: np.ndarray) -> None:
        live = np.array(sorted({e.id for e in rows if e.id}), dtype=np.uint32)
        if live.size < 2:
            return
        mine = np.array([e.id for e in rows], dtype=np.uint32)[:, None]

        is_id = np.isin(mat, live)
        sane = ((mat == 0) | is_id).sum(axis=0)
        elsewhere = ((mat != 0) & is_id & (mat != mine)).sum(axis=0)

        self._sane = sane if self._sane is None else (self._sane + sane)
        self._looks += mat.shape[0]
        self._elsewhere = (elsewhere if self._elsewhere is None
                           else self._elsewhere + elsewhere)

        varied = np.zeros(mat.shape[1], dtype=bool)
        for row, entity in enumerate(rows):
            was = self._last.get(entity.id)
            if was is not None and was.shape == mat[row].shape:
                varied |= was != mat[row]
            self._last[entity.id] = mat[row].copy()
        self._varied = varied if self._varied is None else (self._varied | varied)

    # ------------------------------------------------------------------ health
    def _see_health(self, rows: Sequence[Entity], mat: np.ndarray) -> None:
        if self.layout.hp is None:
            return
        for row, entity in enumerate(rows):
            if not entity.id:
                continue
            seen = self._first.get(entity.id)
            if seen is None:
                self._first[entity.id] = (mat[row].copy(), entity.hp)
                continue
            was, then = seen
            if entity.hp == then or was.shape != mat[row].shape:
                continue
            # Something took a hit. Whatever did not move is a candidate, and a
            # maximum is larger than a current one that has just gone down.
            still = ((was == mat[row]) & (mat[row] > max(entity.hp, then))
                     & (mat[row] < SANE_HEALTH))
            vote = still.astype(np.int32)
            self._still = vote if self._still is None else (self._still + vote)
            self.seen.hits += 1
            self._first[entity.id] = (mat[row].copy(), entity.hp)

    def _see_full_health(self, rows: Sequence[Entity], mat: np.ndarray) -> None:
        """How often each offset holds exactly what health holds.

        A maximum equals a current one whenever nothing is hurt, and on a map of
        three hundred monsters almost nothing is. A clock never equals a health
        bar, and that is what separates the answer from the four counters which
        also sit still and are also larger.
        """
        hp = np.array([e.hp for e in rows], dtype=np.uint32)[:, None]
        same = ((mat == hp) & (hp > 0)).sum(axis=0)
        self._equal_hp = same if self._equal_hp is None else (self._equal_hp + same)
        self._readings += mat.shape[0]

    # -------------------------------------------------------------------- walk
    def _see_walk(self, me: Optional[Entity],
                  mat_row: Optional[np.ndarray]) -> None:
        if me is None or mat_row is None:
            return
        spot = (me.x, me.y, me.z)
        if self._walk and self._walk[-1][1] == spot:
            self._settle_walk()
            self._walk = []
            return
        self.seen.steps += 1
        self._walk.append((mat_row.copy(), spot))

    def _why_not(self, reason: str) -> None:
        self._walk_notes[reason] = self._walk_notes.get(reason, 0) + 1

    def _settle_walk(self) -> None:
        if len(self._walk) < WALKING:
            if len(self._walk) > 1:
                self._why_not("too short to judge")
            return
        self.seen.walks += 1
        bodies = np.stack([body for body, _spot in self._walk])
        spots = [spot for _body, spot in self._walk]
        constant = (bodies == bodies[0]).all(axis=0)
        floats = bodies.view(np.float32)

        triples = constant[:-2] & constant[1:-1] & constant[2:]
        if not triples.any():
            self._why_not("nothing in the object stayed still while we moved")
            return
        closed_any = False
        for k in np.nonzero(triples)[0]:
            triple = floats[0, k:k + 3]
            if not np.isfinite(triple).all() or abs(float(triple[1])) > 1e6:
                continue
            gaps = [float(np.hypot(spot[0] - triple[0], spot[2] - triple[2]))
                    for spot in spots]
            if gaps[0] - gaps[-1] < CLOSED:
                continue
            closed_any = True
            if any(b > a + 0.01 for a, b in zip(gaps, gaps[1:])):
                continue        # it must close every tick, not on average
            where = int(k) * 4
            self._dest_votes[where] = self._dest_votes.get(where, 0) + 1
        if not closed_any:
            self._why_not("we did not close on any fixed point by %.0f units"
                          % CLOSED)

    # ------------------------------------------------------------------- kinds
    def _see_kinds(self, rows: Sequence[Entity]) -> None:
        """Tally kinds, and notice when one thing is filed under two of them.

        If `Leonis Infernal` is kind 18 in one place and 26 in another, the bits
        they differ by cannot be meaning, and one tick of a busy map settles it.
        """
        for entity in rows:
            if entity.name:
                self.kinds.setdefault(entity.kind, set()).add(entity.name)
                self._by_name.setdefault(entity.name, set()).add(entity.kind)

    # -------------------------------------------------------------------- feed
    def observe(self, reader: WorldReader, world: World) -> None:
        rows, mat = _matrix(reader, world)
        if mat is None or len(rows) < 3:
            return
        self.seen.ticks += 1
        self.seen.entities = max(self.seen.entities, len(rows))
        self.width = mat.shape[1]
        self._see_kinds(rows)
        self._see_target(rows, mat)
        self._see_health(rows, mat)
        self._see_full_health(rows, mat)
        me_row = None
        if world.me is not None:
            for row, entity in enumerate(rows):
                if entity.address == world.me.address:
                    me_row = mat[row]
                    break
        self._see_walk(world.me, me_row)
        self._last_tick = (rows, mat)

    # ------------------------------------------------------------------ answer
    def result(self) -> Learned:
        self._settle_walk()
        out = Learned(ticks=self.seen.ticks,
                      kinds={k: sorted(v) for k, v in sorted(self.kinds.items())})

        out.target_id, why = self._answer_target()
        out.notes.append("target: " + why)
        out.max_hp, why = self._answer_health()
        out.notes.append("max health: " + why)
        out.dest, why = self._answer_dest()
        out.notes.append("destination: " + why)
        out.kind_mask, why = self._answer_kind_mask()
        out.notes.append("kind mask: " + why)
        out.notes.append("saw: " + str(self.seen))
        return out

    def _paired(self, k):
        """Is this half of a next/prev pair rather than a target?

        A doubly-linked list threaded by entity id passes every test a target
        passes: it holds live ids, it points at other people, and it changes as
        things move between buckets. What it also does, and a target does not,
        is point back from a DIFFERENT offset - next says B while B's prev says
        A. Combat is reciprocal too, because a monster you hit hits you back,
        but that reciprocity is at the same offset. So the rejection is
        specifically that something ELSE in the target's object names us.
        """
        if self._last_tick is None:
            return None, 0.0
        rows, mat = self._last_tick
        ids = np.array([e.id for e in rows], dtype=np.uint32)
        where = {int(i): r for r, i in enumerate(ids) if i}
        values = mat[:, k]

        a_rows, b_rows = [], []
        for row, value in enumerate(values):
            other = where.get(int(value))
            if value and other is not None and other != row:
                a_rows.append(row)
                b_rows.append(other)
        if len(a_rows) < 4:
            return None, 0.0
        back = mat[b_rows] == ids[a_rows][:, None]
        counts = back.sum(axis=0)
        partner = int(np.argmax(counts))
        return partner, float(counts[partner]) / len(a_rows)

    def _answer_target(self):
        if self._sane is None or not self._looks:
            return None, "not enough of a world to say"
        rate = self._sane / float(self._looks)
        pointed = self._elsewhere >= POINTED
        good = (rate >= MOSTLY) & pointed & self._varied
        picks = [int(k) for k in np.nonzero(good)[0]]

        listed, kept = {}, []
        for k in picks:
            partner, share = self._paired(k)
            if partner is not None and partner != k and share >= 0.8:
                listed[k] = (partner, share)
            else:
                kept.append(k)
        picks = kept
        if not picks:
            near = _top(rate, pointed & self._varied)
            if not near:
                near = _top(rate, pointed)
            if listed:
                worst = sorted(listed.items(), key=lambda kv: -kv[1][1])[:3]
                return None, (
                    "every candidate was half of a linked list, not a target: "
                    + ", ".join("+0x%X is paired with +0x%X %.0f%% of the time"
                                % (k * 4, j * 4, share * 100)
                                for k, (j, share) in worst)
                    + " - so nothing was fighting, or not for long enough")
            return None, (
                "nothing held a live id often enough. The closest were "
                + (", ".join("+0x%X at %.0f%%" % (k, r * 100) for k, r in near)
                   if near else "nothing that ever pointed at anybody")
                + " (%d entities changed nothing; was anything fighting?)"
                % self.seen.entities)
        picks.sort(key=lambda k: (-rate[k], -self._elsewhere[k], k))
        best = picks[0]
        said = ("+0x%X held nothing or a live id %.1f%% of %d readings, pointed "
                "at somebody else %d times, and changed"
                % (best * 4, rate[best] * 100, self._looks,
                   int(self._elsewhere[best])))
        if listed:
            said += ("; rejected %s for being half of a linked list"
                     % ", ".join("+0x%X" % (k * 4) for k in sorted(listed)[:4]))
        if len(picks) > 1:
            said += ("; %d others qualified: " % (len(picks) - 1)
                     + ", ".join("+0x%X at %.0f%%" % (k * 4, rate[k] * 100)
                                 for k in picks[1:5]))
        return best * 4, said

    def _answer_health(self):
        if self._still is None or not self.seen.hits:
            return None, "nothing took a hit while this was watching"
        rate = self._still / float(self.seen.hits)
        full = (self._equal_hp / float(max(self._readings, 1))
                if self._equal_hp is not None else np.zeros_like(rate))
        picks = [int(k) for k in np.nonzero((rate >= USUALLY) & (full > 0.05))[0]]
        if not picks:
            near = _top(rate, rate >= USUALLY)
            return None, (
                "%d hits landed; %s stayed still and larger, but none of them "
                "ever equalled a health bar, so they are counters rather than "
                "maximums" % (self.seen.hits,
                              ", ".join("+0x%X" % k for k, _r in near)
                              or "nothing"))
        picks.sort(key=lambda k: (-full[k], -rate[k], k))
        best = picks[0]
        said = ("+0x%X stayed put and stayed larger through %.0f%% of %d hits, "
                "and equalled health on %.0f%% of readings, which is what a map "
                "of unhurt monsters looks like"
                % (best * 4, rate[best] * 100, self.seen.hits, full[best] * 100))
        if len(picks) > 1:
            said += "; also " + ", ".join("+0x%X" % (k * 4) for k in picks[1:5])
        return best * 4, said

    def _answer_dest(self):
        if not self._dest_votes:
            trouble = ", ".join("%s (%d times)" % (why, count)
                                for why, count in sorted(
                                    self._walk_notes.items(),
                                    key=lambda kv: -kv[1]))
            return None, ("no walk settled one: %d journeys, %d ticks moving%s"
                          % (self.seen.walks, self.seen.steps,
                             ("; " + trouble) if trouble else
                             " - try clicking somewhere far and letting it "
                             "finish, without stopping halfway"))
        ranked = sorted(self._dest_votes.items(), key=lambda kv: (-kv[1], kv[0]))
        best, votes = ranked[0]
        said = ("+0x%X stood still while we walked into it, on %d of %d walks"
                % (best, votes, self.seen.walks))
        if len(ranked) > 1:
            said += "; next best +0x%X on %d" % (ranked[1][0], ranked[1][1])
        return best, said

    def _answer_kind_mask(self):
        noise = 0
        split = []
        for name, kinds in self._by_name.items():
            if len(kinds) > 1:
                low = min(kinds)
                for kind in kinds:
                    noise |= kind ^ low
                split.append(name)
        if not noise:
            return None, ("nothing was filed under two kinds, so which bits are "
                          "the client's own bookkeeping is still unknown")
        mask = 0xFF & ~noise
        return mask, ("0x%X, because %d things were filed under two kinds at "
                      "once (%s), and bits 0x%X cannot mean anything if one "
                      "monster has them both ways"
                      % (mask, len(split), ", ".join(sorted(split)[:3]), noise))


def watch(process: Process, module: Module, layout: Layout,
          seconds: float = 120.0, every: float = 0.4, report=None) -> Learned:
    """Watch the game being played and report what that settled.

    The whole object is read, not the part the bot needs: this is looking for
    fields nobody has named yet, and they are as likely to be at the end as
    anywhere else.
    """
    reader = WorldReader(process, module, layout, span=layout.size)
    learner = Learner(layout)
    until = time.monotonic() + seconds
    said = 0.0
    while time.monotonic() < until:
        world = reader.read()
        learner.observe(reader, world)
        if report and time.monotonic() - said > 5.0:
            said = time.monotonic()
            report("%s, %d movers, %ds left"
                   % (learner.seen, len(world), int(until - time.monotonic())))
        time.sleep(every)
    return learner.result()
