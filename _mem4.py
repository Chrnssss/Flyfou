"""Who points AT the name?

Two dead ends so far, and both were my filtering, not the client:

  * _mem2 counted only pointer values seen 4+ times, which is exactly wrong for
    this - an entity holding `char* m_szName` produces *one* pointer to it. The
    repeats it did find were pool-allocator bookkeeping.
  * _mem3 looked for the name stored inline and found no consistent offset, so
    the name is very likely out-of-line: a pointer, not an array.

So: find qwords equal to a name address, then walk back from each to the nearest
16-aligned qword that points into Neuz.exe. That is the owning object's vtable,
and it tells us the class, the name offset, and how many siblings it has.

Read-only.
"""
import ctypes
import sys
from collections import Counter, defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions
from _mem3 import modules

WALK_BACK = 0x1000


def all_name_hits(handle, private, needle):
    hits = []
    for base, size, _mtype, _protect in private:
        blob = read(handle, base, size)
        if not blob:
            continue
        start = 0
        while True:
            at = blob.find(needle, start)
            if at < 0:
                break
            start = at + 1
            if blob[at + len(needle):at + len(needle) + 1] == b"\x00":
                hits.append(base + at)
    return sorted(set(hits))


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    name = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"
    needle = name.encode("utf-8")

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1

    mod_name, mod_base, mod_size = next(
        m for m in modules(pid) if m[0].lower().startswith("neuz"))
    found = regions(handle)
    private = [r for r in found if r[2] == 0x20000 and r[1] <= 256 * 1024 * 1024]

    blobs = {}
    for base, size, _m, _p in private:
        blob = read(handle, base, size)
        if blob:
            blobs[base] = blob

    hits = all_name_hits(handle, private, needle)
    print(f"{len(hits)} null-terminated '{name}' strings")

    wanted = np.array(hits, dtype=np.uint64)
    holders = defaultdict(list)  # name address -> [addresses of pointers to it]
    for base, blob in blobs.items():
        if len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        mask = np.isin(words, wanted)
        for i in np.nonzero(mask)[0].tolist():
            holders[int(words[i])].append(base + i * 8)

    total = sum(len(v) for v in holders.values())
    print(f"{total} pointers to those strings, across {len(holders)} of them\n")

    def owner_of(address):
        """Nearest 16-aligned qword before `address` that points into Neuz.exe."""
        region = next((b for b in blobs if b <= address < b + len(blobs[b])), None)
        if region is None:
            return None
        blob = blobs[region]
        start = address - region
        for back in range(0, min(WALK_BACK, start), 8):
            at = start - back
            if (region + at) % 16:
                continue
            (value,) = np.frombuffer(blob[at:at + 8], dtype=np.uint64)
            if mod_base <= int(value) < mod_base + mod_size:
                return region + at, int(value), back
        return None

    classes = Counter()
    detail = []
    for target, pointers in sorted(holders.items(), key=lambda kv: -len(kv[1])):
        for pointer in pointers:
            owned = owner_of(pointer)
            if owned is None:
                continue
            obj, vtable, back = owned
            classes[vtable] += 1
            detail.append((obj, vtable, back, target, pointer))

    print(f"{'object':>14} {'vtable(+mod)':>14} {'name@':>7}  string")
    for obj, vtable, back, target, _pointer in detail[:25]:
        print(f"0x{obj:012X}  +0x{vtable - mod_base:08X}  +0x{back:04X}  0x{target:012X}")

    print("\nclasses owning a pointer to the name:")
    for vtable, n in classes.most_common(10):
        print(f"  +0x{vtable - mod_base:08X}  {n} pointers")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
