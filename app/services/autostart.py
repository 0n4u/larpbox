from __future__ import annotations
import sys
from pathlib import Path
from ..config import get_bool, load_config
from ..logging_setup import get_logger

logger = get_logger('autostart')

_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_APP_NAME = 'larpbox'


def is_supported() -> bool:
    return sys.platform == 'win32'


def build_run_command(executable: str, script: str) -> str:
    """Compose the Run-key command string. Pure for testability."""
    if script:
        return f'"{executable}" "{script}"'
    return f'"{executable}"'


def _current_command() -> str:
    executable = sys.executable or 'pythonw.exe'
                                                                         
    if executable.lower().endswith('python.exe'):
        candidate = Path(executable).with_name('pythonw.exe')
        if candidate.exists():
            executable = str(candidate)
    script = ''
    if getattr(sys, 'frozen', False):
        return build_run_command(executable, '')
    try:
        script = str(Path(__file__).resolve().parents[2] / 'run_app.py')
    except Exception:
        script = ''
    return build_run_command(executable, script)


def is_enabled() -> bool:
    if not is_supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    if not is_supported():
        logger.info('Auto-launch is only supported on Windows')
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _current_command())
            else:
                try:
                    winreg.DeleteValue(key, _APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        logger.warning('Could not update auto-launch registry entry', exc_info=True)
        return False


def apply_from_config() -> None:
    """Sync the Run-key with the `launch_on_startup` config flag."""
    if not is_supported():
        return
    desired = get_bool(load_config().get('launch_on_startup', False))
    if desired != is_enabled():
        set_enabled(desired)
