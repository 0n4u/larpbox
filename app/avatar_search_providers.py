from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Callable
from .avatar_search_connectivity import ProviderProbeResult, probe_provider, probe_provider_search
from .config import load_config
from .logging_setup import get_logger, is_debug_mode
from .vrchat_api import AvatarResult, lookup_avatars_by_author, search_avatars_avtrdb, search_avatars_official, search_avatars_requi, search_avatars_endpoint
from .vrchat_auth import VRChatSession
logger = get_logger('avatar_search_providers')
_AVTR_RE = re.compile('^avtr_[a-f0-9-]{36}$', re.IGNORECASE)
_USR_RE = re.compile('^usr_[a-f0-9-]{36}$', re.IGNORECASE)
_PERFORMANCE_ORDER = {'Excellent': 0, 'Good': 1, 'Medium': 2, 'Poor': 3, 'VeryPoor': 4}
PERFORMANCE_COLORS = {'Excellent': '#7db87d', 'Good': '#4ea3ff', 'Medium': '#e8a040', 'Poor': '#e07070', 'VeryPoor': '#c084fc'}
JHP_SEARCH_BASE = 'https://avtr.just-h.party/vrcx_search.php'
AVATAR_RECOVERY_SEARCH_BASE = 'https://api.avatarrecovery.com/Avatar/vrcx'
_COMBINED_SOURCES = ('avtrdb', 'just_h', 'avatar_recovery', 'requi')
_COMBINED_FETCH_CAP = 100
_FILTER_PROVIDER_SUPPORT: dict[str, frozenset[str]] = {'all': frozenset(_COMBINED_SOURCES + ('vrchat',)), 'perf_Excellent': frozenset({'avtrdb'}), 'perf_Good': frozenset({'avtrdb'}), 'perf_Medium': frozenset({'avtrdb'}), 'perf_Poor': frozenset({'avtrdb'}), 'perf_VeryPoor': frozenset({'avtrdb'}), 'sort_name': frozenset({'avtrdb'}), 'sort_updated': frozenset({'avtrdb'}), 'platform_pc': frozenset({'avtrdb'}), 'platform_android': frozenset({'avtrdb'}), 'platform_ios': frozenset({'avtrdb'}), 'featured': frozenset({'vrchat'}), 'featured_recent': frozenset({'vrchat'}), 'mine_all': frozenset({'vrchat'}), 'mine_public': frozenset({'vrchat'}), 'mine_private': frozenset({'vrchat'})}
_FALLBACK_CHAIN: dict[str, tuple[str, ...]] = {'just_h': ('avatar_recovery', 'avtrdb')}

@dataclass(frozen=True)
class AvatarSearchOutcome:
    results: list[AvatarResult]
    notice: str | None = None
    has_more: bool = False

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

def _short_error(exc: Exception) -> str:
    message = str(exc).strip()
    return message[:120] if message else type(exc).__name__

def _filter_avtrdb_performance(results: list[AvatarResult], rating: str) -> list[AvatarResult]:
    return [item for item in results if (item.performance or '') == rating]

def _filter_avtrdb_platform(results: list[AvatarResult], platform: str) -> list[AvatarResult]:
    def matches(item: AvatarResult) -> bool:
        if item.platforms:
            return platform in item.platforms
        if platform == 'pc' and item.performance:
            return True
        return False
    return [item for item in results if matches(item)]

def _sort_avtrdb(results: list[AvatarResult], *, by_name: bool) -> list[AvatarResult]:
    if by_name:
        return sorted(results, key=lambda item: item.name.casefold())
    return sorted(results, key=lambda item: _PERFORMANCE_ORDER.get(item.performance or '', 99))

