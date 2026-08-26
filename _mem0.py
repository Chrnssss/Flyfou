"""Can we read Neuz at all, and does the character name anchor the player struct?

Everything downstream assumes a handle and a starting address. Both are worth
five minutes of proof before any of it gets designed: if the client refuses a
handle there is no memory bot, and if the character name isn't in memory as a
findable string then the finder needs a different anchor.

The name is free - the window title is "Airborn - <charname>", so we know what
to look for without asking the user anything.

Read-only. Nothing here writes to the game.
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

import numpy as np

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE = {0x02, 0x04, 0x20, 0x40, 0x80}


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("__alignment1", wt.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("__alignment2", wt.DWORD),
    ]


k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
k32.ReadProcessMemory.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]


def regions(handle):
    address, out = 0, []
    info = MBI()
    while address < 0x7FFFFFFF0000:
        if not k32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)):
            break
        size = info.RegionSize
        if (info.State == MEM_COMMIT
                and info.Protect & 0xFF in READABLE
                and not info.Protect & PAGE_GUARD
                and info.Protect != PAGE_NOACCESS):
            out.append((info.BaseAddress, size, info.Type, info.Protect))
        address = info.BaseAddress + size
        if size == 0:
            break
    return out


def read(handle, address, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, size, ctypes.byref(got)):
        return None
    return buf.raw[:got.value]


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    name = sys.argv[2] if len(sys.argv) > 2 else "Mynuthbp"

    print(f"python is {64 if sys.maxsize > 2**32 else 32}-bit")
    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed: WinError {ctypes.get_last_error()}")
        return 1
    print(f"opened pid {pid}, handle {handle}")

    began = time.perf_counter()
    found = regions(handle)
    total = sum(size for _, size, _, _ in found)
    print(f"{len(found)} readable regions, {total / 1024 / 1024:.0f} MB, "
          f"enumerated in {time.perf_counter() - began:.2f}s")

    needles = {
        "utf8": name.encode("utf-8"),
        "utf16": name.encode("utf-16-le"),
    }
    hits = {key: [] for key in needles}
    began = time.perf_counter()
    scanned = 0
    for base, size, mtype, protect in found:
        if size > 256 * 1024 * 1024:
            continue
        blob = read(handle, base, size)
        if not blob:
            continue
        scanned += len(blob)
        for key, needle in needles.items():
            start = 0
            while True:
                at = blob.find(needle, start)
                if at < 0:
                    break
                hits[key].append((base + at, mtype, protect))
                start = at + 1
    print(f"scanned {scanned / 1024 / 1024:.0f} MB in {time.perf_counter() - began:.2f}s")

    for key, found_hits in hits.items():
        print(f"\n{key}: {len(found_hits)} hits")
        for address, mtype, protect in found_hits[:20]:
            kind = {0x20000: "private", 0x40000: "mapped", 0x1000000: "image"}.get(mtype, hex(mtype))
            print(f"  0x{address:012X}  {kind:8} protect=0x{protect:02X}")

    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
