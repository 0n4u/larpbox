from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .avatar_search_connectivity import probe_provider_search
from .config import load_config
from .logging_setup import get_logger, is_debug_mode
from .vrchat_api import (
    AvatarResult,
    search_avatars_avtrdb,
    search_avatars_official,
    search_avatars_requi,
    search_avatars_vrcx_endpoint,
)
from .vrchat_auth import VRChatSession

logger = get_logger('avatar_search_providers')

_PERFORMANCE_ORDER = {
    'Excellent': 0,
    'Good': 1,
    'Medium': 2,
    'Poor': 3,
    'VeryPoor': 4,
}


JHP_SEARCH_BASE = 'https://avtr.just-h.party/vrcx_search.php'
AVATAR_RECOVERY_SEARCH_BASE = 'https://api.avatarrecovery.com/Avatar/vrcx'


@dataclass(frozen=True)
class AvatarSearchOutcome:
    results: list[AvatarResult]
    notice: str | None = None


_FALLBACK_CHAIN: dict[str, tuple[str, ...]] = {
    'just_h': ('avatar_recovery', 'avtrdb'),
}


def _short_error(exc: Exception) -> str:
    message = str(exc).strip()
    return message[:120] if message else type(exc).__name__


@dataclass(frozen=True)
class AvatarSearchFilter:
    id: str
    label: str


@dataclass(frozen=True)
class AvatarSearchProvider:
    id: str
    label: str
    description: str
    requires_login: bool
    requires_query: bool
    min_query_len: int
    filters: tuple[AvatarSearchFilter, ...]


def _filter_avtrdb_performance(results: list[AvatarResult], rating: str) -> list[AvatarResult]:
    return [item for item in results if (item.performance or '') == rating]


def _filter_avtrdb_platform(results: list[AvatarResult], platform: str) -> list[AvatarResult]:
    _ = platform
    return results


def _sort_avtrdb(results: list[AvatarResult], *, by_name: bool) -> list[AvatarResult]:
    if by_name:
        return sorted(results, key=lambda item: item.name.casefold())
    return sorted(
        results,
        key=lambda item: _PERFORMANCE_ORDER.get(item.performance or '', 99),
    )


def _search_avtrdb(query: str, filter_id: str, *, limit: int = 40) -> list[AvatarResult]:
    results = search_avatars_avtrdb(query, limit=limit)
    if filter_id.startswith('perf_'):
        results = _filter_avtrdb_performance(results, filter_id.removeprefix('perf_'))
    elif filter_id.startswith('platform_'):
        results = _filter_avtrdb_platform(results, filter_id.removeprefix('platform_'))
    elif filter_id == 'sort_name':
        results = _sort_avtrdb(results, by_name=True)
    elif filter_id == 'sort_updated':
        results = _sort_avtrdb(results, by_name=False)
    return results[:limit]


def _search_jhp(query: str, filter_id: str, *, limit: int = 40) -> list[AvatarResult]:
    _ = filter_id
    return search_avatars_vrcx_endpoint(JHP_SEARCH_BASE, query, limit=limit)


def _search_avatar_recovery(query: str, filter_id: str, *, limit: int = 40) -> list[AvatarResult]:
    _ = filter_id
    return search_avatars_vrcx_endpoint(AVATAR_RECOVERY_SEARCH_BASE, query, limit=limit)


def _search_requi(query: str, filter_id: str, *, limit: int = 40) -> list[AvatarResult]:
    _ = filter_id
    return search_avatars_requi(query, limit=limit)


