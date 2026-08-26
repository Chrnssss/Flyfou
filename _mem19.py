"""Walk the object manager's map instead of the partial static array.

_mem18 ruled out an array. Pointers to live movers are scattered over dozens of
heap allocations, but the gaps between them are almost all multiples of 48, and
48 bytes is exactly an MSVC x64 std::map node: left, parent, right, colour and
isnil flags, then a pair<DWORD, CObj*>. So the object manager is a
std::map<DWORD, CMover*> keyed by entity id, its nodes individually heap
allocated, which is why no single allocation contains them all and why the static
array only ever listed a subset.

If that is right, a mover pointer at address A sits at node+0x28 and the entity's
own id is the DWORD at node+0x20 - a prediction that either holds for every node
or the theory is wrong. Then the parent chain leads to the sentinel head, the
tree can be walked in order, and whatever points at the head is the anchor the bot
resolves each tick without ever sweeping the heap again.

Mynuthyj has moved to 85 Wampa des neiges, so this also finally checks level and
HP on real monsters rather than pets.

Read-only.
"""
import ctypes
import struct
import sys
from collections import Counter

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions
from _mem3 import modules

TABLE_LO, TABLE_HI = 0x00E07CB0, 0x00E0B6C0
MOVER_VT = 0x0096D620
TYPE, ID, POS, LEVEL, HP, MP, NAME = 0x08, 0x28, 0x60, 0x718, 0x738, 0x73C, 0x1EC8
NEED = NAME + 32
LEFT, PARENT, RIGHT, ISNIL, KEY, VALUE = 0x00, 0x08, 0x10, 0x19, 0x20, 0x28
NODE = 0x30


def ascii_at(blob, start, limit=24):
    out = []
    for byte in blob[start:start + limit]:
        if 32 <= byte < 127:
            out.append(chr(byte))
        else:
            break
    return "".join(out)


