from __future__ import annotations
import time

_APP_START = time.monotonic()
STARTUP_WINDOW_SEC = 12.0

_STARTUP_SLOTS_MS: dict[str, int] = {
    'account': 150,
    'friends': 400,
    'player': 900,
    'player_api': 2200,
    'wardrobe': 1200,
}


def app_uptime_ms() -> float:
    return (time.monotonic() - _APP_START) * 1000.0


def in_startup_window() -> bool:
    return time.monotonic() - _APP_START < STARTUP_WINDOW_SEC


def startup_delay_ms(slot: str) -> int:
    target = _STARTUP_SLOTS_MS.get(slot, 0)
    remaining = target - app_uptime_ms()
    return max(0, int(remaining))
