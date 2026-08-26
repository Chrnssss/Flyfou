"""Map the entity struct now that we know which class it is.

_mem5 found it: vtable +0x0096EA18, 216 instances, a D3DXVECTOR3 at +0x60 whose
x and z drift as monsters walk. Everything the bot needs is a field of that
struct, so the job now is to read the layout off the instances themselves.

The method is comparative, not speculative. With 200+ objects of one class, each
offset can be classified by how it behaves across them: constant everywhere is
class data, unique per object is an id, a handful of repeated values is a kind
or a level, and 0..1 floats are fractions. No guessing from a leaked header that
may not match this build.

Read-only.
"""
import ctypes
import struct
import sys
from collections import Counter, defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions
from _mem3 import modules

SPAN = 0x800
WANT_VTABLE = 0x0096EA18


def instances(handle, private, mod_base, mod_size, wanted):
    out = []
    for base, size, _m, _p in private:
        blob = read(handle, base, size)
        if not blob or len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        hit = np.nonzero(words == np.uint64(mod_base + wanted))[0]
        for i in hit.tolist():
            address = base + i * 8
            if address % 16 == 0:
                out.append(address)
    return sorted(out)


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    name = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"
    wanted = int(sys.argv[3], 16) if len(sys.argv) > 3 else WANT_VTABLE

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1

    mod_name, mod_base, mod_size = next(
        m for m in modules(pid) if m[0].lower().startswith("neuz"))
    private = [r for r in regions(handle) if r[2] == 0x20000 and r[1] <= 256 * 1024 * 1024]

    found = instances(handle, private, mod_base, mod_size, wanted)
    print(f"vtable +0x{wanted:08X}: {len(found)} instances")
    gaps = Counter(b - a for a, b in zip(found, found[1:]))
    print(f"address gaps: {gaps.most_common(5)}")

    bodies = {}
    for address in found:
        blob = read(handle, address, SPAN)
        if blob and len(blob) == SPAN:
            bodies[address] = blob
    print(f"read {len(bodies)} bodies of 0x{SPAN:X} bytes\n")
    if not bodies:
        return 1

    # Does any instance carry the character name inline?
    needle = name.encode("utf-8")
    for address, blob in bodies.items():
        at = blob.find(needle)
        if at >= 0:
            print(f"*** '{name}' inline in 0x{address:012X} at +0x{at:X} ***\n")

    # Any printable inline strings at all, and where?
    string_offsets = Counter()
    for blob in bodies.values():
        for off in range(0, SPAN - 4, 4):
            chunk = blob[off:off + 24]
            end = chunk.find(b"\x00")
            text = chunk[:end if end >= 0 else len(chunk)]
            if len(text) >= 3 and all(32 <= c < 127 for c in text):
                string_offsets[off] += 1
    print("inline string offsets seen in 20%+ of instances:")
    for off, n in string_offsets.most_common(12):
        if n < len(bodies) * 0.2:
            break
        samples = []
        for blob in list(bodies.values())[:4]:
            chunk = blob[off:off + 20]
            end = chunk.find(b"\x00")
            samples.append(chunk[:end if end >= 0 else 20].decode("latin-1", "replace"))
        print(f"  +0x{off:03X}  x{n:<4} {samples}")

    # Classify every 4-byte slot by how it varies across instances.
    print(f"\n{'off':>6} {'distinct':>8}  interpretation")
    columns = {}
    for off in range(0, SPAN, 4):
        values = [struct.unpack_from("<I", blob, off)[0] for blob in bodies.values()]
        columns[off] = values

    for off in range(0, SPAN, 4):
        values = columns[off]
        distinct = len(set(values))
        if distinct == 1:
            continue
        ints = [v if v < 2**31 else v - 2**32 for v in values]
        floats = [struct.unpack("<f", struct.pack("<I", v))[0] for v in values]
        notes = []
        if distinct == len(values):
            notes.append("unique-per-object (id?)")
        elif distinct <= 12:
            notes.append(f"{distinct} values: {Counter(values).most_common(4)}")
        finite = [f for f in floats if np.isfinite(f)]
        if finite and all(0.0 <= f <= 1.0 for f in finite) and any(0.0 < f < 1.0 for f in finite):
            notes.append("float 0..1 (fraction?)")
        if finite and all(1.0 < abs(f) < 100000.0 for f in finite):
            notes.append(f"float ~{np.mean(finite):.0f}")
        small = [i for i in ints if 0 < i <= 250]
        if len(small) >= len(values) * 0.8:
            notes.append(f"small int (level?) {Counter(small).most_common(4)}")
        if notes:
            print(f"  +0x{off:03X} {distinct:8}  " + " | ".join(notes))

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
