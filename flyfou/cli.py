"""Entry point for both modes.

Everything heavy is imported lazily so a missing component produces a readable
message instead of a traceback.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import APP_NAME, VERSION, errors


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flyfou",
        description=f"{APP_NAME} {VERSION} — run it with no arguments for the normal window.",
    )
    parser.add_argument("--headless", action="store_true",
                        help="run a profile in this console, with no windows")
    parser.add_argument("--profile", metavar="NAME", help="which profile to run headless")
    parser.add_argument("--list", action="store_true", dest="list_profiles",
                        help="print the saved profile names and exit")
    parser.add_argument("--profiles-dir", metavar="PATH",
                        help="use profiles from somewhere other than the default folder")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    return parser


def _console_speaks_utf8() -> None:
    """Windows consoles still default to a legacy code page, and these messages
    are full of bullets and arrows. Without this, printing one raises."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _console_speaks_utf8()
    args = _build_parser().parse_args(argv)
    console_mode = args.headless or args.list_profiles

    missing = errors.missing_dependencies()
    if missing:
        _report_fatal(errors.dependency_report(missing), console_mode)
        return 2

    from . import winutil
    winutil.enable_dpi_awareness()

    from .profile import ProfileStore
    store = ProfileStore(args.profiles_dir) if args.profiles_dir else ProfileStore()

    if args.list_profiles:
        names = store.names()
        print("\n".join(names) if names else "No profiles yet. Run Flyfou without arguments to make one.")
        return 0

    if args.headless:
        return _run_headless(store, args.profile)

    if getattr(sys, "frozen", False):
        winutil.hide_console()
    from .gui.launcher import run
    return run()


def _run_headless(store, name: Optional[str]) -> int:
    from .bot import run_headless
    from .errors import FlyfouError

    names = store.names()
    if not name:
        if len(names) != 1:
            print("Say which profile to run, for example:  Flyfou.exe --headless --profile "
                  f"\"{names[0] if names else 'my-spot'}\"", file=sys.stderr)
            if names:
                print("\nProfiles you have:\n  " + "\n  ".join(names), file=sys.stderr)
            return 2
        name = names[0]

    try:
        profile = store.load(name)
    except FlyfouError as exc:
        print(exc.full(), file=sys.stderr)
        return 2

    problems = profile.problems()
    if problems:
        print(f"Profile '{profile.name}' isn't finished yet:\n  • " + "\n  • ".join(problems),
              file=sys.stderr)
        print("\nRun Flyfou without --headless and press Set up to finish it.", file=sys.stderr)
        return 2

    return run_headless(profile)


def _report_fatal(text: str, console_mode: bool) -> None:
    if not console_mode:
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_NAME, text)
            root.destroy()
            return
        except Exception:
            pass
    print(text, file=sys.stderr)
