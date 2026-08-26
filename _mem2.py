"""Same question, without the noise.

_mem1 counted any 8-byte value falling in a 700 MB span, so every candidate
scored ~400 and the ranking meant nothing. Two corrections:

  * a name that is a struct field is aligned; a name at 0x...7FF is sitting in
    a chat log or a packed text blob, and most of the 48 hits are exactly that;
  * a referenced object shows up as the *same pointer value* repeated, once per
    container holding it. Counting distinct values instead of hits in a range is
    the difference between signal and heap noise.

Read-only.
"""
import ctypes
import struct
import sys
from collections import Counter

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions

BACK = 0x1800
FWD = 0x80
MIN_REFS = 4


def aligned_name_hits(handle, found, needle, alignment=4):
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
            start = at + 1
            if (base + at) % alignment:
                continue
            if blob[at + len(needle):at + len(needle) + 1] not in (b"\x00", b""):
                continue
            hits.append(base + at)
    return sorted(hits)


def value_census(handle, found, hits):
    """Count how often each pointer value into a candidate's window appears."""
    starts = np.array([h - BACK for h in hits], dtype=np.uint64)
    ends = np.array([h + FWD for h in hits], dtype=np.uint64)
    order = np.argsort(starts)
    starts, ends = starts[order], ends[order]

    counts = Counter()
    for base, size, _mtype, _protect in found:
        if size > 256 * 1024 * 1024:
            continue
        blob = read(handle, base, size)
        if not blob or len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        slot = np.searchsorted(starts, words, side="right") - 1
        keep = (slot >= 0) & (words <= ends[np.clip(slot, 0, len(ends) - 1)])
        for value in words[keep].tolist():
            counts[value] += 1
    return counts


def hexdump(handle, address, before=0x40, length=0xC0):
    blob = read(handle, address - before, length)
    if not blob:
        return
    for row in range(0, len(blob), 16):
        chunk = blob[row:row + 16]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"    {row - before:+05x}  {chunk.hex(' '):<47}  {text}")


def fields(handle, base, span=0x400):
    blob = read(handle, base, span)
    if not blob:
        return
    for off in range(0, span - 8, 4):
        (f,) = struct.unpack_from("<f", blob, off)
        (i,) = struct.unpack_from("<i", blob, off)
        note = []
        if 1.0 < abs(f) < 30000.0:
            note.append(f"f={f:.2f}")
        if 0 < i <= 250:
            note.append(f"i={i}")
        if note:
            print(f"    +0x{off:03X}  {' '.join(note)}")


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    name = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    found = regions(handle)

    hits = aligned_name_hits(handle, found, name.encode("utf-8"))
    print(f"{len(hits)} aligned '{name}' hits (of 48 total)")
    for h in hits:
        print(f"  0x{h:012X}")
    if not hits:
        return 1

    counts = value_census(handle, found, hits)
    interesting = [(v, n) for v, n in counts.items() if n >= MIN_REFS]
    interesting.sort(key=lambda vn: -vn[1])
    print(f"\n{len(interesting)} pointer values referenced {MIN_REFS}+ times:")
    for value, n in interesting[:20]:
        near = min(hits, key=lambda h: abs(h - value))
        print(f"  0x{value:012X}  x{n:<4} name at +0x{near - value:X}")

    for value, n in interesting[:3]:
        print(f"\n=== object 0x{value:012X} ({n} refs) ===")
        hexdump(handle, value, before=0, length=0x80)
        print("  plausible fields:")
        fields(handle, value)

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
