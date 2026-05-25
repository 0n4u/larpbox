from __future__ import annotations
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any
import vrchatapi
from vrchatapi.api import authentication_api, avatars_api, favorites_api, friends_api, instances_api, playermoderation_api, users_api, worlds_api
from vrchatapi.exceptions import ApiException, UnauthorizedException
from vrchatapi.models.add_favorite_request import AddFavoriteRequest
from vrchatapi.models.favorite_type import FavoriteType
from vrchatapi.models.moderate_user_request import ModerateUserRequest
from ..api_rate_limit import rate_limited_external_call, rate_limited_vrchat_call
from ..avatar_cache import get_cached_avatar_id, set_cached_avatar_id, sync_live_log_avatars
from ..logging_setup import get_logger
from ..services.auth_errors import AuthSessionError, auth_session_error_from_api, api_error_text
from ..user_status import UserStatusInfo, resolve_user_location, user_status_from_user
from ..vrchat_auth import VRChatSession, _configure_api_client, USER_AGENT
from ..vrchat_log_players import LogPlayer, log_players_for_current_room, lookup_player_avatar_info, lookup_player_avatar_info_historical, parse_avatar_ids_from_log, current_room_user_ids
from .models import AvatarResult, CurrentUserProfile, FriendEntry, InstanceInfo, InstancePlayer, TrustRank, UserBadge, trust_rank_from_tags
VRCX_USER_AGENT = 'VRCX/2024.1.0'
logger = get_logger('vrchat_api')
AVTRDB_SEARCH_URL = 'https://api.avtrdb.com/v3/avatar/search/vrcx'
REQUI_SEARCH_BASE = 'https://requi.dev/vrcx_search.php'
AVATAR_RECOVERY_SEARCH_BASE = 'https://api.avatarrecovery.com/Avatar/vrcx'
_VRCX_AVATAR_PROVIDER_URLS = (AVTRDB_SEARCH_URL, AVATAR_RECOVERY_SEARCH_BASE, REQUI_SEARCH_BASE)
_ALT_HTTPS_PORTS = (2053, 8443)
DEFAULT_AVATAR_FAVORITE_GROUP = 'avatars1'
_INSTANCE_SUMMARY_CACHE: dict[str, tuple[float, str | None, int | None]] = {}
_INSTANCE_SUMMARY_TTL_SEC = 120.0
_USER_THUMB_CACHE: dict[str, str] = {}
_USER_TRUST_CACHE: dict[str, TrustRank] = {}
_FILE_ID_RE = re.compile('(file_[a-f0-9-]+)', re.IGNORECASE)
_THUMBNAIL_IMAGE_RE = re.compile('https://api\\.vrchat\\.cloud/api/1/image/(file_[a-f0-9-]+)/\\d+/(\\d+)', re.IGNORECASE)
_FRIEND_INSTANCE_PLAYERS: dict[str, list[InstancePlayer]] = {}
_FILE_IMAGE_URL_CACHE: dict[str, str] = {}
_VRCHAT_HIDDEN_AVATAR_FILE_IDS = frozenset({'file_0e8c4e32-7444-44ea-ade4-313c010d4bae'})
_GENERIC_AVATAR_NAMES = frozenset({'robot', 'default'})
_AVATAR_RESOLVE_CACHE: dict[str, tuple[float, str | None]] = {}
_AVATAR_RESOLVE_CACHE_TTL_SEC = 300.0
from ..vrc_image_utils import normalize_vrc_image_url, VRCHAT_API

def normalize_thumbnail_url(url: str) -> str:
    return normalize_vrc_image_url(url, resolution=128)

def thumbnail_url_for_size(url: str, size: int=64) -> str:
    return normalize_vrc_image_url(url, resolution=size)

def _image_url_from_file_record(record: dict[str, Any], *, size: int=256) -> str | None:
    file_id = str(record.get('id') or '').strip()
    versions = record.get('versions') or []
    if not file_id or not versions:
        return None
    latest = 1
    for entry in versions:
        if isinstance(entry, dict):
            latest = max(latest, int(entry.get('version') or 0))
    return f'https://api.vrchat.cloud/api/1/image/{file_id}/{latest}/{size}'

def resolve_vrchat_image_url(session: VRChatSession | None, url: str) -> str:
    normalized = normalize_thumbnail_url(url)
    if not normalized or session is None:
        return normalized
    file_id = _file_id_from_vrchat_asset_url(normalized)
    if not file_id:
        return normalized
    cached = _FILE_IMAGE_URL_CACHE.get(file_id)
    if cached:
        return cached
    record = _fetch_vrchat_file_record(session, file_id)
    if not record:
        return normalized
    size_match = _THUMBNAIL_IMAGE_RE.search(normalized)
    size = int(size_match.group(2)) if size_match else 256
    resolved = _image_url_from_file_record(record, size=size) or normalized
    _FILE_IMAGE_URL_CACHE[file_id] = resolved
    return resolved

def _rebuild_friend_instance_index(friends: list[FriendEntry]) -> None:
    global _FRIEND_INSTANCE_PLAYERS
    index: dict[str, list[InstancePlayer]] = {}
    for friend in friends:
        location = friend.status.location or ''
        if not location or friend.status.key == 'offline':
            continue
        key = _instance_location_key(location)
        if not key:
            continue
        index.setdefault(key, []).append(InstancePlayer(user_id=friend.user_id, display_name=friend.display_name, thumbnail_url=normalize_thumbnail_url(friend.thumbnail_url), is_friend=True, trust=friend.trust))
    _FRIEND_INSTANCE_PLAYERS = index

def avatar_profile_url(avatar_id: str) -> str:
    return f'https://vrchat.com/home/avatar/{avatar_id}'

def user_profile_url(user_id: str) -> str:
    return f'https://vrchat.com/home/user/{user_id}'

def vrchat_join_url(location: str) -> str | None:
    parsed = _parse_location(location)
    if parsed is None:
        return None
    world_id, instance_id = parsed
    query = urllib.parse.urlencode({'worldId': world_id, 'instanceId': instance_id})
    return f'vrchat://launch?{query}'

def join_player_instance(location: str) -> str:
    url = vrchat_join_url(location)
    if not url:
        raise RuntimeError('Could not build a join link for this player.')
    import webbrowser
    webbrowser.open(url)
    return 'Opening VRChat to join this player.'

def make_api_client(session: VRChatSession) -> vrchatapi.ApiClient:
    api_client = vrchatapi.ApiClient(vrchatapi.Configuration())
    _configure_api_client(api_client, auth_token=session.auth_token, two_factor_token=session.two_factor_token)
    original_call_api = api_client.call_api

    def rate_limited_call_api(*args: Any, **kwargs: Any) -> Any:
        return rate_limited_vrchat_call(original_call_api, *args, **kwargs)
    api_client.call_api = rate_limited_call_api
    return api_client

