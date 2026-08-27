"""A handle on a running Neuz client, and the bytes behind it.

Everything the bot perceives arrives through this file: a process handle, the
list of regions worth reading, and the module base that turns a static offset
into a real address. ASLR moves Neuz.exe on every launch, so no address is ever
written down anywhere - offsets are stored relative to the module and resolved
here.

Reads return None rather than raising. A region can be freed between the moment
it is enumerated and the moment it is read, and a client can exit mid-tick;
both are ordinary, and callers that ask for one entity among hundreds should
skip it rather than unwind.

Writing is possible but off by default. The handle is opened without write
access unless it is asked for, so a bug in perception code cannot corrupt the
game.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct
from typing import List, NamedTuple, Optional, Tuple

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000
MEM_IMAGE = 0x1000000

PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE = frozenset({0x02, 0x04, 0x20, 0x40, 0x80})
WRITABLE = frozenset({0x04, 0x40, 0x80})

TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
STILL_ACTIVE = 259

USER_SPACE_END = 0x7FFFFFFF0000
HUGE_REGION = 256 * 1024 * 1024


class ProcessError(RuntimeError):
    """The client could not be opened, or went away."""


class _MBI(ctypes.Structure):
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


class _MODULEENTRY32W(ctypes.Structure):
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


k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CloseHandle.restype = wt.BOOL
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
k32.ReadProcessMemory.restype = wt.BOOL
k32.WriteProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
k32.WriteProcessMemory.restype = wt.BOOL
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.GetExitCodeProcess.restype = wt.BOOL
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.Module32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(_MODULEENTRY32W)]
k32.Module32FirstW.restype = wt.BOOL
k32.Module32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(_MODULEENTRY32W)]
k32.Module32NextW.restype = wt.BOOL


class Region(NamedTuple):
    """One committed, readable span of the client's address space."""

    base: int
    size: int
    kind: int
    protect: int

    @property
    def end(self) -> int:
        return self.base + self.size

    @property
    def private(self) -> bool:
        """Heap and stack, as opposed to mapped files and loaded images."""
        return self.kind == MEM_PRIVATE

    @property
    def writable(self) -> bool:
        return self.protect & 0xFF in WRITABLE

    def holds(self, address: int) -> bool:
        return self.base <= address < self.end


class Module(NamedTuple):
    name: str
    base: int
    size: int

    @property
    def end(self) -> int:
        return self.base + self.size

    def holds(self, address: int) -> bool:
        return self.base <= address < self.end

    def offset_of(self, address: int) -> Optional[int]:
        """Where an address sits inside the module, or None if it is elsewhere."""
        return address - self.base if self.holds(address) else None


def modules_of(pid: int) -> List[Module]:
    """Every module loaded in a process, without needing a handle."""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == wt.HANDLE(-1).value:
        return []
    entry = _MODULEENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    out: List[Module] = []
    ok = k32.Module32FirstW(snap, ctypes.byref(entry))
    while ok:
        base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
        out.append(Module(entry.szModule, base, entry.modBaseSize))
        ok = k32.Module32NextW(snap, ctypes.byref(entry))
    k32.CloseHandle(snap)
    return out


