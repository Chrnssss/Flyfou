"""Memory reading and scanning, checked without a game running.

A live client proves the sweep finds movers, but it cannot prove much else. It
never has an empty result, so the fallback in `HotRegions` never fires; its
regions never move under the scanner on cue; and every answer it gives is a
number nobody can check independently, because the only other way to learn it is
the code under test.

Everything here is decidable instead. Half of it runs against a process made of
bytes, where the right answer is whatever was planted; the other half runs
against this very Python process, which is a real target with real regions and
a real module, and where a `ctypes` buffer is a known value at a known address.
That second half is what catches a wrong `argtypes` or a mislaid struct field -
the failures that make every read return None and every sweep return nothing.

    .venv\\Scripts\\python tests\\test_mem.py
"""
import ctypes
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flyfou.mem import HotRegions, Process, ProcessError, Region, scan_u64, sweep_vtable
from flyfou.mem.process import MEM_IMAGE, MEM_PRIVATE, Module, ascii_at, link_stamp, modules_of
from flyfou.mem.scan import (_words, contiguous_runs, find_bytes, scan_range,
                             scan_u64_many)

failures = []
VT = 0x7FF700001000
MARKER = 0x5F4C59464F55A1B2


def check(name, got, want):
    ok = got == want
    print("  %-52s %s" % (name, "ok" if ok else "FAILED  got %r want %r" % (got, want)))
    if not ok:
        failures.append(name)


def qwords(*values):
    return b"".join(struct.pack("<Q", v) for v in values)


class FakeProcess:
    """A process made of bytes, so scan logic can be judged against a known answer."""

    def __init__(self, blobs):
        self.blobs = dict(blobs)
        self.read_bases = []

    def regions(self, private_only=False, skip_huge=True):
        return [Region(base, len(blob), MEM_PRIVATE, 0x04)
                for base, blob in sorted(self.blobs.items())]

    def read_partial(self, address, size):
        self.read_bases.append(address)
        return self.blobs.get(address, b"")[:size]


def make_world():
    """Three regions; the vtable appears twice in the first and once in the second."""
    return FakeProcess({
        0x10000000: qwords(0, 1, 2, VT, 4, 5, 6, VT),
        0x20000000: qwords(VT, 11, 12, 13),
        0x30000000: qwords(20, 21, 22, 23),
    })


# --------------------------------------------------------------------------- #
print("a qword view ignores a ragged tail rather than reading past it")

check("whole qwords survive", _words(qwords(1, 2, 3)).tolist(), [1, 2, 3])
check("a trailing five bytes are dropped", _words(qwords(1, 2) + b"\x01\x02\x03\x04\x05").tolist(), [1, 2])
check("less than one qword is empty", _words(b"\x01\x02\x03").size, 0)
check("nothing is empty", _words(b"").size, 0)

# --------------------------------------------------------------------------- #
print("\nscanning finds what was planted, and nothing else")

world = make_world()
check("scan_u64 finds both copies and the third", scan_u64(world, VT),
      [0x10000018, 0x10000038, 0x20000000])
check("a value nobody holds is not found", scan_u64(world, 0xDEAD), [])
check("scan_u64 masks a value wider than 64 bits",
      scan_u64(world, VT + (1 << 64)), [0x10000018, 0x10000038, 0x20000000])
check("scan_u64_many pairs address with value",
      scan_u64_many(world, [VT, 21]),
      [(0x10000018, VT), (0x10000038, VT), (0x20000000, VT), (0x30000008, 21)])
check("asking for nothing scans nothing", scan_u64_many(world, []), [])
check("scan_range takes low but not high",
      [a for a, _v in scan_range(world, 20, 22)], [0x30000000, 0x30000008])

# --------------------------------------------------------------------------- #
print("\nbyte search is not fooled by alignment")

skewed = FakeProcess({0x10000000: b"\x00\x00\x00" + b"needle" + b"\x00" * 5 + b"needle"})
check("both copies found, one of them unaligned",
      find_bytes(skewed, b"needle"), [0x10000003, 0x1000000E])
check("the limit is obeyed", find_bytes(skewed, b"needle", limit=1), [0x10000003])
check("an empty needle finds nothing", find_bytes(skewed, b""), [])

# --------------------------------------------------------------------------- #
print("\na sweep reports where it found things, not just what")

found, homes = sweep_vtable(make_world(), VT)
check("every instance", found, [0x10000018, 0x10000038, 0x20000000])
check("counted per region", homes, {0x10000000: 2, 0x20000000: 1})
check("a barren region is not listed", 0x30000000 in homes, False)

