"""Separate movers from scenery, then map the mover.

_mem7 found 7935 objects with sane world positions spread over x 5270..8315,
z 2049..9348 - that is a whole map region of trees and rocks, not monsters. So
+0x60 is a position on some shared base class, and the vtable alone does not
say "monster".

What says "monster" is motion. Scenery never moves; a mover does. Now that the
object stride and alignment are right, the test is exact rather than the fuzzy
walk-back _mem5 used: snapshot every object's own +0x60, wait, read the same
addresses again, and keep the ones that actually walked.

Read-only.
"""
import ctypes
import struct
import sys
import time
from collections import Counter, defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions
from _mem3 import modules

STRIDE = 0x320
POS = 0x60
GAP = 6.0
WORLD_LO, WORLD_HI = 50.0, 20000.0
MOVED_LO, MOVED_HI = 0.05, 500.0


def snapshot(handle, private):
    out = {}
    for base, size, _m, _p in private:
        blob = read(handle, base, size)
        if blob:
            out[base] = blob
    return out


def candidates(blobs, mod_base, mod_size):
    """16-aligned qwords pointing into Neuz.exe, with a sane position at +0x60."""
    found = []
    for base, blob in blobs.items():
        if len(blob) < STRIDE:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        hit = np.nonzero((words >= mod_base) & (words < mod_base + mod_size))[0]
        for i in hit.tolist():
            at = i * 8
            if (base + at) % 16 or at + STRIDE > len(blob):
                continue
            x, y, z = struct.unpack_from("<fff", blob, at + POS)
            if (np.isfinite([x, y, z]).all()
                    and WORLD_LO < abs(x) < WORLD_HI and WORLD_LO < abs(z) < WORLD_HI
                    and -2000.0 < y < 5000.0):
                found.append((base + at, int(words[i]), (x, y, z)))
    return found


def position_at(blobs, address):
    for base, blob in blobs.items():
        off = address - base
        if 0 <= off and off + POS + 12 <= len(blob):
            return struct.unpack_from("<fff", blob, off + POS)
    return None


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    _mn, mod_base, mod_size = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))
    private = [r for r in regions(handle) if r[2] == 0x20000 and r[1] <= 256 * 1024 * 1024]

    print("snapshot 1...")
    first = snapshot(handle, private)
    objects = candidates(first, mod_base, mod_size)
    print(f"  {len(objects)} world objects across {len(set(v for _a, v, _p in objects))} classes")
    del first

    print(f"waiting {GAP:.0f}s...")
    time.sleep(GAP)
    print("snapshot 2...")
    second = snapshot(handle, private)

    movers = []
    for address, vtable, (x, y, z) in objects:
        now = position_at(second, address)
        if now is None:
            continue
        step = ((now[0] - x) ** 2 + (now[2] - z) ** 2) ** 0.5
        if MOVED_LO <= step <= MOVED_HI:
            movers.append((address, vtable, (x, y, z), now, step))

    print(f"\n{len(movers)} objects moved between snapshots")
    by_class = defaultdict(list)
    for address, vtable, was, now, step in movers:
        by_class[vtable].append((address, now, step))

    print(f"\n{'vtable(+mod)':>14} {'movers':>7} {'total':>7}  median step")
    totals = Counter(v for _a, v, _p in objects)
    for vtable, group in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
        steps = sorted(s for _a, _n, s in group)
        print(f"  +0x{vtable - mod_base:08X} {len(group):7} {totals[vtable]:7}  "
              f"{steps[len(steps) // 2]:.2f}")

    if not by_class:
        print("nothing moved - is the character in a zone with monsters?")
        k32.CloseHandle(handle)
        return 1

    best_vtable, group = max(by_class.items(), key=lambda kv: len(kv[1]))
    print(f"\n=== mover class +0x{best_vtable - mod_base:08X}: {len(group)} moving ===")
    bodies = []
    for address, now, _step in group:
        for base, blob in second.items():
            off = address - base
            if 0 <= off and off + STRIDE <= len(blob):
                bodies.append((address, blob[off:off + STRIDE]))
                break
    for address, body in bodies[:8]:
        x, y, z = struct.unpack_from("<fff", body, POS)
        print(f"  0x{address:012X}  ({x:8.1f}, {y:7.1f}, {z:8.1f})")

    print(f"\n{'off':>6} {'distinct':>8}  interpretation")
    for off in range(0, STRIDE, 4):
        values = [struct.unpack_from("<I", b, off)[0] for _a, b in bodies]
        distinct = len(set(values))
        if distinct <= 1:
            continue
        floats = [struct.unpack("<f", struct.pack("<I", v))[0] for v in values]
        finite = [f for f in floats if np.isfinite(f)]
        notes = []
        if distinct == len(values):
            notes.append("unique per mover (id?)")
        elif distinct <= 16:
            notes.append(f"{distinct} values {Counter(values).most_common(5)}")
        else:
            notes.append(f"{distinct} distinct")
        if finite and all(WORLD_LO < abs(f) < WORLD_HI for f in finite):
            notes.append(f"world float ~{np.mean(finite):.0f}")
        elif finite and all(0.0 <= f <= 1.0 for f in finite) and any(0 < f < 1 for f in finite):
            notes.append("float 0..1 (hp fraction?)")
        small = [v for v in values if 0 < v <= 250]
        if len(small) >= len(values) * 0.7:
            notes.append(f"small int {Counter(small).most_common(5)}")
        print(f"  +0x{off:03X} {distinct:8}  " + " | ".join(notes))

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