def _avatar_from_api(avatar: Any) -> AvatarResult:
    performance = None
    perf = getattr(avatar, 'performance', None)
    if perf is not None:
        performance = getattr(perf, 'performance_rating', None) or getattr(perf, 'pc_rating', None)
    return AvatarResult(id=str(getattr(avatar, 'id', '') or ''), name=str(getattr(avatar, 'name', '') or 'Unknown'), description=str(getattr(avatar, 'description', '') or '').strip(), author_name=str(getattr(avatar, 'author_name', '') or ''), image_url=str(getattr(avatar, 'thumbnail_image_url', '') or getattr(avatar, 'image_url', '') or ''), performance=str(performance) if performance else None)

def _avatar_from_avtrdb(item: dict[str, Any]) -> AvatarResult:
    perf = item.get('performance') or {}
    pc_rating = perf.get('pc_rating') if isinstance(perf, dict) else None
    return AvatarResult(id=str(item.get('id') or ''), name=str(item.get('name') or 'Unknown'), description=str(item.get('description') or '').strip(), author_name=str(item.get('authorName') or ''), image_url=str(item.get('imageUrl') or item.get('thumbnailImageUrl') or ''), performance=str(pc_rating) if pc_rating else None, author_id=str(item.get('authorId') or ''))

def _vrcx_headers() -> dict[str, str]:
    return {'User-Agent': VRCX_USER_AGENT, 'Referer': 'https://vrcx.app', 'Accept': 'application/json,*/*'}

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

def _vrcx_search_urls(base_url: str, query_string: str) -> list[str]:
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

def _fetch_vrcx_json(url: str) -> list[dict[str, Any]]:

    def _do_fetch() -> list[dict[str, Any]]:
        request = urllib.request.Request(url, headers=_vrcx_headers())
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

def _fetch_vrcx_avatar_object(url: str, *, timeout: float=12.0) -> dict[str, Any] | None:

    def _do_fetch() -> dict[str, Any] | None:
        request = urllib.request.Request(url, headers=_vrcx_headers())
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
    for url in _vrcx_search_urls(base_url, params):
        payload = _fetch_vrcx_avatar_object(url)
        if not payload:
            continue
        result = _avatar_from_avtrdb(payload)
        if result.id.startswith('avtr_'):
            if url != _vrcx_search_urls(base_url, params)[0]:
                logger.debug('Avatar fileId lookup succeeded via alternate URL: %s', url)
            return result
    return None

def lookup_avatars_by_author(base_url: str, author_id: str) -> list[AvatarResult]:
    author_id = author_id.strip()
    if not author_id:
        return []
    params = urllib.parse.urlencode({'authorId': author_id})
    for url in _vrcx_search_urls(base_url, params):
        items = _fetch_vrcx_json(url)
        if items:
            results = [_avatar_from_avtrdb(item) for item in items if item.get('id')]
            if results:
                if url != _vrcx_search_urls(base_url, params)[0]:
                    logger.debug('Avatar author lookup succeeded via alternate URL: %s', url)
                return results
    return []

def lookup_avatar_id_by_image_file_id(author_id: str, file_id: str) -> str | None:
    file_id = file_id.strip()
    if not file_id:
        return None
    file_key = file_id.casefold()
    uuid_key = file_key.replace('file_', '')
    for base_url in _VRCX_AVATAR_PROVIDER_URLS:
        match = lookup_avatar_by_file_id(base_url, file_id)
        if match and match.id.startswith('avtr_'):
            return match.id
    author_id = author_id.strip()
    if not author_id:
        return None
    for base_url in _VRCX_AVATAR_PROVIDER_URLS:
        for result in lookup_avatars_by_author(base_url, author_id):
            image_url = (result.image_url or '').casefold()
            if file_key in image_url or (uuid_key and uuid_key in image_url):
                return result.id
    return None

def search_avatars_avtrdb(query: str, *, limit: int=40) -> list[AvatarResult]:
    return search_avatars_vrcx_endpoint('https://api.avtrdb.com/v3/avatar/search/vrcx', query, limit=limit)

def search_avatars_vrcx_endpoint(base_url: str, query: str, *, limit: int=40) -> list[AvatarResult]:
    query = query.strip()
    if len(query) < 3:
        return []
    params = urllib.parse.urlencode({'search': query, 'n': str(limit)})
    last_error: Exception | None = None
    urls = _vrcx_search_urls(base_url, params)
    for url in urls:
        try:
            items = _fetch_vrcx_json(url)
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
        for url in _vrcx_search_urls(REQUI_SEARCH_BASE, params):
            try:
                items = _fetch_vrcx_json(url)
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
    sources = (lambda q, n: search_avatars_avtrdb(q, limit=n), lambda q, n: search_avatars_vrcx_endpoint('https://api.avatarrecovery.com/Avatar/vrcx', q, limit=n), lambda q, n: search_avatars_requi(q, limit=n))
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

def search_avatars_official(session: VRChatSession, *, featured: bool=False, own: bool=False, release_status: str='all', sort: str='updated', limit: int=40) -> list[AvatarResult]:
    with make_api_client(session) as api_client:
        api = avatars_api.AvatarsApi(api_client)
        kwargs: dict[str, Any] = {'n': limit, 'offset': 0}
        if featured:
            kwargs['featured'] = True
            kwargs['sort'] = sort
            kwargs['order'] = 'descending'
        elif own:
            kwargs['user'] = 'me'
            kwargs['release_status'] = release_status
            kwargs['sort'] = sort
            kwargs['order'] = 'descending'
        else:
            kwargs['featured'] = True
        results = api.search_avatars(**kwargs)
        return [_avatar_from_api(avatar) for avatar in results or []]

def enrich_avatar_description(session: VRChatSession, avatar_id: str) -> str:
    if not avatar_id:
        return ''
    try:
        with make_api_client(session) as api_client:
            api = avatars_api.AvatarsApi(api_client)
            avatar = api.get_avatar(avatar_id)
            return str(getattr(avatar, 'description', '') or '').strip()
    except Exception:
        logger.debug('Could not fetch description for %s', avatar_id, exc_info=True)
        return ''

def _parse_location(location: str) -> tuple[str, str] | None:
    location = (location or '').strip()
    if not location or location.startswith('offline') or location.startswith('private'):
        return None
    if ':' not in location:
        return None
    world_id, instance_id = location.split(':', 1)
    if not world_id.startswith('wrld_'):
        return None
    return (world_id, instance_id)

def _instance_location_key(location: str | None) -> str | None:
    parsed = _parse_location(location or '')
    if parsed is None:
        return None
    world_id, instance_id = parsed
    return f"{world_id}:{instance_id.split('~')[0]}"

def _same_instance(location_a: str | None, location_b: str | None) -> bool:
    key_a = _instance_location_key(location_a)
    key_b = _instance_location_key(location_b)
    return key_a is not None and key_a == key_b