def _search_avtrdb(query: str, filter_id: str, *, limit: int=40, offset: int=0) -> list[AvatarResult]:
    results = search_avatars_avtrdb(query, limit=limit + offset)
    if filter_id.startswith('perf_'):
        results = _filter_avtrdb_performance(results, filter_id.removeprefix('perf_'))
    elif filter_id.startswith('platform_'):
        results = _filter_avtrdb_platform(results, filter_id.removeprefix('platform_'))
    elif filter_id == 'sort_name':
        results = _sort_avtrdb(results, by_name=True)
    elif filter_id == 'sort_updated':
        results = _sort_avtrdb(results, by_name=False)
    return results[offset:offset + limit]

def _search_jhp(query: str, filter_id: str, *, limit: int=40, offset: int=0) -> list[AvatarResult]:
    _ = filter_id
    results = search_avatars_endpoint(JHP_SEARCH_BASE, query, limit=limit + offset)
    return results[offset:offset + limit]

def _search_avatar_recovery(query: str, filter_id: str, *, limit: int=40, offset: int=0) -> list[AvatarResult]:
    _ = filter_id
    results = search_avatars_endpoint(AVATAR_RECOVERY_SEARCH_BASE, query, limit=limit + offset)
    return results[offset:offset + limit]

def _search_requi(query: str, filter_id: str, *, limit: int=40, offset: int=0) -> list[AvatarResult]:
    _ = filter_id
    results = search_avatars_requi(query, limit=limit + offset)
    return results[offset:offset + limit]

def _search_vrchat(session: VRChatSession | None, query: str, filter_id: str, *, limit: int=40, offset: int=0) -> list[AvatarResult]:
    if session is None:
        raise RuntimeError('Login required for VRChat avatar search.')
    if filter_id == 'featured':
        results = search_avatars_official(session, featured=True, limit=limit + offset)
    elif filter_id == 'mine_all':
        results = search_avatars_official(session, own=True, release_status='all', limit=limit + offset)
    elif filter_id == 'mine_public':
        results = search_avatars_official(session, own=True, release_status='public', limit=limit + offset)
    elif filter_id == 'mine_private':
        results = search_avatars_official(session, own=True, release_status='private', limit=limit + offset)
    elif filter_id == 'featured_recent':
        results = search_avatars_official(session, featured=True, sort='created', limit=limit + offset)
    else:
        results = search_avatars_official(session, featured=True, limit=limit + offset)
    query_cf = query.strip().casefold()
    if query_cf:
        results = [item for item in results if query_cf in item.name.casefold() or query_cf in item.author_name.casefold() or query_cf in item.id.casefold()]
    return results[offset:offset + limit]

def _search_by_avatar_id(session: VRChatSession | None, avatar_id: str) -> list[AvatarResult]:
    from .vrchat.core import enrich_avatar_description, make_api_client
    from vrchatapi.api import avatars_api
    if session is None:
        raise RuntimeError('Login required to look up avatar by ID.')
    with make_api_client(session) as api_client:
        api = avatars_api.AvatarsApi(api_client)
        avatar = api.get_avatar(avatar_id)
    from .vrchat.core import _avatar_from_api
    result = _avatar_from_api(avatar)
    desc = enrich_avatar_description(session, avatar_id)
    if desc:
        result = AvatarResult(id=result.id, name=result.name, description=desc, author_name=result.author_name, image_url=result.image_url, performance=result.performance, author_id=result.author_id)
    return [result]

def _search_by_author_id(author_id: str, *, limit: int=40) -> list[AvatarResult]:
    merged: dict[str, AvatarResult] = {}
    for base in ('https://api.avtrdb.com/v3/avatar/search/vrcx', AVATAR_RECOVERY_SEARCH_BASE, JHP_SEARCH_BASE):
        for item in lookup_avatars_by_author(base, author_id):
            if item.id and item.id not in merged:
                merged[item.id] = item
            if len(merged) >= limit:
                break
        if len(merged) >= limit:
            break
    return list(merged.values())[:limit]

