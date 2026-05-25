from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any
from .config import PROJECT_ROOT
from .logging_setup import get_logger
logger = get_logger('avatar_cache')
_CACHE_PATH = PROJECT_ROOT / 'data' / 'avatar_cache.json'
_CACHE: dict[str, dict[str, Any]] | None = None
_LOG_SOURCES = frozenset({'log_live', 'log', 'log_name'})

def _load() -> dict[str, dict[str, Any]]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not _CACHE_PATH.is_file():
        _CACHE = {}
        return _CACHE
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding='utf-8'))
        _CACHE = raw if isinstance(raw, dict) else {}
    except Exception:
        logger.debug('Could not load avatar cache', exc_info=True)
        _CACHE = {}
    return _CACHE

def _save() -> None:
    if _CACHE is None:
        return
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(_CACHE, indent=2), encoding='utf-8')

def get_cached_avatar_id(user_id: str) -> str | None:
    entry = _load().get(user_id)
    if not isinstance(entry, dict):
        return None
    avatar_id = str(entry.get('avatar_id') or '').strip()
    return avatar_id or None

def sync_live_log_avatars(mapping: dict[str, str]) -> int:
    if not mapping:
        return 0
    cache = _load()
    updated = 0
    for user_id, avatar_id in mapping.items():
        user_id = user_id.strip()
        avatar_id = avatar_id.strip()
        if not user_id or not avatar_id.startswith('avtr_'):
            continue
        existing = cache.get(user_id)
        previous = str(existing.get('avatar_id') or '').strip() if isinstance(existing, dict) else ''
        if previous == avatar_id:
            continue
        cache[user_id] = {'avatar_id': avatar_id, 'source': 'log_live', 'updated_at': time.time()}
        updated += 1
        if previous:
            logger.debug('Log avatar changed for %s: %s -> %s', user_id, previous, avatar_id)
    if updated:
        _save()
    return updated

def set_cached_avatar_id(user_id: str, avatar_id: str, *, source: str) -> None:
    avatar_id = avatar_id.strip()
    user_id = user_id.strip()
    if not user_id or not avatar_id.startswith('avtr_'):
        return
    cache = _load()
    existing = cache.get(user_id)
    previous_id = str(existing.get('avatar_id') or '').strip() if isinstance(existing, dict) else ''
    previous_source = str(existing.get('source') or '') if isinstance(existing, dict) else ''
    if previous_id == avatar_id:
        return
    if source not in _LOG_SOURCES and previous_id and (previous_id != avatar_id) and (previous_source in _LOG_SOURCES) and (source not in ('api', 'instance')):
        return
    cache[user_id] = {'avatar_id': avatar_id, 'source': source, 'updated_at': time.time()}
    _save()

def merge_user_avatar_map(mapping: dict[str, str], *, source: str) -> None:
    if not mapping:
        return
    cache = _load()
    changed = False
    for user_id, avatar_id in mapping.items():
        if not user_id or not avatar_id.startswith('avtr_'):
            continue
        existing = cache.get(user_id, {})
        previous_id = str(existing.get('avatar_id') or '').strip() if isinstance(existing, dict) else ''
        previous_source = str(existing.get('source') or '') if isinstance(existing, dict) else ''
        if previous_id == avatar_id:
            continue
        if previous_id and previous_source in _LOG_SOURCES:
            continue
        cache[user_id] = {'avatar_id': avatar_id, 'source': source, 'updated_at': time.time()}
        changed = True
    if changed:
        _save()

def refresh_from_logs() -> int:
    from .vrchat_amplitude import build_user_avatar_map_from_amplitude
    from .vrchat_log_players import build_user_avatar_map_from_logs
    before = len(_load())
    merge_user_avatar_map(build_user_avatar_map_from_logs(), source='log_scan')
    merge_user_avatar_map(build_user_avatar_map_from_amplitude(), source='amplitude')
    after = len(_load())
    return max(0, after - before)