def _fetch_world_name(api_client: vrchatapi.ApiClient, world_id: str) -> str:
    world_id = (world_id or '').strip()
    if not world_id:
        return ''
    try:
        world = worlds_api.WorldsApi(api_client).get_world(world_id)
    except (UnauthorizedException, ApiException):
        logger.debug('Could not load world %s', world_id, exc_info=True)
        return ''
    except Exception:
        logger.debug('Could not load world %s', world_id, exc_info=True)
        return ''
    return str(getattr(world, 'name', '') or '').strip()

def _instance_player_from_limited(user: Any, *, is_friend: bool=False, avatar_id: str | None=None) -> InstancePlayer:
    tags = list(getattr(user, 'tags', None) or [])
    user_id = str(getattr(user, 'id', '') or '')
    trust = trust_rank_from_tags(tags)
    if user_id:
        _USER_TRUST_CACHE[user_id] = trust
    return InstancePlayer(user_id=user_id, display_name=str(getattr(user, 'display_name', '') or 'Unknown'), thumbnail_url=_profile_image_url(user), is_friend=is_friend or bool(getattr(user, 'is_friend', False)), trust=trust, avatar_id=avatar_id)

def _instance_player_from_log(log_player: LogPlayer, *, is_friend: bool=False, avatar_id: str | None=None) -> InstancePlayer:
    return InstancePlayer(user_id=log_player.user_id, display_name=log_player.display_name, thumbnail_url='', is_friend=is_friend, trust=None, avatar_id=avatar_id)

def get_current_instance_log_only(session: VRChatSession | None=None, *, enrich: bool=True, max_enrich_requests: int=6) -> InstanceInfo | None:
    log_players, log_location = log_players_for_current_room()
    parsed = _parse_location(log_location or '')
    if parsed is None:
        return None
    world_id, instance_id = parsed
    avatar_ids = parse_avatar_ids_from_log()
    sync_live_log_avatars(avatar_ids)
    players = [_instance_player_from_log(player, avatar_id=avatar_ids.get(player.user_id)) for player in log_players]
    players = _apply_cached_thumbnails(players)
    players = _apply_cached_trust(players)
    if session is not None and enrich:
        try:
            with make_api_client(session) as api_client:
                players = _enrich_players_from_api(api_client, players, session=session, max_requests=max_enrich_requests)
        except Exception:
            logger.debug('Could not enrich log-only player list', exc_info=True)
    inst_type = instance_id.split('~', 1)[1].split('(', 1)[0] if '~' in instance_id else ''
    return InstanceInfo(world_id=world_id, instance_id=instance_id, world_name='', player_count=max(len(players), 1), players=players, owner_id='', can_close_instance=False, region='', instance_type=inst_type)

def _players_from_api_users(users: list[Any]) -> list[InstancePlayer]:
    return [_instance_player_from_limited(user, is_friend=bool(getattr(user, 'is_friend', False))) for user in users]

def _players_from_friends(api_client: vrchatapi.ApiClient, location: str, *, cache_only: bool=True) -> list[InstancePlayer]:
    key = _instance_location_key(location)
    if key and key in _FRIEND_INSTANCE_PLAYERS:
        return list(_FRIEND_INSTANCE_PLAYERS[key])
    if cache_only:
        return []
    players: list[InstancePlayer] = []
    seen: set[str] = set()
    api = friends_api.FriendsApi(api_client)
    offset = 0
    while offset < 200:
        try:
            batch = api.get_friends(offset=offset, n=100, offline=False) or []
        except (UnauthorizedException, ApiException):
            break
        if not batch:
            break
        for friend in batch:
            friend_location = str(getattr(friend, 'location', '') or '')
            if not _same_instance(friend_location, location):
                continue
            user_id = str(getattr(friend, 'id', '') or '')
            if not user_id or user_id in seen:
                continue
            seen.add(user_id)
            players.append(_instance_player_from_limited(friend, is_friend=True))
        if len(batch) < 100:
            break
        offset += len(batch)
    return players

def _enrich_log_player(api_client: vrchatapi.ApiClient, log_player: LogPlayer, *, friend_ids: set[str]) -> InstancePlayer:
    try:
        user = users_api.UsersApi(api_client).get_user(log_player.user_id)
        player = _instance_player_from_limited(user, is_friend=log_player.user_id in friend_ids or bool(getattr(user, 'is_friend', False)))
        if player.display_name == 'Unknown' and log_player.display_name:
            return InstancePlayer(user_id=player.user_id, display_name=log_player.display_name, thumbnail_url=player.thumbnail_url, is_friend=player.is_friend, trust=player.trust, avatar_id=resolve_player_avatar_id(player.user_id, log_player.display_name) or player.avatar_id)
        return player
    except Exception:
        logger.debug('Could not enrich log player %s', log_player.user_id, exc_info=True)
        return InstancePlayer(user_id=log_player.user_id, display_name=log_player.display_name, thumbnail_url='', is_friend=log_player.user_id in friend_ids, trust=None, avatar_id=resolve_player_avatar_id(log_player.user_id, log_player.display_name))

def _merge_players(*groups: list[InstancePlayer]) -> list[InstancePlayer]:
    merged: dict[str, InstancePlayer] = {}
    for group in groups:
        for player in group:
            if not player.user_id:
                continue
            existing = merged.get(player.user_id)
            if existing is None:
                merged[player.user_id] = player
                continue
            merged[player.user_id] = InstancePlayer(user_id=player.user_id, display_name=player.display_name or existing.display_name, thumbnail_url=player.thumbnail_url or existing.thumbnail_url, is_friend=existing.is_friend or player.is_friend, trust=player.trust or existing.trust, avatar_id=player.avatar_id or existing.avatar_id)
    return sorted(merged.values(), key=lambda item: item.display_name.casefold())

def _remember_player_thumbnails(players: list[InstancePlayer]) -> None:
    for player in players:
        if player.user_id and player.thumbnail_url:
            thumb = normalize_thumbnail_url(player.thumbnail_url)
            if thumb:
                _USER_THUMB_CACHE[player.user_id] = thumb

def _remember_player_trust(players: list[InstancePlayer]) -> None:
    for player in players:
        if player.user_id and player.trust is not None:
            _USER_TRUST_CACHE[player.user_id] = player.trust

def _apply_cached_trust(players: list[InstancePlayer]) -> list[InstancePlayer]:
    updated: list[InstancePlayer] = []
    for player in players:
        if player.trust is not None:
            updated.append(player)
            continue
        cached = _USER_TRUST_CACHE.get(player.user_id)
        if not cached:
            updated.append(player)
            continue
        updated.append(InstancePlayer(user_id=player.user_id, display_name=player.display_name, thumbnail_url=player.thumbnail_url, is_friend=player.is_friend, trust=cached, avatar_id=player.avatar_id))
    return updated

