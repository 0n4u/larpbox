from __future__ import annotations
import json
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Literal
import vrchatapi
from vrchatapi.api import authentication_api, avatars_api, favorites_api, friends_api, instances_api, invite_api, notifications_api, users_api, worlds_api
from vrchatapi.exceptions import ApiException, UnauthorizedException
from vrchatapi.models.add_favorite_request import AddFavoriteRequest
from vrchatapi.models.favorite_type import FavoriteType
from vrchatapi.models.invite_request import InviteRequest
from ..action_cancel import check_cancelled
from ..api_rate_limit import ApiPriority, api_priority, rate_limited_vrchat_call
from ..avatar_cache import FORCE_CLONE_CACHE_MAX_AGE_SEC, get_cached_avatar_id, get_cached_thumb_file_id, get_friend_api_avatar_id, get_recent_avatar_id_for_copy, note_friend_thumbnail, set_cached_avatar_id, sync_live_log_avatars
from ..logging_setup import get_logger
from ..services.auth_errors import AuthSessionError, auth_session_error_from_api, api_error_text
from ..user_status import UserStatusInfo, resolve_user_location, user_status_from_user
from ..vrchat_auth import VRChatSession, _configure_api_client, USER_AGENT
from ..vrchat_amplitude import build_user_avatar_map_from_amplitude
from ..vrchat_log_players import LogPlayer, PlayerAvatarInfo, current_room_user_ids, invalidate_log_cache, log_players_for_current_room, lookup_player_avatar_info, lookup_player_avatar_info_historical, parse_avatar_ids_from_log
from .models import AvatarResult, CurrentUserProfile, FriendEntry, InstanceInfo, InstancePlayer, TrustRank, UserBadge, trust_rank_from_tags
logger = get_logger('vrchat_api')
from .avatar_providers import (PROVIDER_USER_AGENT, AVTRDB_SEARCH_URL, REQUI_SEARCH_BASE, AVATAR_RECOVERY_SEARCH_BASE, lookup_avatar_by_file_id, lookup_avatar_by_id_external, lookup_avatars_by_author, lookup_avatars_by_author_first_hit, lookup_avatar_id_by_image_file_id, search_avatars_avtrdb, search_avatars_endpoint, search_avatars_requi, search_avatars_combined)
DEFAULT_AVATAR_FAVORITE_GROUP = 'avatars1'
_CACHE_LOCK = threading.RLock()
_INSTANCE_SUMMARY_CACHE: dict[str, tuple[float, str | None, int | None]] = {}
_CURRENT_USER_CACHE: tuple[float, Any] | None = None
_CURRENT_USER_CACHE_TTL_SEC = 12.0

def _get_current_user(api_client: vrchatapi.ApiClient) -> Any:
    global _CURRENT_USER_CACHE
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CURRENT_USER_CACHE is not None and now - _CURRENT_USER_CACHE[0] < _CURRENT_USER_CACHE_TTL_SEC:
            return _CURRENT_USER_CACHE[1]
    auth = authentication_api.AuthenticationApi(api_client)
    me = auth.get_current_user()
    with _CACHE_LOCK:
        _CURRENT_USER_CACHE = (now, me)
    return me
_INSTANCE_SUMMARY_TTL_SEC = 120.0
_USER_THUMB_CACHE: dict[str, str] = {}
_USER_TRUST_CACHE: dict[str, TrustRank] = {}
_USER_STATUS_CACHE: dict[str, UserStatusInfo] = {}
_IN_INSTANCE_STATUS = UserStatusInfo(key='active', label='Online', color='#4cd964', in_world=True)
_FILE_ID_RE = re.compile('(file_[a-f0-9-]+)', re.IGNORECASE)
_THUMBNAIL_IMAGE_RE = re.compile('https://api\\.vrchat\\.cloud/api/1/image/(file_[a-f0-9-]+)/\\d+/(\\d+)', re.IGNORECASE)
_FRIEND_INSTANCE_PLAYERS: dict[str, list[InstancePlayer]] = {}
_FILE_IMAGE_URL_CACHE: dict[str, str] = {}
_VRCHAT_HIDDEN_AVATAR_FILE_IDS = frozenset({'file_0e8c4e32-7444-44ea-ade4-313c010d4bae'})
_GENERIC_AVATAR_NAMES = frozenset({'robot', 'default'})
_EMBEDDED_BY_RE = re.compile('[\\s（(]+(?:by)\\s+(.+?)[)）]?\\s*$', re.IGNORECASE)
_INSTANCE_ROSTER_CACHE: tuple[float, str, dict[str, str]] | None = None
_INSTANCE_ROSTER_CACHE_TTL_SEC = 45.0
_AVATAR_RESOLVE_CACHE: dict[str, tuple[float, str | None]] = {}
_AVATAR_RESOLVE_CACHE_TTL_SEC = 300.0
_AUTHOR_DISPLAY_ID_CACHE: dict[str, str] = {}
_AVATAR_PERF_CACHE: dict[str, str] = {}
ForceCloneMode = Literal['in_room', 'remote']

def clear_session_caches() -> None:
    global _FRIEND_INSTANCE_PLAYERS, _CURRENT_USER_CACHE
    with _CACHE_LOCK:
        _USER_THUMB_CACHE.clear()
        _USER_TRUST_CACHE.clear()
        _USER_STATUS_CACHE.clear()
        _AVATAR_RESOLVE_CACHE.clear()
        _AUTHOR_DISPLAY_ID_CACHE.clear()
        _INSTANCE_SUMMARY_CACHE.clear()
        _AVATAR_PERF_CACHE.clear()
        _FILE_IMAGE_URL_CACHE.clear()
        _FRIEND_INSTANCE_PLAYERS = {}
        _CURRENT_USER_CACHE = None
    global _INSTANCE_ROSTER_CACHE
    _INSTANCE_ROSTER_CACHE = None

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
        index.setdefault(key, []).append(InstancePlayer(user_id=friend.user_id, display_name=friend.display_name, thumbnail_url=normalize_thumbnail_url(friend.thumbnail_url), is_friend=True, trust=friend.trust, status=friend.status, avatar_id=friend.avatar_id))
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

