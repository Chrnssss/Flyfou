"""Pin down HP, level, species and name on real entities.

Confirmed by _mem10, off the object manager's table rather than a heap scan:
  entity table  Neuz.exe+0x00E07CB0 .. +0x00E0B6B8   (static array of pointers)
  +0x028  entity id, unique
  +0x060  position vec3 (x, y, z)
  +0x008  type, 18 for most and 2 for a minority

Two corrections to _mem10 first. Its player-detection counted references inside
the entity table itself - which sits in the static window it scanned - so an
entity listed 58 times scored as the player. And its field dump printed every
varying offset, which buried the interesting ones.

Categorical fields are what's wanted now: species, level and max-HP are shared
by monsters of a kind, so they show up as offsets with a handful of distinct
values that correlate with each other. Printing those as a grid makes the
struct readable in one pass.

Read-only.
"""
import ctypes
import struct
import sys
from collections import Counter, defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions
from _mem3 import modules

TABLE_LO, TABLE_HI = 0x00E07CB0, 0x00E0B6C0
SPAN = 0x600
POS, ID, TYPE = 0x60, 0x28, 0x08


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    name = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    _mn, mod_base, mod_size = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))

    table = read(handle, mod_base + TABLE_LO, TABLE_HI - TABLE_LO)
    pointers = [p for p in np.frombuffer(table, dtype=np.uint64).tolist() if p]

    entities = {}
    for address in dict.fromkeys(pointers):
        body = read(handle, address, SPAN)
        if not body or len(body) < SPAN:
            continue
        vtable = struct.unpack_from("<Q", body, 0)[0]
        if not (mod_base <= vtable < mod_base + mod_size):
            continue
        x, y, z = struct.unpack_from("<fff", body, POS)
        if not np.isfinite([x, y, z]).all():
            continue
        entities[address] = body
    print(f"{len(entities)} entities from the table")

    groups = defaultdict(list)
    for address, body in entities.items():
        groups[struct.unpack_from("<Q", body, 0)[0]].append((address, body))
    for vtable, group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        types = Counter(struct.unpack_from("<I", b, TYPE)[0] for _a, b in group)
        print(f"  vt+0x{vtable - mod_base:08X}  {len(group):4}  type field: {types.most_common(4)}")

    # The player, without counting the entity table's own slots.
    lo, hi = mod_base + 0x00C00000, mod_base + mod_size
    refs = Counter()
    address = lo
    while address < hi:
        chunk = read(handle, address, min(0x100000, hi - address))
        if chunk:
            words = np.frombuffer(chunk[:len(chunk) // 8 * 8], dtype=np.uint64)
            wanted = np.array(sorted(entities), dtype=np.uint64)
            for i in np.nonzero(np.isin(words, wanted))[0].tolist():
                at = address + i * 8
                if not (mod_base + TABLE_LO <= at < mod_base + TABLE_HI):
                    refs[int(words[i])] += 1
        address += 0x100000
    print("\nplayer candidates (statics outside the table):")
    for candidate, n in refs.most_common(5):
        body = entities[candidate]
        x, y, z = struct.unpack_from("<fff", body, POS)
        vt = struct.unpack_from("<Q", body, 0)[0]
        eid = struct.unpack_from("<I", body, ID)[0]
        print(f"  0x{candidate:012X} x{n}  vt+0x{vt - mod_base:08X}  id={eid}  "
              f"({x:.1f}, {y:.1f}, {z:.1f})")

    player = refs.most_common(1)[0][0] if refs else None

    # Where is the name? Follow every pointer in the player's body.
    if player:
        body = entities[player]
        print(f"\nchasing pointers out of the player for '{name}':")
        for off in range(0, SPAN - 8, 8):
            value = struct.unpack_from("<Q", body, off)[0]
            if value < 0x10000 or value > 0x7FFFFFFFFFFF:
                continue
            blob = read(handle, value, 64)
            if blob and blob.startswith(name.encode("utf-8")):
                print(f"  +0x{off:03X} -> 0x{value:012X} = {blob[:16]!r}")
        at = body.find(name.encode("utf-8"))
        print(f"  inline: " + (f"+0x{at:X}" if at >= 0 else "not present in first 0x600"))

    # Categorical fields: few distinct values, shared between entities of a kind.
    monsters = [b for _a, b in max(groups.values(), key=len)]
    print(f"\ncategorical offsets across {len(monsters)} entities of the main class")
    print(f"{'off':>6} {'n':>3}  most common values")
    for off in range(0, SPAN, 4):
        values = [struct.unpack_from("<I", b, off)[0] for b in monsters]
        distinct = len(set(values))
        if not 2 <= distinct <= 40:
            continue
        common = Counter(values).most_common(6)
        if all(v <= 300 for v, _n in common):
            tag = "  <- level/kind?"
        elif all(100 <= v <= 10_000_000 for v, _n in common):
            tag = "  <- hp/exp?"
        else:
            tag = ""
        print(f"  +0x{off:03X} {distinct:3}  {common}{tag}")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
