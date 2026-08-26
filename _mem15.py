"""Why did the four-client intersection come back empty?

_mem14 asked for an offset holding the right level in all four clients and got
nothing, while +0x0718 held Mynuthbp's 161 and read 1..200 on all 55 remote
movers - which is exactly what a level field looks like. An empty intersection
with a candidate that good means one of my assumptions is wrong, not that the
field does not exist.

Three things could do it, so check all three rather than guess: the clients might
not be the same build (different offsets), the entity I matched by name might not
be the local player in every client, or a HUD figure I read off a screenshot may
have gone stale. Printing per client instead of intersecting shows which.

Read-only.
"""
import ctypes
import struct
import sys

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read
from _mem3 import modules

TABLE_LO, TABLE_HI = 0x00E07CB0, 0x00E0B6C0
MOVER_VT = 0x0096D620
SPAN = 0x4000
POS, ID, NAME = 0x60, 0x28, 0x1EC8
LEVEL_GUESS = 0x0718

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


def main():
    for pid, who, level, hp, mp in CLIENTS:
        handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            print(f"{who}: OpenProcess failed: {ctypes.get_last_error()}")
            continue
        name, mod_base, mod_size = next(
            m for m in modules(pid) if m[0].lower().startswith("neuz"))
        header = read(handle, mod_base, 0x400) or b""
        stamp = struct.unpack_from("<I", header, struct.unpack_from("<I", header, 0x3C)[0] + 8)[0]

        table = read(handle, mod_base + TABLE_LO, TABLE_HI - TABLE_LO)
        movers = {}
        for address in dict.fromkeys(p for p in np.frombuffer(table, dtype=np.uint64).tolist() if p):
            body = read(handle, address, SPAN)
            if body and len(body) == SPAN and struct.unpack_from("<Q", body, 0)[0] == mod_base + MOVER_VT:
                movers[address] = body

        print(f"\n=== {who} pid {pid} ===")
        print(f"  {name} base 0x{mod_base:012X} size 0x{mod_size:X} link stamp 0x{stamp:08X}")
        print(f"  {len(movers)} movers")

        player = next((a for a, b in movers.items() if ascii_at(b, NAME) == who), None)
        if player is None:
            print(f"  '{who}' not found among the movers")
            k32.CloseHandle(handle)
            continue
        body = movers[player]
        print(f"  player 0x{player:012X} id={struct.unpack_from('<I', body, ID)[0]} "
              f"+0x{LEVEL_GUESS:04X}={struct.unpack_from('<I', body, LEVEL_GUESS)[0]} (HUD level {level})")

        for label, value in (("level", level), ("hp", hp), ("mp", mp)):
            found = [off for off in range(0, SPAN - 4, 4)
                     if struct.unpack_from("<I", body, off)[0] == value]
            shown = ", ".join(f"+0x{o:04X}" for o in found[:14])
            print(f"  {label:5} {value:>7}: {len(found):3} hits  {shown}")

        # An inventory belongs to the local player alone - does this entity have one?
        slots = [struct.unpack_from("<I", body, off)[0] for off in range(0x0480, 0x0608, 8)]
        real = sum(1 for v in slots if v != 0xFFFFFFFF)
        print(f"  inventory-like slots filled: {real}/{len(slots)}")

        k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
