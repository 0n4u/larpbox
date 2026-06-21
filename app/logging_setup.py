from __future__ import annotations
import ctypes
import io
import logging
import sys
import traceback
from pathlib import Path
from typing import Any
_DEBUG_MODE = False
_LOG_FILE: Path | None = None

def is_debug_mode() -> bool:
    return _DEBUG_MODE

def get_log_file_path() -> Path:
    if _LOG_FILE is not None:
        return _LOG_FILE
    return Path(__file__).resolve().parent.parent / 'logs' / 'app.log'

def get_logger(name: str) -> logging.Logger:
    if name.startswith('larpbox.'):
        return logging.getLogger(name)
    return logging.getLogger(f'larpbox.{name}')

def truncate_for_log(text: str | None, max_len: int=96) -> str:
    if text is None:
        return '<none>'
    s = str(text).replace('\r', '\\r').replace('\n', '\\n')
    if len(s) <= max_len:
        return s
    return f'{s[:max_len - 1]}…'

def log_exception(log: logging.Logger, context: str, exc: BaseException, *, exc_info: tuple[type[BaseException], BaseException, object] | bool | None=None) -> None:
    if exc_info is None:
        exc_info = exc
    log.error('[%s] %s: %s', context, type(exc).__name__, exc, exc_info=exc_info)
    if _DEBUG_MODE:
        log.debug('[%s] traceback:\n%s', context, ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

def debug_event(log: logging.Logger, event: str, **fields: Any) -> None:
    if not _DEBUG_MODE:
        return
    if not fields:
        log.debug(event)
        return
    parts = []
    for key, value in fields.items():
        text = str(value).replace('\n', '\\n')
        if len(text) > 120:
            text = f'{text[:117]}…'
        parts.append(f'{key}={text}')
    log.debug('%s | %s', event, ' '.join(parts))

def _enable_virtual_terminal() -> None:
    if sys.platform != 'win32':
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 4)
    except Exception:
        pass

def hide_console() -> None:
    if sys.platform == 'win32':
        try:
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            console_window = kernel32.GetConsoleWindow()
            if console_window:
                user32.ShowWindow(console_window, 0)
        except Exception:
            pass

def show_console() -> None:
    if sys.platform != 'win32':
        return
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        console_window = kernel32.GetConsoleWindow()
        if not console_window:
            kernel32.AllocConsole()
            sys.stdout = open('CONOUT$', 'w', encoding='utf-8', buffering=1, errors='replace')
            sys.stderr = open('CONOUT$', 'w', encoding='utf-8', buffering=1, errors='replace')
        else:
            user32.ShowWindow(console_window, 5)
        _enable_virtual_terminal()
    except Exception:
        pass

class _PlainFormatter(logging.Formatter):

    def __init__(self) -> None:
        super().__init__(fmt='%(asctime)s | %(levelname)-7s | %(name)s | %(message)s', datefmt='%H:%M:%S')

class _ColoredConsoleFormatter(logging.Formatter):
    _RESET = '\x1b[0m'
    _DIM = '\x1b[90m'
    _LEVEL = {logging.DEBUG: '\x1b[36m', logging.INFO: '\x1b[32m', logging.WARNING: '\x1b[33m', logging.ERROR: '\x1b[31m', logging.CRITICAL: '\x1b[35m'}
    _TAG = {'osc': '\x1b[94m', 'media': '\x1b[95m', 'preset': '\x1b[93m', 'config': '\x1b[90m', 'ui': '\x1b[97m'}

    def __init__(self) -> None:
        super().__init__(datefmt='%H:%M:%S')

    def format(self, record: logging.LogRecord) -> str:
        level_color = self._LEVEL.get(record.levelno, self._RESET)
        level = f'{level_color}{record.levelname:<7}{self._RESET}'
        name = record.name.removeprefix('larpbox.')
        tag = ''
        for key, color in self._TAG.items():
            if key in name.lower():
                tag = f'{color}{name}{self._RESET}'
                break
        if not tag:
            tag = f'{self._DIM}{name}{self._RESET}'
        ts = self.formatTime(record, self.datefmt)
        return f'{self._DIM}{ts}{self._RESET} | {level} | {tag} | {record.getMessage()}'

def setup_logging(debug_mode: bool=False, log_file: str | Path | None=None) -> Path:
    global _DEBUG_MODE, _LOG_FILE
    _DEBUG_MODE = debug_mode
    if log_file is None:
        log_file = Path(__file__).resolve().parent.parent / 'logs' / 'app.log'
    else:
        log_file = Path(log_file)
    _LOG_FILE = log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if debug_mode else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    logging.getLogger('larpbox').setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, '_larpbox_handler', False):
            root.removeHandler(handler)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(_PlainFormatter())
    file_handler._larpbox_handler = True
    root.addHandler(file_handler)
    if debug_mode:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(_ColoredConsoleFormatter())
        console_handler._larpbox_handler = True
        root.addHandler(console_handler)
        logger = get_logger('startup')
        logger.info('-' * 56)
        logger.info('larpbox debug mode active')
        logger.info('Console: verbose | File: %s', log_file)
        logger.info('-' * 56)
    else:
        get_logger('startup').info('Logging to %s (INFO)', log_file)
    return log_file

def shutdown_logging() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, '_larpbox_handler', False):
            handler.close()
            root.removeHandler(handler)

def get_debug_mode_from_config() -> bool:
    from app.config import load_config
    try:
        return bool(load_config().get('debug_mode', False))
    except Exception:
        pass
    return False
