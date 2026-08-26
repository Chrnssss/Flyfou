"""Find the entity class through motion instead of identity.

The name is a dead end: 48 copies, none inline in an object at a consistent
offset, and nothing anywhere points at any of them. They are chat and packet
buffers. So stop asking "where is my character" and ask "what is moving".

Monsters walk around by themselves, so no user action is needed. Floats that
drift by a believable step between two snapshots are position components, and
the objects holding them are entities. Walking back from each to the nearest
vtable groups them into classes, and the class with hundreds of drifting floats
at one consistent offset is CMover - which yields the entity list and the
position offset in a single pass.

Read-only.
"""
import ctypes
import sys
import time
from collections import Counter, defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions
from _mem3 import modules

COORD_LO, COORD_HI = 1.0, 100000.0
MOVED_LO, MOVED_HI = 0.02, 400.0  # a step, not a teleport and not noise
GAP = 6.0  # seconds between snapshots
WALK_BACK = 0x2000


def snapshot(handle, private):
    return {base: read(handle, base, size) for base, size, _m, _p in private}


def coord_floats(blobs):
    """address -> value, for every 4-aligned float that could be a coordinate."""
    addresses, values = [], []
    for base, blob in blobs.items():
        if not blob or len(blob) < 4:
            continue
        floats = np.frombuffer(blob[:len(blob) // 4 * 4], dtype=np.float32)
        keep = np.nonzero(np.isfinite(floats)
                          & (np.abs(floats) >= COORD_LO)
                          & (np.abs(floats) <= COORD_HI))[0]
        if keep.size:
            addresses.append(base + keep.astype(np.uint64) * 4)
            values.append(floats[keep])
    if not addresses:
        return np.array([], dtype=np.uint64), np.array([], dtype=np.float32)
    return np.concatenate(addresses), np.concatenate(values)


def value_at(blobs, address):
    for base, blob in blobs.items():
        if blob and base <= address < base + len(blob) - 4:
            return np.frombuffer(blob[address - base:address - base + 4], dtype=np.float32)[0]
    return None


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1

    mod_name, mod_base, mod_size = next(
        m for m in modules(pid) if m[0].lower().startswith("neuz"))
    private = [r for r in regions(handle) if r[2] == 0x20000 and r[1] <= 256 * 1024 * 1024]

    print("snapshot 1...")
    first = snapshot(handle, private)
    addresses, before = coord_floats(first)
    print(f"  {addresses.size:,} coordinate-shaped floats")
    del first

    print(f"waiting {GAP:.0f}s for monsters to walk...")
    time.sleep(GAP)

    print("snapshot 2...")
    second = snapshot(handle, private)
    after = np.empty_like(before)
    order = np.argsort(addresses)
    addresses, before = addresses[order], before[order]

    # Re-read the same addresses out of the second snapshot, region by region.
    after[:] = np.nan
    for base, blob in second.items():
        if not blob:
            continue
        lo = np.searchsorted(addresses, np.uint64(base), side="left")
        hi = np.searchsorted(addresses, np.uint64(base + len(blob) - 4), side="right")
        if hi <= lo:
            continue
        offsets = (addresses[lo:hi] - np.uint64(base)).astype(np.int64)
        floats = np.frombuffer(blob[:len(blob) // 4 * 4], dtype=np.float32)
        inside = offsets // 4
        valid = inside < floats.size
        idx = np.arange(lo, hi)[valid]
        after[idx] = floats[inside[valid]]

    delta = np.abs(after - before)
    moved = np.nonzero(np.isfinite(delta) & (delta >= MOVED_LO) & (delta <= MOVED_HI))[0]
    print(f"  {moved.size:,} floats moved by {MOVED_LO}-{MOVED_HI}")

    # Group each moving float by the object that owns it: nearest 16-aligned
    # qword before it that points into Neuz.exe.
    vtable_at = {}
    for base, blob in second.items():
        if not blob or len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        hit = np.nonzero((words >= mod_base) & (words < mod_base + mod_size))[0]
        aligned = hit[(hit * 8 + base) % 16 == 0]
        if aligned.size:
            vtable_at[base] = (base + aligned.astype(np.uint64) * 8,
                               words[aligned])

    classes = defaultdict(Counter)   # vtable -> Counter(offset within object)
    members = defaultdict(set)
    for i in moved.tolist():
        address = int(addresses[i])
        region = next((b for b in vtable_at if b <= address < b + len(second[b])), None)
        if region is None:
            continue
        starts, values = vtable_at[region]
        j = int(np.searchsorted(starts, np.uint64(address), side="right")) - 1
        if j < 0:
            continue
        obj = int(starts[j])
        if 0 < address - obj <= WALK_BACK:
            classes[int(values[j])][address - obj] += 1
            members[int(values[j])].add(obj)

    ranked = sorted(classes.items(), key=lambda kv: -sum(kv[1].values()))
    print(f"\n{'vtable(+mod)':>14} {'objs':>5} {'moving':>7}  top offsets")
    for vtable, offsets in ranked[:10]:
        top = "  ".join(f"+0x{o:X}x{n}" for o, n in offsets.most_common(6))
        print(f"  +0x{vtable - mod_base:08X} {len(members[vtable]):5} "
              f"{sum(offsets.values()):7}  {top}")

    if ranked:
        vtable, offsets = ranked[0]
        print(f"\n=== best class +0x{vtable - mod_base:08X}: "
              f"{len(members[vtable])} objects ===")
        for obj in sorted(members[vtable])[:6]:
            row = []
            for off, _n in offsets.most_common(8):
                value = value_at(second, obj + off)
                if value is not None:
                    row.append(f"+0x{off:X}={value:9.2f}")
            print(f"  0x{obj:012X}  " + "  ".join(row))

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
