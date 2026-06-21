from __future__ import annotations
import threading
from PyQt6.QtCore import QThread, pyqtSignal
from .action_cancel import CancelledError, CancelToken, action_cancel_scope
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

    def request_cancel(self) -> None:
        self.cancel.cancel()

    def run(self) -> None:
        result: dict[str, object | None] = {'value': None, 'error': None, 'cancelled': False}
        done = threading.Event()
        debug_event(logger, 'worker start', context=self._context, timeout=self._timeout_sec)

        def target() -> None:
            with action_cancel_scope(self.cancel):
                try:
                    result['value'] = self._action()
                except CancelledError:
                    result['cancelled'] = True
                    debug_event(logger, 'worker cancelled', context=self._context)
                except Exception as exc:
                    result['error'] = exc
                    log_exception(logger, self._context, exc)
                finally:
                    done.set()
        thread = threading.Thread(target=target, daemon=True, name=f'larpbox-{self._context[:24]}')
        thread.start()
        timed_out = False
        if self._timeout_sec is not None:
            if not done.wait(timeout=self._timeout_sec):
                timed_out = True
                self.cancel.cancel()
                done.wait()
        else:
            done.wait()
        if timed_out:
            logger.warning('[%s] timed out after %.1fs', self._context, self._timeout_sec or 0)
            self.finished_error.emit('Request timed out — try again or join their instance.')
            return
        if self.cancel.is_cancelled or result['cancelled']:
            if not timed_out:
                self.finished_cancelled.emit()
            return
        error = result['error']
        if error is not None:
            self.finished_error.emit(str(error) or 'Request failed.')
            return
        message = result['value']
        debug_event(logger, 'worker ok', context=self._context)
        self.finished_ok.emit(str(message) if message else self._success_message)
