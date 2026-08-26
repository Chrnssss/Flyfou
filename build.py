"""Builds dist/Flyfou.exe — one file, no Python needed on the target machine.

    python -m pip install -r requirements.txt -r requirements-build.txt
    python build.py

The exe is built with a console attached so `--headless` has somewhere to
print; GUI mode hides that console immediately (winutil.hide_console).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "flyfou_app.py"
NAME = "Flyfou"

# PyInstaller finds imports by reading the source; these are reached indirectly
# and would otherwise be left out.
HIDDEN = [
    "PIL._tkinter_finder",
    "pydirectinput",
    "keyboard",
    "win32gui",
    "win32ui",
    "win32con",
    "mss.windows",
]

# Nothing here is used, and some of it is enormous.
EXCLUDED = [
    "matplotlib", "scipy", "pandas", "pytest", "IPython", "notebook",
    "PySide6", "PyQt5", "PyQt6", "setuptools", "pip",
]


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller isn't installed. Run:\n"
              "    python -m pip install -r requirements-build.txt", file=sys.stderr)
        return 2

    for stale in ("build", "dist"):
        shutil.rmtree(ROOT / stale, ignore_errors=True)
    (ROOT / f"{NAME}.spec").unlink(missing_ok=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--console",
        "--name", NAME,
        "--noconfirm",
        "--clean",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT),
    ]
    for module in HIDDEN:
        command += ["--hidden-import", module]
    for module in EXCLUDED:
        command += ["--exclude-module", module]
    command.append(str(ENTRY))

    print(" ".join(command), "\n")
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        return result.returncode

    exe = ROOT / "dist" / f"{NAME}.exe"
    size = exe.stat().st_size / (1024 * 1024) if exe.exists() else 0
    print(f"\nBuilt {exe}  ({size:.0f} MB)")
    # Plain ASCII: this prints to whatever console ran the build, and Windows
    # still defaults those to a code page that can't encode a dash.
    print("Hand that single file to anyone on Windows; nothing else needs installing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