def _apply_cached_thumbnails(players: list[InstancePlayer]) -> list[InstancePlayer]:
    updated: list[InstancePlayer] = []
    for player in players:
        cached = _USER_THUMB_CACHE.get(player.user_id, '')
        if player.thumbnail_url or not cached:
            updated.append(player)
            continue
        updated.append(InstancePlayer(user_id=player.user_id, display_name=player.display_name, thumbnail_url=cached, is_friend=player.is_friend, trust=player.trust, avatar_id=player.avatar_id))
    return updated

def apply_cached_thumbnails(players: list[InstancePlayer]) -> list[InstancePlayer]:
    return _apply_cached_thumbnails(players)

def _enrich_players_from_api(api_client: vrchatapi.ApiClient, players: list[InstancePlayer], *, session: VRChatSession | None=None, max_requests: int=24, priority_user_ids: set[str] | None=None) -> list[InstancePlayer]:
    if max_requests <= 0:
        return players
    api = users_api.UsersApi(api_client)
    enriched: list[InstancePlayer] = []
    requests = 0
    priority = priority_user_ids or set()

    def sort_key(player: InstancePlayer) -> tuple[int, int, int, str]:
        return (0 if player.user_id in priority else 1, 0 if not player.thumbnail_url else 1, 0 if player.trust is None else 1, player.display_name.casefold())
    ordered = sorted(players, key=sort_key)
    for player in ordered:
        needs_thumb = not player.thumbnail_url
        needs_trust = player.trust is None
        if not needs_thumb and (not needs_trust) or requests >= max_requests:
            enriched.append(player)
            continue
        try:
            user = api.get_user(player.user_id)
        except (UnauthorizedException, ApiException):
            enriched.append(player)
            continue
        except Exception:
            logger.debug('Could not enrich player %s', player.user_id, exc_info=True)
            enriched.append(player)
            continue
        thumb = _profile_image_url(user) if needs_thumb else player.thumbnail_url
        if thumb:
            thumb = normalize_thumbnail_url(thumb) or thumb
        tags = list(getattr(user, 'tags', None) or [])
        trust = player.trust or trust_rank_from_tags(tags)
        if player.user_id:
            _USER_TRUST_CACHE[player.user_id] = trust
        requests += 1
        enriched.append(InstancePlayer(user_id=player.user_id, display_name=player.display_name, thumbnail_url=thumb or player.thumbnail_url, is_friend=player.is_friend or bool(getattr(user, 'is_friend', False)), trust=trust, avatar_id=player.avatar_id))
    by_id = {player.user_id: player for player in enriched}
    result = [by_id.get(player.user_id, player) for player in players]
    _remember_player_thumbnails(result)
    _remember_player_trust(result)
    return result

def _enrich_missing_thumbnails(api_client: vrchatapi.ApiClient, players: list[InstancePlayer], *, max_requests: int=16) -> list[InstancePlayer]:
    return _enrich_players_from_api(api_client, players, max_requests=max_requests)

def _location_from_user(user: Any, *, log_fallback: str | None=None) -> str | None:
    return resolve_user_location(user, log_fallback=log_fallback)

def _profile_image_url(user: Any) -> str:
    for attr in ('profile_pic_override_thumbnail', 'profile_pic_override', 'user_icon', 'current_avatar_thumbnail_image_url', 'current_avatar_image_url'):
        raw = str(getattr(user, attr, '') or '').strip()
        if not raw:
            continue
        normalized = normalize_thumbnail_url(raw)
        if normalized:
            return normalized
        if raw.startswith('/'):
            normalized = normalize_vrc_image_url(f'{VRCHAT_API}{raw}', resolution=128)
        elif raw.startswith('http'):
            normalized = normalize_vrc_image_url(raw, resolution=128)
        else:
            normalized = ''
        if normalized:
            return normalized
    return ''

def _badges_from_user(user: Any) -> list[UserBadge]:
    badges: list[UserBadge] = []
    for badge in getattr(user, 'badges', None) or []:
        if bool(getattr(badge, 'hidden', False)):
            continue
        badges.append(UserBadge(badge_id=str(getattr(badge, 'badge_id', '') or ''), name=str(getattr(badge, 'badge_name', '') or 'Badge'), image_url=str(getattr(badge, 'badge_image_url', '') or ''), showcased=bool(getattr(badge, 'showcased', False))))
    badges.sort(key=lambda item: (not item.showcased, item.name.casefold()))
    return badges

def _instance_label_from_location(location: str | None) -> str | None:
    if not location:
        return None
    location = location.strip()
    if location.startswith('offline'):
        return 'Offline'
    if location.startswith('private'):
        return 'Private'
    if ':' in location:
        _, instance_id = location.split(':', 1)
        if instance_id:
            return instance_id
    return location

def _friend_from_user(user: Any) -> FriendEntry:
    tags = list(getattr(user, 'tags', None) or [])
    status = user_status_from_user(user)
    return FriendEntry(user_id=str(getattr(user, 'id', '') or ''), display_name=str(getattr(user, 'display_name', '') or 'Unknown'), thumbnail_url=_profile_image_url(user), trust=trust_rank_from_tags(tags), status=status, badges=_badges_from_user(user)[:2])

def _instance_summary(api_client: vrchatapi.ApiClient, location: str) -> tuple[str | None, int | None]:
    parsed = _parse_location(location)
    if parsed is None:
        return (None, None)
    world_id, instance_id = parsed
    try:
        instance = instances_api.InstancesApi(api_client).get_instance(world_id, instance_id)
    except (UnauthorizedException, ApiException):
        logger.debug('Could not load instance for %s', location, exc_info=True)
        return (None, None)
    world = getattr(instance, 'world', None)
    world_name = str(getattr(world, 'name', '') or '').strip() or None
    player_count = int(getattr(instance, 'user_count', 0) or getattr(instance, 'n_users', 0) or 0)
    return (world_name, player_count or None)

def _cached_instance_summary(api_client: vrchatapi.ApiClient, location: str) -> tuple[str | None, int | None]:
    cache_key = _instance_location_key(location) or location
    now = time.monotonic()
    cached = _INSTANCE_SUMMARY_CACHE.get(cache_key)
    if cached is not None and now - cached[0] < _INSTANCE_SUMMARY_TTL_SEC:
        return (cached[1], cached[2])
    summary = _instance_summary(api_client, location)
    _INSTANCE_SUMMARY_CACHE[cache_key] = (now, summary[0], summary[1])
    return summary