def _search_vrchat(
    session: VRChatSession | None,
    query: str,
    filter_id: str,
    *,
    limit: int = 40,
) -> list[AvatarResult]:
    if session is None:
        raise RuntimeError('Login required for VRChat avatar search.')
    if filter_id == 'featured':
        results = search_avatars_official(session, featured=True, limit=limit)
    elif filter_id == 'mine_all':
        results = search_avatars_official(session, own=True, release_status='all', limit=limit)
    elif filter_id == 'mine_public':
        results = search_avatars_official(session, own=True, release_status='public', limit=limit)
    elif filter_id == 'mine_private':
        results = search_avatars_official(session, own=True, release_status='private', limit=limit)
    elif filter_id == 'featured_recent':
        results = search_avatars_official(session, featured=True, sort='created', limit=limit)
    else:
        results = search_avatars_official(session, featured=True, limit=limit)
    query = query.strip().casefold()
    if query:
        results = [
            item for item in results
            if query in item.name.casefold()
            or query in item.author_name.casefold()
            or query in item.id.casefold()
        ]
    return results[:limit]


_AVTRDB_FILTERS = (
    AvatarSearchFilter('all', 'All results'),
    AvatarSearchFilter('perf_Excellent', 'PC: Excellent'),
    AvatarSearchFilter('perf_Good', 'PC: Good'),
    AvatarSearchFilter('perf_Medium', 'PC: Medium'),
    AvatarSearchFilter('perf_Poor', 'PC: Poor'),
    AvatarSearchFilter('perf_VeryPoor', 'PC: Very Poor'),
    AvatarSearchFilter('sort_name', 'Sort by name'),
    AvatarSearchFilter('sort_updated', 'Sort by PC rating'),
    AvatarSearchFilter('platform_pc', 'Platform: PC'),
    AvatarSearchFilter('platform_android', 'Platform: Android'),
    AvatarSearchFilter('platform_ios', 'Platform: iOS'),
)

_VRCX_MIRROR_FILTERS = (
    AvatarSearchFilter('all', 'All results'),
)

_VRCHAT_FILTERS = (
    AvatarSearchFilter('featured', 'Featured avatars'),
    AvatarSearchFilter('featured_recent', 'Featured (newest)'),
    AvatarSearchFilter('mine_all', 'My avatars (all)'),
    AvatarSearchFilter('mine_public', 'My avatars (public)'),
    AvatarSearchFilter('mine_private', 'My avatars (private)'),
)

PROVIDERS: dict[str, AvatarSearchProvider] = {
    'avtrdb': AvatarSearchProvider(
        id='avtrdb',
        label='avtrDB',
        description='Global public avatar search (avtrdb.com)',
        requires_login=False,
        requires_query=True,
        min_query_len=3,
        filters=_AVTRDB_FILTERS,
    ),
    'just_h': AvatarSearchProvider(
        id='just_h',
        label='Just-H Party',
        description='Community database — auto-falls back to AvatarRecovery or avtrDB if unavailable',
        requires_login=False,
        requires_query=True,
        min_query_len=3,
        filters=_VRCX_MIRROR_FILTERS,
    ),
    'avatar_recovery': AvatarSearchProvider(
        id='avatar_recovery',
        label='AvatarRecovery',
        description='Large independent index (avatarrecovery.com)',
        requires_login=False,
        requires_query=True,
        min_query_len=3,
        filters=_VRCX_MIRROR_FILTERS,
    ),
    'requi': AvatarSearchProvider(
        id='requi',
        label='Requi.dev',
        description='Community VRCX search API (requi.dev) — may be offline',
        requires_login=False,
        requires_query=True,
        min_query_len=3,
        filters=_VRCX_MIRROR_FILTERS,
    ),
    'vrchat': AvatarSearchProvider(
        id='vrchat',
        label='VRChat Official',
        description='Featured and your own avatars via VRChat API',
        requires_login=True,
        requires_query=False,
        min_query_len=0,
        filters=_VRCHAT_FILTERS,
    ),
}

