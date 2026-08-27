"""Finding the offsets, given only the character's name.

The bot needs one true fact to start from, and the window title is it: "Airborn
- Mynuthbp" names the character, the character is a mover, and a mover stores
its name inline. Everything else follows from that.

Find the name in the heap. The object it belongs to begins somewhere just
before, so read backwards and collect every qword that points into the client's
image; one of them is the class pointer and its distance from the name is the
name's offset. That leaves a few hundred guesses, and the ones that survive are
the classes whose *other* instances also read as names - monsters have names,
scenery does not.

Four survive, because a class that inherits from several bases carries a vtable
pointer per base and each is a valid handle on the same object. Sorting them by
how well their names read cannot separate them; they are the same names. What
separates them is that only one of the four addresses is where the allocation
starts, and the client itself knows which, because it keeps a static pointer to
the local player and a pointer points at an object rather than into the middle
of one. That test picks the class and hands over the player pointer at once.

Verified against four clients on maps holding 10, 125, 125 and 373 movers: the
same class, the same name offset, the same static, every time.

With the class settled, every mover's body can be read into one matrix and every
offset judged at once against what the field would have to be true of. That is
the second half of the file, and it finds the size of an object, the position,
the id and the kind without being told a single number.

It does not find everything. A target field holds nothing while its owner is not
fighting, and a maximum health is indistinguishable from a current one while
everybody is unhurt, so both wait for the finder to be run somewhere with
monsters in it. The layout says so in as many words rather than leaving a
silent hole.
"""

from __future__ import annotations

import string
import time
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from .layout import Layout, LayoutStore, build_key, verify
from .process import Module, Process, Region, ascii_at

#: How far before a name an object might begin. The name sits 0x1EC8 into a
#: mover on the observed build; this leaves room for it to move a long way.
BACK = 0x8000

#: A class the world is made of has many instances. Below this it is furniture.
MIN_INSTANCES = 8

#: Instances to read when judging whether a class's text is names.
SAMPLE = 96

#: Below this share of name-like text, a candidate is a coincidence.
MIN_NAMED = 0.5

#: Candidates to run the static-pointer test on, best-read first.
FINALISTS = 12

NAME_CHARS = frozenset(string.ascii_letters + string.digits + " '-_.")

Report = Optional[Callable[[str], None]]


class DiscoveryError(RuntimeError):
    """The finder could not make sense of the client."""


def name_like(text: str) -> bool:
    """Text that could be somebody's name, as opposed to bytes that print."""
    return (len(text) >= 3
            and sum(c.isalpha() for c in text) >= 3
            and all(c in NAME_CHARS for c in text))