# --------------------------------------------------------------------------- #
print("\nHotRegions narrows after the first pass")

world = make_world()
hot = HotRegions()
check("the first sweep is full", hot.sweep(world, VT),
      [0x10000018, 0x10000038, 0x20000000])
check("it counted as a full sweep", (hot.full_sweeps, hot.narrow_sweeps), (1, 0))
check("it remembered the two regions that held things", hot.bases, {0x10000000, 0x20000000})

world.read_bases.clear()
check("the second sweep agrees", hot.sweep(world, VT),
      [0x10000018, 0x10000038, 0x20000000])
check("and it was narrow", (hot.full_sweeps, hot.narrow_sweeps), (1, 1))
check("it did not touch the barren region", world.read_bases, [0x10000000, 0x20000000])

# --------------------------------------------------------------------------- #
print("\nand falls back rather than reporting an empty world")

# The game frees the regions it was allocating from and starts using another.
world.blobs[0x10000000] = qwords(0, 0, 0, 0, 0, 0, 0, 0)
world.blobs[0x20000000] = qwords(0, 0, 0, 0)
world.blobs[0x30000000] = qwords(20, VT, 22, 23)
world.read_bases.clear()

check("it found the movers at their new address", hot.sweep(world, VT), [0x30000008])
check("by giving up on the narrow sweep and doing a full one",
      (hot.full_sweeps, hot.narrow_sweeps), (2, 2))
check("it now remembers the new region", hot.bases, {0x30000000})

hot.forget()
check("forget() drops what it knew", hot.bases, set())
check("so the next sweep is due to be wide", hot._due(0.0), True)

# --------------------------------------------------------------------------- #
print("\na world with nothing in it does not get remembered as narrow")

barren = FakeProcess({0x10000000: qwords(0, 0)})
empty = HotRegions()
check("nothing found", empty.sweep(barren, VT), [])
check("it stays due for a full sweep", empty._due(1e9), True)
check("so it never gets stuck sweeping regions that hold nothing",
      (empty.sweep(barren, VT), empty.narrow_sweeps)[1], 0)

# --------------------------------------------------------------------------- #
print("\nruns of evenly spaced slots are told apart from scattered references")

sites = [(0x1000, 7), (0x1008, 8), (0x1010, 9), (0x2000, 4), (0x3000, 5), (0x3008, 5)]
runs = contiguous_runs(sites)
check("three groups", len(runs), 3)
check("the longest run is first, by distinct values", runs[0],
      [(0x1000, 7), (0x1008, 8), (0x1010, 9)])
check("a lone reference is its own group", [r for r in runs if len(r) == 1], [[(0x2000, 4)]])
check("a repeated value sorts below a varied one", runs[-1], [(0x3000, 5), (0x3008, 5)])
check("slack bridges a gap of one slot",
      len(contiguous_runs([(0, 1), (16, 2), (32, 3)], slack=1)), 1)
check("but not without it", len(contiguous_runs([(0, 1), (16, 2), (32, 3)])), 3)
check("nothing in, nothing out", contiguous_runs([]), [])

# --------------------------------------------------------------------------- #
print("\nregions and modules answer questions about addresses")

region = Region(0x1000, 0x100, MEM_PRIVATE, 0x04)
check("end is past the last byte", region.end, 0x1100)
check("it holds its own base", region.holds(0x1000), True)
check("it does not hold its end", region.holds(0x1100), False)
check("private is the heap", region.private, True)
check("an image region is not private", Region(0, 1, MEM_IMAGE, 0x02).private, False)
check("read-write is writable", region.writable, True)
check("read-only is not", Region(0, 1, MEM_PRIVATE, 0x02).writable, False)
check("a guard page does not confuse the protect check",
      Region(0, 1, MEM_PRIVATE, 0x04 | 0x100).writable, True)

module = Module("Neuz.exe", 0x7FF758280000, 0x10E8000)
check("an address inside becomes an offset", module.offset_of(0x7FF758280000 + 0xDA45B0), 0xDA45B0)
check("an address outside becomes None", module.offset_of(0x1000), None)
check("the base is offset zero", module.offset_of(module.base), 0)

# --------------------------------------------------------------------------- #
print("\ninline names stop at the first byte that is not text")

