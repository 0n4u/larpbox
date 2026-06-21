from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from .logging_setup import get_logger
logger = get_logger('config')
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / 'config.json'
VRCHAT_CHATBOX_MIN_INTERVAL_MS = 1500
VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS = 1500
CREDENTIALS_WARNING = 'WARNING: Your VRChat login credentials are stored below. Do not share or stream this file — anyone with these values can access your account.'
AUTH_KEYS = ('remember_login', 'auth_token', 'two_factor_token', 'auth_username', 'auth_display_name', 'auth_user_id')
DEFAULT_CONFIG: dict[str, Any] = {'osc_ip': '127.0.0.1', 'osc_port': 9000, 'insane_egg_mode': False, 'wall_of_china': False, 'egg_mode': True, 'use_secondary_osc': False, 'secondary_osc_ip': '127.0.0.1', 'secondary_osc_port': 9001, 'small_delay_time': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'message_interval': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'blank_message_interval': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'idle_tiny_char': 'U+2060', 'enable_media': False, 'media_format': '{title} by {artist}', 'media_allowed_apps': [], 'debug_mode': False, 'show_friends_list': True, 'show_avatar_search': True, 'show_player_list': True, 'show_account_info': True, 'show_chatbox_preview': True, 'show_preset_config': True, 'show_instance_info': True, 'enable_notifications': True, 'enable_discord_presence': False, 'discord_client_id': '438933080159576067', 'avatar_search_provider': 'avtrdb', 'avatar_search_filter': 'all', 'friend_filter': 'all', 'friend_groups': {}, 'preset_favorites': [], 'preset_recents': [], 'close_to_tray': True, 'enable_hotkeys': True, 'hotkey_preset_1': '', 'hotkey_preset_2': '', 'hotkey_preset_3': '', 'hotkey_preset_4': '', 'hotkey_preset_5': '', 'remember_login': False, 'auth_token': '', 'two_factor_token': '', 'auth_username': '', 'auth_display_name': '', 'auth_user_id': ''}
_DEFAULT_IDLE_CHAR = '\u2060'
_META_KEYS = frozenset({'_credentials_warning'})

def _order_config_for_write(config: dict[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for key, value in config.items():
        if key in AUTH_KEYS or key in _META_KEYS:
            continue
        ordered[key] = value
    ordered['_credentials_warning'] = CREDENTIALS_WARNING
    for key in AUTH_KEYS:
        ordered[key] = config.get(key, DEFAULT_CONFIG.get(key, ''))
    return ordered

def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            stored = json.load(f)
        if not isinstance(stored, dict):
            return DEFAULT_CONFIG.copy()
        merged = DEFAULT_CONFIG.copy()
        merged.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG or k in AUTH_KEYS or k in _META_KEYS})
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
    save_config({'remember_login': remember_login, 'auth_token': auth_token, 'two_factor_token': two_factor_token or '', 'auth_username': username, 'auth_display_name': display_name, 'auth_user_id': user_id})

def clear_auth_session() -> None:
    save_config({'remember_login': False, 'auth_token': '', 'two_factor_token': '', 'auth_username': '', 'auth_display_name': '', 'auth_user_id': ''})
