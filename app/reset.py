from __future__ import annotations

import json
from pathlib import Path

from .config import CONFIG_PATH, DEFAULT_CONFIG, PROJECT_ROOT, write_config

_LOGS_DIR = PROJECT_ROOT / 'logs'
_AVATAR_CACHE_PATH = PROJECT_ROOT / 'data' / 'avatar_cache.json'
_ME_JSON_PATH = PROJECT_ROOT / '_me.json'


def build_default_config(*, debug: bool = False) -> dict:
    config = DEFAULT_CONFIG.copy()
    config['debug_mode'] = bool(debug)
    return config


def reset_app_data(*, debug: bool = False) -> list[str]:
    actions: list[str] = []

    config = build_default_config(debug=debug)
    write_config(config)
    actions.append(
        f'Reset settings and cleared login ({CONFIG_PATH.name}, debug_mode={config["debug_mode"]})'
    )

    if _AVATAR_CACHE_PATH.is_file():
        _AVATAR_CACHE_PATH.unlink()
        actions.append(f'Deleted avatar cache ({_AVATAR_CACHE_PATH.relative_to(PROJECT_ROOT)})')
    else:
        _AVATAR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if _LOGS_DIR.is_dir():
        cleared_logs = 0
        for log_file in _LOGS_DIR.glob('*.log'):
            try:
                log_file.unlink(missing_ok=True)
                cleared_logs += 1
            except OSError:
                try:
                    log_file.write_text('', encoding='utf-8')
                    cleared_logs += 1
                except OSError:
                    pass
        if cleared_logs:
            actions.append(f'Cleared {cleared_logs} log file(s) in logs/')
        else:
            actions.append('No log files to clear in logs/')

    if _ME_JSON_PATH.is_file():
        _ME_JSON_PATH.unlink()
        actions.append(f'Deleted {_ME_JSON_PATH.name}')

    try:
        from . import avatar_cache

        avatar_cache._CACHE = None
    except Exception:
        pass

    return actions


def config_is_fresh(*, debug: bool = False) -> bool:
    if not CONFIG_PATH.is_file():
        return True
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return False
    if not isinstance(stored, dict):
        return False
    expected = build_default_config(debug=debug)
    auth_keys = (
        'auth_token',
        'two_factor_token',
        'auth_username',
        'auth_display_name',
        'auth_user_id',
        'remember_login',
    )
    for key in auth_keys:
        if str(stored.get(key, '')) != str(expected.get(key, '')):
            return False
    if bool(stored.get('debug_mode')) != bool(expected.get('debug_mode')):
        return False
    return not _AVATAR_CACHE_PATH.is_file()
