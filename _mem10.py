"""Read the entity list off the object manager, and map a real entity.

_mem9 landed it. Consecutive static slots from Neuz.exe+0x00E08970 upward each
hold a pointer to a positioned object - that is the object manager's table, not
a heap scan, so it enumerates exactly the entities the client is tracking. Two
classes appear in it, vt+0x0096EA18 and vt+0x0096D620.

One object was referenced by three separate globals while the rest had one. A
thing the client keeps three named handles to is the local player.

Analysing the table's entities instead of 8948 heap objects is what finally
makes the field map readable: these are the few hundred things actually in the
world, so HP, level and kind vary between them instead of being drowned out.

Read-only.
"""
import ctypes
import struct
import sys
from collections import Counter, defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions
from _mem3 import modules

TABLE_HINT = 0x00E08970
SPAN = 0x1000
POS = 0x60
WORLD_LO, WORLD_HI = 50.0, 20000.0


def sane(x, y, z):
    return (np.isfinite([x, y, z]).all()
            and WORLD_LO < abs(x) < WORLD_HI and WORLD_LO < abs(z) < WORLD_HI
            and -2000.0 < y < 5000.0)


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    name = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    _mn, mod_base, mod_size = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))

    def entity_at(address):
        body = read(handle, address, SPAN)
        if not body or len(body) < POS + 12:
            return None
        x, y, z = struct.unpack_from("<fff", body, POS)
        return (x, y, z, body) if sane(x, y, z) else None

    # Walk out from the hint to find the table's real extent.
    window = read(handle, mod_base + TABLE_HINT - 0x4000, 0x8000)
    slots = np.frombuffer(window, dtype=np.uint64)
    origin = mod_base + TABLE_HINT - 0x4000
    good = []
    for i, value in enumerate(slots.tolist()):
        if value and entity_at(value) is not None:
            good.append((origin + i * 8, value))
    if not good:
        print("no entities around the hint")
        return 1
    first, last = good[0][0], good[-1][0]
    print(f"table spans Neuz.exe+0x{first - mod_base:08X} .. +0x{last - mod_base:08X} "
          f"({len(good)} live slots, {(last - first) // 8 + 1} wide)")

    entities = {}
    for slot, address in good:
        got = entity_at(address)
        if got:
            entities[address] = got
    print(f"{len(entities)} distinct entities\n")

    vtables = Counter(struct.unpack_from("<Q", b, 0)[0] for _x, _y, _z, b in entities.values())
    for vtable, n in vtables.most_common():
        print(f"  vt+0x{vtable - mod_base:08X}  {n} entities")

    # The player: referenced by more than one static.
    statics = read(handle, mod_base + 0x00D00000, 0x00180000)
    words = np.frombuffer(statics, dtype=np.uint64)
    refs = Counter()
    for value in words[np.isin(words, np.array(sorted(entities), dtype=np.uint64))].tolist():
        refs[value] += 1
    print("\nentities referenced by several statics (player candidates):")
    for address, n in refs.most_common(5):
        x, y, z, body = entities[address]
        vt = struct.unpack_from("<Q", body, 0)[0]
        print(f"  0x{address:012X}  x{n}  vt+0x{vt - mod_base:08X}  ({x:.1f}, {y:.1f}, {z:.1f})")
    player = refs.most_common(1)[0][0] if refs else None

    if player:
        body = entities[player][3]
        at = body.find(name.encode("utf-8"))
        print(f"\n'{name}' inline in the player entity: "
              + (f"+0x{at:X}" if at >= 0 else "not found"))

    print(f"\n{'off':>6} {'distinct':>8}  values")
    bodies = [b for _x, _y, _z, b in entities.values()]
    for off in range(0, SPAN - 4, 4):
        values = [struct.unpack_from("<I", b, off)[0] for b in bodies]
        distinct = len(set(values))
        if distinct <= 1 or distinct > len(values) * 0.9:
            if distinct <= 1:
                continue
        floats = [struct.unpack("<f", struct.pack("<I", v))[0] for v in values]
        finite = [f for f in floats if np.isfinite(f)]
        notes = []
        if distinct == len(values):
            notes.append("unique (id?)")
        elif distinct <= 20:
            notes.append(f"{Counter(values).most_common(6)}")
        else:
            notes.append(f"{distinct} distinct")
        if finite and all(WORLD_LO < abs(f) < WORLD_HI for f in finite):
            notes.append(f"world ~{np.mean(finite):.0f}")
        levels = [v for v in values if 0 < v <= 200]
        if len(levels) >= len(values) * 0.9:
            notes.append(f"LEVEL? {sorted(set(levels))[:12]}")
        big = [v for v in values if 10 <= v <= 5_000_000]
        if len(big) >= len(values) * 0.9:
            notes.append(f"HP? min={min(big)} max={max(big)}")
        print(f"  +0x{off:03X} {distinct:8}  " + " | ".join(notes))

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
