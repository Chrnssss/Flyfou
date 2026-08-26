"""What PyInstaller builds into Flyfou.exe. See build.py."""

import sys

from flyfou.cli import main

if __name__ == "__main__":
    sys.exit(main())
