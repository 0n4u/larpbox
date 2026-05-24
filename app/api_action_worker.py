from __future__ import annotations

import threading

from PyQt6.QtCore import QThread, pyqtSignal


class ApiActionWorker(QThread):
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, action, success_message: str = 'Done.', *, timeout_sec: float | None = None):
        super().__init__()
        self._action = action
        self._success_message = success_message
        self._timeout_sec = timeout_sec

    def run(self) -> None:
        result: dict[str, object | None] = {'value': None, 'error': None}
        done = threading.Event()

        def target() -> None:
            try:
                result['value'] = self._action()
            except Exception as exc:
                result['error'] = exc
            finally:
                done.set()

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        if self._timeout_sec is not None:
            if not done.wait(timeout=self._timeout_sec):
                self.finished_error.emit('Request timed out — try again or join their instance.')
                return
        else:
            done.wait()
        error = result['error']
        if error is not None:
            self.finished_error.emit(str(error) or 'Request failed.')
            return
        message = result['value']
        self.finished_ok.emit(str(message) if message else self._success_message)