class Nodes:
    def __init__(self, handle):
        self.handle = handle
        self.cache = {}

    def get(self, address):
        if address not in self.cache:
            self.cache[address] = read(self.handle, address, NODE) or b""
        return self.cache[address]

    def field(self, address, off, fmt="<Q"):
        blob = self.get(address)
        return struct.unpack_from(fmt, blob, off)[0] if len(blob) == NODE else 0

    def isnil(self, address):
        blob = self.get(address)
        return blob[ISNIL] if len(blob) == NODE else 1


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 17356

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    _mn, mod_base, mod_size = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))
    every = regions(handle)

    blobs = {}
    for base, size, kind, _protect in every:
        if size > 256 * 1024 * 1024:
            continue
        blob = read(handle, base, size)
        if blob:
            blobs[base] = (blob, kind)

    movers = {}
    for base, (blob, kind) in blobs.items():
        if kind != 0x20000 or len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        for i in np.nonzero(words == np.uint64(mod_base + MOVER_VT))[0].tolist():
            at = base + i * 8
            if at % 16:
                continue
            body = read(handle, at, NEED)
            if body and len(body) == NEED:
                movers[at] = body
    print(f"{len(movers)} mover objects on the heap")

    # Does the node hypothesis hold: mover pointer at node+0x28, its id at node+0x20?
    wanted = np.array(sorted(movers), dtype=np.uint64)
    slots = []
    for base, (blob, _kind) in blobs.items():
        if len(blob) < 8:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        for i in np.nonzero(np.isin(words, wanted))[0].tolist():
            slots.append((base + i * 8, int(words[i])))

    nodes = Nodes(handle)
    good, bad = [], 0
    for address, mover in slots:
        node = address - VALUE
        if nodes.field(node, KEY, "<I") == struct.unpack_from("<I", movers[mover], ID)[0]:
            good.append((node, mover))
        else:
            bad += 1
    print(f"{len(slots)} pointers to movers: {len(good)} sit at node+0x28 with the id at "
          f"node+0x20, {bad} do not")
    if not good:
        print("the std::map node theory is wrong")
        return 1

    # Up the parent chain to the sentinel.
    heads = Counter()
    for node, _mover in good:
        walk, seen = node, 0
        while seen < 64 and not nodes.isnil(walk):
            walk = nodes.field(walk, PARENT)
            if not walk:
                break
            seen += 1
        if walk and nodes.isnil(walk):
            heads[walk] += 1
    print(f"\nsentinel heads reached: {[(hex(h), n) for h, n in heads.most_common(4)]}")
    if not heads:
        print("no sentinel found")
        return 1
    head = heads.most_common(1)[0][0]

    # In-order walk of the whole tree.
    root = nodes.field(head, PARENT)
    found, stack, walk = [], [], root
    while stack or (walk and not nodes.isnil(walk)):
        while walk and not nodes.isnil(walk):
            stack.append(walk)
            walk = nodes.field(walk, LEFT)
        if not stack:
            break
        walk = stack.pop()
        found.append((nodes.field(walk, KEY, "<I"), nodes.field(walk, VALUE)))
        walk = nodes.field(walk, RIGHT)
    print(f"walking the tree from 0x{head:012X} yields {len(found)} entries")

    reached = {v for _k, v in found}
    print(f"  covers {len(reached & set(movers))}/{len(movers)} live movers, "
          f"{len(reached - set(movers))} entries that are not movers")

    table = read(handle, mod_base + TABLE_LO, TABLE_HI - TABLE_LO)
    listed = {p for p in np.frombuffer(table, dtype=np.uint64).tolist() if p} & set(movers)
    print(f"  the old static array covered {len(listed)}/{len(movers)}")

    # Who owns the map? _Myhead then _Mysize is the MSVC layout.
    print("\npointers to the sentinel (the map object itself):")
    target = np.uint64(head)
    owners = []
    for base, (blob, _kind) in blobs.items():
        if len(blob) < 16:
            continue
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        for i in np.nonzero(words == target)[0].tolist():
            at = base + i * 8
            size = int(words[i + 1]) if i + 1 < len(words) else -1
            where = f"Neuz.exe+0x{at - mod_base:08X}" if mod_base <= at < mod_base + mod_size \
                else f"heap 0x{at:012X}"
            owners.append((at, where, size))
            print(f"  {where}  next qword = {size}"
                  + ("   <- _Mysize matches the tree" if size == len(found) else ""))

    # A static route to whatever holds the map.
    for at, where, size in owners:
        if size != len(found) or mod_base <= at < mod_base + mod_size:
            continue
        holder = np.uint64(at)
        print(f"\n  pointers to the map at 0x{at:012X}:")
        shown = 0
        for base, (blob, _kind) in blobs.items():
            if len(blob) < 8:
                continue
            words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
            for i in np.nonzero(words == holder)[0].tolist():
                spot = base + i * 8
                tag = f"Neuz.exe+0x{spot - mod_base:08X}" if mod_base <= spot < mod_base + mod_size \
                    else f"heap 0x{spot:012X}"
                print(f"    {tag}")
                shown += 1
                if shown > 12:
                    break
            if shown > 12:
                break

    # Real monsters at last.
    print("\nmonsters (type 18) as read through the confirmed offsets:")
    kinds = Counter()
    for address, body in movers.items():
        kinds[(struct.unpack_from("<I", body, TYPE)[0], ascii_at(body, NAME))] += 1
    for (kind, name), n in kinds.most_common(8):
        print(f"  type {kind:3}  x{n:<4} {name!r}")
    sample = [b for b in movers.values()
              if struct.unpack_from("<I", body, TYPE)[0] and ascii_at(b, NAME) == "Wampa des neiges"][:6]
    for body in sample:
        x, y, z = struct.unpack_from("<fff", body, POS)
        print(f"    id={struct.unpack_from('<I', body, ID)[0]:<10} "
              f"lvl={struct.unpack_from('<I', body, LEVEL)[0]:<5} "
              f"hp={struct.unpack_from('<I', body, HP)[0]:<8} "
              f"mp={struct.unpack_from('<I', body, MP)[0]:<6} ({x:.0f}, {y:.0f}, {z:.0f})")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
