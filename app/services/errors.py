from __future__ import annotations
from PyQt6.QtCore import QObject, pyqtSignal
from .auth_errors import is_auth_failure

class ErrorBus(QObject):
    message_posted = pyqtSignal(str, str)
    _instance: 'ErrorBus | None' = None

    @classmethod
    def instance(cls) -> 'ErrorBus':
        if cls._instance is None:
            cls._instance = ErrorBus()
        return cls._instance

    def info(self, text: str) -> None:
        self.message_posted.emit(text, 'info')

    def warning(self, text: str) -> None:
        self.message_posted.emit(text, 'warning')

    def error(self, text: str) -> None:
        self.message_posted.emit(text, 'error')

    def session_expired(self) -> None:
        from .session_manager import SessionManager
        SessionManager.instance().try_handle_auth_failure('VRChat session expired.')

def report_api_error(context: str, exc: Exception) -> None:
    from .session_manager import SessionManager
    if is_auth_failure(exc):
        reason = f'{context}: session invalid.' if context else 'VRChat session invalid.'
        if SessionManager.instance().try_handle_auth_failure(reason):
            return
        return
    message = str(exc).strip() or type(exc).__name__
    ErrorBus.instance().warning(f'{context}: {message[:160]}')
