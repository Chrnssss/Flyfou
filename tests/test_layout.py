"""The finder's rules and the layout cache, checked without a game running.

Every rule in `discover` was argued out against four live clients, and each one
replaced an earlier rule that looked just as reasonable and was wrong. Those
arguments are not written down anywhere the computer can check them, so a
refactor is free to quietly reintroduce any of the mistakes. This file writes
them down as worlds where the right answer is known by construction:

    a position offset must beat a column of constants, because ranking by
    "nearest to us" once picked the constants - they are zero away;

    it must survive every mover standing at the same height, because requiring
    all three axes to vary once rejected the real position for being correct
    about flat ground;

    an id offset must beat the low half of a pointer, which is unique per object
    for exactly the same reason an id is;

    the kind mask must come from a bit that flickers on one mover, not from a
    bit that differs between movers - the second is the field doing its job;

    a size must be a pool stride and not merely a common gap.

The cache half is decidable too: a layout written and read back must be the same
layout, an unfinished one must say which behaviours it cannot support, and a
calibration that found nothing must not erase what a previous run found.

    .venv\\Scripts\\python tests\\test_layout.py
"""
import os
import struct
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flyfou.mem.discover import (Calibration, _find_id, _find_kind, _find_position,
                                 _pin, apply_calibration, carry_forward,
                                 name_like, stride_of)
from flyfou.mem.layout import (NEEDED_FOR, Layout, LayoutStore, _hex, _int,
                               resolve_player, verify)

failures = []
checked = 0


def check(name, got, want):
    global checked
    checked += 1
    ok = got == want
    print("  %-58s %s" % (name, "ok" if ok else "FAILED  got %r want %r" % (got, want)))
    if not ok:
        failures.append(name)


# --------------------------------------------------------------------------- #
print("a name is text a person could be called, not bytes that happen to print")

check("a monster's name", name_like("Sablosaure"), True)
check("a player's name", name_like("Mynuthbp"), True)
check("a name with a space", name_like("Leonis Infernal"), True)
check("a name with an apostrophe", name_like("Jennif'hair"), True)
check("scenery's four bytes of junk", name_like("p9.>"), False)
check("more junk", name_like("[A+?"), False)
check("too short", name_like("ab"), False)
check("digits only", name_like("12345"), False)
check("two letters and padding", name_like("ab   "), False)
check("nothing", name_like(""), False)


# --------------------------------------------------------------------------- #
print("\nan object's size is a pool stride, not just a common gap")

check("a clean pool of five", stride_of([0, 0x2210, 0x4420, 0x6630, 0x8840]), 0x2210)
check("a pool with slots taken", stride_of(
    [0, 0x2210, 0x4420, 0x8840, 0xAA50, 0xCC60, 0x11080]), 0x2210)
check("two instances say nothing", stride_of([0, 0x2210]), None)
check("nothing says nothing", stride_of([]), None)
check("gaps that are not multiples of the commonest are not a stride",
      stride_of([0, 100, 200, 300, 451, 613, 787, 971, 1163]), None)


# --------------------------------------------------------------------------- #
print("\nposition beats the decoys that beat every earlier rule")


def world_of_movers(n=40, columns=64, me=0):
    """Floats where only one triple is a place, surrounded by plausible wrongs."""
    rng = np.random.default_rng(7)
    block = np.zeros((n, columns), dtype=np.float32)

    # The real position: x and z spread over the map, y on the same ground.
    x = np.float32(6900) + rng.normal(0, 60, n).astype(np.float32)
    y = np.float32(99) + rng.normal(0, 0.5, n).astype(np.float32)
    z = np.float32(3070) + rng.normal(0, 60, n).astype(np.float32)
    block[:, 4:7] = np.stack([x, y, z], axis=1)

    # A copy of it, as the client really keeps.
    block[:, 20:23] = block[:, 4:7]

    # The decoy that beat "nearest to us": every mover holds the same triple, so
    # the distance from us to all of them is zero and it wins on closeness.
    block[:, 8:11] = np.array([1.0, 1.0, 1.0], dtype=np.float32)

    # The decoy that beat "the first column varies": the tail of a real position
    # followed by two constants, which is a misaligned view of the truth.
    block[:, 12] = z
    block[:, 13:15] = 1.0

    # Garbage that is not a coordinate at all.
    block[:, 30:33] = rng.normal(0, 1e12, (n, 3)).astype(np.float32)
    block[:, 40] = np.float32("inf")
    return block