_PROVIDER_SEARCH: dict[str, Callable[..., list[AvatarResult]]] = {
    'avtrdb': lambda session, query, filter_id, limit=40: _search_avtrdb(query, filter_id, limit=limit),
    'just_h': lambda session, query, filter_id, limit=40: _search_jhp(query, filter_id, limit=limit),
    'avatar_recovery': lambda session, query, filter_id, limit=40: _search_avatar_recovery(query, filter_id, limit=limit),
    'requi': lambda session, query, filter_id, limit=40: _search_requi(query, filter_id, limit=limit),
    'vrchat': lambda session, query, filter_id, limit=40: _search_vrchat(session, query, filter_id, limit=limit),
}


def provider_list() -> list[AvatarSearchProvider]:
    return list(PROVIDERS.values())


def get_provider(provider_id: str | None = None) -> AvatarSearchProvider:
    config = load_config()
    chosen = provider_id or str(config.get('avatar_search_provider', 'avtrdb'))
    return PROVIDERS.get(chosen, PROVIDERS['avtrdb'])


def default_filter_for(provider: AvatarSearchProvider) -> str:
    config = load_config()
    saved = str(config.get('avatar_search_filter', 'all'))
    valid = {item.id for item in provider.filters}
    if saved in valid:
        return saved
    return provider.filters[0].id


def filter_label(provider: AvatarSearchProvider, filter_id: str) -> str:
    for item in provider.filters:
        if item.id == filter_id:
            return item.label
    return provider.filters[0].label


def _format_search_error(provider: AvatarSearchProvider, exc: Exception) -> str:
    detail = _short_error(exc)
    return f'{provider.label}: {detail}'


def probe_all_providers() -> list:
    from .avatar_search_connectivity import probe_provider_search

    probes = (
        ('avtrdb', 'avtrDB'),
        ('just_h', 'Just-H Party'),
        ('avatar_recovery', 'AvatarRecovery'),
        ('requi', 'Requi.dev'),
    )
    results = []
    for provider_id, label in probes:
        search_fn = _PROVIDER_SEARCH.get(provider_id)
        if search_fn is None:
            continue
        results.append(probe_provider_search(provider_id, label, search_fn))
    return results


def search_with_provider(
    provider_id: str,
    *,
    session: VRChatSession | None,
    query: str,
    filter_id: str,
    limit: int = 40,
) -> AvatarSearchOutcome:
    provider = get_provider(provider_id)
    if provider.requires_login and session is None:
        raise RuntimeError(f'Login required for {provider.label}.')
    if provider.requires_query and len(query.strip()) < provider.min_query_len:
        return AvatarSearchOutcome(results=[])
    search_fn = _PROVIDER_SEARCH.get(provider.id)
    if search_fn is None:
        raise RuntimeError(f'Unknown avatar search provider: {provider.id}')
    try:
        results = search_fn(session, query, filter_id, limit=limit)
        return AvatarSearchOutcome(results=results)
    except Exception as exc:
        last_exc = exc
        for fallback_id in _FALLBACK_CHAIN.get(provider.id, ()):
            fallback_provider = PROVIDERS.get(fallback_id)
            fallback_fn = _PROVIDER_SEARCH.get(fallback_id)
            if fallback_provider is None or fallback_fn is None:
                continue
            try:
                fallback_results = fallback_fn(session, query, filter_id, limit=limit)
                notice = (
                    f'{provider.label} is unavailable ({_short_error(exc)}). '
                    f'Showing {fallback_provider.label} results instead.'
                )
                logger.info(
                    'Provider %s failed — using fallback %s',
                    provider.id,
                    fallback_id,
                )
                return AvatarSearchOutcome(results=fallback_results, notice=notice)
            except Exception as fallback_exc:
                logger.info(
                    'Fallback %s failed after %s error: %s',
                    fallback_id,
                    provider.id,
                    _short_error(fallback_exc),
                )
                last_exc = fallback_exc
        friendly = _format_search_error(provider, last_exc)
        logger.warning('Avatar search failed for provider=%s: %s', provider.id, friendly, exc_info=is_debug_mode())
        raise RuntimeError(friendly) from last_exc
