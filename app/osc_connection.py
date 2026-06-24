from __future__ import annotations
import threading
import time
from PyQt6.QtCore import QObject, pyqtSignal

_HEALTH_WINDOW_SEC = 90.0


class OscConnectionTracker(QObject):
    status_changed = pyqtSignal(bool, str)
    _instance: OscConnectionTracker | None = None

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._last_success: float | None = None
        self._last_error: str | None = None
        self._last_error_at: float | None = None

    @classmethod
    def instance(cls) -> OscConnectionTracker:
        if cls._instance is None:
            cls._instance = OscConnectionTracker()
        return cls._instance

    def record_success(self) -> None:
        with self._lock:
            self._last_success = time.monotonic()
            self._last_error = None
            self._last_error_at = None
        self.status_changed.emit(True, 'OSC connected')

    def record_failure(self, message: str) -> None:
        detail = (message or 'Send failed').strip()[:160]
        with self._lock:
            self._last_error = detail
            self._last_error_at = time.monotonic()
        self.status_changed.emit(False, detail)

    def _snapshot(self) -> tuple[float | None, str | None, float | None]:
        with self._lock:
            return (self._last_success, self._last_error, self._last_error_at)

    def is_healthy(self) -> bool:
        last_success, _last_error, last_error_at = self._snapshot()
        now = time.monotonic()
        if last_success is not None and now - last_success <= _HEALTH_WINDOW_SEC:
            if last_error_at is None or last_success >= last_error_at:
                return True
        if last_error_at is not None and last_success is None:
            return False
        if last_error_at is not None and last_success is not None:
            return last_success >= last_error_at
        return True

    def tooltip(self) -> str:
        last_success, last_error, last_error_at = self._snapshot()
        healthy = self.is_healthy()
        if last_error and not healthy:
            return f'OSC error: {last_error}'
        if last_success is not None:
            return 'OSC: recent sends OK'
        return 'OSC: no recent activity'