def _search_combined(session: VRChatSession | None, query: str, filter_id: str, *, limit: int=40, offset: int=0) -> AvatarSearchOutcome:
    query = query.strip()
    if _AVTR_RE.match(query):
        try:
            return AvatarSearchOutcome(results=_search_by_avatar_id(session, query))
        except Exception as exc:
            raise RuntimeError(f'Avatar ID lookup failed: {_short_error(exc)}') from exc
    if _USR_RE.match(query):
        return AvatarSearchOutcome(results=_search_by_author_id(query, limit=limit), notice=f'Showing avatars by author {query}')
    supported = _FILTER_PROVIDER_SUPPORT.get(filter_id, frozenset(_COMBINED_SOURCES))
    active = [pid for pid in _COMBINED_SOURCES if pid in supported]
    if 'vrchat' in supported and session is not None:
        active.append('vrchat')
    merged: dict[str, AvatarResult] = {}
    skipped: list[str] = []
    failed: list[str] = []
    saturated: list[str] = []
    fetch_count = min(_COMBINED_FETCH_CAP, max(limit + offset, limit))
    for provider_id in active:
        search_fn = _PROVIDER_SEARCH.get(provider_id)
        if search_fn is None:
            continue
        try:
            batch = search_fn(session, query, filter_id, limit=fetch_count, offset=0)
            if len(batch) >= fetch_count:
                saturated.append(provider_id)
            for item in batch:
                if item.id and item.id not in merged:
                    merged[item.id] = item
        except Exception:
            logger.debug('Combined search: %s failed', provider_id, exc_info=True)
            label = PROVIDERS.get(provider_id)
            failed.append(label.label if label else provider_id)
    unsupported = [pid for pid in PROVIDERS if pid not in active and pid != 'combined']
    if filter_id != 'all' and unsupported:
        skipped = [PROVIDERS[pid].label for pid in unsupported if pid in PROVIDERS]
    all_results = list(merged.values())
    page = all_results[offset:offset + limit]
    notice_parts: list[str] = []
    if skipped:
        notice_parts.append(f"Filter active on: {', '.join((PROVIDERS[p].label for p in active))}")
    if failed and page:
        notice_parts.append(f'{len(failed)} API(s) unavailable — showing merged results')
    elif failed and (not page):
        notice_parts.append(f'All {len(failed)} APIs unavailable')
    notice = ' · '.join(notice_parts) if notice_parts else None
    has_more = len(all_results) > offset + limit or bool(saturated)
    return AvatarSearchOutcome(results=page, notice=notice, has_more=has_more)
