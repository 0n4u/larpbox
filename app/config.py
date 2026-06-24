from __future__ import annotations
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any
from .logging_setup import get_logger
logger = get_logger('config')
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / 'config.json'
VRCHAT_CHATBOX_MIN_INTERVAL_MS = 1500
VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS = 1500
AUTH_KEYS = ('auth_token', 'two_factor_token', 'auth_username', 'auth_display_name', 'auth_user_id')
DEFAULT_CONFIG: dict[str, Any] = {'osc_ip': '127.0.0.1', 'osc_port': 9000, 'insane_egg_mode': False, 'wall_of_china': False, 'egg_mode': True, 'use_secondary_osc': False, 'secondary_osc_ip': '127.0.0.1', 'secondary_osc_port': 9001, 'small_delay_time': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'message_interval': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'blank_message_interval': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'idle_tiny_char': 'U+2060', 'enable_media': False, 'media_format': '{title} by {artist}', 'media_allowed_apps': [], 'debug_mode': False, 'show_friends_list': True, 'show_avatar_search': True, 'show_player_list': True, 'show_account_info': True, 'show_chatbox_preview': True, 'show_preset_config': True, 'show_instance_info': True, 'enable_notifications': True, 'enable_discord_presence': False, 'discord_client_id': '438933080159576067', 'avatar_search_provider': 'avtrdb', 'avatar_search_filter': 'all', 'http_proxy': '', 'friend_filter': 'all', 'friend_groups': {}, 'preset_favorites': [], 'preset_recents': [], 'close_to_tray': True, 'enable_hotkeys': True, 'hotkey_preset_1': '', 'hotkey_preset_2': '', 'hotkey_preset_3': '', 'hotkey_preset_4': '', 'hotkey_preset_5': '', 'remember_login': False, 'enable_activity_log': True, 'enable_pipeline': False, 'enable_analytics': True, 'enable_vr_overlay': False, 'enable_multi_account': False, 'show_activity_feed': True, 'show_notification_center': True, 'show_dashboard': True, 'dashboard_layout': [{'name': 'Overview', 'widgets': ['playtime', 'recent_activity', 'notifications', 'favorites']}, {'name': 'Worlds', 'widgets': ['recent_worlds', 'media']}], 'show_world_tools': True, 'status_presets': [{'label': 'Available', 'status': 'join me', 'description': 'Come hang out'}, {'label': 'Chilling', 'status': 'active', 'description': ''}, {'label': 'Focusing', 'status': 'ask me', 'description': 'Ask before joining'}, {'label': 'Away', 'status': 'busy', 'description': 'AFK'}], 'launch_on_startup': False, 'enable_server_status': True, 'auto_restart_on_crash': False, 'reduce_motion': True}
_DEFAULT_IDLE_CHAR = '\u2060'
_LEGACY_META_KEYS = frozenset({'_credentials_warning'})

_config_cache_lock = threading.Lock()
_cached_config: dict[str, Any] | None = None
_cached_mtime: float | None = None

def _config_mtime() -> float | None:
    try:
        return CONFIG_PATH.stat().st_mtime
    except OSError:
        return None

def load_config_cached() -> dict[str, Any]:
    global _cached_config, _cached_mtime
    mtime = _config_mtime()
    with _config_cache_lock:
        if _cached_config is not None and _cached_mtime == mtime:
            return _cached_config.copy()
    config = load_config()
    with _config_cache_lock:
        _cached_config = config
        _cached_mtime = mtime
    return config.copy()

def _invalidate_config_cache() -> None:
    global _cached_config, _cached_mtime
    with _config_cache_lock:
        _cached_config = None
        _cached_mtime = None

def _order_config_for_write(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if key not in AUTH_KEYS and key not in _LEGACY_META_KEYS}

def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            stored = json.load(f)
        if not isinstance(stored, dict):
            return DEFAULT_CONFIG.copy()
        merged = DEFAULT_CONFIG.copy()
        merged.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
        logger.debug('Loaded config from %s (%d keys)', CONFIG_PATH, len(merged))
        return merged
    except Exception:
        logger.warning('Failed to load config — using defaults', exc_info=True)
        return DEFAULT_CONFIG.copy()

