from __future__ import annotations
import json
import threading
import time
from typing import Any
from .atomic_io import atomic_write_json
from .config import PROJECT_ROOT
from .logging_setup import get_logger
logger = get_logger('avatar_cache')
_CACHE_PATH = PROJECT_ROOT / 'data' / 'avatar_cache.json'
FORCE_CLONE_CACHE_MAX_AGE_SEC = 6 * 3600
_CACHE: dict[str, dict[str, Any]] | None = None
_LOG_SOURCES = frozenset({'log_live', 'log', 'log_name'})
_MAX_ENTRIES = 5000
_lock = threading.RLock()

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

def _entry_timestamp(entry: Any) -> float:
    if isinstance(entry, dict):
        value = entry.get('updated_at')
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0

def _evict_if_needed() -> None:
    if _CACHE is None or len(_CACHE) <= _MAX_ENTRIES:
        return
    ordered = sorted(_CACHE.items(), key=lambda item: _entry_timestamp(item[1]))
    for key, _ in ordered[:len(_CACHE) - _MAX_ENTRIES]:
        _CACHE.pop(key, None)

def _save() -> None:
    if _CACHE is None:
        return
    _evict_if_needed()
    try:
        atomic_write_json(_CACHE_PATH, _CACHE)
    except Exception:
        logger.debug('Could not write avatar cache', exc_info=True)

def get_cached_thumb_file_id(user_id: str) -> str | None:
    entry = _load().get(user_id.strip())
    if not isinstance(entry, dict):
        return None
    thumb = str(entry.get('thumb_file_id') or '').strip()
    return thumb or None

def note_friend_thumbnail(user_id: str, file_id: str) -> bool:
    """Record the latest friend API thumbnail file id. Returns True when it changed."""
    user_id = user_id.strip()
    file_id = file_id.strip()
    if not user_id or not file_id:
        return False
    with _lock:
        cache = _load()
        existing = cache.get(user_id)
        previous = str(existing.get('thumb_file_id') or '').strip() if isinstance(existing, dict) else ''
        if previous == file_id:
            return False
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged['thumb_file_id'] = file_id
        merged['thumb_updated_at'] = time.time()
        cache[user_id] = merged
        _save()
        return True

def get_friend_api_avatar_id(user_id: str, *, max_age_sec: float=6 * 3600) -> str | None:
    entry = _load().get(user_id.strip())
    if not isinstance(entry, dict):
        return None
    if str(entry.get('source') or '') not in ('friend_api', 'api', 'instance'):
        return None
    avatar_id = str(entry.get('avatar_id') or '').strip()
    if not avatar_id.startswith('avtr_'):
        return None
    updated_at = entry.get('updated_at')
    if isinstance(updated_at, (int, float)) and time.time() - float(updated_at) > max_age_sec:
        return None
    return avatar_id

def get_cached_avatar_id(user_id: str, *, max_age_sec: float | None=None) -> str | None:
    entry = _load().get(user_id)
    if not isinstance(entry, dict):
        return None
    avatar_id = str(entry.get('avatar_id') or '').strip()
    if not avatar_id:
        return None
    if max_age_sec is not None:
        updated_at = entry.get('updated_at')
        if isinstance(updated_at, (int, float)):
            if time.time() - float(updated_at) > max_age_sec:
                return None
    return avatar_id

def get_recent_avatar_id_for_copy(user_id: str, *, in_room: bool) -> str | None:
    entry = _load().get(user_id)
    if not isinstance(entry, dict):
        return None
    avatar_id = str(entry.get('avatar_id') or '').strip()
    if not avatar_id.startswith('avtr_'):
        return None
    source = str(entry.get('source') or '')
    updated_at = entry.get('updated_at')
    age_sec = time.time() - float(updated_at) if isinstance(updated_at, (int, float)) else float('inf')
    if in_room:
        if source in _LOG_SOURCES:
            return avatar_id
        if age_sec <= 3600:
            return avatar_id
        return None
    return get_cached_avatar_id(user_id, max_age_sec=FORCE_CLONE_CACHE_MAX_AGE_SEC)

def sync_live_log_avatars(mapping: dict[str, str]) -> int:
    if not mapping:
        return 0
    with _lock:
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
    with _lock:
        cache = _load()
        existing = cache.get(user_id)
        previous_id = str(existing.get('avatar_id') or '').strip() if isinstance(existing, dict) else ''
        previous_source = str(existing.get('source') or '') if isinstance(existing, dict) else ''
        if previous_id == avatar_id:
            return
        if source not in _LOG_SOURCES and previous_id and (previous_id != avatar_id) and (previous_source in _LOG_SOURCES) and (source not in ('api', 'instance')):
            return
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged['avatar_id'] = avatar_id
        merged['source'] = source
        merged['updated_at'] = time.time()
        cache[user_id] = merged
        _save()

def merge_user_avatar_map(mapping: dict[str, str], *, source: str) -> None:
    if not mapping:
        return
    with _lock:
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
