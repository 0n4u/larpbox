from __future__ import annotations
"""Shared UI performance helpers."""

from .api_startup import in_startup_window
from .theme import motion_enabled

_LIST_BATCH_FAST = 8
_LIST_BATCH_NORMAL = 12
_STARTUP_BATCH = 4
_YIELD_MS = 12
_ANIMATE_INTERVAL_MS = 24


def should_animate_lists() -> bool:
    return motion_enabled()


def list_populate_batch(*, normal: int = _LIST_BATCH_NORMAL, fast: int = _LIST_BATCH_FAST) -> int:
    """Smaller batches during startup to keep the event loop responsive."""
    if in_startup_window():
        return _STARTUP_BATCH
    return normal if should_animate_lists() else fast


def list_populate_delay_ms(*, animated: bool) -> int:
    if animated:
        return _ANIMATE_INTERVAL_MS
    return _YIELD_MS
