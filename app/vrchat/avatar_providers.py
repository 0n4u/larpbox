from __future__ import annotations
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any
from ..api_rate_limit import ApiPriority, rate_limited_external_call
from ..http_proxy import urlopen as proxy_urlopen
from ..logging_setup import get_logger
from ..version import __version__
from .models import AvatarResult

logger = get_logger('vrchat_api')
PROVIDER_USER_AGENT = f'larpbox/{__version__}'
AVTRDB_SEARCH_URL = 'https://api.avtrdb.com/v3/avatar/search/vrcx'
REQUI_SEARCH_BASE = 'https://requi.dev/vrcx_search.php'
AVATAR_RECOVERY_SEARCH_BASE = 'https://api.avatarrecovery.com/Avatar/vrcx'
_AVATAR_PROVIDER_URLS = (AVTRDB_SEARCH_URL, AVATAR_RECOVERY_SEARCH_BASE, REQUI_SEARCH_BASE)
_AUTHOR_LOOKUP_PROVIDER_URLS = (AVTRDB_SEARCH_URL, AVATAR_RECOVERY_SEARCH_BASE, REQUI_SEARCH_BASE)
_ALT_HTTPS_PORTS = (2053, 8443)
_DEAD_PROVIDER_UNTIL: dict[str, float] = {}
_DEAD_PROVIDER_COOLDOWN_SEC = 120.0
_JSON_FETCH_TIMEOUT_SEC = 12.0
_OBJECT_FETCH_TIMEOUT_SEC = 6.0
_AUTHOR_THUMB_SCAN_LIMIT = 24


@dataclass(frozen=True)
class _ProviderFetchResult:
    payload: Any
    host_reached: bool


def _provider_host_key(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return (parsed.hostname or parsed.netloc or url).casefold()


def _mark_provider_dead(url: str) -> None:
    _DEAD_PROVIDER_UNTIL[_provider_host_key(url)] = time.monotonic() + _DEAD_PROVIDER_COOLDOWN_SEC


def _provider_is_dead(url: str) -> bool:
    until = _DEAD_PROVIDER_UNTIL.get(_provider_host_key(url))
    return until is not None and time.monotonic() < until


def _provider_base_is_dead(base_url: str) -> bool:
    return _provider_is_dead(base_url)


def _live_provider_bases(bases: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(base for base in bases if not _provider_base_is_dead(base))


def _platforms_from_avtrdb(item: dict[str, Any]) -> tuple[str, ...]:
    platforms: list[str] = []
    perf = item.get('performance') or {}
    if isinstance(perf, dict):
        if perf.get('pc_rating'):
            platforms.append('pc')
        if perf.get('android_rating') or perf.get('quest_rating'):
            platforms.append('android')
        if perf.get('ios_rating'):
            platforms.append('ios')
    packages = item.get('platformPackages') or item.get('platform_packages') or []
    if isinstance(packages, list):
        for pkg in packages:
            if not isinstance(pkg, dict):
                continue
            plat = str(pkg.get('platform') or '').casefold()
            if 'android' in plat and 'android' not in platforms:
                platforms.append('android')
            elif 'ios' in plat and 'ios' not in platforms:
                platforms.append('ios')
            elif plat in ('standalonewindows', 'standalonemac', 'pc') and 'pc' not in platforms:
                platforms.append('pc')
    return tuple(platforms)


def _avatar_from_avtrdb(item: dict[str, Any]) -> AvatarResult:
    perf = item.get('performance') or {}
    pc_rating = perf.get('pc_rating') if isinstance(perf, dict) else None
    return AvatarResult(id=str(item.get('id') or ''), name=str(item.get('name') or 'Unknown'), description=str(item.get('description') or '').strip(), author_name=str(item.get('authorName') or ''), image_url=str(item.get('imageUrl') or item.get('thumbnailImageUrl') or ''), performance=str(pc_rating) if pc_rating else None, author_id=str(item.get('authorId') or ''), platforms=_platforms_from_avtrdb(item))


def _provider_headers() -> dict[str, str]:
    return {'User-Agent': PROVIDER_USER_AGENT, 'Accept': 'application/json,*/*'}


def _is_retriable_fetch_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (418, 404, 421, 521, 522, 523, 525, 526)
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return True
        message = str(reason).casefold()
        return 'wrong version number' in message or 'connection refused' in message or 'timed out' in message
    if isinstance(exc, (ssl.SSLError, TimeoutError, ConnectionError, OSError)):
        return True
    return False


def _provider_search_urls(base_url: str, query_string: str) -> list[str]:
    if _provider_base_is_dead(base_url):
        return []
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or ''
    path = parsed.path or ''
    scheme = parsed.scheme or 'https'
    port = parsed.port
    joiner = '&' if parsed.query else '?'
    suffix = f'{path}{joiner}{query_string}'
    urls: list[str] = []
    if port:
        candidate = f'{scheme}://{host}:{port}{suffix}'
        return [] if _provider_is_dead(candidate) else [candidate]
    primary = f'{scheme}://{host}{suffix}'
    if not _provider_is_dead(primary):
        urls.append(primary)
    for alt_port in _ALT_HTTPS_PORTS:
        candidate = f'{scheme}://{host}:{alt_port}{suffix}'
        if not _provider_is_dead(candidate):
            urls.append(candidate)
    return urls


def _fetch_provider_json_outcome(url: str, *, timeout: float=_JSON_FETCH_TIMEOUT_SEC) -> _ProviderFetchResult:

    def _do_fetch() -> list[dict[str, Any]]:
        request = urllib.request.Request(url, headers=_provider_headers())
        with proxy_urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]
    try:
        items = rate_limited_external_call(_do_fetch, priority=ApiPriority.INTERACTIVE)
        return _ProviderFetchResult(items, True)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 418, 421):
            return _ProviderFetchResult([], True)
        if _is_retriable_fetch_error(exc):
            _mark_provider_dead(url)
            return _ProviderFetchResult([], False)
        logger.debug('Avatar search fetch failed for %s', url)
        return _ProviderFetchResult([], True)
    except Exception as exc:
        if _is_retriable_fetch_error(exc):
            _mark_provider_dead(url)
            return _ProviderFetchResult([], False)
        logger.debug('Avatar search fetch failed for %s', url)
        return _ProviderFetchResult([], False)


