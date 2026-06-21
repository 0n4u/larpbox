from __future__ import annotations
import json
import time
from pathlib import Path
from .config import PROJECT_ROOT
from .logging_setup import get_logger
from .vrchat.models import AvatarResult
logger = get_logger('wardrobe_cache')
_CACHE_PATH = PROJECT_ROOT / 'data' / 'wardrobe_cache.json'
_CACHE: dict[str, dict[str, str]] | None = None
_MAX_AGE_SEC = 30 * 24 * 3600
_DIRTY = False
_LAST_SAVE = 0.0
_SAVE_DEBOUNCE_SEC = 2.5

def _load_raw() -> dict[str, dict[str, str]]:
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
        logger.debug('Could not load wardrobe cache', exc_info=True)
        _CACHE = {}
    return _CACHE

def _write_disk() -> None:
    global _DIRTY, _LAST_SAVE
    if _CACHE is None:
        return
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(_CACHE, indent=2), encoding='utf-8')
    _DIRTY = False
    _LAST_SAVE = time.time()

def flush_cache() -> None:
    if _DIRTY:
        _write_disk()

def get_cached_avatar(avatar_id: str) -> AvatarResult | None:
    avatar_id = (avatar_id or '').strip()
    if not avatar_id:
        return None
    entry = _load_raw().get(avatar_id)
    if not isinstance(entry, dict):
        return None
    updated = float(entry.get('updated_at') or 0)
    if updated and time.time() - updated > _MAX_AGE_SEC:
        return None
    name = str(entry.get('name') or '').strip()
    image_url = str(entry.get('image_url') or '').strip()
    if not name and not image_url:
        return None
    return AvatarResult(id=avatar_id, name=name or 'Favorite avatar', description=str(entry.get('description') or ''), author_name=str(entry.get('author_name') or ''), image_url=image_url, performance=str(entry.get('performance') or '') or None, author_id=str(entry.get('author_id') or ''))

def get_cached_avatars(avatar_ids: list[str]) -> dict[str, AvatarResult]:
    results: dict[str, AvatarResult] = {}
    for avatar_id in avatar_ids:
        cached = get_cached_avatar(avatar_id)
        if cached is not None:
            results[avatar_id] = cached
    return results

def store_avatar(result: AvatarResult) -> None:
    global _DIRTY, _LAST_SAVE
    if not result.id:
        return
    cache = _load_raw()
    cache[result.id] = {'name': result.name, 'description': result.description, 'author_name': result.author_name, 'image_url': result.image_url, 'performance': result.performance or '', 'author_id': result.author_id, 'updated_at': time.time()}
    _DIRTY = True
    now = time.time()
    if now - _LAST_SAVE >= _SAVE_DEBOUNCE_SEC:
        _write_disk()

def clear_cache() -> None:
    global _CACHE, _DIRTY, _LAST_SAVE
    _CACHE = {}
    _DIRTY = False
    _LAST_SAVE = 0.0
    if _CACHE_PATH.is_file():
        try:
            _CACHE_PATH.unlink()
        except Exception:
            logger.debug('Could not clear wardrobe cache file', exc_info=True)