_AVTRDB_FILTERS = (AvatarSearchFilter('all', 'All results'), AvatarSearchFilter('perf_Excellent', 'PC: Excellent'), AvatarSearchFilter('perf_Good', 'PC: Good'), AvatarSearchFilter('perf_Medium', 'PC: Medium'), AvatarSearchFilter('perf_Poor', 'PC: Poor'), AvatarSearchFilter('perf_VeryPoor', 'PC: Very Poor'), AvatarSearchFilter('platform_pc', 'Platform: PC'), AvatarSearchFilter('platform_android', 'Platform: Android'), AvatarSearchFilter('platform_ios', 'Platform: iOS'), AvatarSearchFilter('sort_name', 'Sort by name'), AvatarSearchFilter('sort_updated', 'Sort by PC rating'))
_COMBINED_FILTERS = _AVTRDB_FILTERS + (AvatarSearchFilter('featured', 'VRChat: Featured'), AvatarSearchFilter('mine_all', 'VRChat: My avatars'))
_MIRROR_FILTERS = (AvatarSearchFilter('all', 'All results'),)
_VRCHAT_FILTERS = (AvatarSearchFilter('featured', 'Featured avatars'), AvatarSearchFilter('featured_recent', 'Featured (newest)'), AvatarSearchFilter('mine_all', 'My avatars (all)'), AvatarSearchFilter('mine_public', 'My avatars (public)'), AvatarSearchFilter('mine_private', 'My avatars (private)'))
PROVIDERS: dict[str, AvatarSearchProvider] = {'combined': AvatarSearchProvider(id='combined', label='All APIs', description='Search all databases together — filters apply per supported API', requires_login=False, requires_query=True, min_query_len=2, filters=_COMBINED_FILTERS), 'avtrdb': AvatarSearchProvider(id='avtrdb', label='avtrDB', description='Global public avatar search (avtrdb.com)', requires_login=False, requires_query=True, min_query_len=3, filters=_AVTRDB_FILTERS), 'just_h': AvatarSearchProvider(id='just_h', label='Just-H Party', description='Community database — auto-falls back if unavailable', requires_login=False, requires_query=True, min_query_len=3, filters=_MIRROR_FILTERS), 'avatar_recovery': AvatarSearchProvider(id='avatar_recovery', label='AvatarRecovery', description='Large independent index (avatarrecovery.com)', requires_login=False, requires_query=True, min_query_len=3, filters=_MIRROR_FILTERS), 'requi': AvatarSearchProvider(id='requi', label='Requi.dev', description='Community avatar search API (requi.dev)', requires_login=False, requires_query=True, min_query_len=3, filters=_MIRROR_FILTERS), 'vrchat': AvatarSearchProvider(id='vrchat', label='VRChat Official', description='Featured and your own avatars via VRChat API', requires_login=True, requires_query=False, min_query_len=0, filters=_VRCHAT_FILTERS)}
_PROVIDER_SEARCH: dict[str, Callable[..., list[AvatarResult]]] = {'avtrdb': lambda session, query, filter_id, limit=40, offset=0: _search_avtrdb(query, filter_id, limit=limit, offset=offset), 'just_h': lambda session, query, filter_id, limit=40, offset=0: _search_jhp(query, filter_id, limit=limit, offset=offset), 'avatar_recovery': lambda session, query, filter_id, limit=40, offset=0: _search_avatar_recovery(query, filter_id, limit=limit, offset=offset), 'requi': lambda session, query, filter_id, limit=40, offset=0: _search_requi(query, filter_id, limit=limit, offset=offset), 'vrchat': lambda session, query, filter_id, limit=40, offset=0: _search_vrchat(session, query, filter_id, limit=limit, offset=offset)}

def provider_list() -> list[AvatarSearchProvider]:
    return list(PROVIDERS.values())

def get_provider(provider_id: str | None=None) -> AvatarSearchProvider:
    config = load_config()
    chosen = provider_id or str(config.get('avatar_search_provider', 'combined'))
    return PROVIDERS.get(chosen, PROVIDERS['combined'])

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

def search_author_avatars(author_id: str, *, limit: int=40) -> list[AvatarResult]:
    return _search_by_author_id(author_id, limit=limit)