block = world_of_movers()
offset, why = _find_position(block, me=0)
check("the position is found", offset, 4 * 4)
check("the copy is not preferred to the original", offset != 4 * 20, True)
check("the constant triple is rejected", offset != 4 * 8, True)
check("the misaligned view is rejected", offset != 4 * 12, True)
check("it says how it chose", "agree on" in why, True)

# Flat ground: every mover at exactly one height, which an earlier rule punished.
flat = world_of_movers()
flat[:, 5] = np.float32(99.0)
flat[:, 21] = np.float32(99.0)
offset, _why = _find_position(flat, me=0)
check("a hundred movers sharing one height is still a position", offset, 4 * 4)

# Nothing that looks like a place at all.
empty = np.zeros((20, 64), dtype=np.float32)
offset, why = _find_position(empty, me=0)
check("a world with no position says so", offset, None)
check("and says why", "behaves like a place" in why, True)


# --------------------------------------------------------------------------- #
print("\nan id beats the low half of a pointer, which is unique for the same reason")


class Dereferencer:
    """Answers whether a qword is an address, from a list of ones that are."""

    def __init__(self, mapped):
        self.mapped = set(mapped)

    def u64(self, address):
        return self.values.get(address)

    def read(self, address, size):
        return b"\x00" * size if address in self.mapped else None


def id_world(n=40):
    """Ints where four columns are unique and steady and only one is an id."""
    rng = np.random.default_rng(11)
    block = np.zeros((n, 32), dtype=np.uint32)
    # An id from a counter, at +0x28. Things that spawned together got
    # consecutive numbers; the three jumps are the map filling up over an hour.
    # It therefore spans most of its own magnitude, which the rule this file
    # exists to prevent read as noise: a live client failed at a ratio of 0.518.
    counter = np.concatenate([np.arange(10) + 596022, np.arange(10) + 731500,
                              np.arange(10) + 998311, np.arange(10) + 1237300])
    block[:, 10] = counter[rng.permutation(n)]
    # A coordinate at +0x3C: unique, steady, and not an id. As an integer it is
    # around 2^30, which is what every float of a sane size looks like.
    block[:, 15] = np.array([1119528812 + i * 8834 for i in range(n)],
                            dtype=np.uint32)[rng.permutation(n)]
    # A pointer, split over two columns at +0x40: the low half is unique too.
    # Never the zeroth slot: a low half of exactly 0 is dropped as an empty
    # field before the dereference test ever gets a chance to reject it, which
    # would make this decoy pass for the wrong reason.
    base = 0x0000022300000000 + (rng.permutation(n) + 1) * 0x2210
    block[:, 16] = (base & 0xFFFFFFFF).astype(np.uint32)
    block[:, 17] = (base >> 32).astype(np.uint32)
    # A clock at +0x50: a whole number, not a pointer, unique and steady - and
    # separated from the id only by its step, which is milliseconds, not one.
    block[:, 20] = 33990543 + rng.permutation(n) * 1000
    # Something that changes, so it cannot be an id.
    block[:, 24] = rng.integers(0, 1 << 30, n, dtype=np.uint64).astype(np.uint32)
    return block, base


block, base = id_world()
later = block.copy()
later[:, 24] += 1

process = Dereferencer(mapped=base.tolist())
process.values = {}
# Objects a real pool-stride apart, not 1 apart: with addresses 0..39 and
# fields at +0x28 and +0x40, one object's target slot IS another object's
# pointer slot, and the fake process answers a question nobody asked.
kept = [0x100000 + i * 0x2210 for i in range(40)]
# Each mover's object holds its own pointer at +0x40; nothing else dereferences.
for i, address in enumerate(kept):
    process.values[address + 0x40] = int(base[i])
    process.values[address + 0x28] = int(block[i, 10])

