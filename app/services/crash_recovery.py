from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from ..atomic_io import atomic_write_json
from ..logging_setup import get_logger

logger = get_logger('crash_recovery')

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = PROJECT_ROOT / 'data' / 'runtime_state.json'

                                                                               
_RESTART_WINDOW_SEC = 600.0
_MAX_RESTARTS = 3


def _read_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug('Could not read runtime state', exc_info=True)
        return {}


def detect_unclean_shutdown(path: Path = STATE_PATH) -> dict[str, Any] | None:
    """Return the previous run's state if it never recorded a clean exit."""
    prev = _read_state(path)
    if prev and not prev.get('clean', True):
        return prev
    return None


def mark_running(path: Path = STATE_PATH) -> None:
    """Record that a run is in progress (clean=False until shutdown)."""
    prev = _read_state(path)
    state = {
        'pid': os.getpid(),
        'started_ts': time.time(),
        'clean': False,
                                                         
        'restart_count': int(prev.get('restart_count') or 0),
        'restart_window_start': float(prev.get('restart_window_start') or 0.0),
    }
    try:
        atomic_write_json(path, state)
    except Exception:
        logger.debug('Could not write runtime state', exc_info=True)


def mark_clean_exit(path: Path = STATE_PATH) -> None:
    """Record a clean shutdown; resets the restart-loop budget."""
    state = _read_state(path)
    state['clean'] = True
    state['ended_ts'] = time.time()
    state['restart_count'] = 0
    state['restart_window_start'] = 0.0
    try:
        atomic_write_json(path, state)
    except Exception:
        logger.debug('Could not write clean-exit state', exc_info=True)


def within_restart_budget(now: float | None = None, path: Path = STATE_PATH) -> bool:
    """True if another auto-restart is allowed without risking a crash loop."""
    now = time.time() if now is None else now
    state = _read_state(path)
    window_start = float(state.get('restart_window_start') or 0.0)
    count = int(state.get('restart_count') or 0)
    if now - window_start > _RESTART_WINDOW_SEC:
        return True
    return count < _MAX_RESTARTS


def register_restart(now: float | None = None, path: Path = STATE_PATH) -> int:
    """Record an auto-restart attempt; returns the count within the current window."""
    now = time.time() if now is None else now
    state = _read_state(path)
    window_start = float(state.get('restart_window_start') or 0.0)
    count = int(state.get('restart_count') or 0)
    if now - window_start > _RESTART_WINDOW_SEC:
        window_start = now
        count = 0
    count += 1
    state['restart_count'] = count
    state['restart_window_start'] = window_start
    state['clean'] = False
    try:
        atomic_write_json(path, state)
    except Exception:
        logger.debug('Could not record restart', exc_info=True)
    return count


def relaunch_detached() -> bool:
    """Spawn a fresh, detached instance of the app. Returns True on success."""
    try:
        import subprocess
        if getattr(sys, 'frozen', False):
            args = [sys.executable]
        else:
            args = [sys.executable, str(PROJECT_ROOT / 'run_app.py')]
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        subprocess.Popen(args, close_fds=True, creationflags=creationflags)
        logger.info('Relaunched larpbox after an unexpected exit')
        return True
    except Exception:
        logger.warning('Could not relaunch after crash', exc_info=True)
        return False
