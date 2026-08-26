"""Find the container that holds *every* mover, not just some.

_mem17 broke the assumption everything since _mem9 rested on. The static array at
Neuz.exe+0x00E07CB0 lists 52 movers in Mynuthyj's client while a heap sweep for
the same vtable finds 133, including dozens of live Keroberos standing around the
character. A bot enumerating that array would walk past most of the monsters in
front of it and report an empty spot, so the array is a partial view - a render
or nearby list - and the real container is still unfound.

Sweeping the heap every tick is not an answer either: it costs seconds. So find
what owns the movers. Every address in the process that points at a live mover is
collected, then grouped by the allocation it sits in; whatever holds nearly all of
them is the container, and its internal spacing says whether it is a flat array,
a bucket table or a list.

Stale pool slots would inflate the sweep and invent a problem that is not there,
so a mover only counts as live if its name, position and level are all sane.

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
TYPE, ID, POS, LEVEL, NAME = 0x08, 0x28, 0x60, 0x718, 0x1EC8
NEED = NAME + 32


def ascii_at(blob, start, limit=24):
    out = []
    for byte in blob[start:start + limit]:
        if 32 <= byte < 127:
            out.append(chr(byte))
        else:
            break
    return "".join(out)


def live(body):
    x, y, z = struct.unpack_from("<fff", body, POS)
    if not np.isfinite([x, y, z]).all() or not (0.0 < abs(x) < 20000.0 and 0.0 < abs(z) < 20000.0):
        return False
    if not 0 < struct.unpack_from("<I", body, LEVEL)[0] <= 250:
        return False
    return len(ascii_at(body, NAME)) >= 1


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 17356

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    _mn, mod_base, mod_size = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))
    every = regions(handle)

    print("sweeping the heap for movers...")
    blobs = {}
    for base, size, kind, _protect in every:
        if size > 256 * 1024 * 1024:
            continue
        blob = read(handle, base, size)
        if blob:
            blobs[base] = (blob, kind)

    candidates = set()
    for base, (blob, kind) in blobs.items():
        if kind != 0x20000 or len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        for i in np.nonzero(words == np.uint64(mod_base + MOVER_VT))[0].tolist():
            at = base + i * 8
            if at % 16 == 0:
                candidates.add(at)

    movers = {}
    for address in candidates:
        body = read(handle, address, NEED)
        if body and len(body) == NEED and live(body):
            movers[address] = body
    print(f"  {len(candidates)} objects with the mover vtable, {len(movers)} of them live")

    table = read(handle, mod_base + TABLE_LO, TABLE_HI - TABLE_LO)
    listed = {p for p in np.frombuffer(table, dtype=np.uint64).tolist() if p} & set(movers)
    print(f"  {len(listed)} of those appear in the static array, {len(movers) - len(listed)} do not")

    names = Counter(ascii_at(b, NAME) for b in movers.values())
    print("  live movers by name: " + ", ".join(f"{t!r} x{n}" for t, n in names.most_common(5)))

    # Who points at them?
    wanted = np.array(sorted(movers), dtype=np.uint64)
    holders = defaultdict(set)
    for base, (blob, _kind) in blobs.items():
        if len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        for i in np.nonzero(np.isin(words, wanted))[0].tolist():
            holders[base].add((base + i * 8, int(words[i])))

    print(f"\nallocations holding pointers to live movers ({len(movers)} to find):")
    ranked = sorted(holders.items(), key=lambda kv: -len({v for _a, v in kv[1]}))[:8]
    for base, entries in ranked:
        distinct = {v for _a, v in entries}
        slots = sorted(a for a, _v in entries)
        gaps = Counter(b - a for a, b in zip(slots, slots[1:]))
        where = f"Neuz.exe+0x{base - mod_base:08X}" if mod_base <= base < mod_base + mod_size \
            else f"heap 0x{base:012X}"
        span = f"0x{slots[0] - base:X}..0x{slots[-1] - base:X}"
        print(f"  {where}  {len(distinct):4} movers  {len(slots)} slots  at +{span}  "
              f"gaps {gaps.most_common(4)}")

    if ranked:
        base, entries = ranked[0]
        got = {v for _a, v in entries}
        print(f"\nbest container misses {len(set(movers) - got)} live movers")
        for address in sorted(set(movers) - got)[:8]:
            body = movers[address]
            x, y, z = struct.unpack_from("<fff", body, POS)
            print(f"    0x{address:012X} type={struct.unpack_from('<I', body, TYPE)[0]} "
                  f"lvl={struct.unpack_from('<I', body, LEVEL)[0]} "
                  f"{ascii_at(body, NAME)!r} ({x:.0f}, {y:.0f}, {z:.0f})")

        blob, _kind = blobs[base]
        head = min(a for a, _v in entries) - base
        print(f"\n  first 24 qwords of that allocation from +0x{max(0, head - 0x40):X}:")
        start = max(0, head - 0x40)
        for i in range(24):
            off = start + i * 8
            if off + 8 > len(blob):
                break
            value = struct.unpack_from("<Q", blob, off)[0]
            mark = " <- mover" if value in got else ""
            print(f"    +0x{off:06X}  0x{value:016X}{mark}")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
