from __future__ import annotations
from typing import Any
from vrchatapi.api import worlds_api
from vrchatapi.exceptions import ApiException, UnauthorizedException
from ..logging_setup import get_logger
from ..services.auth_errors import api_error_text
from ..vrchat_auth import VRChatSession
from .core import make_api_client, normalize_thumbnail_url

logger = get_logger('vrchat_worlds')


def _world_to_dict(world: Any) -> dict[str, Any]:
    image = str(getattr(world, 'thumbnail_image_url', '') or getattr(world, 'image_url', '') or '')
    return {
        'id': str(getattr(world, 'id', '') or ''),
        'name': str(getattr(world, 'name', '') or 'Unknown'),
        'author_name': str(getattr(world, 'author_name', '') or ''),
        'author_id': str(getattr(world, 'author_id', '') or ''),
        'image_url': normalize_thumbnail_url(image) or image,
        'capacity': int(getattr(world, 'capacity', 0) or 0),
        'occupants': int(getattr(world, 'occupants', 0) or 0),
        'favorites': int(getattr(world, 'favorites', 0) or 0),
        'visits': int(getattr(world, 'visits', 0) or 0),
        'heat': int(getattr(world, 'heat', 0) or 0),
        'description': str(getattr(world, 'description', '') or '').strip(),
        'release_status': str(getattr(getattr(world, 'release_status', None), 'value', None) or getattr(world, 'release_status', '') or ''),
    }


def search_worlds(session: VRChatSession, query: str, *, sort: str = 'popularity', limit: int = 30) -> list[dict[str, Any]]:
    with make_api_client(session) as api_client:
        api = worlds_api.WorldsApi(api_client)
        kwargs: dict[str, Any] = {'n': max(1, min(limit, 100)), 'offset': 0, 'order': 'descending'}
        cleaned = (query or '').strip()
        if cleaned:
            kwargs['search'] = cleaned
            kwargs['sort'] = sort
        else:
            kwargs['featured'] = True
            kwargs['sort'] = 'popularity'
        try:
            results = api.search_worlds(**kwargs) or []
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(api_error_text(exc)) from exc
    return [_world_to_dict(world) for world in results]


def get_world_detail(session: VRChatSession, world_id: str) -> dict[str, Any] | None:
    world_id = (world_id or '').strip()
    if not world_id.startswith('wrld_'):
        return None
    with make_api_client(session) as api_client:
        api = worlds_api.WorldsApi(api_client)
        try:
            world = api.get_world(world_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(api_error_text(exc)) from exc
    return _world_to_dict(world)


def world_page_url(world_id: str) -> str:
    return f'https://vrchat.com/home/world/{world_id}'
