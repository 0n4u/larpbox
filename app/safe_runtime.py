from __future__ import annotations
import functools
import logging
import sys
import threading
import traceback
from collections.abc import Callable
from typing import Any, TypeVar
from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
from PyQt6.QtWidgets import QApplication
from .logging_setup import debug_event, get_logger, is_debug_mode, log_exception
logger = get_logger('safe_runtime')
T = TypeVar('T')
_installed = False

def widget_is_valid(widget: Any) -> bool:
    if widget is None:
        return False
    try:
        from PyQt6 import sip
        return not sip.isdeleted(widget)
    except Exception:
        return True

def safe_call(fn: Callable[..., T], /, *args: Any, context: str, log: logging.Logger | None=None, default: T | None=None, **kwargs: Any) -> T | None:
    active = log or logger
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log_exception(active, context, exc)
        return default

def safe_slot(*, context: str | None=None, log: logging.Logger | None=None) -> Callable[[Callable[..., T]], Callable[..., T | None]]:

    def decorator(fn: Callable[..., T]) -> Callable[..., T | None]:
        label = context or fn.__qualname__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T | None:
            return safe_call(fn, *args, context=label, log=log, default=None, **kwargs)
        return wrapper
    return decorator

def connect_safe(signal: Any, slot: Callable[..., Any], *, context: str) -> None:
    wrapped = safe_slot(context=context)(slot)
    signal.connect(wrapped)

def _handle_uncaught(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
    if exc_type is KeyboardInterrupt:
        sys.__excepthook__(exc_type, exc, tb)
        return
    log_exception(logger, 'uncaught exception (main thread)', exc, exc_info=(exc_type, exc, tb))
    try:
        from .services.errors import ErrorBus
        ErrorBus.instance().error(f'Unexpected error: {type(exc).__name__}')
    except Exception:
        pass

def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:
    log_exception(logger, f'uncaught exception (thread {args.thread.name})', args.exc_value, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

def _qt_message_handler(mode: QtMsgType, context: object, message: str) -> None:
    text = message.strip()
    if not text:
        return
    if mode == QtMsgType.QtFatalMsg:
        logger.critical('Qt fatal: %s', text)
    elif mode == QtMsgType.QtCriticalMsg:
        logger.error('Qt critical: %s', text)
    elif mode == QtMsgType.QtWarningMsg:
        if is_debug_mode():
            logger.warning('Qt warning: %s', text)
    elif mode == QtMsgType.QtInfoMsg:
        if is_debug_mode():
            logger.info('Qt info: %s', text)
    elif is_debug_mode():
        logger.debug('Qt debug: %s', text)

def install_crash_guards(app: QApplication | None=None) -> None:
    global _installed
    if _installed:
        return
    _installed = True
    sys.excepthook = _handle_uncaught
    if hasattr(threading, 'excepthook'):
        threading.excepthook = _handle_thread_exception
    try:
        qInstallMessageHandler(_qt_message_handler)
    except Exception as exc:
        logger.debug('Could not install Qt message handler', exc_info=exc)
    if app is not None:
        _orig_notify = app.notify

        def notify(receiver: object, event: object) -> bool:
            try:
                return _orig_notify(receiver, event)
            except Exception as exc:
                receiver_name = type(receiver).__name__ if receiver is not None else 'None'
                log_exception(logger, f'Qt event dispatch ({receiver_name})', exc)
                return False
        app.notify = notify                               
    logger.debug('Crash guards installed (debug=%s)', is_debug_mode())

def run_shutdown_step(label: str, fn: Callable[[], None]) -> None:
    try:
        fn()
        debug_event(logger, 'shutdown step ok', step=label)
    except Exception as exc:
        log_exception(logger, f'shutdown step failed ({label})', exc)
