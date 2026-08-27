"""Work out the last two offsets from a recorded session.

Both belong to the local player, but neither is *in* the player. A hundred and
forty-seven seconds of walking proved it for one of them: every float triple in
the object stayed within a unit of where we were standing, which is what a copy
of a position looks like and not what a destination looks like. And no dword in
the object ever held another entity's id, which rules out the obvious shape for
the other. A mover keeps its behaviour in a satellite object and points at it.

So every question below is asked of the player and of each satellite alike, and
an answer is a path rather than an offset - which pointer to follow, and how far
into what it finds.

  Target. Whatever we have targeted comes to a bad end, because that is what
  attacking it consists of. That has to mean two things and not one: monsters
  here die to a single hit, so there is frequently no decline to watch, only a
  thing that was there and then is not. Losing health OR leaving the world both
  count, and the rate is measured against the rate for everything else alive at
  the time - on a map that churns as things wander in and out of range, "the
  thing I named vanished" means nothing without that control.

  It is looked for twice over, as a number naming a monster and as a pointer to
  one, because the recording keeps addresses as well as ids and there is no
  reason to assume which the client chose.

  Destination. Where we are walking to is a point we are walking towards. When
  we stop we are standing on it; while we move the gap closes. Both halves are
  needed: the first alone is satisfied by any copy of our position - and this
  build has several - and the second by anything ahead of us in a straight line.

Nothing here touches the game. It reads a file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .record import Recording

#: A target field may name somebody out of range now and then, but not often.
SANE = 0.90

#: How often the thing we name must come to a visible end.
ENDING = 0.05

#: How many times likelier that has to be than it is for everything else alive.
LIFT = 3.0

#: Ticks a field must name somebody on before its rate means anything, and
#: deaths it must have presided over. A rate computed from one event is not a
#: rate: a list of nearby entities once scored ten times the background on the
#: strength of a single monster dying somewhere in it, and was written to the
#: cache as the target field. Counting the events refuses that outright.
MIN_NAMED, MIN_EVENTS = 12, 4

#: Standing on our destination means this close, in world units.
ON_IT = 3.0

#: A tick where the character actually went somewhere.
MOVED = 0.05

#: A destination worth believing is one we were once this far from.
JOURNEY = 8.0


@dataclass
class Finding:
    """One field, where it lives, and the numbers that argue for it."""

    offset: int
    via: Optional[int] = None       # pointer offset in the player, if any
    score: float = 0.0
    detail: str = ""

    @property
    def path(self) -> str:
        if self.via is None:
            return "+0x%X" % self.offset
        return "+0x%X -> +0x%X" % (self.via, self.offset)


@dataclass
class Study:
    target_id: Optional[int] = None
    target_via: Optional[int] = None
    target_is_pointer: bool = False
    dest: Optional[int] = None
    dest_via: Optional[int] = None
    target_ranking: List[Finding] = field(default_factory=list)
    dest_ranking: List[Finding] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _places(recording: Recording):
    """Every object a field of ours could live in: us, then each satellite."""
    yield None, recording.me
    for n in range(recording.satellites):
        yield (int(recording.sat_offsets[n]),
               np.ascontiguousarray(recording.sat[:, n, :]))


def _lookup(sorted_keys: np.ndarray, values: np.ndarray):
    """Where each value sits in a sorted list, and whether it is really there."""
    if sorted_keys.size == 0:
        return (np.zeros(values.shape, dtype=np.int64),
                np.zeros(values.shape, dtype=bool))
    at = np.clip(np.searchsorted(sorted_keys, values), 0, sorted_keys.size - 1)
    return at, (sorted_keys[at] == values) & (values != 0)


def _frames(recording: Recording):
    """Per tick: what everything was, and what became of it by the next tick."""
    out = []
    ends = seen = 0
    for t in range(recording.ticks - 1):
        ids, hp, xs, zs = recording.tick(t)
        order = np.argsort(ids)
        ids, hp, xs, zs = ids[order], hp[order], xs[order], zs[order]
        addr = recording.addresses(t)[order]
        by_addr = np.argsort(addr)

        later_ids, later_hp, _x, _z = recording.tick(t + 1)
        later_order = np.argsort(later_ids)
        later_ids, later_hp = later_ids[later_order], later_hp[later_order]

        keep, alive = _lookup(later_ids, ids)
        ended = (~alive) | (alive & (later_hp[keep] < hp))
        seen += ids.size
        ends += int(ended.sum())
        out.append((ids, addr[by_addr], by_addr, hp, xs, zs, ended))
    return out, ends / float(max(seen, 1))


def study_target(recording: Recording) -> Tuple[List[Finding], bool]:
    """Rank every dword and qword by how much it names something we are killing."""
    if recording.ticks < 4:
        return [], True
    frames, background = _frames(recording)

    best: List[Finding] = []
    misses: List[Finding] = []

    for via, blob in _places(recording):
        if blob.size == 0:
            continue
        for as_pointer in (False, True):
            words = blob.view(np.uint64) if as_pointer else blob.view(np.uint32)
            width = words.shape[1]
            sane = np.zeros(width, np.int64)
            named = np.zeros(width, np.int64)
            ending = np.zeros(width, np.int64)
            furthest = np.zeros(width, np.float64)

            for t, frame in enumerate(frames):
                ids, addr_sorted, by_addr, hp, xs, zs, ended = frame
                value = words[t]
                if as_pointer:
                    at, here = _lookup(addr_sorted, value)
                    row = by_addr[at]
                else:
                    at, here = _lookup(ids, value)
                    row = at
                    if recording.me_id is not None and recording.me_id.size > t:
                        here &= value != recording.me_id[t]
                sane += (value == 0) | here
                named += here
                ending += here & ended[row]
                gap = np.hypot(xs[row] - recording.me_pos[t, 0],
                               zs[row] - recording.me_pos[t, 2])
                furthest = np.maximum(furthest, np.where(here, gap, 0.0))

            unique = np.array([np.unique(words[:, k]).size for k in range(width)])
            sane_rate = sane / float(len(frames))
            end_rate = np.divide(ending, np.maximum(named, 1), dtype=np.float64)
            lift = end_rate / max(background, 1e-9)
            step = 8 if as_pointer else 4
            kind = "a pointer to" if as_pointer else "the id of"

            good = ((sane_rate >= SANE) & (unique >= 2) & (named >= MIN_NAMED)
                    & (ending >= MIN_EVENTS) & (end_rate >= ENDING)
                    & (lift >= LIFT))
            for k in np.nonzero(good)[0]:
                best.append(Finding(
                    offset=int(k) * step, via=via, score=float(lift[k]),
                    detail=("holds %s a mover on %d ticks; it died or was hurt "
                            "%d times (%.0f%%, %.1fx the %.1f%% that befalls "
                            "everything else), up to %.0f units away, %d values"
                            % (kind, named[k], ending[k], end_rate[k] * 100,
                               lift[k], background * 100, furthest[k],
                               unique[k]))))

            maybe = np.nonzero((named >= 5) & (unique >= 2))[0]
            for k in maybe[np.argsort(-lift[maybe])][:2]:
                misses.append(Finding(
                    offset=int(k) * step, via=via, score=float(lift[k]),
                    detail=("holds %s a mover on %d ticks, but only %.0f%% came "
                            "to harm against a background of %.1f%% (%.1fx); "
                            "never further than %.0f units"
                            % (kind, named[k], end_rate[k] * 100,
                               background * 100, lift[k], furthest[k]))))

    best.sort(key=lambda f: -f.score)
    if best:
        return best, False
    misses.sort(key=lambda f: -f.score)
    return misses[:8], True


def study_dest(recording: Recording) -> List[Finding]:
    """Rank every float triple by how much it is somewhere we are heading."""
    spot = recording.me_pos
    if recording.ticks < 4:
        return []
    step = np.hypot(np.diff(spot[:, 0]), np.diff(spot[:, 2]))
    walking = step > MOVED
    resting = ~walking
    strides, stops = max(int(walking.sum()), 1), max(int(resting.sum()), 1)

    out: List[Finding] = []
    for via, blob in _places(recording):
        if blob.size == 0:
            continue
        floats = blob.view(np.float32)
        with np.errstate(invalid="ignore", over="ignore"):
            gap = np.hypot(floats[:, :-2] - spot[:, 0:1],
                           floats[:, 2:] - spot[:, 2:3])
        gap = np.where(np.isfinite(gap) & (gap < 1e7), gap, 1e7)

        toward = ((np.diff(gap, axis=0) < 0.01) & walking[:, None]).sum(axis=0)
        parked = ((gap[:-1] < ON_IT) & resting[:, None]).sum(axis=0)
        travelled = gap.max(axis=0) >= JOURNEY

        score = np.where(travelled, (toward / strides) * (parked / stops), 0.0)
        for k in np.nonzero(score > 0)[0]:
            out.append(Finding(
                offset=int(k) * 4, via=via, score=float(score[k]),
                detail=("closed on it during %d of %d moving ticks (%.0f%%), "
                        "stood on it during %d of %d still ticks (%.0f%%), "
                        "was once %.0f units away"
                        % (toward[k], strides, toward[k] * 100.0 / strides,
                           parked[k], stops, parked[k] * 100.0 / stops,
                           gap[:, k].max()))))
    out.sort(key=lambda f: -f.score)
    return out[:8]


def study(recording: Recording) -> Study:
    """Everything the recording can settle, with its reasoning attached."""
    out = Study()
    out.notes.append(recording.summary())

    ranking, only_misses = study_target(recording)
    out.target_ranking = ranking
    if ranking and not only_misses:
        pick = ranking[0]
        out.target_id, out.target_via = pick.offset, pick.via
        out.target_is_pointer = "pointer" in pick.detail
        out.notes.append("target: %s - %s" % (pick.path, pick.detail))
        for other in ranking[1:4]:
            out.notes.append("        also %s - %s" % (other.path, other.detail))
    elif ranking:
        out.notes.append("target: nothing named something that then came to "
                         "harm. The closest were:")
        for other in ranking[:5]:
            out.notes.append("        %s - %s" % (other.path, other.detail))
    else:
        out.notes.append("target: nothing in the player or any satellite ever "
                         "held a live mover's id or address - was anything "
                         "actually targeted while this ran?")

    out.dest_ranking = study_dest(recording)
    if out.dest_ranking:
        pick = out.dest_ranking[0]
        out.dest, out.dest_via = pick.offset, pick.via
        out.notes.append("destination: %s - %s" % (pick.path, pick.detail))
        for other in out.dest_ranking[1:4]:
            out.notes.append("             also %s - %s"
                             % (other.path, other.detail))
    else:
        out.notes.append("destination: no triple both closed while walking and "
                         "was stood on while still. Did the character walk a "
                         "good distance and then stop?")
    return out