check("a name followed by padding", ascii_at(b"Mynuthbp\x00\x00\x00rest", 0), "Mynuthbp")
check("read from an offset", ascii_at(b"\x00\x00Sablosaure\x00", 2), "Sablosaure")
check("the limit truncates", ascii_at(b"Leonis Infernal\x00", 0, limit=6), "Leonis")
check("no text at all", ascii_at(b"\x00\x01\x02", 0), "")
check("past the end", ascii_at(b"abc", 99), "")

# --------------------------------------------------------------------------- #
print("\nagainst this very process, which is a real target")

buffer = ctypes.create_string_buffer(b"flyfou-marker", 64)
numbers = (ctypes.c_uint64 * 3)(1, MARKER, 0xFFFFFFFFFFFFFFFF)
floats = (ctypes.c_float * 3)(6936.0, 99.5, 3076.0)
# Bound to a name on purpose: as a temporary it is freed the instant addressof
# returns, and the read below would be of released memory.
pointer_slot = (ctypes.c_void_p * 1)(ctypes.addressof(buffer))
here = Process(os.getpid())

check("it is alive", here.alive, True)
check("it opened read-only", here.writable, False)
check("reads come back byte for byte",
      here.read(ctypes.addressof(buffer), 13), b"flyfou-marker")
check("a read that cannot complete is None, not short", here.read(0x10, 8), None)
check("a zero-length read is None", here.read(ctypes.addressof(buffer), 0), None)
check("u64", here.u64(ctypes.addressof(numbers) + 8), MARKER)
check("u32 takes the low half", here.u32(ctypes.addressof(numbers) + 8), MARKER & 0xFFFFFFFF)
check("u32 of all ones", here.u32(ctypes.addressof(numbers) + 16), 0xFFFFFFFF)
check("i32 sees a sign where u32 does not",
      here.i32(ctypes.addressof(numbers) + 16), -1)
check("u8", here.u8(ctypes.addressof(buffer)), ord("f"))
check("vec3", here.vec3(ctypes.addressof(floats)), (6936.0, 99.5, 3076.0))
check("f32", here.f32(ctypes.addressof(floats) + 4), 99.5)
check("a plain integer is not mistaken for a pointer",
      here.pointer(ctypes.addressof(numbers)), None)
check("a real pointer is", here.pointer(ctypes.addressof(pointer_slot)),
      ctypes.addressof(buffer))
check("a chain of one hop is just a read",
      here.chain(ctypes.addressof(pointer_slot), 0), ctypes.addressof(buffer))
check("a chain through nothing gives up", here.chain(ctypes.addressof(numbers), 0, 0), None)

spans = here.regions()
private = here.regions(private_only=True)
check("it enumerated regions", len(spans) > 10, True)
check("private is a subset of everything", len(private) <= len(spans), True)
check("all of them are private", all(r.private for r in private), True)
check("regions come in address order", spans == sorted(spans), True)
check("none of them overlap",
      all(a.end <= b.base for a, b in zip(spans, spans[1:])), True)
check("the buffer lives in one of them",
      any(r.holds(ctypes.addressof(buffer)) for r in spans), True)

exe = here.module("python")
check("the module was found", exe.name.lower().startswith("python"), True)
check("its base is a real address", exe.base > 0x10000, True)
check("the module list is cached", here.modules() is here.modules(), True)
check("modules_of agrees without a handle",
      exe in modules_of(os.getpid()), True)
check("a link stamp was read out of the PE header",
      (link_stamp(here, exe) or 0) > 0x40000000, True)

try:
    here.write(ctypes.addressof(buffer), b"x")
    check("writing through a read-only handle raises", False, True)
except ProcessError:
    check("writing through a read-only handle raises", True, True)

try:
    here.module("no-such-module-anywhere")
    check("asking for a module that is not loaded raises", False, True)
except ProcessError:
    check("asking for a module that is not loaded raises", True, True)

hits = scan_u64(here, MARKER)
check("scanning this process finds the marker in the buffer",
      ctypes.addressof(numbers) + 8 in hits, True)

here.close()
check("a closed process is not alive", here.alive, False)
check("reads after close are None", here.read(ctypes.addressof(buffer), 4), None)
check("closing twice is harmless", here.close(), None)

try:
    Process(0x7FFFFFFF)
    check("opening a pid that does not exist raises", False, True)
except ProcessError:
    check("opening a pid that does not exist raises", True, True)

print("\n%s" % ("all checks passed" if not failures else "FAILED: %s" % ", ".join(failures)))
sys.exit(1 if failures else 0)
