"""Which "movers" does the game actually track, and how do we reach them again?

_mem8 found 337 moving objects of one class, but packed into a 25x25 unit patch
- far too dense for monsters, and the shape of a skeleton's bones or a particle
system rather than a spawn. Density alone can't settle it, so use ownership:
the object manager holds real entities, and a real entity is therefore reachable
from a static in Neuz.exe. Bones hang off a model instance and are not.

Walking backwards answers both questions at once - which objects are tracked,
and the static chain to re-find them on a later run without scanning 1.6 GB.

Read-only.
"""
import ctypes
import struct
import sys
from collections import defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions
from _mem3 import modules

STRIDE = 0x320
POS = 0x60
DEPTH = 3
WORLD_LO, WORLD_HI = 50.0, 20000.0


def load(handle, wanted):
    out = {}
    for base, size, _m, _p in wanted:
        blob = read(handle, base, size)
        if blob:
            out[base] = blob
    return out


def holders_of(blobs, targets):
    """address -> the target value it holds, for every qword pointing at a target."""
    wanted = np.array(sorted(targets), dtype=np.uint64)
    found = {}
    for base, blob in blobs.items():
        if len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        for i in np.nonzero(np.isin(words, wanted))[0].tolist():
            found[base + i * 8] = int(words[i])
    return found


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    _mn, mod_base, mod_size = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))
    all_regions = regions(handle)
    private = [r for r in all_regions if r[2] == 0x20000 and r[1] <= 256 * 1024 * 1024]
    image = [r for r in all_regions if mod_base <= r[0] < mod_base + mod_size]

    print("reading memory...")
    blobs = load(handle, private + image)
    print(f"  {len(blobs)} regions, {sum(len(b) for b in blobs.values()) / 1024 / 1024:.0f} MB")

    # Every object with a believable world position, whatever its class.
    objects = {}
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
                objects[base + at] = (int(words[i]), x, y, z)
    print(f"  {len(objects)} positioned objects\n")

    def in_module(address):
        return mod_base <= address < mod_base + mod_size

    targets = set(objects)
    parents = []
    for depth in range(1, DEPTH + 1):
        found = holders_of(blobs, targets)
        statics = {a: v for a, v in found.items() if in_module(a)}
        print(f"depth {depth}: {len(found)} holders, {len(statics)} of them static in Neuz.exe")
        parents.append(found)
        if statics:
            print(f"\n=== statics reaching a positioned object at depth {depth} ===")
            reached = defaultdict(list)
            for address, value in statics.items():
                reached[value].append(address)
            for value, addresses in list(reached.items())[:15]:
                chain = " -> ".join(f"Neuz.exe+0x{a - mod_base:08X}" for a in addresses[:3])
                if depth == 1 and value in objects:
                    vtable, x, y, z = objects[value]
                    print(f"  {chain}  =>  0x{value:012X} "
                          f"vt+0x{vtable - mod_base:08X} ({x:.1f}, {y:.1f}, {z:.1f})")
                else:
                    print(f"  {chain}  =>  0x{value:012X}")
            break
        targets = set(found)
        if len(targets) > 4_000_000:
            print("  fan-out too wide to keep walking")
            break

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