def _enrich_joinable_friends(api_client: vrchatapi.ApiClient, friends: list[FriendEntry], *, max_world_lookups: int=8) -> list[FriendEntry]:
    cache: dict[str, tuple[str | None, int | None]] = {}
    lookups = 0
    enriched: list[FriendEntry] = []
    for friend in friends:
        if not friend.can_join or not friend.status.location:
            enriched.append(friend)
            continue
        location = friend.status.location
        cache_key = _instance_location_key(location) or location
        if cache_key not in cache:
            if lookups >= max_world_lookups:
                cache[cache_key] = (None, None)
            else:
                cache[cache_key] = _cached_instance_summary(api_client, location)
                lookups += 1
        world_name, player_count = cache[cache_key]
        enriched.append(FriendEntry(user_id=friend.user_id, display_name=friend.display_name, thumbnail_url=friend.thumbnail_url, trust=friend.trust, status=friend.status, badges=friend.badges, world_name=world_name, player_count=player_count))
    return enriched
_FRIEND_STATUS_SORT_ORDER: dict[str, int] = {'join_me': 0, 'active': 1, 'invisible': 1, 'private': 1, 'ask_me': 2, 'busy': 3, 'offline': 4}

def _friend_sort_key(entry: FriendEntry) -> tuple[int, str]:
    order = _FRIEND_STATUS_SORT_ORDER.get(entry.status.key, 5)
    return (order, entry.display_name.casefold())

def _fetch_friend_users(api: friends_api.FriendsApi, *, include_offline: bool=True) -> list[Any]:
    users: list[Any] = []
    seen: set[str] = set()
    offline_modes = (False, True) if include_offline else (False,)
    for offline in offline_modes:
        offset = 0
        while offset < 2000:
            try:
                batch = api.get_friends(offset=offset, n=100, offline=offline) or []
            except (UnauthorizedException, ApiException) as exc:
                if getattr(exc, 'status', None) == 401:
                    return []
                raise
            if not batch:
                break
            for friend in batch:
                user_id = str(getattr(friend, 'id', '') or '')
                if not user_id or user_id in seen:
                    continue
                seen.add(user_id)
                users.append(friend)
            if len(batch) < 100:
                break
            offset += len(batch)
    return users

def _raise_api_error(exc: UnauthorizedException | ApiException) -> None:
    detail = api_error_text(exc).casefold()
    if getattr(exc, 'status', None) == 429 or 'too many' in detail or 'rate limit' in detail:
        raise AuthSessionError('VRChat rate limit reached — wait a few minutes and try again.') from exc
    auth_error = auth_session_error_from_api(exc)
    if auth_error is not None:
        raise auth_error
    raise exc

def get_friends_list(session: VRChatSession, *, include_offline: bool=True, enrich_worlds: bool=True, max_world_lookups: int=8) -> list[FriendEntry]:
    try:
        friends: list[FriendEntry] = []
        with make_api_client(session) as api_client:
            api = friends_api.FriendsApi(api_client)
            for friend in _fetch_friend_users(api, include_offline=include_offline):
                friends.append(_friend_from_user(friend))
            if enrich_worlds:
                friends = _enrich_joinable_friends(api_client, friends, max_world_lookups=max_world_lookups)
        friends.sort(key=_friend_sort_key)
        resolved: list[FriendEntry] = []
        for friend in friends:
            thumb = normalize_thumbnail_url(friend.thumbnail_url) or friend.thumbnail_url
            if thumb != friend.thumbnail_url:
                friend = FriendEntry(user_id=friend.user_id, display_name=friend.display_name, thumbnail_url=thumb, trust=friend.trust, status=friend.status, badges=friend.badges, world_name=friend.world_name, player_count=friend.player_count)
            resolved.append(friend)
        friends = resolved
        _rebuild_friend_instance_index(friends)
        for friend in friends:
            thumb = normalize_thumbnail_url(friend.thumbnail_url)
            if thumb:
                _USER_THUMB_CACHE[friend.user_id] = thumb
            _USER_TRUST_CACHE[friend.user_id] = friend.trust
        return friends
    except AuthSessionError:
        raise
    except (UnauthorizedException, ApiException) as exc:
        _raise_api_error(exc)
    except Exception:
        logger.debug('Could not load friends list', exc_info=True)
        return []

def get_current_user_profile(session: VRChatSession) -> CurrentUserProfile | None:
    _, log_location = log_players_for_current_room()
    try:
        with make_api_client(session) as api_client:
            auth = authentication_api.AuthenticationApi(api_client)
            try:
                me = auth.get_current_user()
            except AuthSessionError:
                raise
            except (UnauthorizedException, ApiException) as exc:
                _raise_api_error(exc)
            tags = list(getattr(me, 'tags', None) or [])
            bio = str(getattr(me, 'bio', '') or '').strip()
            status_description = str(getattr(me, 'status_description', '') or '').strip()
            description = bio or status_description
            location = _location_from_user(me, log_fallback=log_location)
            status = user_status_from_user(me, log_fallback=log_location)
            world_name: str | None = None
            instance_label = _instance_label_from_location(location)
            parsed = _parse_location(location or '')
            if parsed is not None:
                world_id, instance_id = parsed
                inst_api = instances_api.InstancesApi(api_client)
                try:
                    instance = inst_api.get_instance(world_id, instance_id)
                    world = getattr(instance, 'world', None)
                    if world is not None:
                        world_name = str(getattr(world, 'name', '') or '').strip() or None
                except (UnauthorizedException, ApiException) as exc:
                    if getattr(exc, 'status', None) != 401:
                        logger.debug('Could not load world for profile panel', exc_info=True)
                if instance_label is None and instance_id:
                    instance_label = instance_id
            return CurrentUserProfile(user_id=str(getattr(me, 'id', '') or session.user_id), display_name=str(getattr(me, 'display_name', '') or session.display_name or 'Unknown'), bio=description, status_description=status_description, image_url=_profile_image_url(me), trust=trust_rank_from_tags(tags), badges=_badges_from_user(me), status=status, world_name=world_name, instance_label=instance_label)
    except AuthSessionError:
        raise
    except Exception:
        logger.debug('Could not load current user profile', exc_info=True)
        return None