offset, why = _find_id(process, kept, block, later)
check("the id is found", offset, 0x28)
check("the pointer half is rejected", offset != 0x40, True)
check("a spread-out counter is still a counter", offset, 0x28)
check("the coordinate is rejected for reading as a float", offset != 0x3C, True)
check("the clock is rejected for stepping by a thousand", offset != 0x50, True)
check("it says what came second", "against +0x" in why, True)

moving = block.copy()
moving[:, 10] += 1
offset, why = _find_id(process, kept, block, moving)
check("an id that changes in a moment is not an id", offset, 0x50)
check("and the clock it settles for is called one", "least bad" in why, True)


# --------------------------------------------------------------------------- #
print("\nthe kind mask comes from a bit that flickers, not one that means something")

kinds = np.zeros((40, 8), dtype=np.uint32)
kinds[:30, 1] = 2         # players
kinds[30:, 1] = 18        # pets
after = kinds.copy()
after[5, 1] = 10          # one player the client started drawing: bit 0x8
offset, mask, why = _find_kind(kinds, after)
check("the kind field is found", offset, 4)
check("the flickering bit is masked out", mask, 0xFF & ~0x8)
check("the bit that separates players from pets is kept", bool(mask & 0x10), True)

offset, mask, why = _find_kind(kinds, kinds.copy())
check("with nothing flickering it refuses to guess a mask", mask, None)
check("and says the mask is unknown", "still unknown" in why, True)


# --------------------------------------------------------------------------- #
print("\ncalibration pins a typed number, or says why it could not")

