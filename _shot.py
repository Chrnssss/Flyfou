"""Capture one client's UI so its HP, MP and level can be read as ground truth.

The memory probes have run out of ways to tell a real field from a coincidence:
plenty of offsets hold plausible numbers. Knowing the actual values turns that
into an exact search. PrintWindow draws the client without stealing focus, so
nothing is disturbed on the four running clients.
"""
import sys

sys.path.insert(0, ".")

import win32gui
import win32process

from flyfou import vision, winutil


def hwnds_for(pid):
    found = []

    def visit(hwnd, _extra):
        if not win32gui.IsWindowVisible(hwnd):
            return
        _tid, owner = win32process.GetWindowThreadProcessId(hwnd)
        if owner == pid and win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(visit, None)
    return found


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13140
    out = sys.argv[2] if len(sys.argv) > 2 else "_ui.png"

    winutil.enable_dpi_awareness()
    targets = hwnds_for(pid)
    if not targets:
        print(f"no visible window for pid {pid}")
        return 1
    hwnd = targets[0]
    print(f"hwnd 0x{hwnd:X}  {win32gui.GetWindowText(hwnd)!r}")

    image = winutil.print_window(hwnd)
    if image is None or vision.frame_is_blank(image):
        print("frame came back blank")
        return 1
    vision.imwrite(out, image)
    print(f"saved {out}  {image.shape[1]}x{image.shape[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