def _fetch_provider_json(url: str) -> list[dict[str, Any]]:
    return _fetch_provider_json_outcome(url).payload


def _fetch_provider_avatar_object_outcome(url: str, *, timeout: float=_OBJECT_FETCH_TIMEOUT_SEC) -> _ProviderFetchResult:

    def _do_fetch() -> dict[str, Any] | None:
        request = urllib.request.Request(url, headers=_provider_headers())
        with proxy_urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if isinstance(payload, dict):
            return payload
        return None
    try:
        payload = rate_limited_external_call(_do_fetch, priority=ApiPriority.INTERACTIVE)
        return _ProviderFetchResult(payload, True)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 418, 421):
            return _ProviderFetchResult(None, True)
        if _is_retriable_fetch_error(exc):
            _mark_provider_dead(url)
            return _ProviderFetchResult(None, False)
        logger.debug('Avatar lookup fetch failed for %s', url)
        return _ProviderFetchResult(None, True)
    except Exception as exc:
        if _is_retriable_fetch_error(exc):
            _mark_provider_dead(url)
            return _ProviderFetchResult(None, False)
        logger.debug('Avatar lookup fetch failed for %s', url)
        return _ProviderFetchResult(None, False)


def _fetch_provider_avatar_object(url: str, *, timeout: float=_OBJECT_FETCH_TIMEOUT_SEC) -> dict[str, Any] | None:
    return _fetch_provider_avatar_object_outcome(url, timeout=timeout).payload


def lookup_avatar_by_file_id(base_url: str, file_id: str) -> AvatarResult | None:
    file_id = file_id.strip()
    if not file_id or _provider_base_is_dead(base_url):
        return None
    params = urllib.parse.urlencode({'fileId': file_id})
    urls = _provider_search_urls(base_url, params)
    for index, url in enumerate(urls):
        outcome = _fetch_provider_avatar_object_outcome(url)
        payload = outcome.payload
        if isinstance(payload, dict):
            result = _avatar_from_avtrdb(payload)
            if result.id.startswith('avtr_'):
                if index > 0:
                    logger.debug('Avatar fileId lookup succeeded via alternate URL: %s', url)
                return result
        if outcome.host_reached:
            break
    return None


def lookup_avatar_by_id_external(avatar_id: str) -> AvatarResult | None:
    avatar_id = (avatar_id or '').strip()
    if not avatar_id.startswith('avtr_'):
        return None
    params = urllib.parse.urlencode({'search': avatar_id, 'n': '5'})
    for base_url in _live_provider_bases(_AVATAR_PROVIDER_URLS):
        for url in _provider_search_urls(base_url, params):
            outcome = _fetch_provider_json_outcome(url)
            for item in outcome.payload or []:
                if str(item.get('id') or '').casefold() == avatar_id.casefold():
                    return _avatar_from_avtrdb(item)
            if outcome.host_reached:
                break
    return None


