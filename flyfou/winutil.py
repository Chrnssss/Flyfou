"""Windows window handling: DPI, enumeration, client rects, focus."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    import win32gui
except ImportError:  # reported properly by errors.missing_dependencies()
    win32gui = None

try:
    import win32ui
except ImportError:
    win32ui = None

Rect = Tuple[int, int, int, int]  # x, y, w, h

_SHELL_TITLES = {
    "program manager",
    "windows input experience",
    "windows shell experience host",
    "settings",
    "microsoft text input application",
    "nvidia geforce overlay",
}
_MIN_CLIENT = (320, 240)


def enable_dpi_awareness() -> None:
    """Make screen coordinates real pixels. Must run before any Tk window exists."""
    user32 = ctypes.windll.user32
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    process: str
    client: Rect

    @property
    def client_size(self) -> Tuple[int, int]:
        return self.client[2], self.client[3]

    @property
    def label(self) -> str:
        return f"{self.title}  —  {self.process}" if self.process else self.title


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    return ""


def client_rect(hwnd: int) -> Rect:
    """Client area in screen coordinates: (x, y, width, height)."""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    sx, sy = win32gui.ClientToScreen(hwnd, (left, top))
    return sx, sy, right - left, bottom - top


def window_exists(hwnd: Optional[int]) -> bool:
    return bool(hwnd) and bool(win32gui.IsWindow(hwnd)) and bool(win32gui.IsWindowVisible(hwnd))


def is_minimised(hwnd: int) -> bool:
    return bool(win32gui.IsIconic(hwnd))


def is_foreground(hwnd: Optional[int]) -> bool:
    return bool(hwnd) and win32gui.GetForegroundWindow() == hwnd


def window_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def describe(hwnd: int) -> Optional[WindowInfo]:
    if not window_exists(hwnd):
        return None
    try:
        return WindowInfo(hwnd, window_title(hwnd), _process_name(hwnd), client_rect(hwnd))
    except Exception:
        return None


def list_candidate_windows(own_pid_titles: Optional[List[str]] = None) -> List[WindowInfo]:
    """Visible, restored, reasonably-sized top-level windows a game could be."""
    own = {t.lower() for t in (own_pid_titles or [])}
    found: List[WindowInfo] = []

    def cb(hwnd, _extra):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title or title.lower() in _SHELL_TITLES or title.lower() in own:
            return
        try:
            rect = client_rect(hwnd)
        except Exception:
            return
        if rect[2] < _MIN_CLIENT[0] or rect[3] < _MIN_CLIENT[1]:
            return
        found.append(WindowInfo(hwnd, title, _process_name(hwnd), rect))

    win32gui.EnumWindows(cb, None)
    found.sort(key=lambda w: (0 if "flyff" in (w.title + w.process).lower() else 1, w.title.lower()))
    return found


def find_window(title_contains: str = "", process: str = "") -> Optional[WindowInfo]:
    """Locate the game again on a later run.

    Process name is tried first: Flyff clients put the character name and level in
    the title bar, so a title saved today often won't match tomorrow.
    """
    candidates = list_candidate_windows()
    if process:
        for info in candidates:
            if info.process.lower() == process.lower():
                return info
    if title_contains:
        needle = title_contains.lower()
        for info in candidates:
            if needle in info.title.lower():
                return info
    return None


def bring_to_front(hwnd: int) -> bool:
    """Best-effort focus steal. We're the foreground app when the user clicks a
    button, which is exactly when Windows permits this."""
    SW_RESTORE = 9
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        try:
            ctypes.windll.user32.SwitchToThisWindow(hwnd, True)
            return True
        except Exception:
            return False


def print_window(hwnd: int):
    """Ask the window to draw itself into a bitmap, which works even when it's
    covered by our own windows. Games that render through DirectX often return a
    black frame instead, so callers must check and fall back to screen capture."""
    if win32ui is None or win32gui is None:
        return None
    import numpy as np

    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    width, height = right - left, bottom - top
    if width < 1 or height < 1:
        return None

    window_dc = mfc_dc = save_dc = bitmap = None
    try:
        window_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(window_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        PW_CLIENTONLY_RENDERFULLCONTENT = 3
        if not ctypes.windll.user32.PrintWindow(
            hwnd, save_dc.GetSafeHdc(), PW_CLIENTONLY_RENDERFULLCONTENT
        ):
            return None
        info = bitmap.GetInfo()
        raw = bitmap.GetBitmapBits(True)
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(info["bmHeight"], info["bmWidth"], 4)
        return frame[:, :, :3].copy()
    except Exception:
        return None
    finally:
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
            if save_dc is not None:
                save_dc.DeleteDC()
            if mfc_dc is not None:
                mfc_dc.DeleteDC()
            if window_dc:
                win32gui.ReleaseDC(hwnd, window_dc)
        except Exception:
            pass


def virtual_screen() -> Rect:
    user32 = ctypes.windll.user32
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def hide_console() -> None:
    """The exe is built with a console so headless mode has somewhere to print;
    GUI mode hides it on the way up."""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass
