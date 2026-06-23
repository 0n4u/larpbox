from __future__ import annotations
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from ..api_rate_limit import rate_limited_external_call
from ..logging_setup import get_logger
from ..version import __version__
from .models import AvatarResult

logger = get_logger('vrchat_api')
PROVIDER_USER_AGENT = f'larpbox/{__version__}'
AVTRDB_SEARCH_URL = 'https://api.avtrdb.com/v3/avatar/search/vrcx'
REQUI_SEARCH_BASE = 'https://requi.dev/vrcx_search.php'
AVATAR_RECOVERY_SEARCH_BASE = 'https://api.avatarrecovery.com/Avatar/vrcx'
_AVATAR_PROVIDER_URLS = (AVTRDB_SEARCH_URL, AVATAR_RECOVERY_SEARCH_BASE, REQUI_SEARCH_BASE)
_AUTHOR_LOOKUP_PROVIDER_URLS = (AVATAR_RECOVERY_SEARCH_BASE, REQUI_SEARCH_BASE, AVTRDB_SEARCH_URL)
_ALT_HTTPS_PORTS = (2053, 8443)


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
    if isinstance(exc, (ssl.SSLError, TimeoutError, ConnectionError)):
        return True
    return False


def _provider_search_urls(base_url: str, query_string: str) -> list[str]:
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or ''
    path = parsed.path or ''
    scheme = parsed.scheme or 'https'
    port = parsed.port
    joiner = '&' if parsed.query else '?'
    suffix = f'{path}{joiner}{query_string}'
    urls: list[str] = []
    if port:
        urls.append(f'{scheme}://{host}:{port}{suffix}')
        return urls
    urls.append(f'{scheme}://{host}{suffix}')
    for alt_port in _ALT_HTTPS_PORTS:
        urls.append(f'{scheme}://{host}:{alt_port}{suffix}')
    return urls


def _fetch_provider_json(url: str) -> list[dict[str, Any]]:

    def _do_fetch() -> list[dict[str, Any]]:
        request = urllib.request.Request(url, headers=_provider_headers())
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]
    try:
        return rate_limited_external_call(_do_fetch)
    except Exception:
        logger.debug('Avatar search fetch failed for %s', url, exc_info=True)
        return []


def _fetch_provider_avatar_object(url: str, *, timeout: float=12.0) -> dict[str, Any] | None:

    def _do_fetch() -> dict[str, Any] | None:
        request = urllib.request.Request(url, headers=_provider_headers())
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if isinstance(payload, dict):
            return payload
        return None
    try:
        return rate_limited_external_call(_do_fetch)
    except Exception:
        logger.debug('Avatar lookup fetch failed for %s', url, exc_info=True)
        return None


def lookup_avatar_by_file_id(base_url: str, file_id: str) -> AvatarResult | None:
    file_id = file_id.strip()
    if not file_id:
        return None
    params = urllib.parse.urlencode({'fileId': file_id})
    for url in _provider_search_urls(base_url, params):
        payload = _fetch_provider_avatar_object(url)
        if not payload:
            continue
        result = _avatar_from_avtrdb(payload)
        if result.id.startswith('avtr_'):
            if url != _provider_search_urls(base_url, params)[0]:
                logger.debug('Avatar fileId lookup succeeded via alternate URL: %s', url)
            return result
    return None


def lookup_avatar_by_id_external(avatar_id: str) -> AvatarResult | None:
    avatar_id = (avatar_id or '').strip()
    if not avatar_id.startswith('avtr_'):
        return None
    params = urllib.parse.urlencode({'search': avatar_id, 'n': '5'})
    for base_url in _AVATAR_PROVIDER_URLS:
        for url in _provider_search_urls(base_url, params):
            try:
                items = _fetch_provider_json(url)
            except Exception:
                continue
            for item in items:
                if str(item.get('id') or '').casefold() == avatar_id.casefold():
                    return _avatar_from_avtrdb(item)
    return None


def lookup_avatars_by_author(base_url: str, author_id: str) -> list[AvatarResult]:
    author_id = author_id.strip()
    if not author_id:
        return []
    params = urllib.parse.urlencode({'authorId': author_id})
    for url in _provider_search_urls(base_url, params):
        items = _fetch_provider_json(url)
        if items:
            results = [_avatar_from_avtrdb(item) for item in items if item.get('id')]
            if results:
                if url != _provider_search_urls(base_url, params)[0]:
                    logger.debug('Avatar author lookup succeeded via alternate URL: %s', url)
                return results
    return []


def lookup_avatars_by_author_first_hit(author_id: str) -> list[AvatarResult]:
    author_id = author_id.strip()
    if not author_id:
        return []
    for base_url in _AUTHOR_LOOKUP_PROVIDER_URLS:
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
    for base_url in _AVATAR_PROVIDER_URLS:
        match = lookup_avatar_by_file_id(base_url, file_id)
        if match and match.id.startswith('avtr_'):
            return match.id
    author_id = author_id.strip()
    if not author_id:
        return None
    for base_url in _AVATAR_PROVIDER_URLS:
        for result in lookup_avatars_by_author(base_url, author_id):
            image_url = (result.image_url or '').casefold()
            if file_key in image_url or (uuid_key and uuid_key in image_url):
                return result.id
    return None


def search_avatars_avtrdb(query: str, *, limit: int=40) -> list[AvatarResult]:
    return search_avatars_endpoint('https://api.avtrdb.com/v3/avatar/search/vrcx', query, limit=limit)


def search_avatars_endpoint(base_url: str, query: str, *, limit: int=40) -> list[AvatarResult]:
    query = query.strip()
    if len(query) < 3:
        return []
    params = urllib.parse.urlencode({'search': query, 'n': str(limit)})
    last_error: Exception | None = None
    urls = _provider_search_urls(base_url, params)
    for url in urls:
        try:
            items = _fetch_provider_json(url)
            results = [_avatar_from_avtrdb(item) for item in items]
            if url != urls[0]:
                logger.debug('Avatar search succeeded via alternate URL: %s', url)
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
    if len(query) < 3:
        return []
    param_variants = (urllib.parse.urlencode({'search': query, 'n': str(limit)}), urllib.parse.urlencode({'searchTerm': query, 'limit': str(limit)}))
    last_error: Exception | None = None
    for params in param_variants:
        for url in _provider_search_urls(REQUI_SEARCH_BASE, params):
            try:
                items = _fetch_provider_json(url)
                return [_avatar_from_avtrdb(item) for item in items][:limit]
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
    sources = (lambda q, n: search_avatars_avtrdb(q, limit=n), lambda q, n: search_avatars_endpoint('https://api.avatarrecovery.com/Avatar/vrcx', q, limit=n), lambda q, n: search_avatars_requi(q, limit=n))
    fetch_count = min(100, max(limit + offset, limit))
    for search in sources:
        try:
            for result in search(query, fetch_count):
                if result.id and result.id not in merged:
                    merged[result.id] = result
        except Exception:
            logger.debug('Combined avatar search failed for %r', query, exc_info=True)
    all_results = list(merged.values())
    return all_results[offset:offset + limit]
