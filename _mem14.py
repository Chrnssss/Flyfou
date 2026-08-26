"""Pin level, HP and MP by exact value, across four clients at once.

Reading the four HUDs gave real numbers, so guessing is over:

  Mynuthbp  161  24656 HP  1516 MP
  Mynuthfss 173  28355 HP 13307 MP
  Mynuthkng 169  11100 HP   511 MP
  Mynuthyj  189  65642 HP  1181 MP

One client alone would still be weak - in a 16 KB struct some offset holds 161 by
chance. Four independent clients running the same build kill that: an offset that
carries the right level in all four is the level, and coincidences do not survive
the intersection. The four also happen to differ in every value, so nothing is
ambiguous through equality.

Mynuthyj has a Smilodon Bestial selected, so its player body should contain that
monster's entity id - which is the target field the bot needs to write.

Read-only.
"""
import ctypes
import struct
import sys
from collections import defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read
from _mem3 import modules

TABLE_LO, TABLE_HI = 0x00E07CB0, 0x00E0B6C0
MOVER_VT = 0x0096D620
SPAN = 0x4000
POS, ID, NAME = 0x60, 0x28, 0x1EC8

CLIENTS = [
    (13140, "Mynuthbp", 161, 24656, 1516),
    (13960, "Mynuthfss", 173, 28355, 13307),
    (14664, "Mynuthkng", 169, 11100, 511),
    (17356, "Mynuthyj", 189, 65642, 1181),
]


def ascii_at(blob, start, limit=24):
    out = []
    for byte in blob[start:start + limit]:
        if 32 <= byte < 127:
            out.append(chr(byte))
        else:
            break
    return "".join(out)


def snapshot(pid, who):
    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"  pid {pid}: OpenProcess failed: {ctypes.get_last_error()}")
        return None
    _mn, mod_base, mod_size = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))
    table = read(handle, mod_base + TABLE_LO, TABLE_HI - TABLE_LO)
    movers = {}
    for address in dict.fromkeys(p for p in np.frombuffer(table, dtype=np.uint64).tolist() if p):
        body = read(handle, address, SPAN)
        if body and len(body) == SPAN and struct.unpack_from("<Q", body, 0)[0] == mod_base + MOVER_VT:
            movers[address] = body
    player = next((a for a, b in movers.items() if ascii_at(b, NAME) == who), None)
    k32.CloseHandle(handle)
    if player is None:
        print(f"  pid {pid}: '{who}' not among {len(movers)} movers")
        return None
    return mod_base, movers, player


def offsets_holding(body, value):
    found = set()
    for off in range(0, SPAN - 4, 4):
        if struct.unpack_from("<I", body, off)[0] == value:
            found.add(off)
    return found


def main():
    shots = {}
    for pid, who, _lvl, _hp, _mp in CLIENTS:
        got = snapshot(pid, who)
        if got:
            shots[pid] = got
            print(f"{who:10} pid {pid}: {len(got[1])} movers, player 0x{got[2]:012X}")
    if len(shots) < 2:
        print("need at least two clients")
        return 1

    fields = {"level": 2, "hp": 3, "mp": 4}
    for label, index in fields.items():
        common = None
        for spec in CLIENTS:
            pid = spec[0]
            if pid not in shots:
                continue
            body = shots[pid][1][shots[pid][2]]
            here = offsets_holding(body, spec[index])
            common = here if common is None else (common & here)
        print(f"\n{label}: offsets holding the right value in every client")
        for off in sorted(common or ()):
            row = "  ".join(
                f"{spec[1][6:]}={struct.unpack_from('<I', shots[spec[0]][1][shots[spec[0]][2]], off)[0]}"
                for spec in CLIENTS if spec[0] in shots)
            print(f"  +0x{off:04X}  {row}")
        if not common:
            print("  none")

    # Is the level readable on other movers too, or only on the local player?
    print("\nlevel offsets that also look sane on remote movers:")
    pid, who, level, _hp, _mp = CLIENTS[0]
    if pid in shots:
        _base, movers, player = shots[pid]
        others = [b for a, b in movers.items() if a != player]
        for off in sorted(offsets_holding(movers[player], level)):
            values = [struct.unpack_from("<I", b, off)[0] for b in others]
            sane = sum(1 for v in values if 1 <= v <= 200)
            print(f"  +0x{off:04X}  {sane}/{len(values)} remote movers in 1..200  "
                  f"sample {values[:12]}")

    # Mynuthyj has something selected; its id should be inside the player body.
    print("\ntarget: entity ids of other movers found inside each player")
    for pid, who, _lvl, _hp, _mp in CLIENTS:
        if pid not in shots:
            continue
        _base, movers, player = shots[pid]
        body = movers[player]
        mine = struct.unpack_from("<I", body, ID)[0]
        ids = {struct.unpack_from("<I", b, ID)[0]: b for a, b in movers.items() if a != player}
        rows = []
        for off in range(0, SPAN - 4, 4):
            value = struct.unpack_from("<I", body, off)[0]
            if value != mine and value in ids:
                rows.append((off, value, ascii_at(ids[value], NAME)))
        print(f"  {who} (own id {mine}): " + (", ".join(
            f"+0x{o:04X}->{v} {n!r}" for o, v, n in rows[:10]) or "nothing selected"))

    # Names of everything around, so species grouping can be checked by eye.
    print("\nmovers seen by the first client:")
    _base, movers, player = shots[CLIENTS[0][0]]
    by_name = defaultdict(list)
    for address, body in movers.items():
        by_name[ascii_at(body, NAME)].append(address)
    for name, group in sorted(by_name.items(), key=lambda kv: -len(kv[1]))[:14]:
        print(f"  {len(group):3}  {name!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