def make_api_client(session: VRChatSession, *, priority: ApiPriority | str=ApiPriority.BACKGROUND) -> vrchatapi.ApiClient:
    api_client = vrchatapi.ApiClient(vrchatapi.Configuration())
    _configure_api_client(api_client, auth_token=session.auth_token, two_factor_token=session.two_factor_token)
    original_call_api = api_client.call_api
    call_priority = ApiPriority(priority) if isinstance(priority, str) else priority

    def rate_limited_call_api(*args: Any, **kwargs: Any) -> Any:
        return rate_limited_vrchat_call(original_call_api, *args, priority=call_priority, **kwargs)
    api_client.call_api = rate_limited_call_api
    return api_client

def _avatar_from_api(avatar: Any) -> AvatarResult:
    performance = None
    perf = getattr(avatar, 'performance', None)
    if perf is not None:
        performance = getattr(perf, 'performance_rating', None) or getattr(perf, 'pc_rating', None)
    return AvatarResult(id=str(getattr(avatar, 'id', '') or ''), name=str(getattr(avatar, 'name', '') or 'Unknown'), description=str(getattr(avatar, 'description', '') or '').strip(), author_name=str(getattr(avatar, 'author_name', '') or ''), image_url=str(getattr(avatar, 'thumbnail_image_url', '') or getattr(avatar, 'image_url', '') or ''), performance=str(performance) if performance else None)

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
    status = user_status_from_user(user)
    if user_id:
        _USER_TRUST_CACHE[user_id] = trust
        _USER_STATUS_CACHE[user_id] = status
    perf = _avatar_performance_from_id(avatar_id)
    return InstancePlayer(user_id=user_id, display_name=str(getattr(user, 'display_name', '') or 'Unknown'), thumbnail_url=_profile_image_url(user), is_friend=is_friend or bool(getattr(user, 'is_friend', False)), trust=trust, avatar_id=avatar_id, status=status, avatar_performance=perf)

def _instance_player_from_log(log_player: LogPlayer, *, is_friend: bool=False, avatar_id: str | None=None) -> InstancePlayer:
    cached_status = _USER_STATUS_CACHE.get(log_player.user_id)
    return InstancePlayer(user_id=log_player.user_id, display_name=log_player.display_name, thumbnail_url='', is_friend=is_friend, trust=None, avatar_id=avatar_id, status=cached_status or _IN_INSTANCE_STATUS)

def get_current_instance_log_only(session: VRChatSession | None=None, *, enrich: bool=True, max_enrich_requests: int=6) -> InstanceInfo | None:
    log_players, log_location = log_players_for_current_room()
    parsed = _parse_location(log_location or '')
    if parsed is None:
        return None
    world_id, instance_id = parsed
    avatar_ids = parse_avatar_ids_from_log()
    sync_live_log_avatars(avatar_ids)
    players = [_instance_player_from_log(player, avatar_id=avatar_ids.get(player.user_id)) for player in log_players]
    players = apply_cached_thumbnails(players)
    players = _apply_cached_trust(players)
    if session is not None and enrich:
        try:
            with make_api_client(session) as api_client:
                players = _enrich_players_from_api(api_client, players, session=session, max_requests=max_enrich_requests)
        except Exception:
            logger.debug('Could not enrich log-only player list', exc_info=True)
    inst_type = instance_id.split('~', 1)[1].split('(', 1)[0] if '~' in instance_id else ''
    players = _apply_avatar_perf(players)
    return _build_instance_info(world_id, instance_id, player_count=max(len(players), 1), players=players, instance_type=inst_type)

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
            return replace(player, display_name=log_player.display_name, avatar_id=resolve_player_avatar_id(player.user_id, log_player.display_name) or player.avatar_id)
        return player
    except Exception:
        logger.debug('Could not enrich log player %s', log_player.user_id, exc_info=True)
        return InstancePlayer(user_id=log_player.user_id, display_name=log_player.display_name, thumbnail_url='', is_friend=log_player.user_id in friend_ids, trust=None, avatar_id=resolve_player_avatar_id(log_player.user_id, log_player.display_name), status=_USER_STATUS_CACHE.get(log_player.user_id) or _IN_INSTANCE_STATUS)

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
            merged[player.user_id] = replace(existing, display_name=player.display_name or existing.display_name, thumbnail_url=player.thumbnail_url or existing.thumbnail_url, is_friend=existing.is_friend or player.is_friend, trust=player.trust or existing.trust, avatar_id=player.avatar_id or existing.avatar_id, status=player.status or existing.status, avatar_performance=player.avatar_performance or existing.avatar_performance)
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

def _remember_player_status(players: list[InstancePlayer]) -> None:
    for player in players:
        if player.user_id and player.status is not None:
            _USER_STATUS_CACHE[player.user_id] = player.status

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
        updated.append(replace(player, trust=cached))
    return updated

def _apply_cached_status(players: list[InstancePlayer]) -> list[InstancePlayer]:
    updated: list[InstancePlayer] = []
    for player in players:
        if player.status is not None:
            updated.append(player)
            continue
        cached = _USER_STATUS_CACHE.get(player.user_id)
        if not cached:
            updated.append(player)
            continue
        updated.append(replace(player, status=cached))
    return updated

def _apply_cached_thumbnails(players: list[InstancePlayer]) -> list[InstancePlayer]:
    updated: list[InstancePlayer] = []
    for player in players:
        cached = _USER_THUMB_CACHE.get(player.user_id, '')
        if player.thumbnail_url or not cached:
            updated.append(player)
            continue
        updated.append(replace(player, thumbnail_url=cached))
    return updated

def apply_cached_thumbnails(players: list[InstancePlayer]) -> list[InstancePlayer]:
    players = _apply_cached_thumbnails(players)
    players = _apply_cached_status(players)
    return players

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
        needs_status = player.status is None
        if not needs_thumb and (not needs_trust) and (not needs_status) or requests >= max_requests:
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
        status = user_status_from_user(user)
        if player.user_id:
            _USER_TRUST_CACHE[player.user_id] = trust
            _USER_STATUS_CACHE[player.user_id] = status
        requests += 1
        enriched.append(replace(player, thumbnail_url=thumb or player.thumbnail_url, is_friend=player.is_friend or bool(getattr(user, 'is_friend', False)), trust=trust, status=status))
    by_id = {player.user_id: player for player in enriched}
    result = [by_id.get(player.user_id, player) for player in players]
    _remember_player_thumbnails(result)
    _remember_player_trust(result)
    _remember_player_status(result)
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

