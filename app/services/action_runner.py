from __future__ import annotations
from typing import Callable
from PyQt6.QtCore import QObject
from ..api_action_worker import ApiActionWorker


class ActionRunner(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: ApiActionWorker | None = None
        self._connections: list[tuple] = []

    @property
    def worker(self) -> ApiActionWorker | None:
        return self._worker

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def run(self, worker: ApiActionWorker, *, on_ok: Callable[[str], None] | None = None, on_error: Callable[[str], None] | None = None, on_cancelled: Callable[[], None] | None = None) -> ApiActionWorker:
        self.cancel_and_disconnect()
        self._worker = worker
        pairs: list[tuple] = []
        if on_ok is not None:
            pairs.append((worker.finished_ok, on_ok))
        if on_error is not None:
            pairs.append((worker.finished_error, on_error))
        if on_cancelled is not None:
            pairs.append((worker.finished_cancelled, on_cancelled))
        for signal, slot in pairs:
            signal.connect(slot)
        self._connections = pairs
        worker.start()
        return worker

    def cancel_and_disconnect(self) -> None:
        worker = self._worker
        if worker is None:
            return
        if worker.isRunning():
            worker.request_cancel()
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections = []

    def wait(self, ms: int = 2000) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(ms)

    def cleanup(self) -> None:
        self.cancel_and_disconnect()
        self.wait()
