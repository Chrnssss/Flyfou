"""Is the entity table complete, and can any client see a monster right now?

Two loose ends before the layout can be trusted.

First, completeness. Everything so far reads a fixed static array at
Neuz.exe+0x00E07CB0. If that array is a partial view - a render list, a nearby
cache - the bot would silently ignore monsters that are really there, which is
the worst kind of bug because it looks like an empty spot. Comparing the table's
movers against a full heap sweep for the same vtable settles it.

Second, the type-18 entities turned out to be pets, not monsters: the target
window showed "Niveau S / Special FOR +75" and a heart. So nothing observed so
far is actually a monster, and monster level, max HP and aggro state are still
unmapped. Checking all four clients says whether any of them is somewhere that
would let me map them.

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
MOVER_VT = 0x0096D620
TYPE, ID, POS, LEVEL, HP, MP, NAME = 0x08, 0x28, 0x60, 0x718, 0x738, 0x73C, 0x1EC8
HEAD = 0x800

CLIENTS = [(13140, "Mynuthbp"), (13960, "Mynuthfss"), (14664, "Mynuthkng"), (17356, "Mynuthyj")]


def ascii_at(blob, start, limit=24):
    out = []
    for byte in blob[start:start + limit]:
        if 32 <= byte < 127:
            out.append(chr(byte))
        else:
            break
    return "".join(out)


def table_movers(handle, mod_base):
    table = read(handle, mod_base + TABLE_LO, TABLE_HI - TABLE_LO)
    found = set()
    for address in dict.fromkeys(p for p in np.frombuffer(table, dtype=np.uint64).tolist() if p):
        head = read(handle, address, 16)
        if head and struct.unpack_from("<Q", head, 0)[0] == mod_base + MOVER_VT:
            found.add(address)
    return found


def heap_movers(handle, mod_base):
    """Every 16-aligned qword in private memory equal to the mover vtable."""
    found = set()
    for base, size, kind, _protect in regions(handle):
        if kind != 0x20000 or size > 256 * 1024 * 1024:
            continue
        blob = read(handle, base, size)
        if not blob or len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        for i in np.nonzero(words == np.uint64(mod_base + MOVER_VT))[0].tolist():
            at = base + i * 8
            if at % 16 == 0:
                found.add(at)
    return found


def main():
    deep = "--deep" in sys.argv

    for pid, who in CLIENTS:
        handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            print(f"{who}: OpenProcess failed: {ctypes.get_last_error()}")
            continue
        _mn, mod_base, _ms = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))

        listed = table_movers(handle, mod_base)
        bodies = {}
        for address in listed:
            body = read(handle, address, NAME + 32)
            if body:
                bodies[address] = body

        print(f"\n=== {who} pid {pid}: {len(listed)} movers in the table ===")
        by_type = defaultdict(list)
        for address, body in bodies.items():
            by_type[struct.unpack_from("<I", body, TYPE)[0]].append((address, body))
        for kind, group in sorted(by_type.items()):
            names = Counter(ascii_at(b, NAME) for _a, b in group)
            levels = sorted(struct.unpack_from("<I", b, LEVEL)[0] for _a, b in group)
            print(f"  type {kind:3}: {len(group):3} movers, levels {levels[0]}..{levels[-1]}")
            for text, n in names.most_common(6):
                print(f"      {n:3}  {text!r}")

        player = next((a for a, b in bodies.items() if ascii_at(b, NAME) == who), None)
        if player:
            x, y, z = struct.unpack_from("<fff", bodies[player], POS)
            print(f"  player at ({x:.0f}, {y:.0f}, {z:.0f})")

        if deep:
            everything = heap_movers(handle, mod_base)
            missing = everything - listed
            print(f"  heap sweep: {len(everything)} mover objects, "
                  f"{len(missing)} not in the table")
            for address in sorted(missing)[:10]:
                body = read(handle, address, NAME + 32)
                if not body:
                    continue
                x, y, z = struct.unpack_from("<fff", body, POS)
                print(f"    0x{address:012X} type={struct.unpack_from('<I', body, TYPE)[0]} "
                      f"{ascii_at(body, NAME)!r} ({x:.0f}, {y:.0f}, {z:.0f})")

        k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
