from __future__ import annotations
import json
import shutil
from pathlib import Path
from .config import CONFIG_PATH, DEFAULT_CONFIG, PROJECT_ROOT, write_config

_LOGS_DIR = PROJECT_ROOT / 'logs'
_DATA_DIR = PROJECT_ROOT / 'data'
_AVATAR_CACHE_PATH = _DATA_DIR / 'avatar_cache.json'
_WARDROBE_CACHE_PATH = _DATA_DIR / 'wardrobe_cache.json'
_RUNTIME_STATE_PATH = _DATA_DIR / 'runtime_state.json'
_DB_PATH = _DATA_DIR / 'larpbox.db'
_PRESETS_PATH = PROJECT_ROOT / 'presets.json'
_ME_JSON_PATH = PROJECT_ROOT / '_me.json'
_DEV_ARTIFACT_DIRS = (
    PROJECT_ROOT / '.firecrawl',
    PROJECT_ROOT / '.pytest_cache',
    PROJECT_ROOT / 'ImageCache',
)


def build_default_config(*, debug: bool = False) -> dict:
    config = DEFAULT_CONFIG.copy()
    config['debug_mode'] = bool(debug)
    return config


def _delete_file(path: Path, actions: list[str], label: str) -> None:
    if not path.is_file():
        return
    try:
        path.unlink()
        actions.append(label)
    except OSError:
        pass


def _delete_tree(path: Path, actions: list[str], label: str) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
        actions.append(label)
    except OSError:
        pass


def _clear_pycache(actions: list[str]) -> None:
    removed = 0
    for cache_dir in PROJECT_ROOT.rglob('__pycache__'):
        try:
            shutil.rmtree(cache_dir)
            removed += 1
        except OSError:
            pass
    if removed:
        actions.append(f'Removed {removed} __pycache__ folder(s)')


def reset_app_data(*, debug: bool = False, distribution: bool = False) -> list[str]:
    actions: list[str] = []
    config = build_default_config(debug=debug)
    write_config(config)
    actions.append(f"Reset settings ({CONFIG_PATH.name}, debug_mode={config['debug_mode']})")
    from .secret_store import SECRETS_PATH, clear_all_secrets
    had_secrets = SECRETS_PATH.is_file()
    clear_all_secrets()
    if had_secrets:
        actions.append('Cleared saved login credentials')
    else:
        actions.append('Cleared credential stores')
    _delete_file(_AVATAR_CACHE_PATH, actions, f'Deleted avatar cache ({_AVATAR_CACHE_PATH.relative_to(PROJECT_ROOT)})')
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _delete_file(_WARDROBE_CACHE_PATH, actions, f'Deleted wardrobe cache ({_WARDROBE_CACHE_PATH.relative_to(PROJECT_ROOT)})')
    _delete_file(_RUNTIME_STATE_PATH, actions, f'Deleted runtime state ({_RUNTIME_STATE_PATH.relative_to(PROJECT_ROOT)})')
    _delete_file(_DB_PATH, actions, f'Deleted local database ({_DB_PATH.relative_to(PROJECT_ROOT)})')
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
    _delete_file(_ME_JSON_PATH, actions, f'Deleted {_ME_JSON_PATH.name}')
    if distribution:
        for extra in _DATA_DIR.glob('*'):
            if extra.is_file() and extra.name not in ('.gitkeep',):
                _delete_file(extra, actions, f'Deleted {extra.relative_to(PROJECT_ROOT)}')
        if _PRESETS_PATH.is_file():
            _PRESETS_PATH.write_text('{}\n', encoding='utf-8')
            actions.append('Reset presets.json to empty')
        for dev_dir in _DEV_ARTIFACT_DIRS:
            _delete_tree(dev_dir, actions, f'Removed {dev_dir.relative_to(PROJECT_ROOT)}/')
        _clear_pycache(actions)
        try:
            import app.preset_storage as preset_storage
            preset_storage._PRESETS_CACHE = None
        except Exception:
            pass
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
    try:
        from .http_proxy import invalidate_proxy_opener
        invalidate_proxy_opener()
    except Exception:
        pass
    return actions


def factory_reset_for_distribution(*, debug: bool = False) -> list[str]:
    return reset_app_data(debug=debug, distribution=True)


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
    if bool(stored.get('remember_login')) != bool(expected.get('remember_login')):
        return False
    from .secret_store import has_secrets
    if has_secrets():
        return False
    if bool(stored.get('debug_mode')) != bool(expected.get('debug_mode')):
        return False
    return not _AVATAR_CACHE_PATH.is_file() and not _WARDROBE_CACHE_PATH.is_file()
