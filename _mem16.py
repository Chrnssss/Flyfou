"""Find max HP, and check the confirmed fields hold up on remote movers.

Confirmed by _mem15 across four clients of the same build (link stamp 0x6A8E2800):

  +0x0718  level      161 / 174 / 169 / 189
  +0x0738  current HP
  +0x073C  current MP
  +0x1EC8  name, inline ascii
  +0x0028  entity id      +0x0060  position      +0x0008  type

Max HP is the gap. Searching Mynuthbp's body for 24656 returned exactly one hit -
current HP - even though the character is at full health, so max is not a second
int beside it. In Flyff it is derived from stamina, level and job, so it may
simply never be stored; but before accepting that, look for it as a float and
behind the player's pointers.

The other half of the job is confirming the fields describe *other* movers too,
not just the local player. A bot that can only read its own level cannot filter
monsters by level. Printing the whole roster with name, level and HP shows in one
table whether the offsets generalise.

Read-only.
"""
import ctypes
import struct
import sys
from collections import Counter

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read
from _mem3 import modules

TABLE_LO, TABLE_HI = 0x00E07CB0, 0x00E0B6C0
MOVER_VT = 0x0096D620
SPAN = 0x8000
TYPE, ID, POS, LEVEL, HP, MP, NAME = 0x08, 0x28, 0x60, 0x718, 0x738, 0x73C, 0x1EC8


def ascii_at(blob, start, limit=24):
    out = []
    for byte in blob[start:start + limit]:
        if 32 <= byte < 127:
            out.append(chr(byte))
        else:
            break
    return "".join(out)


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    who = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    _mn, mod_base, _ms = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))

    table = read(handle, mod_base + TABLE_LO, TABLE_HI - TABLE_LO)
    movers = {}
    for address in dict.fromkeys(p for p in np.frombuffer(table, dtype=np.uint64).tolist() if p):
        head = read(handle, address, 16)
        if not head or struct.unpack_from("<Q", head, 0)[0] != mod_base + MOVER_VT:
            continue
        body = read(handle, address, SPAN)
        if body and len(body) == SPAN:
            movers[address] = body
    print(f"{len(movers)} movers, body read as 0x{SPAN:X} bytes")

    player = next((a for a, b in movers.items() if ascii_at(b, NAME) == who), None)
    if player is None:
        print(f"'{who}' not found")
        return 1
    body = movers[player]
    hp = struct.unpack_from("<I", body, HP)[0]
    print(f"player 0x{player:012X}  level {struct.unpack_from('<I', body, LEVEL)[0]}  "
          f"hp {hp}  mp {struct.unpack_from('<I', body, MP)[0]}")

    # 1. Max HP as an int or a float, anywhere in a much larger body.
    print(f"\ncopies of {hp} in the player body (0x{SPAN:X}):")
    for off in range(0, SPAN - 4, 4):
        if struct.unpack_from("<I", body, off)[0] == hp:
            print(f"  +0x{off:04X}  int")
    for off in range(0, SPAN - 4, 4):
        value = struct.unpack_from("<f", body, off)[0]
        if np.isfinite(value) and abs(value - hp) < 0.5:
            print(f"  +0x{off:04X}  float {value}")

    # 2. Max HP behind one of the player's pointers.
    print(f"\n{hp} behind a pointer out of the player:")
    hits = 0
    for off in range(0, SPAN, 8):
        value = struct.unpack_from("<Q", body, off)[0]
        if not (0x10000 < value < 0x7FFFFFFFFFFF):
            continue
        blob = read(handle, value, 0x200)
        if not blob:
            continue
        words = np.frombuffer(blob[:len(blob) // 4 * 4], dtype=np.uint32)
        for i in np.nonzero(words == np.uint32(hp))[0].tolist():
            print(f"  +0x{off:04X} -> 0x{value:012X} +0x{i * 4:03X}")
            hits += 1
            break
    if not hits:
        print("  none - max HP is computed from stats, not stored")

    # 3. The stat block around the confirmed fields.
    print("\nstat block, player vs three monsters")
    smilodon = [b for a, b in movers.items()
                if ascii_at(b, NAME) == "Smilodon Bestial" and a != player][:3]
    rows = [("player", body)] + [(f"mob{i}", b) for i, b in enumerate(smilodon)]
    print("   off    " + "".join(f"{label:>12}" for label, _b in rows))
    for off in range(0x6E0, 0x7C0, 4):
        values = [struct.unpack_from("<I", b, off)[0] for _l, b in rows]
        if all(v == 0 for v in values):
            continue
        mark = {LEVEL: " level", HP: " hp", MP: " mp"}.get(off, "")
        print(f"  +0x{off:04X}" + "".join(f"{v:>12}" for v in values) + mark)

    # 4. Do the offsets mean anything on movers that are not the local player?
    print(f"\nroster by these offsets ({len(movers)} movers)")
    print(f"  {'name':<22}{'type':>5}{'level':>7}{'hp':>9}{'mp':>8}   position")
    roster = sorted(movers.items(), key=lambda kv: -struct.unpack_from("<I", kv[1], LEVEL)[0])
    for address, other in roster[:26]:
        x, y, z = struct.unpack_from("<fff", other, POS)
        print(f"  {ascii_at(other, NAME)[:21]:<22}"
              f"{struct.unpack_from('<I', other, TYPE)[0]:>5}"
              f"{struct.unpack_from('<I', other, LEVEL)[0]:>7}"
              f"{struct.unpack_from('<I', other, HP)[0]:>9}"
              f"{struct.unpack_from('<I', other, MP)[0]:>8}"
              f"   ({x:.0f}, {y:.0f}, {z:.0f})")

    types = Counter(struct.unpack_from("<I", b, TYPE)[0] for b in movers.values())
    print(f"\ntype field: {types.most_common()}")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
