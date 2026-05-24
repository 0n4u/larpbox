from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal


class ApiActionWorker(QThread):
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, action, success_message: str = 'Done.'):
        super().__init__()
        self._action = action
        self._success_message = success_message

    def run(self) -> None:
        try:
            message = self._action()
            self.finished_ok.emit(message or self._success_message)
        except Exception as exc:
            self.finished_error.emit(str(exc) or 'Request failed.')
