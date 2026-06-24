from __future__ import annotations
from typing import Any
from vrchatapi.api import groups_api, users_api
from vrchatapi.exceptions import ApiException, UnauthorizedException
from ..logging_setup import get_logger
from ..services.auth_errors import api_error_text
from ..vrchat_auth import VRChatSession
from .core import make_api_client, normalize_thumbnail_url

logger = get_logger('vrchat_groups')


def _group_to_dict(group: Any) -> dict[str, Any]:
    group_id = str(getattr(group, 'group_id', '') or getattr(group, 'id', '') or '')
    icon = str(getattr(group, 'icon_url', '') or '')
    banner = str(getattr(group, 'banner_url', '') or '')
    return {
        'id': group_id,
        'name': str(getattr(group, 'name', '') or 'Unknown group'),
        'short_code': str(getattr(group, 'short_code', '') or ''),
        'discriminator': str(getattr(group, 'discriminator', '') or ''),
        'description': str(getattr(group, 'description', '') or '').strip(),
        'member_count': int(getattr(group, 'member_count', 0) or 0),
        'icon_url': normalize_thumbnail_url(icon) or icon,
        'banner_url': banner,
        'owner_id': str(getattr(group, 'owner_id', '') or ''),
        'privacy': str(getattr(getattr(group, 'privacy', None), 'value', None) or getattr(group, 'privacy', '') or ''),
    }


def get_user_groups(session: VRChatSession, user_id: str) -> list[dict[str, Any]]:
    user_id = (user_id or '').strip()
    if not user_id:
        return []
    with make_api_client(session) as api_client:
        api = users_api.UsersApi(api_client)
        try:
            groups = api.get_user_groups(user_id) or []
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(api_error_text(exc)) from exc
    out = [_group_to_dict(group) for group in groups]
    out.sort(key=lambda g: g['name'].casefold())
    return out


def get_group(session: VRChatSession, group_id: str) -> dict[str, Any] | None:
    group_id = (group_id or '').strip()
    if not group_id:
        return None
    with make_api_client(session) as api_client:
        api = groups_api.GroupsApi(api_client)
        try:
            group = api.get_group(group_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(api_error_text(exc)) from exc
    return _group_to_dict(group)


def get_group_instances(session: VRChatSession, group_id: str) -> list[dict[str, Any]]:
    group_id = (group_id or '').strip()
    if not group_id:
        return []
    with make_api_client(session) as api_client:
        api = groups_api.GroupsApi(api_client)
        try:
            instances = api.get_group_instances(group_id) or []
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(api_error_text(exc)) from exc
    result: list[dict[str, Any]] = []
    for inst in instances:
        world = getattr(inst, 'world', None)
        result.append({
            'instance_id': str(getattr(inst, 'instance_id', '') or ''),
            'location': str(getattr(inst, 'location', '') or ''),
            'member_count': int(getattr(inst, 'member_count', 0) or 0),
            'world_id': str(getattr(world, 'id', '') or ''),
            'world_name': str(getattr(world, 'name', '') or ''),
        })
    return result


def get_group_posts(session: VRChatSession, group_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    group_id = (group_id or '').strip()
    if not group_id:
        return []
    with make_api_client(session) as api_client:
        api = groups_api.GroupsApi(api_client)
        try:
            response = api.get_group_posts(group_id, n=max(1, min(limit, 100)), offset=0)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(api_error_text(exc)) from exc
    posts = getattr(response, 'posts', None)
    if posts is None and isinstance(response, list):
        posts = response
    result: list[dict[str, Any]] = []
    for post in posts or []:
        result.append({
            'id': str(getattr(post, 'id', '') or ''),
            'title': str(getattr(post, 'title', '') or '').strip(),
            'text': str(getattr(post, 'text', '') or '').strip(),
            'author_id': str(getattr(post, 'author_id', '') or ''),
            'created_at': str(getattr(post, 'created_at', '') or ''),
        })
    return result


def group_page_url(group_id: str) -> str:
    return f'https://vrchat.com/home/group/{group_id}'
