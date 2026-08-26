"""Find the entity class by its vtable, not by its name.

_mem2 landed in a pool allocator: the things referencing the name were block
headers with self-pointers and a literal "FREE" marker, all at fixed distances
from the same string. Chasing references through a custom allocator is a dead
end.

The class itself is a better anchor than any of its fields. CMover is
polymorphic, so every instance begins with the same vtable pointer into
Neuz.exe, and that gives all of it at once:

  * group heap objects by the module pointer they start with;
  * the group holding our character name is the entity class;
  * the name's offset within it is then a constant, not a guess;
  * and the other members of that group are the monsters - i.e. the entity list,
    without ever finding the object manager.

Read-only.
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys
from collections import Counter, defaultdict

import numpy as np

from _mem0 import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ, k32, read, regions

TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MIN_INSTANCES = 8


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wt.DWORD),
        ("hModule", ctypes.c_void_p),
        ("szModule", ctypes.c_wchar * 256),
        ("szExePath", ctypes.c_wchar * 260),
    ]


k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.Module32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
k32.Module32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]


def modules(pid):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == wt.HANDLE(-1).value:
        return []
    entry = MODULEENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    out = []
    ok = k32.Module32FirstW(snap, ctypes.byref(entry))
    while ok:
        out.append((entry.szModule, ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value,
                    entry.modBaseSize))
        ok = k32.Module32NextW(snap, ctypes.byref(entry))
    k32.CloseHandle(snap)
    return out


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    name = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"
    needle = name.encode("utf-8")

    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1

    mods = modules(pid)
    main_mod = next((m for m in mods if m[0].lower().startswith("neuz")), None)
    if main_mod is None:
        print("Neuz.exe module not found")
        return 1
    mod_name, mod_base, mod_size = main_mod
    print(f"{mod_name}  base=0x{mod_base:012X}  size=0x{mod_size:X} ({mod_size / 1024 / 1024:.1f} MB)")

    found = regions(handle)
    private = [r for r in found if r[2] == 0x20000 and r[1] <= 256 * 1024 * 1024]

    # Pass 1: every heap qword pointing into Neuz.exe is a vtable candidate, and
    # the address it was found at is the object that owns it.
    lo, hi = mod_base, mod_base + mod_size
    instances = defaultdict(list)
    blobs = {}
    for base, size, _mtype, _protect in private:
        blob = read(handle, base, size)
        if not blob or len(blob) < 8:
            continue
        blobs[base] = blob
        words = np.frombuffer(blob[:len(blob) // 8 * 8], dtype=np.uint64)
        hit = np.nonzero((words >= lo) & (words < hi))[0]
        for i in hit.tolist():
            instances[int(words[i])].append(base + i * 8)

    classes = {v: a for v, a in instances.items() if len(a) >= MIN_INSTANCES}
    print(f"{len(classes)} vtable candidates with {MIN_INSTANCES}+ instances "
          f"(of {len(instances)} module pointers seen)")

    # Pass 2: which class has our name inside its instances, and at what offset?
    print(f"\nlooking for '{name}' inside instances...")
    scored = []
    for vtable, addresses in classes.items():
        offsets = Counter()
        for address in addresses:
            region = next((b for b in blobs if b <= address < b + len(blobs[b])), None)
            if region is None:
                continue
            start = address - region
            window = blobs[region][start:start + 0x800]
            at = window.find(needle)
            if at >= 0 and window[at + len(needle):at + len(needle) + 1] in (b"\x00",):
                offsets[at] += 1
        if offsets:
            scored.append((sum(offsets.values()), vtable, addresses, offsets))

    scored.sort(key=lambda s: -s[0])
    if not scored:
        print("  no class contains the name - widen the window or the instance floor")
        k32.CloseHandle(handle)
        return 1

    for carrying, vtable, addresses, offsets in scored[:5]:
        print(f"\n=== vtable 0x{vtable:012X} (+0x{vtable - mod_base:X} in module) ===")
        print(f"    {len(addresses)} instances, {carrying} carrying the name, "
              f"name offsets: {offsets.most_common(3)}")
        stride = Counter()
        ordered = sorted(addresses)
        for a, b in zip(ordered, ordered[1:]):
            stride[b - a] += 1
        print(f"    address gaps: {stride.most_common(4)}")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