class Process:
    """An open client. Reads are cheap; opening is not, so keep one around."""

    def __init__(self, pid: int, writable: bool = False):
        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        handle = k32.OpenProcess(access, False, pid)
        if not handle:
            raise ProcessError(
                f"cannot open pid {pid}: WinError {ctypes.get_last_error()}"
                " (is the client running, and is Flyfou elevated the same way it is?)"
            )
        self.pid = pid
        self.writable = writable
        self._handle = handle
        self._modules: Optional[List[Module]] = None

    # -- lifetime ---------------------------------------------------------

    def close(self) -> None:
        if self._handle:
            k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "Process":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<Process pid={self.pid}{' rw' if self.writable else ' ro'}>"

    @property
    def alive(self) -> bool:
        if not self._handle:
            return False
        code = wt.DWORD()
        if not k32.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE

    # -- layout -----------------------------------------------------------

    def regions(self, private_only: bool = False,
                skip_huge: bool = True) -> List[Region]:
        """Committed, readable spans, in address order."""
        if not self._handle:
            return []
        out: List[Region] = []
        info = _MBI()
        address = 0
        while address < USER_SPACE_END:
            if not k32.VirtualQueryEx(self._handle, ctypes.c_void_p(address),
                                      ctypes.byref(info), ctypes.sizeof(info)):
                break
            size = info.RegionSize
            if size == 0:
                break
            usable = (info.State == MEM_COMMIT
                      and info.Protect & 0xFF in READABLE
                      and not info.Protect & PAGE_GUARD)
            if usable and not (skip_huge and size > HUGE_REGION):
                region = Region(info.BaseAddress, size, info.Type, info.Protect)
                if not private_only or region.private:
                    out.append(region)
            address = info.BaseAddress + size
        return out

    def modules(self, refresh: bool = False) -> List[Module]:
        if self._modules is None or refresh:
            self._modules = modules_of(self.pid)
        return self._modules

    def module(self, prefix: str) -> Module:
        """The loaded module whose name starts with `prefix`, case-insensitively."""
        want = prefix.lower()
        for mod in self.modules():
            if mod.name.lower().startswith(want):
                return mod
        raise ProcessError(f"pid {self.pid} has no module starting with {prefix!r}")

    # -- reading ----------------------------------------------------------

    def read(self, address: int, size: int) -> Optional[bytes]:
        """`size` bytes, or None if any of it could not be read."""
        if not self._handle or size <= 0 or address <= 0:
            return None
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        ok = k32.ReadProcessMemory(self._handle, ctypes.c_void_p(address), buf,
                                   size, ctypes.byref(got))
        if not ok or got.value != size:
            return None
        return buf.raw

    def read_partial(self, address: int, size: int) -> bytes:
        """As much as could be read, possibly nothing. For scanning whole regions."""
        if not self._handle or size <= 0:
            return b""
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        k32.ReadProcessMemory(self._handle, ctypes.c_void_p(address), buf,
                              size, ctypes.byref(got))
        return buf.raw[:got.value]

    def u8(self, address: int) -> Optional[int]:
        raw = self.read(address, 1)
        return raw[0] if raw else None

    def u32(self, address: int) -> Optional[int]:
        raw = self.read(address, 4)
        return struct.unpack("<I", raw)[0] if raw else None

    def i32(self, address: int) -> Optional[int]:
        raw = self.read(address, 4)
        return struct.unpack("<i", raw)[0] if raw else None

    def u64(self, address: int) -> Optional[int]:
        raw = self.read(address, 8)
        return struct.unpack("<Q", raw)[0] if raw else None

    def f32(self, address: int) -> Optional[float]:
        raw = self.read(address, 4)
        return struct.unpack("<f", raw)[0] if raw else None

    def vec3(self, address: int) -> Optional[Tuple[float, float, float]]:
        raw = self.read(address, 12)
        return struct.unpack("<fff", raw) if raw else None

    def pointer(self, address: int) -> Optional[int]:
        """A pointer that is plausibly a user-space address, else None."""
        value = self.u64(address)
        if value is None or not 0x10000 < value < USER_SPACE_END:
            return None
        return value

    def chain(self, base: int, *offsets: int) -> Optional[int]:
        """Follow a pointer chain, stopping at the first bad link."""
        address: Optional[int] = base
        for step in offsets:
            if address is None:
                return None
            address = self.pointer(address + step)
        return address

    # -- writing ----------------------------------------------------------

    def write(self, address: int, data: bytes) -> bool:
        if not self.writable:
            raise ProcessError("this Process was opened read-only")
        if not self._handle or not data:
            return False
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        put = ctypes.c_size_t()
        ok = k32.WriteProcessMemory(self._handle, ctypes.c_void_p(address), buf,
                                    len(data), ctypes.byref(put))
        return bool(ok) and put.value == len(data)

    def write_u32(self, address: int, value: int) -> bool:
        return self.write(address, struct.pack("<I", value & 0xFFFFFFFF))

    def write_f32(self, address: int, value: float) -> bool:
        return self.write(address, struct.pack("<f", value))


def ascii_at(blob: bytes, start: int, limit: int = 32) -> str:
    """The printable run at an offset, the way the client stores inline names."""
    out = []
    for byte in blob[start:start + limit]:
        if 32 <= byte < 127:
            out.append(chr(byte))
        else:
            break
    return "".join(out)


def link_stamp(process: Process, module: Module) -> Optional[int]:
    """The PE link timestamp, which identifies one build of the client."""
    header = process.read(module.base, 0x400)
    if not header or len(header) < 0x40:
        return None
    try:
        pe = struct.unpack_from("<I", header, 0x3C)[0]
        return struct.unpack_from("<I", header, pe + 8)[0]
    except (struct.error, IndexError):
        return None


def is_client(pid: int, module_prefix: str = "neuz") -> bool:
    """Is this process actually the game?

    A window whose title starts the same way is not the game - a browser tab
    called after it will do - and opening one raises where the caller was
    enumerating. Asking first turns a crash into a window that is skipped.
    """
    try:
        process = Process(pid)
    except ProcessError:
        return False
    try:
        process.module(module_prefix)
        return True
    except ProcessError:
        return False
    finally:
        process.close()


def open_client(pid: int, writable: bool = False,
                module_prefix: str = "neuz") -> Tuple[Process, Module]:
    """Open a client and resolve its main module in one step."""
    process = Process(pid, writable=writable)
    try:
        return process, process.module(module_prefix)
    except ProcessError:
        process.close()
        raise
