"""The entity struct, read off live objects only.

_mem6 had two faults worth naming, because the finder must not repeat them:

  * it read 0x800 per object when the stride is 0x320, so every "field" past
    +0x320 was the next object's - that is where the phantom periodicity at
    +0x328 and +0x648 came from;
  * it analysed all 7782 instances, but the client keeps a pool and most slots
    are inert, so 7748-of-7757 identical zeros drowned every real field.

So: fix the stride, keep only objects whose +0x60 vector is a believable world
position, and analyse those. Then look for a static pointer in Neuz.exe's own
data pointing at one of them - that is the local player, and its module offset
is the anchor a later run can resolve without scanning at all.

Read-only.
"""
import ctypes
import struct
import sys
from collections import Counter

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions
from _mem3 import modules

STRIDE = 0x320
POS = 0x60
WANT_VTABLE = 0x0096EA18
WORLD_LO, WORLD_HI = 50.0, 20000.0


def live_entities(handle, private, vtable_value):
    found = []
    for base, size, _m, _p in private:
        blob = read(handle, base, size)
        if not blob or len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        for i in np.nonzero(words == np.uint64(vtable_value))[0].tolist():
            at = i * 8
            if (base + at) % 16 or at + STRIDE > len(blob):
                continue
            body = blob[at:at + STRIDE]
            x, y, z = struct.unpack_from("<fff", body, POS)
            if (np.isfinite([x, y, z]).all()
                    and WORLD_LO < abs(x) < WORLD_HI and WORLD_LO < abs(z) < WORLD_HI
                    and -2000.0 < y < 5000.0):
                found.append((base + at, body))
    return found


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    name = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1

    _mn, mod_base, mod_size = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))
    found_regions = regions(handle)
    private = [r for r in found_regions if r[2] == 0x20000 and r[1] <= 256 * 1024 * 1024]

    entities = live_entities(handle, private, mod_base + WANT_VTABLE)
    print(f"{len(entities)} live entities (position sane at +0x{POS:X}, stride 0x{STRIDE:X})")
    if not entities:
        return 1

    xs = [struct.unpack_from("<f", b, POS)[0] for _a, b in entities]
    zs = [struct.unpack_from("<f", b, POS + 8)[0] for _a, b in entities]
    print(f"x range {min(xs):.0f}..{max(xs):.0f}   z range {min(zs):.0f}..{max(zs):.0f}")

    # Which slots actually vary between live entities?
    print(f"\n{'off':>6} {'distinct':>8}  interpretation")
    for off in range(0, STRIDE, 4):
        values = [struct.unpack_from("<I", b, off)[0] for _a, b in entities]
        distinct = len(set(values))
        if distinct <= 1:
            continue
        floats = [struct.unpack("<f", struct.pack("<I", v))[0] for v in values]
        finite = [f for f in floats if np.isfinite(f)]
        notes = []
        if distinct == len(values):
            notes.append("unique per entity (id?)")
        elif distinct <= 16:
            notes.append(f"{distinct} values {Counter(values).most_common(5)}")
        else:
            notes.append(f"{distinct} distinct")
        if finite and all(WORLD_LO < abs(f) < WORLD_HI for f in finite):
            notes.append(f"world float ~{np.mean(finite):.0f}")
        elif finite and all(0.0 <= f <= 1.0 for f in finite) and any(0 < f < 1 for f in finite):
            notes.append("float 0..1")
        small = [v for v in values if 0 < v <= 250]
        if len(small) >= len(values) * 0.7:
            notes.append(f"small int {Counter(small).most_common(5)}")
        print(f"  +0x{off:03X} {distinct:8}  " + " | ".join(notes))

    # A static pointer to one of these is the local player.
    addresses = {a for a, _b in entities}
    print("\nstatic pointers into the entity set, inside Neuz.exe:")
    image = [r for r in found_regions
             if mod_base <= r[0] < mod_base + mod_size and r[3] in (0x04, 0x08, 0x40, 0x80)]
    hits = 0
    for base, size, _m, _p in image:
        blob = read(handle, base, size)
        if not blob or len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        for i in np.nonzero(np.isin(words, np.array(sorted(addresses), dtype=np.uint64)))[0].tolist():
            target = int(words[i])
            body = next(b for a, b in entities if a == target)
            x, y, z = struct.unpack_from("<fff", body, POS)
            print(f"  Neuz.exe+0x{base + i * 8 - mod_base:08X} -> 0x{target:012X} "
                  f"at ({x:.1f}, {y:.1f}, {z:.1f})")
            hits += 1
    if not hits:
        print("  none - the player is reached through a chain, not a direct global")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
