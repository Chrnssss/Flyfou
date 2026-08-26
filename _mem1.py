"""Which of the 56 name hits is the player object?

Motion would settle it, but that needs the user at the keyboard, and there is a
passive test that is stronger anyway: the live player object is referenced from
the object manager, the render list and the targeting code, while a name sitting
in a chat buffer or a UI cache is referenced by almost nothing.

So: count pointers landing just before each hit. The count picks the struct out,
and the *delta* those pointers share reveals where the name sits inside it -
which is the offset the finder actually needs.

Read-only.
"""
import ctypes
import sys
from collections import Counter, defaultdict
import struct

import numpy as np

from _mem0 import MBI, PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions

BACK = 0x1200  # how far before a name a struct base might plausibly start
FWD = 0x40


def name_hits(handle, found, needle):
    hits = []
    for base, size, mtype, _protect in found:
        if mtype != 0x20000 or size > 256 * 1024 * 1024:
            continue
        blob = read(handle, base, size)
        if not blob:
            continue
        start = 0
        while True:
            at = blob.find(needle, start)
            if at < 0:
                break
            # A name is a name, not a substring of a longer word.
            after = blob[at + len(needle):at + len(needle) + 1]
            if after in (b"\x00", b""):
                hits.append(base + at)
            start = at + 1
    return hits


def pointer_census(handle, found, hits):
    """For every candidate, which pointers land in the window before it."""
    lo = min(hits) - BACK
    hi = max(hits) + FWD
    targets = np.array(sorted(hits), dtype=np.uint64)
    referenced = defaultdict(list)

    for base, size, mtype, _protect in found:
        if size > 256 * 1024 * 1024:
            continue
        blob = read(handle, base, size)
        if not blob or len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        inside = words[(words >= lo) & (words <= hi)]
        if not inside.size:
            continue
        # Which candidate each pointer belongs to: the next hit at or after it.
        idx = np.searchsorted(targets, inside, side="left")
        idx[idx >= targets.size] = targets.size - 1
        for value, i in zip(inside.tolist(), idx.tolist()):
            for candidate in (targets[i], targets[max(0, i - 1)]):
                delta = int(candidate) - int(value)
                if 0 <= delta <= BACK or -FWD <= delta < 0:
                    referenced[int(candidate)].append(delta)
    return referenced


def decode(handle, address, back=0x400, fwd=0x200):
    """Floats that look like world coordinates and ints that look like levels."""
    blob = read(handle, address - back, back + fwd)
    if not blob:
        return [], []
    coords, smalls = [], []
    for off in range(0, len(blob) - 4, 4):
        (value,) = struct.unpack_from("<f", blob, off)
        if 1.0 < abs(value) < 30000.0 and abs(value) > 0.01:
            if value != int(value) or abs(value) > 100:
                coords.append((off - back, value))
        (ivalue,) = struct.unpack_from("<i", blob, off)
        if 1 <= ivalue <= 250:
            smalls.append((off - back, ivalue))
    return coords, smalls


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    name = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1

    found = regions(handle)
    hits = name_hits(handle, found, name.encode("utf-8"))
    print(f"{len(hits)} null-terminated '{name}' hits in private memory")
    if not hits:
        return 1

    referenced = pointer_census(handle, found, hits)
    ranked = sorted(referenced.items(), key=lambda kv: -len(kv[1]))
    print(f"\n{'address':>14}  refs  common deltas (pointer -> name)")
    for address, deltas in ranked[:12]:
        common = Counter(deltas).most_common(4)
        shown = "  ".join(f"+0x{d:X}x{n}" for d, n in common)
        print(f"0x{address:012X}  {len(deltas):4}  {shown}")

    if not ranked:
        print("\nNothing references any of them - the name anchor is not enough on its own.")
        return 1

    for address, deltas in ranked[:3]:
        base_delta = Counter(deltas).most_common(1)[0][0]
        print(f"\n=== 0x{address:012X}  (struct base likely 0x{address - base_delta:012X}, "
              f"name at +0x{base_delta:X}) ===")
        coords, smalls = decode(handle, address)
        print("  coord-ish floats:", ", ".join(f"{o:+#x}={v:.1f}" for o, v in coords[:16]) or "none")
        print("  level-ish ints:  ", ", ".join(f"{o:+#x}={v}" for o, v in smalls[:16]) or "none")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