@dataclass
class Candidate:
    """One guess at which class movers are, and where they keep their name."""

    vtable: int                     # absolute, while the client is running
    name_offset: int
    instances: int
    named_share: float
    distinct_names: int
    player: int                     # the object this guess implies is us
    statics: List[int] = field(default_factory=list)   # absolute
    examples: List[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        """The client's own image points at the object, so it is a real object."""
        return bool(self.statics)


def _say(report: Report, message: str) -> None:
    if report:
        report(message)


def census_and_names(process: Process, module: Module, spans: Sequence[Region],
                     needle: bytes) -> Tuple[Counter, List[Tuple[int, Region]]]:
    """One pass over the heap: count image pointers, note where the name is stored.

    Both jobs need every byte of the heap, and the heap is gigabytes, so they
    share the read rather than each paying for it.
    """
    low, high = np.uint64(module.base), np.uint64(module.end)
    census: Counter = Counter()
    sites: List[Tuple[int, Region]] = []
    for region in spans:
        blob = process.read_partial(region.base, region.size)
        at = blob.find(needle)
        while at >= 0:
            sites.append((region.base + at, region))
            at = blob.find(needle, at + 1)
        usable = len(blob) // 8 * 8
        if usable < 8:
            continue
        words = np.frombuffer(blob[:usable], dtype=np.uint64)
        inside = words[(words >= low) & (words < high)]
        if inside.size:
            values, counts = np.unique(inside, return_counts=True)
            for value, count in zip(values.tolist(), counts.tolist()):
                census[value] += count
    return census, sites


def guesses_before(process: Process, site: int, region: Region,
                   census: Counter) -> List[Tuple[int, int, int]]:
    """(class pointer, distance, holder) for each image pointer before a name."""
    start = max(region.base, site - BACK)
    blob = process.read_partial(start, site - start)
    usable = len(blob) // 8 * 8
    if usable < 8:
        return []
    words = np.frombuffer(blob[:usable], dtype=np.uint64)
    out = []
    for i in range(words.size):
        value = int(words[i])
        if census.get(value, 0) >= MIN_INSTANCES:
            out.append((value, site - (start + i * 8), start + i * 8))
    return out


def instances_of(process: Process, spans: Sequence[Region],
                 values: Set[int], limit: int) -> Dict[int, List[int]]:
    """Up to `limit` addresses holding each of many class pointers, in one pass."""
    out: Dict[int, List[int]] = defaultdict(list)
    wanted = np.array(sorted(values), dtype=np.uint64)
    if not wanted.size:
        return out
    for region in spans:
        blob = process.read_partial(region.base, region.size)
        usable = len(blob) // 8 * 8
        if usable < 8:
            continue
        words = np.frombuffer(blob[:usable], dtype=np.uint64)
        for i in np.nonzero(np.isin(words, wanted))[0].tolist():
            bucket = out[int(words[i])]
            if len(bucket) < limit:
                bucket.append(region.base + i * 8)
    return out


def statics_pointing_at(process: Process, module: Module,
                        address: int) -> List[int]:
    """Addresses inside the client's own image that hold a given address."""
    needle = np.uint64(address)
    out: List[int] = []
    for region in process.regions():
        if not module.holds(region.base):
            continue
        blob = process.read_partial(region.base, region.size)
        usable = len(blob) // 8 * 8
        if usable < 8:
            continue
        words = np.frombuffer(blob[:usable], dtype=np.uint64)
        out.extend(region.base + int(i) * 8
                   for i in np.nonzero(words == needle)[0])
    return out


def find_class(process: Process, module: Module, character: str,
               report: Report = None) -> Candidate:
    """Which class movers are, where they keep their name, and where we are.

    Raises rather than guessing: a wrong class does not fail later, it quietly
    reports the wrong world.
    """
    if not character:
        raise DiscoveryError("no character name to search for")

    spans = process.regions(private_only=True)
    _say(report, f"Reading {sum(r.size for r in spans) / 1e9:.1f} GB of the client's heap...")
    census, sites = census_and_names(process, module, spans,
                                     character.encode("utf-8", "ignore") + b"\x00")
    if not sites:
        raise DiscoveryError(
            f"the name {character!r} is not stored anywhere in the client - "
            "check the character is logged in and the window title names it")
    _say(report, f"The name is stored in {len(sites)} places; "
                 f"{len(census)} image addresses are held in the heap.")

    voters: Dict[Tuple[int, int], Set[int]] = defaultdict(set)
    for site, region in sites:
        for value, distance, holder in guesses_before(process, site, region, census):
            voters[(value, distance)].add(holder)
    if not voters:
        raise DiscoveryError("no class pointer sits before the character's name")
    _say(report, f"{len(voters)} guesses to sort out.")

    found = instances_of(process, spans, {v for v, _d in voters}, SAMPLE)
    ranked: List[Candidate] = []
    for (vtable, distance), voted in voters.items():
        holders = found.get(vtable, [])
        if not holders:
            continue
        names = [t for t in (ascii_at(process.read(a + distance, 24) or b"", 0)
                             for a in holders) if name_like(t)]
        share = len(names) / len(holders)
        if share >= MIN_NAMED:
            ranked.append(Candidate(
                vtable=vtable, name_offset=distance, instances=census[vtable],
                named_share=share, distinct_names=len(set(names)),
                player=min(voted), examples=sorted(set(names))[:4]))
    if not ranked:
        raise DiscoveryError(
            "no class near the character's name has instances that read as names")

    ranked.sort(key=lambda c: (-c.named_share, -c.distinct_names))
    _say(report, f"{len(ranked)} classes read as named; asking the client "
                 "which one it points at.")

    for candidate in ranked[:FINALISTS]:
        candidate.statics = statics_pointing_at(process, module, candidate.player)

    confirmed = [c for c in ranked[:FINALISTS] if c.confirmed]
    if confirmed:
        # Several bases can be confirmed only if several are pointed at, which
        # does not happen; where it ties, the most specific class is the one
        # with fewest instances, since a shared base has more.
        confirmed.sort(key=lambda c: (-len(c.statics), c.instances))
        return confirmed[0]

    _say(report, "Nothing in the client points at any candidate; "
                 "falling back on the best-read one.")
    return ranked[0]


# --------------------------------------------------------------------- fields
#
# Everything below judges offsets by what has to be true of the field at run
# time, on every mover at once. Each test earned its shape by failing first:
#
#   Size. Objects of one class come out of the same pools, so the commonest
#   distance between neighbouring instances is the stride. This is measured
#   rather than assumed because assuming 0x2000 read 0x210 bytes short of the
#   truth, and every offset that then looked interesting past the end turned out
#   to be the next object's fields seen through the wall.
#
#   Position. Not "the triple nearest us", which selects the offsets where every
#   mover holds the same constant, since those are zero apart and win. Not "all
#   three axes must vary" either: a hundred movers standing on flat ground share
#   a height honestly, and a test that punishes a field for being right is not a
#   test. What is true is that x and z differ per mover, height barely does, the
#   rest of the map is tens to thousands of units away, and the client keeps
#   several copies - so the answer is the value the most offsets agree on.
#
#   Identity. Unique per mover and unchanged a moment later - and then three
#   kinds of impostor have to be shown the door, because all three are unique
#   and steady too.
#
#   It was once also required to be clustered, on the theory that a counter's
#   values span a few percent of their own magnitude. That is not a fact about
#   the field, it is a fact about how long the client has been running: a client
#   up for an hour spanned 596022 to 1237336, a ratio of 0.518, and was refused
#   by a rule set at 0.5, while a client restarted minutes earlier spanned 7616
#   to 13236 and passed. Same field, same build, same map. The rule went.
#
#   What replaced it costs nothing to be sure of. A coordinate read as an
#   integer is around 2^30, and an id read as a float is a denormal near 1e-38,
#   so reading each column the other way round sorts floats from whole numbers
#   with no threshold worth arguing about. A pointer can be dereferenced, so the
#   enclosing qword is read and the halves of mapped addresses drop out. What
#   survives on this build is the id and a set of clocks, and those differ in
#   their step: a counter gives consecutive numbers to everything that spawns in
#   the same breath, so its sorted values step by one even when they span a
#   million, while a millisecond clock steps by whatever time passed. The
#   candidates are ranked by that step and the note says what came second, so a
#   close call is visible rather than silent.

#: Two readings of the world, this far apart, to see what holds still.
SETTLE = 1.2

#: How much of a mover to read while judging fields.
LOOK = 0x2400

#: A position is somewhere on this map, not at the origin and not in orbit.
NEAR, FAR = 1.0, 20000.0

#: Movers stand on ground, so heights vary by metres, not by thousands.
FLAT = 100.0

#: The magnitudes a game stores as floats. An id read as one lands far below.
FLOATY = (1e-6, 1e9)

#: Numbers a counter hands out one after another, allowing for a gap or two.
CONSECUTIVE = 2


@dataclass
class Fields:
    """What one look at the world could say about the numeric offsets."""

    size: Optional[int] = None
    position: Optional[int] = None
    entity_id: Optional[int] = None
    kind: Optional[int] = None
    kind_mask: Optional[int] = None
    movers: int = 0
    notes: List[str] = field(default_factory=list)


def stride_of(addresses: Sequence[int]) -> Optional[int]:
    """The size of one instance, read off the spacing of the pool."""
    ordered = sorted(addresses)
    gaps = Counter(b - a for a, b in zip(ordered, ordered[1:])
                   if 0 < b - a < 0x40000)
    if not gaps:
        return None
    stride, seen = gaps.most_common(1)[0]
    if seen < 3:
        return None
    # The other common gaps should be multiples of it - that is what a pool with
    # some slots taken looks like, and what tells a stride from a coincidence.
    multiples = sum(count for gap, count in gaps.items() if gap % stride == 0)
    return int(stride) if multiples >= 0.6 * sum(gaps.values()) else None


def _bodies(process: Process, movers: Sequence[int],
            span: int) -> Tuple[np.ndarray, np.ndarray]:
    raw = np.zeros((len(movers), span), dtype=np.uint8)
    alive = np.zeros(len(movers), dtype=bool)
    for i, address in enumerate(movers):
        body = process.read(address, span)
        if body:
            raw[i] = np.frombuffer(body, dtype=np.uint8)
            alive[i] = True
    return raw, alive


def _find_position(floats: np.ndarray, me: int) -> Tuple[Optional[int], str]:
    n = floats.shape[0]
    sane = np.isfinite(floats) & (np.abs(floats) < 1e7)
    triple = sane[:, :-2] & sane[:, 1:-1] & sane[:, 2:]
    # Columns where no mover has a sane triple are all-NaN, and taking a median
    # or a deviation of nothing is a warning, not a mistake: those columns are
    # meant to come out as NaN and fail the comparisons below.
    with np.errstate(invalid="ignore", over="ignore"), \
            warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        apart = floats - floats[me]
        # Ground distance only: x and z. Height is not evidence of anything.
        span = np.where(triple, np.hypot(apart[:, :-2], apart[:, 2:]), np.nan)
        median = np.nanmedian(span, axis=0)
        height = np.nanstd(np.where(triple, floats[:, 1:-1], np.nan), axis=0)
    spread = np.array([np.unique(floats[:, k]).size
                       for k in range(floats.shape[1])])
    varies = np.minimum(spread[:-2], spread[2:])        # x and z, never y

    good = np.nonzero((triple.mean(axis=0) >= 0.95) & (varies >= 0.8 * n)
                      & (median > NEAR) & (median < FAR) & (height < FLAT))[0]
    if not good.size:
        return None, "no triple of floats behaves like a place on a map"

    groups: Counter = Counter(tuple(floats[me, k:k + 3].tolist()) for k in good)
    agreed, votes = groups.most_common(1)[0]
    where = sorted(4 * int(k) for k in good
                   if tuple(floats[me, k:k + 3].tolist()) == agreed)
    return where[0], (f"{len(good)} candidates in {len(groups)} groups by value; "
                      f"{votes} agree on ({agreed[0]:.0f}, {agreed[1]:.0f}, "
                      f"{agreed[2]:.0f}) at "
                      + ", ".join(f"+0x{a:X}" for a in where))


def _looks_like_a_pointer(process: Process, addresses: Sequence[int],
                          offset: int, sample: int = 32) -> bool:
    """Is the qword this dword sits in an address the client can read?"""
    quad = offset & ~7
    tried = mapped = 0
    for address in addresses[:sample]:
        value = process.u64(address + quad)
        if value is None:
            continue
        tried += 1
        if 0x10000 < value < (1 << 47) and process.read(value, 8) is not None:
            mapped += 1
    return bool(tried) and mapped / tried >= 0.5


def _reads_as_floats(column: np.ndarray) -> float:
    """How much of this column is a number the client would have stored as one."""
    value = np.abs(column.view(np.float32))
    return float((np.isfinite(value) & (value > FLOATY[0])
                  & (value < FLOATY[1])).mean())


def _step_of(column: np.ndarray) -> float:
    """The usual distance between neighbouring values once they are sorted."""
    values = np.unique(column)
    if values.size < 2:
        return float("inf")
    return float(np.median(np.diff(values.astype(np.int64))))


def _find_id(process: Process, kept: Sequence[int], ints: np.ndarray,
             later: np.ndarray) -> Tuple[Optional[int], str]:
    n = ints.shape[0]
    unique = np.array([np.unique(ints[:, k]).size for k in range(ints.shape[1])])
    steady = (ints == later).all(axis=0)
    picks = [int(k) for k in np.nonzero((unique >= max(3, int(0.98 * n))) & steady
                                        & (ints.min(axis=0) > 0))[0]]
    if not picks:
        return None, "nothing is unique to a mover and steady enough to be an id"

    whole = [k for k in picks if _reads_as_floats(ints[:, k]) < 0.5]
    if not whole:
        return None, (f"{len(picks)} offsets are unique per mover but every one "
                      "reads as a float, so they are places and angles")

    ids = [k for k in whole if not _looks_like_a_pointer(process, kept, 4 * k)]
    if not ids:
        return None, (f"{len(whole)} offsets are unique whole numbers but every "
                      "one is half of a pointer")

    ranked = sorted(ids, key=lambda k: (_step_of(ints[:, k]), k))
    best, step = ranked[0], _step_of(ints[:, ranked[0]])
    said = (f"{len(picks)} unique and steady, {len(whole)} of them whole "
            f"numbers, {len(ids)} not half of a pointer; +0x{4 * best:X} steps "
            f"by {step:.0f}")
    if len(ranked) > 1:
        said += (f" against +0x{4 * ranked[1]:X} by "
                 f"{_step_of(ints[:, ranked[1]]):.0f}")
    if step > CONSECUTIVE:
        said = ("nothing here counts up one at a time, so this is the least bad "
                "of several clocks rather than an id: ") + said
    return 4 * best, said


def _find_kind(ints: np.ndarray,
               later: np.ndarray) -> Tuple[Optional[int], Optional[int], str]:
    """A small enumeration that most movers share and that says what they are.

    Two clients watching one world disagree about this field: bp saw 2 and 18
    where kng saw 2, 10, 18 and 26, and 2+10 came to exactly bp's count of 2s.
    One bit is bookkeeping the client does for itself, and treating it as
    meaning would split every species in two.

    The bit cannot be found by looking at how the values differ *between*
    movers, because that is the field doing its job. It shows up in how a value
    differs from itself: a mover that is still the same mover a moment later is
    still the same kind of thing, so any bit that flipped in the meantime is
    bookkeeping. If nothing flips in the time available this returns no mask
    rather than a guessed one, because a wrong mask is worse than none.
    """
    n = ints.shape[0]
    best = None
    for k in range(min(ints.shape[1], 0x100 // 4)):
        seen = Counter(ints[:, k].tolist())
        if not (2 <= len(seen) <= 8) or max(seen) > 0xFF or min(seen) == 0:
            continue
        if seen.most_common(1)[0][1] < 0.25 * n:
            continue
        best = 4 * k
        break
    if best is None:
        return None, None, "no small enumeration that most movers share"

    column, again = ints[:, best // 4], later[:, best // 4]
    flipped = int(np.bitwise_or.reduce(column ^ again)) & 0xFF
    values = sorted(set(column.tolist()))
    if not flipped:
        return best, None, (f"values {values}, none of which changed while the "
                            "movers holding them stayed put, so which bits are "
                            "the client's own bookkeeping is still unknown")
    return best, 0xFF & ~flipped, (
        f"values {values}; bits 0x{flipped:X} flipped on a mover that did not "
        f"otherwise change, so only 0x{0xFF & ~flipped:X} is what it is")


def find_fields(process: Process, vtable: int, player: int,
                report: Report = None) -> Fields:
    """The offsets that can be settled by watching the world twice."""
    from .scan import sweep_vtable

    movers, _homes = sweep_vtable(process, vtable)
    if player not in movers:
        movers.append(player)
    found = Fields(size=stride_of(movers))
    span = min(LOOK, found.size or LOOK)

    raw, alive = _bodies(process, movers, span)
    time.sleep(SETTLE)
    after, alive2 = _bodies(process, movers, span)
    alive &= alive2
    found.movers = int(alive.sum())
    if found.movers < 4:
        found.notes.append(f"only {found.movers} movers could be read twice, "
                           "which is too few to judge an offset by")
        return found

    kept = [movers[i] for i in np.nonzero(alive)[0]]
    me = int(np.cumsum(alive)[movers.index(player)] - 1)
    floats = raw.view(np.float32)[alive]
    ints = raw.view(np.uint32)[alive]
    later = after.view(np.uint32)[alive]

    _say(report, f"Judging {found.movers} movers of "
                 f"{'0x%X' % found.size if found.size else 'unknown'} bytes.")

    found.position, why = _find_position(floats, me)
    found.notes.append(f"position: {why}")
    found.entity_id, why = _find_id(process, kept, ints, later)
    found.notes.append(f"id: {why}")
    found.kind, mask, why = _find_kind(ints, later)
    found.notes.append(f"kind: {why}")
    found.kind_mask = mask
    return found


# ----------------------------------------------------------------- calibration
#
# Level and health cannot be reasoned out of a snapshot, and an earlier attempt
# that pretended otherwise was wrong twice over. Species-invariance needs a map
# with several of one kind of monster on it, and a town has none. Watching for a
# number that falls needs somebody to be fighting, and nobody was.
#
# The client is willing to show both to a human, so the human is asked, and the
# search is then exact: an offset holding that number in our own object, holding
# a believable number in everybody else's. On the observed build each of level,
# health and mana came out at a single offset - there is nothing to choose
# between, which is the outcome to hope for and to check for.
#
# Health is asked for as the pair the window draws, "current / max". If the
# character happens to be unhurt the two numbers are the same and no search can
# tell the two fields apart; that is said out loud rather than picking one.

#: A level in this game, generously bounded.
LEVELS = (1, 250)

#: Health large enough to be health and small enough to be a number.
HEALTH = (1, 100_000_000)


@dataclass
class Calibration:
    """What the typed-in numbers pinned down, and what they could not."""

    level: Optional[int] = None
    hp: Optional[int] = None
    max_hp: Optional[int] = None
    mp: Optional[int] = None
    notes: List[str] = field(default_factory=list)

    @property
    def anything(self) -> bool:
        return any(v is not None for v in
                   (self.level, self.hp, self.max_hp, self.mp))


def _pin(label: str, wanted: Optional[int], ints: np.ndarray, me: int,
         bounds: Tuple[int, int], notes: List[str],
         forbid: Sequence[int] = ()) -> Optional[int]:
    """The offset holding `wanted` in our object and something sane in the rest."""
    if not wanted:
        return None
    low, high = bounds
    at = [4 * int(k) for k in np.nonzero(ints[me] == wanted)[0]]
    at = [a for a in at if a not in forbid]
    if not at:
        notes.append(
            f"{label}: nothing in our object holds {wanted}. Read it off the "
            "window again - health and mana change the instant a buff lands or "
            "lapses, and a number that was right a minute ago is not right now")
        return None

    believable = []
    for offset in at:
        column = ints[:, offset // 4]
        share = float(((column >= low) & (column <= high)).mean())
        if share >= 0.95:
            believable.append((offset, share, int(np.unique(column).size)))
    if not believable:
        notes.append(f"{label}: {len(at)} offsets hold {wanted}, but none holds "
                     f"a number between {low} and {high} for every other mover")
        return None

    picked = believable[0][0]
    if len(believable) == 1:
        notes.append(f"{label}: +0x{picked:X}, the only offset holding {wanted} "
                     f"that is believable for every mover")
    else:
        notes.append(f"{label}: +0x{picked:X}, first of "
                     + ", ".join(f"+0x{a:X}" for a, _s, _d in believable)
                     + f" - all of them hold {wanted} and read sanely elsewhere, "
                       "so this one is a guess")
    return picked


def calibrate(process: Process, vtable: int, player: int, *,
              level: Optional[int] = None, hp: Optional[int] = None,
              max_hp: Optional[int] = None, mp: Optional[int] = None,
              report: Report = None) -> Calibration:
    """Find level, health and mana from the numbers the client shows the user."""
    from .scan import sweep_vtable

    out = Calibration()
    if not any((level, hp, max_hp, mp)):
        out.notes.append("nothing was typed in, so nothing was looked for")
        return out

    movers, _homes = sweep_vtable(process, vtable)
    if player not in movers:
        movers.append(player)
    raw, alive = _bodies(process, movers, LOOK)
    if not alive[movers.index(player)]:
        out.notes.append("our own object could not be read")
        return out
    ints = raw.view(np.uint32)[alive]
    me = int(np.cumsum(alive)[movers.index(player)] - 1)
    _say(report, f"Looking through {int(alive.sum())} movers for the numbers "
                 "the window shows.")

    out.level = _pin("level", level, ints, me, LEVELS, out.notes)

    if hp and max_hp and hp == max_hp:
        out.hp = _pin("health", hp, ints, me, HEALTH, out.notes)
        out.notes.append(
            f"maximum health: not searched for. The window says {hp}/{max_hp}, "
            "so the character is unhurt and the two fields hold the same number; "
            "nothing here can say which offset is which. Run this again after "
            "taking some damage.")
    else:
        out.hp = _pin("health", hp, ints, me, HEALTH, out.notes)
        out.max_hp = _pin("maximum health", max_hp, ints, me, HEALTH, out.notes,
                          forbid=[out.hp] if out.hp else [])

    out.mp = _pin("mana", mp, ints, me, HEALTH, out.notes)
    for line in out.notes:
        _say(report, line)
    return out


def apply_calibration(layout: Layout, found: Calibration) -> Layout:
    """Fold typed-in numbers into a layout without losing what was known.

    A calibration that found nothing must not blank an offset that an earlier
    run did find, so only the fields that came back are written.
    """
    for name in ("level", "hp", "max_hp", "mp"):
        value = getattr(found, name)
        if value is not None:
            setattr(layout, name, value)
    return layout


def discover(process: Process, module: Module, character: str,
             report: Report = None) -> Layout:
    """A layout for this client, found from scratch."""
    clock = time.perf_counter()
    found = find_class(process, module, character, report)
    layout = Layout(
        build=build_key(process, module),
        mover_vtable=found.vtable - module.base,
        name=found.name_offset,
        player_ptrs=[s - module.base for s in found.statics],
        note=f"{found.instances} instances, {100 * found.named_share:.0f}% named"
             + ("" if found.confirmed else ", NOT confirmed by a static pointer"),
    )
    _say(report, f"Class at +0x{layout.mover_vtable:X}, name at "
                 f"+0x{layout.name:X}.")

    fields = find_fields(process, found.vtable, found.player, report)
    layout.size = fields.size
    layout.position = fields.position
    layout.entity_id = fields.entity_id
    layout.kind = fields.kind
    layout.kind_mask = fields.kind_mask
    for line in fields.notes:
        _say(report, line)

    _say(report, f"Found in {time.perf_counter() - clock:.0f}s."
                 + (f" Still missing: {layout.why_incomplete()}"
                    if layout.missing() else ""))
    return layout


#: Offsets discovery cannot produce. Level, health and mana come from numbers a
#: person read off the window; target and destination come from a recorded play
#: session. Rediscovery finds none of them, so it must not be allowed to erase
#: them - doing exactly that silently recorded a hundred and forty-seven seconds
#: of zeroes and cost a play session to notice.
EARNED = ("level", "hp", "max_hp", "mp", "target_id", "target_via",
          "target_is_pointer", "dest", "dest_via", "kind_mask")

#: A client on a loading screen has no player object. That is not a stale cache.
PATIENCE, BREATH = 3, 0.6


def carry_forward(old: Layout, new: Layout) -> List[str]:
    """Move the offsets that had to be earned onto a freshly found layout.

    Only when the two describe the same class of the same size, because that is
    what makes a field offset within it mean the same thing. If the class moved,
    everything about the old entry is suspect and none of it is carried.
    """
    if not old or old.mover_vtable != new.mover_vtable or old.size != new.size:
        return []
    moved = []
    for name in EARNED:
        mine, theirs = getattr(new, name, None), getattr(old, name, None)
        if name == "target_is_pointer":          # a flag, not an offset
            if theirs and not mine:
                setattr(new, name, theirs)
                moved.append(name)
            continue
        if mine is None and theirs is not None:
            setattr(new, name, theirs)
            moved.append(name)
    return moved


def layout_for(process: Process, module: Module, character: str, *,
               store: Optional[LayoutStore] = None, report: Report = None,
               rediscover: bool = False) -> Layout:
    """The offsets for this client: remembered if they still fit, else found.

    The cache exists because discovery reads a gigabyte and a half of heap and
    takes the better part of a minute, which is too long to spend every launch.
    It is checked rather than trusted, because the failure mode of a stale
    layout is not a crash - it is a bot that reads a plausible number from the
    wrong place and acts on it.

    Two things temper that, both learned the expensive way. A verify failure is
    given a few seconds to sort itself out first, because the commonest cause is
    a loading screen rather than a wrong offset, and one of the player statics
    genuinely moves between launches. And when discovery does run, it inherits
    whatever the old entry knew that discovery cannot work out for itself -
    otherwise a moment of bad luck during a map change throws away a
    calibration, and the next thing that reads health reads nothing at all.
    """
    store = store or LayoutStore()
    key = build_key(process, module)

    cached = None if rediscover else store.load(key)
    if cached:
        complaint = ""
        for attempt in range(PATIENCE):
            complaint = verify(process, module, cached)
            if not complaint:
                _say(report, f"Using the offsets found on {cached.found}."
                     + (f" {cached.why_incomplete()}" if cached.missing() else ""))
                return cached
            if attempt + 1 < PATIENCE:
                _say(report, f"The client did not answer ({complaint}); "
                             f"waiting, in case it is loading.")
                time.sleep(BREATH)
        _say(report, f"The stored offsets still do not fit ({complaint}), "
                     f"so they are being found again.")

    layout = discover(process, module, character, report)
    inherited = carry_forward(cached, layout)
    if inherited:
        _say(report, "Kept " + ", ".join(inherited) + " from the old entry, "
             "since discovery cannot work those out on its own.")
    store.save(layout)
    return layout