def _build_instance_info(world_id: str, instance_id: str, *, world_name: str='', player_count: int=0, players: list[InstancePlayer] | None=None, owner_id: str='', can_close_instance: bool=False, region: str='', instance_type: str='', owner_display_name: str='', max_players: int | None=None) -> InstanceInfo:
    location = f'{world_id}:{instance_id}' if world_id and instance_id else ''
    return InstanceInfo(world_id=world_id, instance_id=instance_id, world_name=world_name, player_count=player_count, players=players or [], owner_id=owner_id, can_close_instance=can_close_instance, region=region, instance_type=instance_type, location=location, owner_display_name=owner_display_name, max_players=max_players)

def _avatar_performance_from_id(avatar_id: str | None) -> str | None:
    if not avatar_id:
        return None
    return _AVATAR_PERF_CACHE.get(avatar_id)

def _player_with_avatar_perf(player: InstancePlayer) -> InstancePlayer:
    if player.avatar_performance or not player.avatar_id:
        return player
    perf = _avatar_performance_from_id(player.avatar_id)
    if not perf:
        return player
    return replace(player, avatar_performance=perf)

def _apply_avatar_perf(players: list[InstancePlayer]) -> list[InstancePlayer]:
    return [_player_with_avatar_perf(player) for player in players]

def get_avatar_performance(session: VRChatSession, avatar_id: str) -> str | None:
    avatar_id = (avatar_id or '').strip()
    if not avatar_id:
        return None
    cached = _AVATAR_PERF_CACHE.get(avatar_id)
    if cached:
        return cached
    try:
        with make_api_client(session) as api_client:
            avatar = avatars_api.AvatarsApi(api_client).get_avatar(avatar_id)
        result = _avatar_from_api(avatar)
        if result.performance:
            _AVATAR_PERF_CACHE[avatar_id] = result.performance
        return result.performance
    except Exception:
        logger.debug('Could not fetch avatar performance for %s', avatar_id, exc_info=True)
        return None

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
        enriched.append(FriendEntry(user_id=friend.user_id, display_name=friend.display_name, thumbnail_url=friend.thumbnail_url, trust=friend.trust, status=friend.status, badges=friend.badges, world_name=world_name, player_count=player_count, avatar_id=friend.avatar_id))
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
                auth_error = auth_session_error_from_api(exc)
                if auth_error is not None:
                    raise auth_error
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

def _friend_avatar_thumbnail_url(user: Any) -> str:
    """Raw current-avatar thumbnail from a friend/user API object (not profile override)."""
    for attr in ('current_avatar_thumbnail_image_url', 'current_avatar_image_url'):
        raw = str(getattr(user, attr, '') or '').strip()
        if raw:
            return raw
    return ''

def _resolve_avatar_from_friend_user(session: VRChatSession, user: Any, *, allow_name_search: bool=True) -> str | None:
    user_id = str(getattr(user, 'id', '') or '').strip()
    if not user_id:
        return None
    thumb_url = _friend_avatar_thumbnail_url(user)
    file_id = _file_id_from_vrchat_asset_url(thumb_url) or ''
    if not file_id:
        return None
    status = user_status_from_user(user)
    if _is_privacy_hidden_thumbnail(file_id, status):
        return None
    thumb_changed = note_friend_thumbnail(user_id, file_id)
    if not thumb_changed:
        cached = get_friend_api_avatar_id(user_id)
        if cached:
            return cached
    resolved = _resolve_avatar_from_thumbnail_file(session, user_id, file_id, skip_name_search=not allow_name_search)
    if resolved:
        set_cached_avatar_id(user_id, resolved, source='friend_api')
    return resolved

def _enrich_friend_avatars(session: VRChatSession, friend_users: list[Any], friends: list[FriendEntry], *, max_resolves: int=16) -> list[FriendEntry]:
    if not friend_users or max_resolves <= 0:
        return friends
    user_by_id = {str(getattr(user, 'id', '') or ''): user for user in friend_users}
    order = sorted(friends, key=lambda entry: (0 if entry.status.key == 'join_me' else 1 if entry.is_online else 2, entry.display_name.casefold()))
    resolves = 0
    avatar_by_id: dict[str, str | None] = {}
    for entry in order:
        if resolves >= max_resolves:
            break
        if entry.status.key == 'offline':
            cached = get_friend_api_avatar_id(entry.user_id, max_age_sec=FORCE_CLONE_CACHE_MAX_AGE_SEC)
            if cached:
                avatar_by_id[entry.user_id] = cached
            continue
        user = user_by_id.get(entry.user_id)
        if user is None:
            continue
        file_id = _file_id_from_vrchat_asset_url(_friend_avatar_thumbnail_url(user)) or ''
        if file_id and (not note_friend_thumbnail(entry.user_id, file_id)):
            cached = get_friend_api_avatar_id(entry.user_id)
            if cached:
                avatar_by_id[entry.user_id] = cached
                continue
        try:
            check_cancelled()
            resolved = _resolve_avatar_from_friend_user(session, user)
            resolves += 1
            if resolved:
                avatar_by_id[entry.user_id] = resolved
        except Exception:
            logger.debug('Friend avatar resolve failed for %s', entry.user_id, exc_info=True)
    if not avatar_by_id:
        return friends
    enriched: list[FriendEntry] = []
    for entry in friends:
        avatar_id = avatar_by_id.get(entry.user_id) or entry.avatar_id or get_friend_api_avatar_id(entry.user_id)
        if avatar_id and avatar_id != entry.avatar_id:
            entry = FriendEntry(user_id=entry.user_id, display_name=entry.display_name, thumbnail_url=entry.thumbnail_url, trust=entry.trust, status=entry.status, badges=entry.badges, world_name=entry.world_name, player_count=entry.player_count, avatar_id=avatar_id)
        enriched.append(entry)
    return enriched

