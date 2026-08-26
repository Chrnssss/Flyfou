"""Map the mover struct: name, HP, level.

_mem11 misread which class matters. The player came back as vt+0x0096D620 - the
71-entity class, not the 550-entity one whose fields I dumped. And that dump was
reading past the end anyway: +0x328 repeated the type byte from +0x008, so
vt+0x0096EA18 has stride 0x320 and everything after it belonged to the next
object. That class is scenery; vt+0x0096D620 is CMover.

Two levers this time. Four game clients are logged in at once and their four
characters stand within three units of each other, so several movers here should
be player characters - searching every mover body for "Mynuth" finds the name
offset directly, and finding it at the same offset in more than one entity
confirms it rather than guessing. Second, current and max HP sit near each other
with cur <= max always, so scanning nearby offset pairs under that constraint
narrows HP without needing to fight anything.

Read-only.
"""
import ctypes
import struct
import sys
from collections import Counter, defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read
from _mem3 import modules

TABLE_LO, TABLE_HI = 0x00E07CB0, 0x00E0B6C0
MOVER_VT = 0x0096D620
PROP_VT = 0x0096EA18
SPAN = 0x2000
POS, ID = 0x60, 0x28
NEAR = 0x40


def ascii_at(blob, start=0, limit=32):
    out = []
    for byte in blob[start:start + limit]:
        if 32 <= byte < 127:
            out.append(chr(byte))
        else:
            break
    return "".join(out)


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    stem = sys.argv[2] if len(sys.argv) > 2 else "Mynuth"

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    _mn, mod_base, mod_size = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))

    table = read(handle, mod_base + TABLE_LO, TABLE_HI - TABLE_LO)
    pointers = [p for p in np.frombuffer(table, dtype=np.uint64).tolist() if p]

    movers = {}
    for address in dict.fromkeys(pointers):
        body = read(handle, address, SPAN)
        if not body or len(body) < SPAN:
            continue
        if struct.unpack_from("<Q", body, 0)[0] == mod_base + MOVER_VT:
            movers[address] = body
    print(f"{len(movers)} movers (vt+0x{MOVER_VT:08X})")
    if not movers:
        return 1

    bodies = list(movers.values())
    needle = stem.encode("utf-8")

    # 1. Names, inline. Four clients are logged in, so several should hit.
    print(f"\ninline '{stem}' in mover bodies:")
    inline = defaultdict(list)
    for address, body in movers.items():
        at = body.find(needle)
        while at >= 0:
            inline[at].append((address, ascii_at(body, at)))
            at = body.find(needle, at + 1)
    for off, hits in sorted(inline.items()):
        names = ", ".join(f"{n}@0x{a:X}" for a, n in hits[:6])
        print(f"  +0x{off:04X}  {len(hits):3} movers  {names}")
    if not inline:
        print("  none")

    # 2. Names behind a pointer.
    print(f"\n'{stem}' behind a pointer out of a mover:")
    seen = {}
    behind = defaultdict(list)
    for address, body in movers.items():
        for off in range(0, SPAN, 8):
            value = struct.unpack_from("<Q", body, off)[0]
            if not (0x10000 < value < 0x7FFFFFFFFFFF):
                continue
            if value not in seen:
                seen[value] = read(handle, value, 48) or b""
            blob = seen[value]
            if needle in blob[:32]:
                behind[off].append((address, ascii_at(blob, blob.find(needle))))
    for off, hits in sorted(behind.items()):
        names = ", ".join(f"{n}@0x{a:X}" for a, n in hits[:6])
        print(f"  +0x{off:04X}  {len(hits):3} movers  {names}")
    if not behind:
        print("  none")

    # 3. Where does the next allocation start? Bounds the real struct size.
    print("\nnext vtable inside the body (struct size bound):")
    ends = Counter()
    for body in bodies:
        words = np.frombuffer(body, dtype=np.uint64)
        hit = np.nonzero((words == np.uint64(mod_base + MOVER_VT))
                         | (words == np.uint64(mod_base + PROP_VT)))[0]
        after = [int(i) * 8 for i in hit.tolist() if i]
        if after:
            ends[after[0]] += 1
    for off, n in ends.most_common(6):
        print(f"  +0x{off:04X}  {n} movers")
    if not ends:
        print(f"  none within 0x{SPAN:X} - movers are larger than that or spaced out")

    # 4. Categorical fields across movers.
    print(f"\ncategorical offsets across {len(bodies)} movers")
    for off in range(0, SPAN, 4):
        values = [struct.unpack_from("<I", b, off)[0] for b in bodies]
        distinct = len(set(values))
        if not 2 <= distinct <= 20:
            continue
        common = Counter(values).most_common(6)
        tag = ""
        if all(0 < v <= 200 for v, _n in common):
            tag = "  <- level?"
        elif all(50 <= v <= 5_000_000 for v, _n in common):
            tag = "  <- hp/exp?"
        print(f"  +0x{off:04X} {distinct:3}  {common}{tag}")

    # 5. cur/max pairs: cur <= max everywhere, max shared between same-kind movers.
    print("\ncurrent/max candidates (cur <= max for every mover, max categorical)")
    columns = {}
    for off in range(0, SPAN, 4):
        columns[off] = [struct.unpack_from("<I", b, off)[0] for b in bodies]
    for cur_off in range(0, SPAN, 4):
        cur = columns[cur_off]
        if not all(0 < v <= 10_000_000 for v in cur) or len(set(cur)) < 2:
            continue
        for max_off in range(max(0, cur_off - NEAR), min(SPAN, cur_off + NEAR), 4):
            if max_off == cur_off:
                continue
            mx = columns[max_off]
            if len(set(mx)) > len(set(cur)) or len(set(mx)) < 2:
                continue
            if not all(0 < c <= m <= 10_000_000 for c, m in zip(cur, mx)):
                continue
            fractions = [c / m for c, m in zip(cur, mx)]
            print(f"  cur +0x{cur_off:04X}  max +0x{max_off:04X}  "
                  f"maxes {Counter(mx).most_common(4)}  "
                  f"min frac {min(fractions):.2f}")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
