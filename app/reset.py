from __future__ import annotations
import json
from .config import CONFIG_PATH, DEFAULT_CONFIG, PROJECT_ROOT, write_config
_LOGS_DIR = PROJECT_ROOT / 'logs'
_AVATAR_CACHE_PATH = PROJECT_ROOT / 'data' / 'avatar_cache.json'
_WARDROBE_CACHE_PATH = PROJECT_ROOT / 'data' / 'wardrobe_cache.json'
_ME_JSON_PATH = PROJECT_ROOT / '_me.json'

def build_default_config(*, debug: bool=False) -> dict:
    config = DEFAULT_CONFIG.copy()
    config['debug_mode'] = bool(debug)
    return config

def reset_app_data(*, debug: bool=False) -> list[str]:
    actions: list[str] = []
    config = build_default_config(debug=debug)
    write_config(config)
    actions.append(f"Reset settings ({CONFIG_PATH.name}, debug_mode={config['debug_mode']})")
    from .secret_store import SECRETS_PATH, clear_secrets
    had_secrets = SECRETS_PATH.is_file()
    clear_secrets()
    if had_secrets:
        actions.append('Cleared saved login credentials')
    if _AVATAR_CACHE_PATH.is_file():
        _AVATAR_CACHE_PATH.unlink()
        actions.append(f'Deleted avatar cache ({_AVATAR_CACHE_PATH.relative_to(PROJECT_ROOT)})')
    else:
        _AVATAR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _WARDROBE_CACHE_PATH.is_file():
        _WARDROBE_CACHE_PATH.unlink()
        actions.append(f'Deleted wardrobe cache ({_WARDROBE_CACHE_PATH.relative_to(PROJECT_ROOT)})')
    try:
        from .image_cache import cache_root, clear_image_cache
        had_image_cache = cache_root().is_dir()
        clear_image_cache()
        if had_image_cache:
            actions.append('Cleared image cache (ImageCache/)')
    except Exception:
        pass
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
    try:
        from . import wardrobe_cache
        wardrobe_cache._CACHE = None
    except Exception:
        pass
    return actions

def config_is_fresh(*, debug: bool=False) -> bool:
    if not CONFIG_PATH.is_file():
        return True
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return False
    if not isinstance(stored, dict):
        return False
    expected = build_default_config(debug=debug)
    if bool(stored.get('remember_login')) != bool(expected.get('remember_login')):
        return False
    from .secret_store import has_secrets
    if has_secrets():
        return False
    if bool(stored.get('debug_mode')) != bool(expected.get('debug_mode')):
        return False
    return not _AVATAR_CACHE_PATH.is_file() and not _WARDROBE_CACHE_PATH.is_file()