def resolve_avatar_from_pipeline_user(session: VRChatSession, user_id: str, user: dict[str, Any]) -> str | None:
    """Resolve avatar when the VRChat pipeline reports a friend/user update (VRCX-style)."""
    from types import SimpleNamespace
    user_id = user_id.strip()
    if not user_id:
        return None
    thumb_url = str(user.get('currentAvatarThumbnailImageUrl') or user.get('currentAvatarImageUrl') or '')
    if not thumb_url:
        return None
    shim = SimpleNamespace(
        id=user_id,
        current_avatar_thumbnail_image_url=thumb_url,
        current_avatar_image_url=thumb_url,
        status=user.get('status'),
        tags=user.get('tags') or [],
    )
    return _resolve_avatar_from_friend_user(session, shim)

    detail = api_error_text(exc).casefold()
    if getattr(exc, 'status', None) == 429 or 'too many' in detail or 'rate limit' in detail:
        raise AuthSessionError('VRChat rate limit reached — wait a few minutes and try again.') from exc
    auth_error = auth_session_error_from_api(exc)
    if auth_error is not None:
        raise auth_error
    raise exc

def get_friends_list(session: VRChatSession, *, include_offline: bool=True, enrich_worlds: bool=True, max_world_lookups: int=8, max_avatar_resolves: int=16) -> list[FriendEntry]:
    try:
        friends: list[FriendEntry] = []
        with make_api_client(session) as api_client:
            api = friends_api.FriendsApi(api_client)
            friend_users = _fetch_friend_users(api, include_offline=include_offline)
            friends = [_friend_from_user(friend) for friend in friend_users]
            if max_avatar_resolves > 0:
                friends = _enrich_friend_avatars(session, friend_users, friends, max_resolves=max_avatar_resolves)
            if enrich_worlds:
                friends = _enrich_joinable_friends(api_client, friends, max_world_lookups=max_world_lookups)
        friends.sort(key=_friend_sort_key)
        resolved: list[FriendEntry] = []
        for friend in friends:
            thumb = normalize_thumbnail_url(friend.thumbnail_url) or friend.thumbnail_url
            if thumb != friend.thumbnail_url:
                friend = FriendEntry(user_id=friend.user_id, display_name=friend.display_name, thumbnail_url=thumb, trust=friend.trust, status=friend.status, badges=friend.badges, world_name=friend.world_name, player_count=friend.player_count, avatar_id=friend.avatar_id)
            resolved.append(friend)
        friends = resolved
        _rebuild_friend_instance_index(friends)
        for friend in friends:
            thumb = normalize_thumbnail_url(friend.thumbnail_url)
            if thumb:
                _USER_THUMB_CACHE[friend.user_id] = thumb
            _USER_TRUST_CACHE[friend.user_id] = friend.trust
            _USER_STATUS_CACHE[friend.user_id] = friend.status
        return friends
    except AuthSessionError:
        raise
    except (UnauthorizedException, ApiException) as exc:
        _raise_api_error(exc)
    except Exception as exc:
        logger.warning('Could not load friends list', exc_info=True)
        raise RuntimeError('Could not load friends list.') from exc

def get_current_user_profile(session: VRChatSession) -> CurrentUserProfile | None:
    _, log_location = log_players_for_current_room()
    try:
        with make_api_client(session) as api_client:
            try:
                me = _get_current_user(api_client)
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
            try:
                me = _get_current_user(api_client)
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
            owner_display_name = ''
            max_players: int | None = None
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
                max_players = int(getattr(instance, 'capacity', 0) or getattr(instance, 'recommended_capacity', 0) or 0) or None
                owner_display_name = str(getattr(instance, 'display_name', '') or '').strip()
            friend_players = _players_from_friends(api_client, location or '', cache_only=friend_players_cache_only)
            api_friend_ids = {player.user_id for player in api_players if player.is_friend}
            api_friend_ids.update((player.user_id for player in friend_players))
            log_enriched = [_instance_player_from_log(player, is_friend=player.user_id in api_friend_ids, avatar_id=avatar_ids.get(player.user_id)) for player in log_players]
            me_player = _instance_player_from_limited(me, avatar_id=avatar_ids.get(str(getattr(me, 'id', '') or session.user_id)))
            players = _merge_players(api_players, friend_players, log_enriched, [me_player])
            players = apply_cached_thumbnails(players)
            players = _apply_cached_trust(players)
            players = _enrich_players_from_api(api_client, players, session=session, max_requests=max_enrich_requests)
            _remember_player_thumbnails(players)
            _remember_player_trust(players)
            _remember_player_status(players)
            players = _apply_avatar_perf(players)
            count = max(api_count, len(players), len(log_players))
            return _build_instance_info(world_id, instance_id, world_name=world_name, player_count=count, players=players, owner_id=owner_id, can_close_instance=owner_id == session.user_id, region=region, instance_type=instance_type, owner_display_name=owner_display_name, max_players=max_players)
    except Exception:
        logger.debug('Could not load current instance', exc_info=True)
        return get_current_instance_log_only(session)