n = 30
ints = np.zeros((n, 512), dtype=np.uint32)
ints[:, 0x718 // 4] = 161                      # a level, believable for everyone
ints[:, 0x738 // 4] = np.arange(n) + 9000      # health, ours at row 0
ints[0, 0x738 // 4] = 9184
ints[:, 0x100 // 4] = 161                      # 161 again, but absurd elsewhere
ints[1:, 0x100 // 4] = 999999

notes = []
check("a level is pinned", _pin("level", 161, ints, 0, (1, 250), notes), 0x718)
check("the offset that is absurd for others is rejected",
      _pin("level", 161, ints, 0, (1, 250), notes) != 0x100, True)
check("health is pinned", _pin("hp", 9184, ints, 0, (1, 100_000_000), notes), 0x738)

notes = []
check("a number nowhere in the object is not pinned",
      _pin("hp", 12345, ints, 0, (1, 100_000_000), notes), None)
check("and the reason blames the number, not the offset",
      "lapses" in notes[-1], True)

notes = []
check("a forbidden offset is skipped",
      _pin("max_hp", 9184, ints, 0, (1, 100_000_000), notes, forbid=[0x738]), None)


# --------------------------------------------------------------------------- #
print("\na calibration fills a layout in without erasing it")

layout = Layout(build="b", mover_vtable=0x1000, name=0x1EC8, player_ptrs=[0x20],
                level=0x718, hp=0x738)
apply_calibration(layout, Calibration())
check("an empty calibration changes nothing", (layout.level, layout.hp),
      (0x718, 0x738))
apply_calibration(layout, Calibration(mp=0x73C, max_hp=0x740))
check("what it found is written", (layout.mp, layout.max_hp), (0x73C, 0x740))
check("what it did not find is left alone", layout.level, 0x718)


# --------------------------------------------------------------------------- #
print("\na layout survives being written down and read back")

full = Layout(build="neuz.exe-6A8E2800-10E8000", mover_vtable=0x96D620,
              name=0x1EC8, player_ptrs=[0xDA45B0, 0xE08AB0, 0xE1AA18],
              size=0x2210, position=0x60, entity_id=0x28, level=0x718,
              hp=0x738, mp=0x73C, kind=0x8, kind_mask=0xF7,
              note="124 instances, 97% named")
again = Layout.from_dict(full.to_dict())
for name in ("build", "mover_vtable", "name", "player_ptrs", "size", "position",
             "entity_id", "level", "hp", "max_hp", "mp", "target_id", "kind",
             "kind_mask", "note"):
    check("round trip keeps %s" % name, getattr(again, name), getattr(full, name))

check("offsets are written as hex a person can compare",
      full.to_dict()["mover_vtable"], "0x96D620")
check("an absent offset stays absent", full.to_dict()["fields"]["target_id"], None)
check("hex reads back", _int("0x2210"), 0x2210)
check("a plain int reads back", _int(8720), 8720)
check("nothing reads back as nothing", _int(None), None)
check("an empty string is nothing, not zero", _int(""), None)
check("nothing is written as nothing", _hex(None), None)


# --------------------------------------------------------------------------- #
print("\nan unfinished layout says which behaviours it cannot support")

check("a full layout is usable", full.usable, True)
# A target field and a destination field are not on the list of things the bot
# needs, because it no longer needs them: targets are read off who is losing
# health, and movement is a click. A build without them is complete.
check("a layout without a target or destination field is still complete",
      full.missing(), [])
check("so it has nothing to complain about", full.why_incomplete(), "")

hollow = Layout(build="b", mover_vtable=0x1, name=0x2, player_ptrs=[0x3])
check("but a layout with no health still says what that costs",
      "who is fighting whom" in hollow.why_incomplete(), True)

bare = Layout(build="b", mover_vtable=0x1000, name=0x1EC8, player_ptrs=[0x20])
check("a bare layout is still usable for enumerating the world", bare.usable, True)
check("but misses everything else", bare.missing(), sorted(NEEDED_FOR,
                                                           key=list(NEEDED_FOR).index))
check("no class pointer is not usable",
      Layout(build="b", mover_vtable=0, name=0, player_ptrs=[0x20]).usable, False)
check("no player pointer is not usable",
      Layout(build="b", mover_vtable=0x1000, name=0, player_ptrs=[]).usable, False)


# --------------------------------------------------------------------------- #
print("\nthe cache is one file, one entry per build")

with tempfile.TemporaryDirectory() as home:
    store = LayoutStore(os.path.join(home, "layouts.yaml"))
    check("an empty cache has no builds", store.builds(), [])
    check("a miss is None", store.load("neuz.exe-DEADBEEF-1000"), None)

    store.save(full)
    check("a saved build is listed", store.builds(), [full.build])
    loaded = store.load(full.build)
    check("it comes back the same", loaded.to_dict(), full.to_dict())
    check("saving stamped it with a date", bool(loaded.found), True)

    other = Layout(build="neuz.exe-11112222-2000", mover_vtable=0x1234,
                   name=0x10, player_ptrs=[0x40])
    store.save(other)
    check("two builds coexist", store.builds(), sorted([full.build, other.build]))

    store.forget(full.build)
    check("forgetting removes one", store.builds(), [other.build])
    check("and leaves the other readable", store.load(other.build).mover_vtable,
          0x1234)
    store.forget("a build that was never there")
    check("forgetting nothing is harmless", store.builds(), [other.build])

    with open(os.path.join(home, "layouts.yaml"), "r", encoding="utf-8") as handle:
        text = handle.read()
    check("the file says it was not written by hand",
          "not written by hand" in text, True)

    broken = os.path.join(home, "broken.yaml")
    with open(broken, "w", encoding="utf-8") as handle:
        handle.write("this: is: not: yaml:\n  - [")
    check("an unreadable cache is an empty cache, not a crash",
          LayoutStore(broken).builds(), [])


# --------------------------------------------------------------------------- #
print("\nverify refuses a layout that does not describe the client in front of it")


class Client:
    """A module and one object, enough to resolve a player through a layout."""

    def __init__(self, vtable_at=0x96D620, statics=(0xDA45B0,), player=0x50000,
                 name=b"Mynuthbp\x00", stamp=0x6A8E2800):
        self.base = 0x7FF700000000
        self.player = player
        self.stamp = stamp
        self.words = {self.base + s: player for s in statics}
        self.words[player] = self.base + vtable_at
        self.name = name

    def u64(self, address):
        return self.words.get(address)

    def pointer(self, address):
        return self.words.get(address)

    def read(self, address, size):
        if address == self.player + 0x1EC8:
            return self.name.ljust(size, b"\x00")
        return None

    def vec3(self, address):
        return (6936.0, 99.0, 3076.0) if address == self.player + 0x60 else None


module = type("M", (), {"name": "Neuz.exe", "base": 0x7FF700000000,
                        "size": 0x10E8000})()
good = Layout(build="neuz.exe-6A8E2800-10E8000", mover_vtable=0x96D620,
              name=0x1EC8, player_ptrs=[0xDA45B0], position=0x60)

client = Client()
check("the player resolves through the static",
      resolve_player(client, module, good), 0x50000)


def _stamped(process, module, _real=None):
    return 0x6A8E2800


import flyfou.mem.layout as layout_module                       # noqa: E402
layout_module.link_stamp = _stamped

check("a layout that fits says nothing", verify(client, module, good), "")

wrong_build = Layout.from_dict(good.to_dict())
wrong_build.build = "neuz.exe-11112222-2000"
check("a layout from another build is refused",
      "this client is" in verify(client, module, wrong_build), True)

wrong_static = Layout.from_dict(good.to_dict())
wrong_static.player_ptrs = [0xDEAD00]
check("a static that leads nowhere is refused",
      verify(client, module, wrong_static),
      "no static pointer leads to an object of the expected class")

wrong_class = Layout.from_dict(good.to_dict())
wrong_class.mover_vtable = 0x111111
check("a static leading to the wrong class is refused",
      "no static pointer leads" in verify(client, module, wrong_class), True)

nameless = Client(name=b"\x01\x02\x03\x00")
check("an object with no name at the name offset is refused",
      "there is no name" in verify(nameless, module, good), True)

check("an unusable layout is refused before anything is read",
      "missing the class pointer" in verify(
          client, module, Layout(build="x", mover_vtable=0, name=0)), True)


# --------------------------------------------------------------------------- #
print("\nrediscovery inherits what discovery cannot work out for itself")

# This is not a nicety. A verify failure during a map change once threw away a
# calibration, and the next two minutes of recording held nothing but zeroes,
# because discovery finds ids and positions but health only ever comes from a
# person reading a number off the window.
old = Layout(build="b", mover_vtable=0x96D620, name=0x1EC8, player_ptrs=[0xDA45B0],
             size=0x2210, position=0x60, entity_id=0x28, level=0x718, hp=0x738,
             max_hp=None, mp=0x73C, target_id=0x900, dest=0x910, kind=0x8,
             kind_mask=0xF7)
fresh = Layout(build="b", mover_vtable=0x96D620, name=0x1EC8,
               player_ptrs=[0xDA45B0], size=0x2210, position=0x60,
               entity_id=0x28, kind=0x8)

moved = carry_forward(old, fresh)
check("health survives a rediscovery", fresh.hp, 0x738)
check("so does the level", fresh.level, 0x718)
check("so does the target field", fresh.target_id, 0x900)
check("so does the destination", fresh.dest, 0x910)
check("so does the kind mask", fresh.kind_mask, 0xF7)
check("it says what it moved", sorted(moved),
      ["dest", "hp", "kind_mask", "level", "mp", "target_id"])
check("what was never known stays unknown", fresh.max_hp, None)
check("and what was rediscovered is not overwritten", fresh.entity_id, 0x28)

# A different class is a different object, and none of its offsets carry.
elsewhere = Layout(build="b", mover_vtable=0x111111, name=0x1EC8,
                   player_ptrs=[0xDA45B0], size=0x2210, position=0x60)
check("a class that moved carries nothing", carry_forward(old, elsewhere), [])
check("and its health stays empty", elsewhere.hp, None)

resized = Layout(build="b", mover_vtable=0x96D620, name=0x1EC8,
                 player_ptrs=[0xDA45B0], size=0x3000, position=0x60)
check("an object that changed size carries nothing",
      carry_forward(old, resized), [])
check("nothing to inherit from is harmless", carry_forward(None, resized), [])


# --------------------------------------------------------------------------- #
print("\n%d checks, %d failed" % (checked, len(failures)))
if failures:
    print("\nFAILED:")
    for name in failures:
        print("  " + name)
sys.exit(1 if failures else 0)
