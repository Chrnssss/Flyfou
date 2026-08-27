"""Finding things in a client's memory.

The bot's whole perception rests on one trick: every instance of a polymorphic
C++ class begins with the same vtable pointer, so sweeping the heap for that one
qword enumerates every entity that exists, with no container to locate and
nothing to walk. Twelve probes went looking for the object list the client
actually uses and found, in turn, fourteen kilobytes of unrelated .data, the NT
heap's own free list, and a real array with no discoverable bounds and no
pointer to it anywhere in the process. The sweep was correct the entire time.

Its one flaw was cost, and that is what `HotRegions` is for. Entities cluster:
on a busy map they occupied ten regions totalling 4.4 MB, which is 0.3% of the
client's private heap, and that set did not change over twelve seconds. Sweeping
only those regions found exactly the same entities 49 times faster - 75ms rather
than 3.7s. A full sweep every so often picks up regions the game has since
started allocating from.

Scans read 8-aligned qwords. Heap blocks are 16-aligned and a vtable is the
first member, so nothing that matters is missed, and an alignment filter beyond
that only drops real objects.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .process import Process, Region

FULL_SWEEP_EVERY = 30.0


def _words(blob: bytes) -> np.ndarray:
    """A qword view of a blob, ignoring any ragged tail."""
    usable = len(blob) // 8 * 8
    if usable < 8:
        return np.empty(0, dtype=np.uint64)
    return np.frombuffer(blob[:usable], dtype=np.uint64)


def _regions_for(process: Process, regions: Optional[Sequence[Region]],
                 private_only: bool) -> List[Region]:
    if regions is not None:
        return list(regions)
    return process.regions(private_only=private_only)


def scan_u64(process: Process, value: int,
             regions: Optional[Sequence[Region]] = None,
             private_only: bool = True) -> List[int]:
    """Every 8-aligned address holding exactly `value`."""
    needle = np.uint64(value & 0xFFFFFFFFFFFFFFFF)
    out: List[int] = []
    for region in _regions_for(process, regions, private_only):
        words = _words(process.read_partial(region.base, region.size))
        if words.size:
            out.extend(region.base + int(i) * 8
                       for i in np.nonzero(words == needle)[0])
    return out


def scan_u64_many(process: Process, values: Iterable[int],
                  regions: Optional[Sequence[Region]] = None,
                  private_only: bool = True) -> List[Tuple[int, int]]:
    """Every 8-aligned address holding any of `values`, as (address, value)."""
    wanted = np.array(sorted({v & 0xFFFFFFFFFFFFFFFF for v in values}), dtype=np.uint64)
    if not wanted.size:
        return []
    out: List[Tuple[int, int]] = []
    for region in _regions_for(process, regions, private_only):
        words = _words(process.read_partial(region.base, region.size))
        if words.size:
            for i in np.nonzero(np.isin(words, wanted))[0]:
                out.append((region.base + int(i) * 8, int(words[i])))
    out.sort()
    return out


def scan_range(process: Process, low: int, high: int,
               regions: Optional[Sequence[Region]] = None,
               private_only: bool = True) -> List[Tuple[int, int]]:
    """Addresses whose qword points somewhere in [low, high)."""
    lo, hi = np.uint64(low), np.uint64(high)
    out: List[Tuple[int, int]] = []
    for region in _regions_for(process, regions, private_only):
        words = _words(process.read_partial(region.base, region.size))
        if not words.size:
            continue
        for i in np.nonzero((words >= lo) & (words < hi))[0]:
            out.append((region.base + int(i) * 8, int(words[i])))
    out.sort()
    return out


def find_bytes(process: Process, needle: bytes,
               regions: Optional[Sequence[Region]] = None,
               private_only: bool = False,
               limit: int = 4096) -> List[int]:
    """Every occurrence of a byte pattern, at any alignment."""
    out: List[int] = []
    if not needle:
        return out
    for region in _regions_for(process, regions, private_only):
        blob = process.read_partial(region.base, region.size)
        at = blob.find(needle)
        while at >= 0:
            out.append(region.base + at)
            if len(out) >= limit:
                return out
            at = blob.find(needle, at + 1)
    return out


def sweep_vtable(process: Process, vtable: int,
                 regions: Optional[Sequence[Region]] = None
                 ) -> Tuple[List[int], Dict[int, int]]:
    """Every object of one class, and how many were found per region."""
    needle = np.uint64(vtable)
    found: List[int] = []
    homes: Dict[int, int] = {}
    for region in _regions_for(process, regions, private_only=True):
        words = _words(process.read_partial(region.base, region.size))
        if not words.size:
            continue
        hits = np.nonzero(words == needle)[0]
        if hits.size:
            homes[region.base] = int(hits.size)
            found.extend(region.base + int(i) * 8 for i in hits)
    return found, homes


class HotRegions:
    """Sweeps only the regions that held objects last time.

    A full sweep costs seconds and a narrow one costs milliseconds, so the
    narrow one runs every tick and the full one runs occasionally - often
    enough that a region the game only just started allocating entities from
    is picked up within `interval` seconds rather than never.
    """

    def __init__(self, interval: float = FULL_SWEEP_EVERY):
        self.interval = interval
        self.bases: set = set()
        self.last_full: float = 0.0
        self.full_sweeps = 0
        self.narrow_sweeps = 0
        self._busy = False

    def forget(self) -> None:
        """Force the next sweep to look everywhere."""
        self.bases = set()
        self.last_full = 0.0

    def _due(self, now: float) -> bool:
        return not self.bases or now - self.last_full >= self.interval

    def _refresh_later(self, process: Process, vtable: int) -> None:
        """Re-scan everywhere on another thread.

        A full sweep of a busy map costs two to three seconds, and doing that on
        the tick froze the bot mid-fight every half minute - which from the
        outside looked like it stopping and starting again for no reason. The
        regions it finds are only a hint about where to look next time, so they
        are perfectly happy to arrive late.
        """
        self._busy = True

        def work():
            try:
                _found, homes = sweep_vtable(process, vtable)
                self.bases = self.bases | set(homes)
                self.last_full = time.monotonic()
                self.full_sweeps += 1
            except Exception:                         # noqa: BLE001
                pass                                  # the client went away
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()

    def sweep(self, process: Process, vtable: int,
              force_full: bool = False) -> List[int]:
        now = time.monotonic()
        if force_full or not self.bases:
            found, homes = sweep_vtable(process, vtable)
            self.bases = set(homes)
            self.last_full = now
            self.full_sweeps += 1
            return found

        if now - self.last_full >= self.interval and not self._busy:
            self._refresh_later(process, vtable)

        known = [r for r in process.regions(private_only=True) if r.base in self.bases]
        found, homes = sweep_vtable(process, vtable, regions=known)
        self.narrow_sweeps += 1
        if not found:
            # The heap moved under us; fall back rather than report an empty world.
            return self.sweep(process, vtable, force_full=True)
        self.bases |= set(homes)
        return found


def contiguous_runs(sites: Sequence[Tuple[int, int]], stride: int = 8,
                    slack: int = 0) -> List[List[Tuple[int, int]]]:
    """Group sorted (address, value) pairs into runs of evenly spaced slots.

    Used by the offset finder to recognise an array of pointers among the
    scattered single references that any object accumulates.
    """
    runs: List[List[Tuple[int, int]]] = []
    run: List[Tuple[int, int]] = []
    for site in sites:
        if run and 0 < site[0] - run[-1][0] <= stride * (slack + 1):
            run.append(site)
        else:
            if run:
                runs.append(run)
            run = [site]
    if run:
        runs.append(run)
    runs.sort(key=lambda group: len({value for _a, value in group}), reverse=True)
    return runs