def enrich_instance_players(session: VRChatSession, players: list[InstancePlayer], *, max_requests: int=12, priority_user_ids: set[str] | None=None) -> list[InstancePlayer]:
    if not players:
        return players
    players = apply_cached_thumbnails(players)
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
    check_cancelled()
    if not avatar_id:
        raise ValueError('Avatar ID is required.')
    with make_api_client(session, priority=ApiPriority.INTERACTIVE) as api_client:
        api = avatars_api.AvatarsApi(api_client)
        try:
            check_cancelled()
            api.select_avatar(avatar_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return f'Switched to avatar (change applies in VRChat): {avatar_id}'

def _normalize_display_name(value: str) -> str:
    return unicodedata.normalize('NFKC', value or '').strip().casefold()

def _display_names_match(a: str, b: str) -> bool:
    return _normalize_display_name(a) == _normalize_display_name(b)

def _split_log_avatar_name(avatar_name: str) -> tuple[str, str | None]:
    """Strip embedded author suffixes like 'denji （by predictable）'."""
    name = avatar_name.strip()
    match = _EMBEDDED_BY_RE.search(name)
    if match:
        clean = name[:match.start()].strip()
        author_hint = match.group(1).strip().strip(')）')
        if clean:
            return (clean, author_hint or None)
    by_parts = re.split('\\s+by\\s+', name, maxsplit=1, flags=re.IGNORECASE)
    if len(by_parts) > 1 and by_parts[0].strip():
        return (by_parts[0].strip(), by_parts[1].strip() or None)
    return (name, None)

def _avatar_name_query_variants(name: str) -> list[str]:
    variants: list[str] = []
    seen: set[str] = set()
    clean_name, _embedded_author = _split_log_avatar_name(name)

    def add(value: str) -> None:
        cleaned = value.strip()
        key = cleaned.casefold()
        min_len = 1 if cleaned in {'`', "'", '"'} else 2
        if len(cleaned) >= min_len and key not in seen:
            seen.add(key)
            variants.append(cleaned)
    add(clean_name)
    add(name)
    if ' - ' in clean_name:
        add(clean_name.split(' - ', 1)[0])
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
        partial_only = [result for result in results if target in result.name.casefold()]
        if len(partial_only) == 1:
            return partial_only[0].id
        return None
    scored.sort(key=lambda item: -item[0])
    best_score, best = scored[0]
    if best.name.casefold() == target:
        return best.id
    if best_score >= 100 and len(scored) == 1:
        return best.id
    if best_score >= 70 and len(scored) == 1:
        return best.id
    return None

def lookup_avatar_id_by_name(avatar_name: str, author_name: str | None=None, *, image_file_id: str | None=None, user_id: str | None=None) -> str | None:
    avatar_name, _ = _split_log_avatar_name(avatar_name.strip())
    if len(avatar_name) < 1:
        return None
    if len(avatar_name) < 2 and author_name is None and user_id is None:
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

def _lookup_author_id_by_display_name(session: VRChatSession, display_name: str) -> str | None:
    display_name = display_name.strip()
    if len(display_name) < 2:
        return None
    cache_key = display_name.casefold()
    cached = _AUTHOR_DISPLAY_ID_CACHE.get(cache_key)
    if cached:
        return cached
    try:
        with make_api_client(session) as api_client:
            api = users_api.UsersApi(api_client)
            users = api.search_users(search=display_name, n=10) or []
    except Exception:
        logger.debug('Author search failed for %r', display_name, exc_info=True)
        return None
    target = display_name.casefold()
    exact = [user for user in users if str(getattr(user, 'display_name', '') or '').strip().casefold() == target]
    author_id = ''
    if len(exact) == 1:
        author_id = str(getattr(exact[0], 'id', '') or '')
    elif len(users) == 1:
        author_id = str(getattr(users[0], 'id', '') or '')
    if author_id:
        _AUTHOR_DISPLAY_ID_CACHE[cache_key] = author_id
        return author_id
    return None

def _lookup_avatar_by_author_name(session: VRChatSession, avatar_name: str, author_display_name: str) -> str | None:
    avatar_name = avatar_name.strip()
    author_display_name = author_display_name.strip()
    if not avatar_name or len(author_display_name) < 2:
        return None
    author_id = _lookup_author_id_by_display_name(session, author_display_name)
    if not author_id:
        return None
    results = lookup_avatars_by_author_first_hit(author_id)
    if not results:
        return None
    return _pick_best_avatar_match(results, avatar_name, author_name=author_display_name, user_id=author_id)

def lookup_avatar_id_by_log_author(session: VRChatSession, avatar_name: str, author_display_name: str) -> str | None:
    avatar_name, _ = _split_log_avatar_name(avatar_name.strip())
    author_display_name = author_display_name.strip()
    if len(author_display_name) < 2 or not avatar_name:
        return None
    return _lookup_avatar_by_author_name(session, avatar_name, author_display_name)

def _resolve_from_log_avatar_info(session: VRChatSession | None, live_info: PlayerAvatarInfo, wearer_user_id: str, *, allow_name_search: bool, display_name: str | None=None) -> str | None:
    if not live_info.avatar_name or not allow_name_search:
        return None
    clean_name, embedded_author = _split_log_avatar_name(live_info.avatar_name)
    author_hints: list[str] = []
    if live_info.author_name:
        author_hints.append(live_info.author_name)
    if embedded_author:
        author_hints.append(embedded_author)
    if display_name and live_info.author_name and _display_names_match(live_info.author_name, display_name):
        resolved = _lookup_avatar_id_by_owner_match(clean_name, wearer_user_id)
        if resolved:
            return resolved
    for author in author_hints:
        if len(clean_name) >= 2 or author:
            resolved = lookup_avatar_id_by_name(clean_name, author, user_id=wearer_user_id)
            if resolved:
                return resolved
        if session is not None:
            resolved = lookup_avatar_id_by_log_author(session, clean_name, author)
            if resolved:
                return resolved
    if len(clean_name) >= 2:
        resolved = _lookup_avatar_id_by_owner_match(clean_name, wearer_user_id)
        if resolved:
            return resolved
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

_THUMBNAIL_MATCH_MAX_GET_AVATAR = 8

def _lookup_avatar_id_by_thumbnail_match(session: VRChatSession, file_id: str, avatar_name: str, *, user_id: str | None=None) -> str | None:
    results = search_avatars_combined(avatar_name, limit=40)
    if not results:
        return None
    target_file = file_id.casefold()
    try:
        with make_api_client(session, priority=ApiPriority.INTERACTIVE) as api_client:
            api = avatars_api.AvatarsApi(api_client)
            for result in results[:_THUMBNAIL_MATCH_MAX_GET_AVATAR]:
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

def _resolve_avatar_from_thumbnail_file(session: VRChatSession, user_id: str, file_id: str, *, skip_name_search: bool=False, fast: bool=False) -> str | None:
    if _is_robot_placeholder_file_id(file_id):
        return None
    resolved = lookup_avatar_id_by_image_file_id(user_id, file_id)
    if resolved:
        return resolved
    if fast:
        return None
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

def _instance_roster_thumb_file_ids(session: VRChatSession, shared_location: str) -> dict[str, str]:
    """Cache get_instance user thumbnails for the current room (one API call per TTL)."""
    global _INSTANCE_ROSTER_CACHE
    parsed = _parse_location(shared_location)
    if parsed is None:
        return {}
    world_id, instance_id = parsed
    cache_key = f'{world_id}:{instance_id}'
    now = time.monotonic()
    if _INSTANCE_ROSTER_CACHE is not None:
        cached_at, cached_location, mapping = _INSTANCE_ROSTER_CACHE
        if cached_location == cache_key and now - cached_at < _INSTANCE_ROSTER_CACHE_TTL_SEC:
            return dict(mapping)
    mapping: dict[str, str] = {}
    try:
        with make_api_client(session) as api_client:
            instance = instances_api.InstancesApi(api_client).get_instance(world_id, instance_id)
            for user in getattr(instance, 'users', None) or []:
                user_id = str(getattr(user, 'id', '') or '').strip()
                if not user_id:
                    continue
                thumbnail_url = str(
                    getattr(user, 'current_avatar_thumbnail_image_url', '')
                    or getattr(user, 'thumbnail_url', '')
                    or ''
                )
                file_id = _file_id_from_vrchat_asset_url(thumbnail_url) or ''
                if file_id:
                    mapping[user_id] = file_id
    except Exception:
        logger.debug('Could not load instance roster thumbnails for %s', cache_key, exc_info=True)
        return mapping
    _INSTANCE_ROSTER_CACHE = (now, cache_key, mapping)
    return dict(mapping)

def _resolve_avatar_id_from_instance_presence(session: VRChatSession, user_id: str, display_name: str | None=None, *, skip_name_search: bool=False) -> str | None:
    check_cancelled()
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
    roster_thumbs = _instance_roster_thumb_file_ids(session, shared_location)
    roster_file_id = roster_thumbs.get(user_id, '')
    if roster_file_id and not _is_robot_placeholder_file_id(roster_file_id):
        resolved = _resolve_avatar_from_thumbnail_file(session, user_id, roster_file_id, skip_name_search=skip_name_search)
        if resolved:
            set_cached_avatar_id(user_id, resolved, source='instance')
            return resolved
    if display_name and skip_name_search:
        return None
    live_info = lookup_player_avatar_info(user_id, display_name)
    if live_info.avatar_name:
        return _resolve_from_log_avatar_info(session, live_info, user_id, allow_name_search=not skip_name_search, display_name=display_name)
    return None
_JOIN_FOR_CURRENT_AVATAR = 'Join their instance to get their current avatar. VRChat does not expose their latest avatar unless you are in-world together.'

def _resolve_avatar_id_from_user_api(session: VRChatSession, user_id: str, *, allow_cached_fallback: bool=True, skip_name_search: bool=False) -> str | None:
    check_cancelled()
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

def resolve_player_avatar_id(user_id: str, display_name: str | None=None, *, session: VRChatSession | None=None, for_force_clone: bool=False, force_clone_mode: ForceCloneMode | None=None) -> str | None:
    check_cancelled()
    live_map = parse_avatar_ids_from_log()
    sync_live_log_avatars(live_map)
    in_current_room = user_id in current_room_user_ids()
    if for_force_clone and force_clone_mode is None:
        force_clone_mode = 'in_room' if in_current_room else 'remote'
    strict_remote = for_force_clone and force_clone_mode == 'remote'
    allow_name_search = not strict_remote
    allow_cached_fallback = not strict_remote
    live_avatar_id = live_map.get(user_id)
    if live_avatar_id:
        return live_avatar_id
    live_info = lookup_player_avatar_info(user_id, display_name)
    if live_info.avatar_id:
        sync_live_log_avatars({user_id: live_info.avatar_id})
        return live_info.avatar_id
    if live_info.avatar_name and in_current_room and allow_name_search:
        resolved = _resolve_from_log_avatar_info(session, live_info, user_id, allow_name_search=allow_name_search, display_name=display_name)
        if resolved:
            set_cached_avatar_id(user_id, resolved, source='log_name')
            return resolved
    if session is not None and (not in_current_room):
        check_cancelled()
        api_resolved = _resolve_avatar_id_from_user_api(session, user_id, allow_cached_fallback=allow_cached_fallback, skip_name_search=strict_remote)
        if api_resolved:
            return api_resolved
        instance_resolved = _resolve_avatar_id_from_instance_presence(session, user_id, display_name, skip_name_search=strict_remote)
        if instance_resolved:
            return instance_resolved
        if for_force_clone:
            return None
    if session is not None and in_current_room:
        check_cancelled()
        if live_info.avatar_name and allow_name_search:
            resolved = _resolve_from_log_avatar_info(session, live_info, user_id, allow_name_search=allow_name_search, display_name=display_name)
            if resolved:
                set_cached_avatar_id(user_id, resolved, source='log_name')
                return resolved
        instance_resolved = _resolve_avatar_id_from_instance_presence(session, user_id, display_name, skip_name_search=strict_remote)
        if instance_resolved:
            return instance_resolved
        api_resolved = _resolve_avatar_id_from_user_api(session, user_id, allow_cached_fallback=allow_cached_fallback, skip_name_search=strict_remote)
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

@dataclass(frozen=True)
class ForceClonePreview:
    available: bool
    message: str

def preview_force_clone_player(session: VRChatSession | None, user_id: str, *, display_name: str | None=None, avatar_id: str | None=None, status: UserStatusInfo | None=None, resolve: bool=False) -> ForceClonePreview:
    in_current_room = user_id in current_room_user_ids()
    live_id = parse_avatar_ids_from_log().get(user_id)
    if live_id and str(live_id).strip().startswith('avtr_'):
        return ForceClonePreview(True, 'Avatar ID available from VRChat log')
    if in_current_room:
        live_info = lookup_player_avatar_info(user_id, display_name)
        if live_info.avatar_id and str(live_info.avatar_id).strip().startswith('avtr_'):
            return ForceClonePreview(True, 'Avatar ID available from VRChat log')
        if live_info.avatar_name:
            return ForceClonePreview(True, f'Avatar "{live_info.avatar_name}" seen in VRChat log')
    cached = get_cached_avatar_id(user_id, max_age_sec=FORCE_CLONE_CACHE_MAX_AGE_SEC)
    if cached:
        if in_current_room and not live_id:
            return ForceClonePreview(True, 'Cached avatar (may be outdated — wait for log)')
        return ForceClonePreview(True, 'Cached avatar ID')
    amplitude_id = build_user_avatar_map_from_amplitude().get(user_id)
    if amplitude_id and str(amplitude_id).strip().startswith('avtr_'):
        return ForceClonePreview(True, 'Avatar ID from analytics cache')
    if avatar_id and str(avatar_id).strip().startswith('avtr_'):
        hint = 'Avatar ID available from instance'
        if in_current_room:
            hint = 'Instance avatar ID (may be outdated — log preferred)'
        return ForceClonePreview(True, hint)
    status = status or _USER_STATUS_CACHE.get(user_id)
    if status is not None and not in_current_room:
        if status.key == 'private':
            return ForceClonePreview(False, 'Private — avatar is hidden')
        if status.key in ('ask_me', 'busy', 'invisible'):
            return ForceClonePreview(False, f'{status.label} — avatar hidden from API')
    if resolve and session is not None:
        mode: ForceCloneMode = 'in_room' if in_current_room else 'remote'
        resolved = resolve_player_avatar_id(user_id, display_name, session=session, for_force_clone=True, force_clone_mode=mode)
        if resolved:
            return ForceClonePreview(True, 'Avatar can be resolved')
    if in_current_room:
        room_info = lookup_player_avatar_info(user_id, display_name)
        if room_info.avatar_name and not room_info.avatar_id:
            clean_name, _ = _split_log_avatar_name(room_info.avatar_name)
            return ForceClonePreview(False, f'Avatar "{clean_name}" is not in public databases — wait for VRChat to log the avatar ID')
        return ForceClonePreview(False, 'Avatar not in log yet — wait for download or switch')
    return ForceClonePreview(False, _JOIN_FOR_CURRENT_AVATAR)

def _force_clone_failure_reason(session: VRChatSession | None, user_id: str, *, display_name: str | None=None, avatar_id: str | None=None, status: UserStatusInfo | None=None) -> str:
    in_current_room = user_id in current_room_user_ids()
    if in_current_room:
        live_info = lookup_player_avatar_info(user_id, display_name)
        if live_info.avatar_name:
            author_hint = f' by {live_info.author_name}' if live_info.author_name else ''
            return f'Could not resolve avatar ID for "{live_info.avatar_name}"{author_hint}. Wait for their avatar to finish downloading, then try again.'
    preview = preview_force_clone_player(session, user_id, display_name=display_name, avatar_id=avatar_id, status=status, resolve=False)
    if preview.available:
        return 'Avatar was seen in log but ID lookup failed — wait and try again.'
    return preview.message

def _valid_force_clone_avatar_id(value: str | None) -> str | None:
    cleaned = str(value or '').strip()
    return cleaned if cleaned.startswith('avtr_') else None

def resolve_known_avatar_id(user_id: str, display_name: str | None=None, *, avatar_id: str | None=None, session: VRChatSession | None=None) -> str | None:
    in_room = user_id in current_room_user_ids()
    live_info = lookup_player_avatar_info(user_id, display_name)
    candidate = _valid_force_clone_avatar_id(parse_avatar_ids_from_log().get(user_id))
    if candidate:
        return candidate
    candidate = _valid_force_clone_avatar_id(live_info.avatar_id)
    if candidate:
        return candidate
    if session is not None and live_info.avatar_name:
        if live_info.author_name:
            resolved = lookup_avatar_id_by_log_author(session, live_info.avatar_name, live_info.author_name)
            if resolved:
                return resolved
        resolved = lookup_avatar_id_by_name(live_info.avatar_name, live_info.author_name, user_id=user_id)
        if resolved:
            return resolved
    instance_candidate = _valid_force_clone_avatar_id(avatar_id)
    if instance_candidate and not in_room:
        return instance_candidate
    if instance_candidate and in_room and not live_info.avatar_name:
        return instance_candidate
    candidate = _valid_force_clone_avatar_id(get_recent_avatar_id_for_copy(user_id, in_room=in_room))
    if candidate:
        return candidate
    candidate = _valid_force_clone_avatar_id(build_user_avatar_map_from_amplitude().get(user_id))
    if candidate and not in_room:
        return candidate
    return instance_candidate if not in_room else None

def _complete_force_clone(session: VRChatSession, user_id: str, resolved: str) -> str:
    set_cached_avatar_id(user_id, resolved, source='force_clone')
    _AVATAR_RESOLVE_CACHE.pop(user_id, None)
    return select_avatar(session, resolved)

def force_clone_player_avatar(session: VRChatSession, user_id: str, *, display_name: str | None=None, avatar_id: str | None=None, status: UserStatusInfo | None=None, thumbnail_url: str | None=None) -> str:
    with api_priority(ApiPriority.INTERACTIVE):
        return _force_clone_player_avatar_impl(session, user_id, display_name=display_name, avatar_id=avatar_id, status=status, thumbnail_url=thumbnail_url)


def _force_clone_player_avatar_impl(session: VRChatSession, user_id: str, *, display_name: str | None=None, avatar_id: str | None=None, status: UserStatusInfo | None=None, thumbnail_url: str | None=None) -> str:
    check_cancelled()
    if not user_id:
        raise ValueError('User ID is required.')
    invalidate_log_cache()
    in_current_room = user_id in current_room_user_ids()
    force_clone_mode: ForceCloneMode = 'in_room' if in_current_room else 'remote'
    instance_id = _valid_force_clone_avatar_id(avatar_id)
    if in_current_room:
        check_cancelled()
        live_id = _valid_force_clone_avatar_id(parse_avatar_ids_from_log().get(user_id))
        if live_id:
            return _complete_force_clone(session, user_id, live_id)
        known = resolve_known_avatar_id(user_id, display_name, avatar_id=avatar_id, session=session)
        if known:
            return _complete_force_clone(session, user_id, known)
        if thumbnail_url:
            file_id = _file_id_from_vrchat_asset_url(thumbnail_url)
            if file_id and not _is_robot_placeholder_file_id(file_id):
                check_cancelled()
                resolved = _resolve_avatar_from_thumbnail_file(session, user_id, file_id, fast=True)
                if resolved:
                    return _complete_force_clone(session, user_id, resolved)
        check_cancelled()
        resolved = resolve_player_avatar_id(user_id, display_name, session=session, for_force_clone=True, force_clone_mode=force_clone_mode)
        if resolved:
            return _complete_force_clone(session, user_id, resolved)
        if thumbnail_url:
            file_id = _file_id_from_vrchat_asset_url(thumbnail_url)
            if file_id and not _is_robot_placeholder_file_id(file_id):
                check_cancelled()
                resolved = _resolve_avatar_from_thumbnail_file(session, user_id, file_id)
                if resolved:
                    return _complete_force_clone(session, user_id, resolved)
        if instance_id:
            return _complete_force_clone(session, user_id, instance_id)
        friend_api_id = _valid_force_clone_avatar_id(get_friend_api_avatar_id(user_id))
        if friend_api_id:
            return _complete_force_clone(session, user_id, friend_api_id)
    check_cancelled()
    if instance_id and not in_current_room:
        return _complete_force_clone(session, user_id, instance_id)
    friend_api_id = _valid_force_clone_avatar_id(get_friend_api_avatar_id(user_id))
    if friend_api_id:
        return _complete_force_clone(session, user_id, friend_api_id)
    if not in_current_room:
        check_cancelled()
        known = resolve_known_avatar_id(user_id, display_name, avatar_id=avatar_id, session=session)
        if known:
            return _complete_force_clone(session, user_id, known)
        resolved = resolve_player_avatar_id(user_id, display_name, session=session, for_force_clone=True, force_clone_mode=force_clone_mode)
        if resolved:
            return _complete_force_clone(session, user_id, resolved)
    check_cancelled()
    amplitude_id = _valid_force_clone_avatar_id(build_user_avatar_map_from_amplitude().get(user_id))
    if amplitude_id:
        set_cached_avatar_id(user_id, amplitude_id, source='amplitude')
        return _complete_force_clone(session, user_id, amplitude_id)
    check_cancelled()
    cached_id = _valid_force_clone_avatar_id(get_cached_avatar_id(user_id, max_age_sec=FORCE_CLONE_CACHE_MAX_AGE_SEC))
    if cached_id:
        return _complete_force_clone(session, user_id, cached_id)
    reason = _force_clone_failure_reason(session, user_id, display_name=display_name, avatar_id=avatar_id, status=status)
    raise RuntimeError(reason)

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

def list_favorite_avatar_ids(session: VRChatSession, *, limit: int=1000) -> list[str]:
    favorite_ids: list[str] = []
    seen: set[str] = set()
    with make_api_client(session) as api_client:
        api = favorites_api.FavoritesApi(api_client)
        offset = 0
        while len(favorite_ids) < limit and offset < 500:
            try:
                batch = api.get_favorites(n=min(100, limit - len(favorite_ids)), offset=offset, type=FavoriteType.AVATAR) or []
            except (UnauthorizedException, ApiException) as exc:
                raise RuntimeError(_api_error_message(exc)) from exc
            if not batch:
                break
            for fav in batch:
                avatar_id = str(getattr(fav, 'favorite_id', '') or '').strip()
                if not avatar_id.startswith('avtr_') or avatar_id in seen:
                    continue
                seen.add(avatar_id)
                favorite_ids.append(avatar_id)
            if len(batch) < 100:
                break
            offset += len(batch)
    return favorite_ids

def wardrobe_avatar_placeholder(avatar_id: str) -> AvatarResult:
    from ..wardrobe_cache import get_cached_avatar
    cached = get_cached_avatar(avatar_id)
    if cached is not None:
        return cached
    short = avatar_id.replace('avtr_', '')[:8]
    return AvatarResult(id=avatar_id, name=f'Avatar …{short}', description='', author_name='', image_url='')

def enrich_wardrobe_avatar(session: VRChatSession, avatar_id: str, *, allow_vrc_fallback: bool=True) -> AvatarResult:
    from ..wardrobe_cache import get_cached_avatar, store_avatar
    cached = get_cached_avatar(avatar_id)
    if cached is not None and cached.image_url and cached.name and (not cached.name.startswith('Avatar …')):
        return cached
    external = lookup_avatar_by_id_external(avatar_id)
    if external is not None:
        name = (external.name or '').strip()
        has_name = bool(name) and not name.startswith('Avatar …')
        if external.image_url or has_name:
            store_avatar(external)
            if external.image_url or not allow_vrc_fallback:
                return external
    if not allow_vrc_fallback:
        return external or wardrobe_avatar_placeholder(avatar_id)
    with make_api_client(session) as api_client:
        avatar = avatars_api.AvatarsApi(api_client).get_avatar(avatar_id)
    result = _avatar_from_api(avatar)
    store_avatar(result)
    return result

def get_favorite_avatars(session: VRChatSession, *, limit: int=100) -> list[AvatarResult]:
    return [wardrobe_avatar_placeholder(avatar_id) for avatar_id in list_favorite_avatar_ids(session, limit=limit)]

def invite_user_to_instance(session: VRChatSession, user_id: str, *, world_id: str, instance_id: str, message_slot: int=0) -> str:
    if not user_id:
        raise ValueError('User ID is required.')
    if not instance_id:
        raise ValueError('Instance ID is required.')
    request = InviteRequest(instance_id=instance_id, message_slot=message_slot)
    with make_api_client(session) as api_client:
        api = invite_api.InviteApi(api_client)
        try:
            api.invite_user(user_id, invite_request=request)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return f'Invite sent to {user_id}.'

_POLL_NOTIFICATION_TYPES = frozenset({'friendRequest', 'invite', 'requestInvite', 'inviteResponse', 'requestInviteResponse'})

def get_pending_notifications(session: VRChatSession) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    with make_api_client(session) as api_client:
        api = notifications_api.NotificationsApi(api_client)
        try:
            batch = api.get_notifications() or []
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    for notif in batch:
        notif_type = str(getattr(getattr(notif, 'type', None), 'value', None) or getattr(notif, 'type', '') or '')
        if notif_type not in _POLL_NOTIFICATION_TYPES:
            continue
        if bool(getattr(notif, 'seen', False)):
            continue
        details_raw = getattr(notif, 'details', None)
        world_name = ''
        if details_raw:
            try:
                details = json.loads(details_raw) if isinstance(details_raw, str) else details_raw
                if isinstance(details, dict):
                    world_name = str(details.get('worldName') or details.get('world_name') or '')
            except Exception:
                pass
        items.append({'id': str(getattr(notif, 'id', '') or ''), 'type': notif_type, 'sender_username': str(getattr(notif, 'sender_username', '') or ''), 'sender_user_id': str(getattr(notif, 'sender_user_id', '') or ''), 'message': str(getattr(notif, 'message', '') or ''), 'world_name': world_name})
    return items

def accept_friend_request(session: VRChatSession, notification_id: str) -> str:
    if not notification_id:
        raise ValueError('Notification ID is required.')
    with make_api_client(session) as api_client:
        api = notifications_api.NotificationsApi(api_client)
        try:
            api.accept_friend_request(notification_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return 'Friend request accepted.'
