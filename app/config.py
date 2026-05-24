from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .logging_setup import get_logger
logger = get_logger('config')
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / 'config.json'
VRCHAT_CHATBOX_MIN_INTERVAL_MS = 1500
VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS = 1500
DEFAULT_CONFIG: dict[str, Any] = {'osc_ip': '127.0.0.1', 'osc_port': 9000, 'insane_egg_mode': False, 'wall_of_china': False, 'egg_mode': True, 'use_secondary_osc': False, 'secondary_osc_ip': '127.0.0.1', 'secondary_osc_port': 9001, 'small_delay_time': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'message_interval': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'blank_message_interval': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'idle_tiny_char': 'U+2060', 'enable_media': False, 'debug_mode': False, 'remember_login': False, 'auth_token': '', 'two_factor_token': '', 'auth_username': '', 'auth_display_name': '', 'auth_user_id': '', 'show_friends_list': True, 'show_avatar_search': True, 'show_player_list': True, 'show_account_info': True, 'show_chatbox_preview': True, 'show_preset_config': True, 'avatar_search_provider': 'avtrdb', 'avatar_search_filter': 'all'}
_DEFAULT_IDLE_CHAR = '\u2060'

def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            stored = json.load(f)
        if not isinstance(stored, dict):
            return DEFAULT_CONFIG.copy()
        merged = DEFAULT_CONFIG.copy()
        merged.update(stored)
        logger.debug('Loaded config from %s (%d keys)', CONFIG_PATH, len(merged))
        return merged
    except Exception:
        logger.warning('Failed to load config — using defaults', exc_info=True)
        return DEFAULT_CONFIG.copy()

def save_config(updates: dict[str, Any]) -> None:
    config = load_config()
    config.update(updates)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4)
    logger.debug('Saved config updates: %s', ', '.join(sorted(updates.keys())))

def write_config(config: dict[str, Any]) -> None:
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4)
    logger.debug('Wrote full config (%d keys)', len(config))

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

def save_auth_session(
    *,
    auth_token: str,
    two_factor_token: str | None,
    username: str,
    display_name: str,
    user_id: str,
    remember_login: bool,
) -> None:
    save_config({
        'remember_login': remember_login,
        'auth_token': auth_token,
        'two_factor_token': two_factor_token or '',
        'auth_username': username,
        'auth_display_name': display_name,
        'auth_user_id': user_id,
    })

def clear_auth_session() -> None:
    save_config({
        'remember_login': False,
        'auth_token': '',
        'two_factor_token': '',
        'auth_username': '',
        'auth_display_name': '',
        'auth_user_id': '',
    })
