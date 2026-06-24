from __future__ import annotations
from dataclasses import dataclass
from ..logging_setup import get_logger
logger = get_logger('vrchat_window')
_VRCHAT_PROCESS = 'vrchat.exe'

@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

def _process_name_for_hwnd(hwnd: int) -> str | None:
    try:
        import psutil
        import win32process
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name().lower()
    except Exception:
        return None

def find_vrchat_window() -> tuple[int, WindowRect] | None:
    try:
        import win32gui
    except ImportError:
        logger.debug('win32gui unavailable — VRChat window tracking disabled')
        return None
    best: tuple[int, WindowRect, int] | None = None

    def callback(hwnd, _extra) -> bool:
        nonlocal best
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.IsIconic(hwnd):
            return True
        if _process_name_for_hwnd(hwnd) != _VRCHAT_PROCESS:
            return True
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        width = right - left
        height = bottom - top
        if width < 320 or height < 240:
            return True
        area = width * height
        rect = WindowRect(left, top, width, height)
        if best is None or area > best[2]:
            best = (hwnd, rect, area)
        return True
    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        logger.debug('VRChat window enumeration failed', exc_info=True)
        return None
    if best is None:
        return None
    return (best[0], best[1])

def is_vrchat_foreground() -> bool:
    try:
        import win32gui
    except ImportError:
        return False
    try:
        foreground = win32gui.GetForegroundWindow()
        if not foreground:
            return False
        return _process_name_for_hwnd(foreground) == _VRCHAT_PROCESS
    except Exception:
        return False
