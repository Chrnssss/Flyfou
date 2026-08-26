"""Species, level, HP and target, off a confirmed player anchor.

_mem12 settled identity: the name sits inline at +0x1EC8, and it matched two
movers - this client's character and another of the four logged-in clients. The
same entity is the only one carrying an (id, count) pair array at +0x0480..0x0604
where all seventy others hold 0xFFFFFFFF, which is an inventory and so can only
be the player. So the player no longer has to be guessed from reference counts.

The categorical dump has gone as far as it can; 300 offsets with plausible-looking
numbers is not evidence. What separates a real field from a coincidence is
agreement: everything a monster inherits from its species - level, max HP, attack,
size, exp - is constant across monsters of that species and changes together
between species. So offsets that induce the *same partition* of the movers are
almost certainly one block of species data, and the largest such cluster locates
it without knowing a single value in advance.

Current HP then follows: it varies within a species group but never exceeds that
group's constant. And the target is found by looking for another mover's entity
id stored inside the player.

Read-only.
"""
import ctypes
import struct
import sys
import time
from collections import Counter, defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read
from _mem3 import modules

TABLE_LO, TABLE_HI = 0x00E07CB0, 0x00E0B6C0
MOVER_VT = 0x0096D620
SPAN = 0x2000
POS, ID, TYPE, NAME = 0x60, 0x28, 0x08, 0x1EC8
SAMPLES, GAP = 6, 2.5


def ascii_at(blob, start, limit=24):
    out = []
    for byte in blob[start:start + limit]:
        if 32 <= byte < 127:
            out.append(chr(byte))
        else:
            break
    return "".join(out)


def partition_key(column):
    """Canonical label per row, so two columns grouping rows alike compare equal."""
    _values, inverse = np.unique(column, return_inverse=True)
    order, seen = {}, 0
    labels = []
    for label in inverse.tolist():
        if label not in order:
            order[label] = seen
            seen += 1
        labels.append(order[label])
    return tuple(labels)


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    who = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    _mn, mod_base, mod_size = next(m for m in modules(pid) if m[0].lower().startswith("neuz"))

    table = read(handle, mod_base + TABLE_LO, TABLE_HI - TABLE_LO)
    movers = {}
    for address in dict.fromkeys(p for p in np.frombuffer(table, dtype=np.uint64).tolist() if p):
        body = read(handle, address, SPAN)
        if body and len(body) == SPAN and struct.unpack_from("<Q", body, 0)[0] == mod_base + MOVER_VT:
            movers[address] = body
    print(f"{len(movers)} movers")

    # 1. What is at the name offset for everything, not just the player?
    named = Counter(ascii_at(b, NAME) for b in movers.values())
    print(f"\n+0x{NAME:04X} across movers:")
    for text, n in named.most_common(12):
        print(f"  {n:3}  {text!r}")

    player = next((a for a, b in movers.items() if ascii_at(b, NAME) == who), None)
    if player is None:
        print(f"\n'{who}' not found at +0x{NAME:04X} - is that character in this client?")
        return 1
    body = movers[player]
    print(f"\nplayer 0x{player:012X}  id={struct.unpack_from('<I', body, ID)[0]}  "
          f"type={struct.unpack_from('<I', body, TYPE)[0]}  "
          f"pos={struct.unpack_from('<fff', body, POS)}")

    # 2. The static that points at the player: the anchor a later run resolves.
    print("\nstatics in Neuz.exe pointing at the player:")
    wanted = np.uint64(player)
    address = mod_base
    while address < mod_base + mod_size:
        chunk = read(handle, address, min(0x100000, mod_base + mod_size - address))
        if chunk:
            words = np.frombuffer(chunk[:len(chunk) // 8 * 8], dtype=np.uint64)
            for i in np.nonzero(words == wanted)[0].tolist():
                at = address + i * 8
                where = "inside the entity table" if mod_base + TABLE_LO <= at < mod_base + TABLE_HI else ""
                print(f"  Neuz.exe+0x{at - mod_base:08X}  {where}")
        address += 0x100000

    # 3. Does the player hold another mover's id? That is the target.
    ids = {struct.unpack_from("<I", b, ID)[0]: a for a, b in movers.items()}
    others = set(ids) - {struct.unpack_from("<I", body, ID)[0]}
    print("\nentity ids stored inside the player (target/last-hit candidates):")
    hits = 0
    for off in range(0, SPAN, 4):
        value = struct.unpack_from("<I", body, off)[0]
        if value in others:
            target = movers[ids[value]]
            print(f"  +0x{off:04X} -> id {value} at {struct.unpack_from('<fff', target, POS)}")
            hits += 1
    if not hits:
        print("  none - nothing is targeted right now")

    # 4. Species block: offsets that group the movers identically.
    monsters = [b for a, b in movers.items() if not ascii_at(b, NAME)]
    print(f"\n{len(monsters)} unnamed movers treated as monsters")
    matrix = np.stack([np.frombuffer(b, dtype=np.uint32) for b in monsters])
    clusters = defaultdict(list)
    for column in range(matrix.shape[1]):
        values = matrix[:, column]
        groups = len(np.unique(values))
        if 2 <= groups <= 12:
            clusters[partition_key(values)].append(column * 4)

    print("\noffset clusters that partition the monsters identically:")
    ranked = sorted(clusters.items(), key=lambda kv: -len(kv[1]))[:4]
    for key, offsets in ranked:
        sizes = Counter(key)
        print(f"\n  {len(offsets)} offsets, {len(sizes)} species, sizes {sorted(sizes.values(), reverse=True)}")
        for off in offsets[:40]:
            values = matrix[:, off // 4]
            first = [int(values[key.index(g)]) for g in range(len(sizes))]
            tag = "  <- level?" if all(0 < v <= 200 for v in first) else ""
            print(f"    +0x{off:04X}  {first}{tag}")
        if len(offsets) > 40:
            print(f"    ... {len(offsets) - 40} more")

    # 5. Current HP: varies inside a species, never exceeds that species' constant.
    if ranked:
        key, offsets = ranked[0]
        labels = np.array(key)
        constant = np.array(sorted(offsets)) // 4
        print("\ncurrent-value offsets bounded by a species constant:")
        found = 0
        for column in range(matrix.shape[1]):
            values = matrix[:, column]
            if values.max() == 0 or values.max() > 10_000_000:
                continue
            if all(len(np.unique(values[labels == g])) == 1 for g in range(labels.max() + 1)):
                continue
            for cap in constant.tolist():
                caps = matrix[:, cap]
                if caps.min() == 0 or not np.all(values <= caps):
                    continue
                fraction = values / np.maximum(caps, 1)
                if fraction.min() > 0.999:
                    continue
                print(f"  cur +0x{column * 4:04X}  max +0x{cap * 4:04X}  "
                      f"frac {fraction.min():.2f}..{fraction.max():.2f}  "
                      f"caps {sorted(set(caps.tolist()))[:6]}")
                found += 1
                break
            if found > 30:
                break
        if not found:
            print("  none - every monster may be at full health")

    # 6. What changes on the player over time?
    print(f"\nsampling the player {SAMPLES}x over {SAMPLES * GAP:.0f}s...")
    series = [body]
    for _ in range(SAMPLES - 1):
        time.sleep(GAP)
        again = read(handle, player, SPAN)
        if again and len(again) == SPAN:
            series.append(again)
    print("offsets that changed:")
    for off in range(0, SPAN, 4):
        values = [struct.unpack_from("<I", b, off)[0] for b in series]
        if len(set(values)) == 1:
            continue
        floats = [struct.unpack_from("<f", b, off)[0] for b in series]
        as_float = ""
        if all(np.isfinite(floats)) and any(abs(f) > 1e-6 for f in floats):
            if all(abs(f) < 1e6 for f in floats):
                as_float = "  f=" + ",".join(f"{f:.2f}" for f in floats[:4])
        print(f"  +0x{off:04X}  {values[:5]}{as_float}")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
