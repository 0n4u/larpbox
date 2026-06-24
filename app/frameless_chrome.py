from __future__ import annotations
import ctypes
import sys
from PyQt6.QtWidgets import QWidget

def apply_frameless_chrome(window: QWidget) -> None:
    if sys.platform != 'win32':
        return
    try:
        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi
        disabled = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 2, ctypes.byref(disabled), ctypes.sizeof(disabled))
        no_round = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(no_round), ctypes.sizeof(no_round))
    except Exception:
        pass
