from __future__ import annotations
import threading
from PyQt6.QtCore import QThread, pyqtSignal
from .action_cancel import CancelledError, CancelToken, action_cancel_scope
from .api_rate_limit import ApiPriority, api_priority
from .logging_setup import debug_event, get_logger, log_exception
logger = get_logger('api_worker')

class ApiActionWorker(QThread):
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)
    finished_cancelled = pyqtSignal()

    def __init__(self, action, success_message: str='Done.', *, timeout_sec: float | None=None, context: str='api action'):
        super().__init__()
        self._action = action
        self._success_message = success_message
        self._timeout_sec = timeout_sec
        self._context = context
        self.cancel = CancelToken()
        self._timed_out = False

    def request_cancel(self) -> None:
        self.cancel.cancel()

    def _on_timeout(self) -> None:
        self._timed_out = True
        self.cancel.cancel()

    def run(self) -> None:
        debug_event(logger, 'worker start', context=self._context, timeout=self._timeout_sec)
        timer: threading.Timer | None = None
        if self._timeout_sec is not None:
            timer = threading.Timer(self._timeout_sec, self._on_timeout)
            timer.daemon = True
            timer.start()
        try:
            with action_cancel_scope(self.cancel):
                with api_priority(ApiPriority.INTERACTIVE):
                    value = self._action()
        except CancelledError:
            if self._timed_out:
                logger.warning('[%s] timed out after %.1fs', self._context, self._timeout_sec or 0)
                self.finished_error.emit('Request timed out — try again or join their instance.')
            else:
                debug_event(logger, 'worker cancelled', context=self._context)
                self.finished_cancelled.emit()
            return
        except Exception as exc:
            log_exception(logger, self._context, exc)
            from .services.errors import report_worker_error
            report_worker_error(self._context, exc)
            self.finished_error.emit(str(exc) or 'Request failed.')
            return
        finally:
            if timer is not None:
                timer.cancel()
        if self._timed_out:
            logger.warning('[%s] timed out after %.1fs', self._context, self._timeout_sec or 0)
            self.finished_error.emit('Request timed out — try again or join their instance.')
            return
        if self.cancel.is_cancelled:
            self.finished_cancelled.emit()
            return
        debug_event(logger, 'worker ok', context=self._context)
        self.finished_ok.emit(str(value) if value else self._success_message)