def get_current_instance(session: VRChatSession, *, max_enrich_requests: int=8, friend_players_cache_only: bool=True) -> InstanceInfo | None:
    log_players, log_location = log_players_for_current_room()
    avatar_ids = parse_avatar_ids_from_log()
    sync_live_log_avatars(avatar_ids)
    try:
        with make_api_client(session) as api_client:
            auth = authentication_api.AuthenticationApi(api_client)
            try:
                me = auth.get_current_user()
            except (UnauthorizedException, ApiException) as exc:
                if getattr(exc, 'status', None) == 401:
                    logger.debug('VRChat auth expired or missing for instance lookup')
                    return get_current_instance_log_only(session)
                raise
            location = _location_from_user(me) or log_location
            parsed = _parse_location(location or '')
            if parsed is None and log_location:
                parsed = _parse_location(log_location)
            if parsed is None:
                return None
            world_id, instance_id = parsed
            world_name = ''
            owner_id = ''
            region = ''
            instance_type = ''
            api_count = 0
            api_players: list[InstancePlayer] = []
            inst_api = instances_api.InstancesApi(api_client)
            try:
                instance = inst_api.get_instance(world_id, instance_id)
            except (UnauthorizedException, ApiException) as exc:
                if getattr(exc, 'status', None) == 401:
                    return get_current_instance_log_only(session)
                instance = None
            if instance is not None:
                world = getattr(instance, 'world', None)
                if world is not None:
                    world_name = str(getattr(world, 'name', '') or '').strip()
                if not world_name:
                    world_name = _fetch_world_name(api_client, world_id)
                api_players = [_instance_player_from_limited(user, is_friend=bool(getattr(user, 'is_friend', False)), avatar_id=avatar_ids.get(str(getattr(user, 'id', '') or ''))) for user in getattr(instance, 'users', None) or []]
                owner_id = str(getattr(instance, 'owner_id', '') or '')
                region = str(getattr(instance, 'region', '') or '')
                if '~' in instance_id:
                    instance_type = instance_id.split('~', 1)[1].split('(', 1)[0]
                api_count = int(getattr(instance, 'user_count', 0) or getattr(instance, 'n_users', 0) or len(api_players))
                if not location:
                    location = str(getattr(instance, 'location', '') or '') or log_location
            friend_players = _players_from_friends(api_client, location or '', cache_only=friend_players_cache_only)
            api_friend_ids = {player.user_id for player in api_players if player.is_friend}
            api_friend_ids.update((player.user_id for player in friend_players))
            log_enriched = [_instance_player_from_log(player, is_friend=player.user_id in api_friend_ids, avatar_id=avatar_ids.get(player.user_id)) for player in log_players]
            me_player = _instance_player_from_limited(me, avatar_id=avatar_ids.get(str(getattr(me, 'id', '') or session.user_id)))
            players = _merge_players(api_players, friend_players, log_enriched, [me_player])
            players = _apply_cached_thumbnails(players)
            players = _apply_cached_trust(players)
            players = _enrich_players_from_api(api_client, players, session=session, max_requests=max_enrich_requests)
            _remember_player_thumbnails(players)
            _remember_player_trust(players)
            count = max(api_count, len(players), len(log_players))
            return InstanceInfo(world_id=world_id, instance_id=instance_id, world_name=world_name, player_count=count, players=players, owner_id=owner_id, can_close_instance=owner_id == session.user_id, region=region, instance_type=instance_type)
    except Exception:
        logger.debug('Could not load current instance', exc_info=True)
        return get_current_instance_log_only(session)

def enrich_instance_players(session: VRChatSession, players: list[InstancePlayer], *, max_requests: int=12, priority_user_ids: set[str] | None=None) -> list[InstancePlayer]:
    if not players:
        return players
    players = _apply_cached_thumbnails(players)
    if max_requests <= 0:
        return players
    missing = sum((1 for player in players if not player.thumbnail_url))
    if missing <= 0:
        return players
    try:
        with make_api_client(session) as api_client:
            return _enrich_players_from_api(api_client, players, session=session, max_requests=max_requests, priority_user_ids=priority_user_ids)
    except Exception:
        logger.debug('Could not enrich instance players', exc_info=True)
        return players

def _api_error_message(exc: Exception) -> str:
    if isinstance(exc, ApiException):
        body = getattr(exc, 'body', None) or ''
        if body:
            try:
                payload = json.loads(body)
                message = payload.get('error', {}).get('message')
                if message:
                    return str(message).strip('"')
            except Exception:
                pass
        reason = getattr(exc, 'reason', None)
        if reason:
            return str(reason)
    return str(exc) or 'Request failed.'

