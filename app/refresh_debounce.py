from __future__ import annotations

from collections.abc import Callable
from PyQt6.QtCore import QObject, QTimer


class RefreshDebouncer(QObject):
    """Coalesce rapid refresh requests into a single callback."""

    def __init__(self, parent: QObject | None, callback: Callable[[], None], *, delay_ms: int = 250) -> None:
        super().__init__(parent)
        self._callback = callback
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._fire)

    def schedule(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.stop()

    def _fire(self) -> None:
        self._callback()