def write_config(config: dict[str, Any]) -> None:
    ordered = _order_config_for_write(config)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='config.', suffix='.json', dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(ordered, f, indent=4)
        os.replace(temp_path, CONFIG_PATH)
        _invalidate_config_cache()
        logger.debug('Wrote full config (%d keys)', len(ordered))
    except Exception as exc:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        logger.error('Failed to write config to %s', CONFIG_PATH, exc_info=True)
        try:
            from .services.errors import ErrorBus
            ErrorBus.instance().error(f'Could not save settings: {exc}')
        except Exception:
            pass
        raise

def save_config(updates: dict[str, Any]) -> None:
    config = load_config()
    config.update(updates)
    try:
        write_config(config)
    except Exception:
        logger.warning('Config update not persisted: %s', ', '.join(sorted(updates.keys())))
        return
    logger.debug('Saved config updates: %s', ', '.join(sorted(updates.keys())))

def get_bool(value: Any, default: bool=False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == 'true'
    return default

def parse_idle_char(value: Any, default: str=_DEFAULT_IDLE_CHAR) -> str:
    try:
        if isinstance(value, str):
            s = value.strip()
            if len(s) == 1:
                return s
            if s.lower().startswith('u+'):
                s = s[2:]
            if s.lower().startswith('0x'):
                s = s[2:]
            return chr(int(s, 16))
    except Exception:
        pass
    return default

def clamp_message_interval(ms: int) -> int:
    return max(VRCHAT_CHATBOX_MIN_INTERVAL_MS, int(ms))

def message_interval_ms(config: dict[str, Any] | None=None) -> int:
    cfg = config if config is not None else load_config()
    delay = int(cfg.get('message_interval', cfg.get('small_delay_time', VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS)))
    return clamp_message_interval(delay)

def save_auth_session(*, auth_token: str, two_factor_token: str | None, username: str, display_name: str, user_id: str, remember_login: bool) -> None:
    from .secret_store import clear_secrets, save_secrets
    save_config({'remember_login': bool(remember_login)})
    if not remember_login or not auth_token:
        clear_secrets()
        return
    save_secrets({'auth_token': auth_token, 'two_factor_token': two_factor_token or '', 'auth_username': username, 'auth_display_name': display_name, 'auth_user_id': user_id})

def clear_auth_session() -> None:
    from .secret_store import clear_all_secrets
    save_config({'remember_login': False})
    clear_all_secrets()

def load_auth_session() -> dict[str, Any]:
    from .secret_store import load_secrets
    data = load_secrets()
    return {'remember_login': get_bool(load_config().get('remember_login')), 'auth_token': str(data.get('auth_token') or ''), 'two_factor_token': str(data.get('two_factor_token') or ''), 'auth_username': str(data.get('auth_username') or ''), 'auth_display_name': str(data.get('auth_display_name') or ''), 'auth_user_id': str(data.get('auth_user_id') or '')}

def migrate_legacy_auth() -> None:
    if not CONFIG_PATH.exists():
        return
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            stored = json.load(f)
    except Exception:
        return
    if not isinstance(stored, dict):
        return
    has_legacy_keys = any(key in stored for key in AUTH_KEYS) or '_credentials_warning' in stored
    if not has_legacy_keys:
        return
    from .secret_store import load_secrets, save_secrets
    token = str(stored.get('auth_token') or '').strip()
    if token and get_bool(stored.get('remember_login')) and (not load_secrets().get('auth_token')):
        save_secrets({'auth_token': token, 'two_factor_token': str(stored.get('two_factor_token') or ''), 'auth_username': str(stored.get('auth_username') or ''), 'auth_display_name': str(stored.get('auth_display_name') or ''), 'auth_user_id': str(stored.get('auth_user_id') or '')})
        logger.info('Migrated saved VRChat login into encrypted credential store')
    cleaned = {k: v for k, v in stored.items() if k not in AUTH_KEYS and k not in _LEGACY_META_KEYS}
    if cleaned != stored:
        try:
            write_config(cleaned)
            logger.info('Removed plaintext auth fields from config.json')
        except Exception:
            logger.warning('Failed to strip plaintext auth fields from config.json', exc_info=True)