def select_avatar(session: VRChatSession, avatar_id: str) -> str:
    if not avatar_id:
        raise ValueError('Avatar ID is required.')
    with make_api_client(session) as api_client:
        api = avatars_api.AvatarsApi(api_client)
        try:
            api.select_avatar(avatar_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return f'Switched to avatar (change applies in VRChat): {avatar_id}'

def _avatar_name_query_variants(name: str) -> list[str]:
    variants: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        cleaned = value.strip()
        key = cleaned.casefold()
        if len(cleaned) >= 2 and key not in seen:
            seen.add(key)
            variants.append(cleaned)
    add(name)
    if ' - ' in name:
        add(name.split(' - ', 1)[0])
    by_parts = re.split('\\s+by\\s+', name, maxsplit=1, flags=re.IGNORECASE)
    if len(by_parts) > 1:
        add(by_parts[0])
    return variants

def _pick_best_avatar_match(results: list[AvatarResult], avatar_name: str, *, author_name: str | None=None, user_id: str | None=None, image_file_id: str | None=None) -> str | None:
    if not results:
        return None
    target = avatar_name.casefold()
    target_author = (author_name or '').strip().casefold()
    file_key = (image_file_id or '').strip().casefold()
    uuid_key = file_key.replace('file_', '') if file_key else ''
    if file_key:
        for result in results:
            image_url = (result.image_url or '').casefold()
            if file_key in image_url or (uuid_key and uuid_key in image_url):
                return result.id
    exact_name = [result for result in results if result.name.casefold() == target]
    if target in _GENERIC_AVATAR_NAMES:
        if user_id:
            owned = [result for result in exact_name if result.author_id == user_id]
            if len(owned) == 1:
                return owned[0].id
        return None
    if user_id:
        for result in exact_name:
            if result.author_id == user_id:
                return result.id
    if target_author:
        for result in exact_name:
            if target_author in result.author_name.casefold():
                return result.id
    if len(exact_name) == 1:
        return exact_name[0].id
    scored: list[tuple[int, AvatarResult]] = []
    for result in results:
        name_key = result.name.casefold()
        score = 0
        if name_key == target:
            score += 100
        elif name_key.startswith(target) or target.startswith(name_key):
            score += 70
        elif target in name_key:
            score += 40
        if user_id and result.author_id == user_id:
            score += 50
        if target_author and target_author in result.author_name.casefold():
            score += 35
        if score >= 70:
            scored.append((score, result))
    if not scored:
        return None
    scored.sort(key=lambda item: -item[0])
    best_score, best = scored[0]
    if best.name.casefold() == target:
        return best.id
    if best_score >= 100 and len(scored) == 1:
        return best.id
    return None

def lookup_avatar_id_by_name(avatar_name: str, author_name: str | None=None, *, image_file_id: str | None=None, user_id: str | None=None) -> str | None:
    avatar_name = avatar_name.strip()
    if len(avatar_name) < 2:
        return None
    merged: dict[str, AvatarResult] = {}
    for query in _avatar_name_query_variants(avatar_name):
        search_query = f'{query} {author_name}'.strip() if author_name else query
        for result in search_avatars_combined(search_query, limit=40):
            if result.id and result.id not in merged:
                merged[result.id] = result
        picked = _pick_best_avatar_match(list(merged.values()), avatar_name, author_name=author_name, user_id=user_id, image_file_id=image_file_id)
        if picked:
            return picked
    return None

def _is_robot_placeholder_file_id(file_id: str | None) -> bool:
    if not file_id:
        return False
    return file_id.casefold() in {item.casefold() for item in _VRCHAT_HIDDEN_AVATAR_FILE_IDS}

def _is_privacy_hidden_thumbnail(file_id: str | None, status: UserStatusInfo) -> bool:
    if not _is_robot_placeholder_file_id(file_id):
        return False
    return status.key in ('ask_me', 'busy', 'invisible')

def _file_id_from_vrchat_asset_url(url: str) -> str | None:
    match = _FILE_ID_RE.search(url or '')
    return match.group(1) if match else None

def _avatar_name_from_vrchat_file_name(file_name: str) -> str | None:
    if not file_name.startswith('Avatar - '):
        return None
    rest = file_name[len('Avatar - '):]
    if ' - Image - ' in rest:
        return rest.split(' - Image - ', 1)[0].strip()
    return None

def _fetch_vrchat_file_record(session: VRChatSession, file_id: str) -> dict[str, Any] | None:
    file_id = file_id.strip()
    if not file_id:
        return None
    url = f'https://api.vrchat.cloud/api/1/file/{file_id}'
    cookie = f'auth={session.auth_token}'
    if session.two_factor_token:
        cookie += f'; twoFactorAuth={session.two_factor_token}'

    def _do_fetch() -> dict[str, Any]:
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Cookie': cookie})
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('Unexpected file record payload')
        return payload
    try:
        return rate_limited_vrchat_call(_do_fetch)
    except Exception:
        logger.debug('Could not load VRChat file record for %s', file_id, exc_info=True)
        return None

def _lookup_avatar_id_by_thumbnail_match(session: VRChatSession, file_id: str, avatar_name: str, *, user_id: str | None=None) -> str | None:
    results = search_avatars_combined(avatar_name, limit=40)
    if not results:
        return None
    target_file = file_id.casefold()
    try:
        with make_api_client(session) as api_client:
            api = avatars_api.AvatarsApi(api_client)
            for result in results:
                try:
                    avatar = api.get_avatar(result.id)
                except (UnauthorizedException, ApiException):
                    continue
                for field in ('thumbnail_image_url', 'image_url'):
                    candidate_file = _file_id_from_vrchat_asset_url(str(getattr(avatar, field, '') or ''))
                    if candidate_file and candidate_file.casefold() == target_file:
                        return result.id
    except Exception:
        logger.debug('Thumbnail match lookup failed for %r', avatar_name, exc_info=True)
    return lookup_avatar_id_by_name(avatar_name, user_id=user_id, image_file_id=file_id)

def _lookup_avatar_id_by_owner_match(avatar_name: str, user_id: str, *, image_file_id: str | None=None) -> str | None:
    avatar_name = avatar_name.strip()
    if len(avatar_name) < 2 or not user_id:
        return None
    target = avatar_name.casefold()
    file_key = (image_file_id or '').strip().casefold()
    uuid_key = file_key.replace('file_', '') if file_key else ''
    merged: dict[str, AvatarResult] = {}
    for query in _avatar_name_query_variants(avatar_name):
        for result in search_avatars_combined(query, limit=40):
            if result.id and result.author_id == user_id and (result.id not in merged):
                merged[result.id] = result
    owned = list(merged.values())
    if not owned:
        return None
    if file_key:
        for result in owned:
            image_url = (result.image_url or '').casefold()
            if file_key in image_url or (uuid_key and uuid_key in image_url):
                return result.id
    exact = [result for result in owned if result.name.casefold() == target]
    if len(exact) == 1:
        return exact[0].id
    partial = [result for result in owned if target in result.name.casefold() or result.name.casefold() in target]
    if len(partial) == 1:
        return partial[0].id
    if len(owned) == 1:
        return owned[0].id
    return None

def _resolve_avatar_from_thumbnail_file(session: VRChatSession, user_id: str, file_id: str, *, skip_name_search: bool=False) -> str | None:
    if _is_robot_placeholder_file_id(file_id):
        return None
    resolved = lookup_avatar_id_by_image_file_id(user_id, file_id)
    if resolved:
        return resolved
    record = _fetch_vrchat_file_record(session, file_id)
    if record is None:
        return None
    avatar_name = _avatar_name_from_vrchat_file_name(str(record.get('name') or ''))
    if not avatar_name or avatar_name.casefold() in _GENERIC_AVATAR_NAMES:
        return None
    if skip_name_search:
        return None
    resolved = _lookup_avatar_id_by_thumbnail_match(session, file_id, avatar_name, user_id=user_id)
    if resolved:
        return resolved
    return _lookup_avatar_id_by_owner_match(avatar_name, user_id, image_file_id=file_id)

def _resolve_avatar_id_from_instance_presence(session: VRChatSession, user_id: str, display_name: str | None=None, *, skip_name_search: bool=False) -> str | None:
    log_players, log_location = log_players_for_current_room()
    in_log = user_id in {player.user_id for player in log_players}
    my_location = log_location
    friend_location: str | None = None
    try:
        with make_api_client(session) as api_client:
            auth = authentication_api.AuthenticationApi(api_client)
            me = auth.get_current_user()
            my_location = _location_from_user(me, log_fallback=log_location) or my_location
            if not in_log:
                user = users_api.UsersApi(api_client).get_user(user_id)
                friend_location = resolve_user_location(user)
    except Exception:
        logger.debug('Could not resolve instance presence for %s', user_id, exc_info=True)
        if not in_log:
            return None
    shared_location: str | None = None
    if in_log and log_location:
        shared_location = log_location
    elif my_location and friend_location and _same_instance(my_location, friend_location):
        shared_location = friend_location
    elif in_log:
        shared_location = log_location
    if not shared_location:
        return None
    parsed = _parse_location(shared_location)
    if parsed is None:
        return None
    world_id, instance_id = parsed
    try:
        with make_api_client(session) as api_client:
            instance = instances_api.InstancesApi(api_client).get_instance(world_id, instance_id)
            for user in getattr(instance, 'users', None) or []:
                if str(getattr(user, 'id', '')) != user_id:
                    continue
                thumbnail_url = str(getattr(user, 'current_avatar_thumbnail_image_url', '') or getattr(user, 'thumbnail_url', '') or '')
                file_id = _file_id_from_vrchat_asset_url(thumbnail_url)
                resolved = _resolve_avatar_from_thumbnail_file(session, user_id, file_id or '', skip_name_search=skip_name_search)
                if resolved:
                    set_cached_avatar_id(user_id, resolved, source='instance')
                    return resolved
    except Exception:
        logger.debug('Instance avatar lookup failed for %s', user_id, exc_info=True)
    return None
_JOIN_FOR_CURRENT_AVATAR = 'Join their instance to get their current avatar. VRChat does not expose their latest avatar unless you are in-world together.'

def _resolve_avatar_id_from_user_api(session: VRChatSession, user_id: str, *, allow_cached_fallback: bool=True, skip_name_search: bool=False) -> str | None:
    cached = _AVATAR_RESOLVE_CACHE.get(user_id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _AVATAR_RESOLVE_CACHE_TTL_SEC:
        return cached[1]
    resolved: str | None = None
    try:
        with make_api_client(session) as api_client:
            user = users_api.UsersApi(api_client).get_user(user_id)
    except (UnauthorizedException, ApiException):
        logger.debug('Could not load user %s for avatar lookup', user_id, exc_info=True)
        _AVATAR_RESOLVE_CACHE[user_id] = (now, None)
        return None
    except Exception:
        logger.debug('Could not load user %s for avatar lookup', user_id, exc_info=True)
        _AVATAR_RESOLVE_CACHE[user_id] = (now, None)
        return None
    status = user_status_from_user(user)
    thumbnail_url = str(getattr(user, 'current_avatar_thumbnail_image_url', '') or getattr(user, 'current_avatar_image_url', '') or '')
    file_id = _file_id_from_vrchat_asset_url(thumbnail_url)
    if _is_privacy_hidden_thumbnail(file_id, status):
        if not allow_cached_fallback:
            _AVATAR_RESOLVE_CACHE[user_id] = (now, None)
            return None
        cached_id = get_cached_avatar_id(user_id)
        if cached_id:
            _AVATAR_RESOLVE_CACHE[user_id] = (now, cached_id)
            return cached_id
        instance_resolved = _resolve_avatar_id_from_instance_presence(session, user_id, skip_name_search=skip_name_search)
        if instance_resolved:
            _AVATAR_RESOLVE_CACHE[user_id] = (now, instance_resolved)
            return instance_resolved
        _AVATAR_RESOLVE_CACHE[user_id] = (now, None)
        return None
    if file_id:
        resolved = _resolve_avatar_from_thumbnail_file(session, user_id, file_id, skip_name_search=skip_name_search)
    if resolved:
        set_cached_avatar_id(user_id, resolved, source='api')
    _AVATAR_RESOLVE_CACHE[user_id] = (now, resolved)
    return resolved

def _force_clone_failure_reason(session: VRChatSession, user_id: str) -> str:
    del session, user_id
    return _JOIN_FOR_CURRENT_AVATAR

def resolve_player_avatar_id(user_id: str, display_name: str | None=None, *, session: VRChatSession | None=None, for_force_clone: bool=False) -> str | None:
    live_map = parse_avatar_ids_from_log()
    sync_live_log_avatars(live_map)
    in_current_room = user_id in current_room_user_ids()
    live_avatar_id = live_map.get(user_id)
    if live_avatar_id:
        return live_avatar_id
    live_info = lookup_player_avatar_info(user_id, display_name)
    if live_info.avatar_id:
        sync_live_log_avatars({user_id: live_info.avatar_id})
        return live_info.avatar_id
    if live_info.avatar_name and in_current_room and (not for_force_clone):
        resolved = lookup_avatar_id_by_name(live_info.avatar_name, live_info.author_name or None, user_id=user_id)
        if resolved:
            set_cached_avatar_id(user_id, resolved, source='log_name')
            return resolved
    if session is not None and (not in_current_room):
        api_resolved = _resolve_avatar_id_from_user_api(session, user_id, allow_cached_fallback=not for_force_clone, skip_name_search=for_force_clone)
        if api_resolved:
            return api_resolved
        instance_resolved = _resolve_avatar_id_from_instance_presence(session, user_id, display_name, skip_name_search=for_force_clone)
        if instance_resolved:
            return instance_resolved
        if for_force_clone:
            return None
    if session is not None and in_current_room:
        if live_info.avatar_name and (not for_force_clone):
            resolved = lookup_avatar_id_by_name(live_info.avatar_name, live_info.author_name or None, user_id=user_id)
            if resolved:
                set_cached_avatar_id(user_id, resolved, source='log_name')
                return resolved
        instance_resolved = _resolve_avatar_id_from_instance_presence(session, user_id, display_name, skip_name_search=for_force_clone)
        if instance_resolved:
            return instance_resolved
        api_resolved = _resolve_avatar_id_from_user_api(session, user_id, allow_cached_fallback=not for_force_clone, skip_name_search=for_force_clone)
        if api_resolved:
            return api_resolved
    if for_force_clone:
        return None
    if not in_current_room:
        hist_info = lookup_player_avatar_info_historical(user_id, display_name)
        if hist_info.avatar_id:
            set_cached_avatar_id(user_id, hist_info.avatar_id, source='log')
            return hist_info.avatar_id
        if hist_info.avatar_name:
            resolved = lookup_avatar_id_by_name(hist_info.avatar_name, hist_info.author_name or None, user_id=user_id)
            if resolved:
                set_cached_avatar_id(user_id, resolved, source='log_name')
                return resolved
        cached = get_cached_avatar_id(user_id)
        if cached:
            return cached
    return None

def force_clone_player_avatar(session: VRChatSession, user_id: str, *, display_name: str | None=None, avatar_id: str | None=None) -> str:
    if not user_id:
        raise ValueError('User ID is required.')

    def _valid_avatar_id(value: str | None) -> str | None:
        cleaned = str(value or '').strip()
        return cleaned if cleaned.startswith('avtr_') else None
    for candidate in (avatar_id, parse_avatar_ids_from_log().get(user_id), get_cached_avatar_id(user_id)):
        resolved = _valid_avatar_id(candidate)
        if resolved:
            set_cached_avatar_id(user_id, resolved, source='force_clone')
            return select_avatar(session, resolved)
    resolved = resolve_player_avatar_id(user_id, display_name, session=session, for_force_clone=True)
    if not resolved:
        reason = _force_clone_failure_reason(session, user_id) if session else _JOIN_FOR_CURRENT_AVATAR
        raise RuntimeError(reason)
    set_cached_avatar_id(user_id, resolved, source='force_clone')
    return select_avatar(session, resolved)

def favorite_avatar(session: VRChatSession, avatar_id: str, *, group: str=DEFAULT_AVATAR_FAVORITE_GROUP) -> str:
    if not avatar_id:
        raise ValueError('Avatar ID is required.')
    request = AddFavoriteRequest(favorite_id=avatar_id, type=FavoriteType.AVATAR, tags=[group])
    with make_api_client(session) as api_client:
        api = favorites_api.FavoritesApi(api_client)
        try:
            api.add_favorite(add_favorite_request=request)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return f'Favorited avatar to {group}.'