def search_with_provider(provider_id: str, *, session: VRChatSession | None, query: str, filter_id: str, limit: int=40, offset: int=0) -> AvatarSearchOutcome:
    if provider_id == 'combined':
        if len(query.strip()) < 2 and (not (_AVTR_RE.match(query.strip()) or _USR_RE.match(query.strip()))):
            return AvatarSearchOutcome(results=[])
        return _search_combined(session, query, filter_id, limit=limit, offset=offset)
    provider = get_provider(provider_id)
    if provider.requires_login and session is None:
        raise RuntimeError(f'Login required for {provider.label}.')
    if provider.requires_query and len(query.strip()) < provider.min_query_len:
        if not (_AVTR_RE.match(query.strip()) or _USR_RE.match(query.strip())):
            return AvatarSearchOutcome(results=[])
    if _AVTR_RE.match(query.strip()):
        try:
            return AvatarSearchOutcome(results=_search_by_avatar_id(session, query.strip()))
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
    if _USR_RE.match(query.strip()):
        return AvatarSearchOutcome(results=_search_by_author_id(query.strip(), limit=limit), notice=f'Author page: {query.strip()}')
    search_fn = _PROVIDER_SEARCH.get(provider.id)
    if search_fn is None:
        raise RuntimeError(f'Unknown avatar search provider: {provider.id}')
    try:
        results = search_fn(session, query, filter_id, limit=limit, offset=offset)
        return AvatarSearchOutcome(results=results, has_more=len(results) >= limit)
    except Exception as exc:
        last_exc = exc
        for fallback_id in _FALLBACK_CHAIN.get(provider.id, ()):
            fallback_fn = _PROVIDER_SEARCH.get(fallback_id)
            fallback_provider = PROVIDERS.get(fallback_id)
            if fallback_fn is None or fallback_provider is None:
                continue
            try:
                fallback_results = fallback_fn(session, query, filter_id, limit=limit, offset=offset)
                notice = f'{provider.label} unavailable ({_short_error(exc)}). Showing {fallback_provider.label}.'
                return AvatarSearchOutcome(results=fallback_results, notice=notice)
            except Exception as fallback_exc:
                last_exc = fallback_exc
        friendly = f'{provider.label}: {_short_error(last_exc)}'
        logger.warning('Avatar search failed for provider=%s: %s', provider.id, friendly, exc_info=is_debug_mode())
        raise RuntimeError(friendly) from last_exc

def probe_all_providers(*, session: VRChatSession | None=None) -> list[ProviderProbeResult]:
    from .vrchat.core import REQUI_SEARCH_BASE
    probe_bases = {'avtrdb': 'https://api.avtrdb.com/v3/avatar/search/vrcx', 'just_h': JHP_SEARCH_BASE, 'avatar_recovery': AVATAR_RECOVERY_SEARCH_BASE, 'requi': REQUI_SEARCH_BASE}
    probes = (('avtrdb', 'avtrDB'), ('just_h', 'Just-H Party'), ('avatar_recovery', 'AvatarRecovery'), ('requi', 'Requi.dev'))
    results: list[ProviderProbeResult] = []
    for provider_id, label in probes:
        base_url = probe_bases.get(provider_id)
        search_fn = _PROVIDER_SEARCH.get(provider_id)
        tls_result = probe_provider(provider_id, label, base_url) if base_url else None
        search_result = probe_provider_search(provider_id, label, search_fn) if search_fn else None
        tls_ok = bool(tls_result and tls_result.tls_ok) or bool(search_result and search_result.tls_ok)
        api_ok = bool(search_result and search_result.api_ok) or bool(tls_result and tls_result.api_ok)
        detail_parts: list[str] = []
        if tls_result is not None:
            detail_parts.append(f"TLS: {('OK' if tls_result.tls_ok else 'fail')}")
            if tls_result.detail and tls_result.detail != 'TLS OK':
                detail_parts.append(f'HTTP: {tls_result.detail}')
        if search_result is not None:
            detail_parts.append(f'Search: {search_result.detail}')
        detail = ' | '.join(detail_parts) if detail_parts else 'Probe unavailable'
        results.append(ProviderProbeResult(provider_id, label, tls_ok, api_ok, detail))
    vrchat_fn = _PROVIDER_SEARCH.get('vrchat')
    if session is not None and vrchat_fn is not None:
        results.append(probe_provider_search('vrchat', 'VRChat Official', vrchat_fn))
    else:
        results.append(ProviderProbeResult('vrchat', 'VRChat Official', False, False, 'Login required to test'))
    try:
        outcome = search_with_provider('combined', session=session, query='test', filter_id='all', limit=3, offset=0)
        count = len(outcome.results)
        if count > 0:
            detail = f'OK ({count} merged result(s))'
            results.append(ProviderProbeResult('combined', 'Combined', True, True, detail))
        else:
            results.append(ProviderProbeResult('combined', 'Combined', True, False, 'Reachable but no test results'))
    except Exception as exc:
        results.append(ProviderProbeResult('combined', 'Combined', False, False, _short_error(exc)))
    return results