def lookup_avatars_by_author(base_url: str, author_id: str) -> list[AvatarResult]:
    author_id = author_id.strip()
    if not author_id or _provider_base_is_dead(base_url):
        return []
    params = urllib.parse.urlencode({'authorId': author_id})
    urls = _provider_search_urls(base_url, params)
    for index, url in enumerate(urls):
        outcome = _fetch_provider_json_outcome(url)
        items = outcome.payload or []
        if items:
            results = [_avatar_from_avtrdb(item) for item in items if item.get('id')]
            if results:
                if index > 0:
                    logger.debug('Avatar author lookup succeeded via alternate URL: %s', url)
                return results
        if outcome.host_reached:
            break
    return []


def lookup_avatars_by_author_first_hit(author_id: str) -> list[AvatarResult]:
    author_id = author_id.strip()
    if not author_id:
        return []
    for base_url in _live_provider_bases(_AUTHOR_LOOKUP_PROVIDER_URLS):
        results = lookup_avatars_by_author(base_url, author_id)
        if results:
            return results
    return []


def lookup_avatar_id_by_image_file_id(author_id: str, file_id: str) -> str | None:
    file_id = file_id.strip()
    if not file_id:
        return None
    file_key = file_id.casefold()
    uuid_key = file_key.replace('file_', '')
    for base_url in _live_provider_bases(_AVATAR_PROVIDER_URLS):
        match = lookup_avatar_by_file_id(base_url, file_id)
        if match and match.id.startswith('avtr_'):
            return match.id
    author_id = author_id.strip()
    if not author_id:
        return None
    for base_url in _live_provider_bases((AVTRDB_SEARCH_URL,)):
        for result in lookup_avatars_by_author(base_url, author_id)[:_AUTHOR_THUMB_SCAN_LIMIT]:
            image_url = (result.image_url or '').casefold()
            if file_key in image_url or (uuid_key and uuid_key in image_url):
                return result.id
    return None


def search_avatars_avtrdb(query: str, *, limit: int=40) -> list[AvatarResult]:
    return search_avatars_endpoint('https://api.avtrdb.com/v3/avatar/search/vrcx', query, limit=limit)


def search_avatars_endpoint(base_url: str, query: str, *, limit: int=40) -> list[AvatarResult]:
    query = query.strip()
    if len(query) < 3 or _provider_base_is_dead(base_url):
        return []
    params = urllib.parse.urlencode({'search': query, 'n': str(limit)})
    urls = _provider_search_urls(base_url, params)
    last_error: Exception | None = None
    for url in urls:
        try:
            outcome = _fetch_provider_json_outcome(url)
            results = [_avatar_from_avtrdb(item) for item in (outcome.payload or [])]
            if outcome.host_reached:
                return results[:limit]
        except Exception as exc:
            last_error = exc
            if _is_retriable_fetch_error(exc):
                logger.debug('Avatar search retry after %s on %s', type(exc).__name__, url)
                continue
            raise
    if last_error is not None:
        raise last_error
    return []


def search_avatars_requi(query: str, *, limit: int=40) -> list[AvatarResult]:
    query = query.strip()
    if len(query) < 3 or _provider_base_is_dead(REQUI_SEARCH_BASE):
        return []
    param_variants = (urllib.parse.urlencode({'search': query, 'n': str(limit)}), urllib.parse.urlencode({'searchTerm': query, 'limit': str(limit)}))
    last_error: Exception | None = None
    for params in param_variants:
        for url in _provider_search_urls(REQUI_SEARCH_BASE, params):
            try:
                outcome = _fetch_provider_json_outcome(url)
                if outcome.host_reached:
                    return [_avatar_from_avtrdb(item) for item in (outcome.payload or [])][:limit]
            except Exception as exc:
                last_error = exc
                if _is_retriable_fetch_error(exc):
                    continue
                raise
    if last_error is not None:
        raise last_error
    return []


def search_avatars_combined(query: str, *, limit: int=40, offset: int=0) -> list[AvatarResult]:
    query = query.strip()
    if len(query) < 2:
        return []
    merged: dict[str, AvatarResult] = {}
    fetch_count = min(100, max(limit + offset, limit))
    try:
        for result in search_avatars_avtrdb(query, limit=fetch_count):
            if result.id and result.id not in merged:
                merged[result.id] = result
    except Exception:
        logger.debug('Combined avatar search avtrdb failed for %r', query, exc_info=True)
    if not merged:
        fallbacks = (
            lambda q, n: search_avatars_endpoint(AVATAR_RECOVERY_SEARCH_BASE, q, limit=n),
            lambda q, n: search_avatars_requi(q, limit=n),
        )
        for search in fallbacks:
            try:
                for result in search(query, fetch_count):
                    if result.id and result.id not in merged:
                        merged[result.id] = result
            except Exception:
                logger.debug('Combined avatar search fallback failed for %r', query, exc_info=True)
    all_results = list(merged.values())
    return all_results[offset:offset + limit]
