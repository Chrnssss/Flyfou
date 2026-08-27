"""Reading the game's state out of the client's memory.

`process` opens a client and reads bytes out of it. `scan` finds things in
those bytes. `layout` writes down where things are for one build of the client
and `discover` works that out from scratch. Nothing below `layout` knows what a
monster is.
"""

from .learn import Learned, Learner, watch
from .record import Recording, record
from .study import Finding, Study, study
from .world import (Entity, World, WorldReader, PET, PLAYER)
from .discover import (Calibration, Candidate, DiscoveryError, Fields,
                       apply_calibration, calibrate, discover, find_class,
                       find_fields, layout_for, name_like)
from .layout import (Layout, LayoutStore, build_key, default_path,
                     resolve_player, verify)
from .process import (Module, Process, ProcessError, Region, ascii_at,
                      is_client, link_stamp, modules_of, open_client)
from .scan import (HotRegions, contiguous_runs, find_bytes, scan_range,
                   scan_u64, scan_u64_many, sweep_vtable)

__all__ = [
    # process
    "Module",
    "Process",
    "ProcessError",
    "Region",
    "ascii_at",
    "is_client",
    "link_stamp",
    "modules_of",
    "open_client",
    # scan
    "HotRegions",
    "contiguous_runs",
    "find_bytes",
    "scan_range",
    "scan_u64",
    "scan_u64_many",
    "sweep_vtable",
    # layout
    "Layout",
    "LayoutStore",
    "build_key",
    "default_path",
    "resolve_player",
    "verify",
    # discover
    "Calibration",
    "Candidate",
    "DiscoveryError",
    "Fields",
    "apply_calibration",
    "calibrate",
    "discover",
    "find_class",
    "find_fields",
    "layout_for",
    "name_like",
    # world
    "Entity",
    "World",
    "WorldReader",
    "PET",
    "PLAYER",
    # learn
    "Learned",
    "Learner",
    "watch",
    # record and study
    "Recording",
    "record",
    "Finding",
    "Study",
    "study",
]
