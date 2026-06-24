from __future__ import annotations
from typing import Any
from vrchatapi.api import users_api
from vrchatapi.exceptions import ApiException, UnauthorizedException
from vrchatapi.models.update_user_request import UpdateUserRequest
from ..logging_setup import get_logger
from ..services.auth_errors import api_error_text
from ..vrchat_auth import VRChatSession
from .core import make_api_client

logger = get_logger('vrchat_profile')

VALID_STATUS = ('join me', 'active', 'ask me', 'busy')
STATUS_LABELS = {
    'join me': 'Join Me',
    'active': 'Active',
    'ask me': 'Ask Me',
    'busy': 'Do Not Disturb',
}
_MAX_STATUS_DESCRIPTION = 32


def update_status(session: VRChatSession, *, status: str | None = None, status_description: str | None = None) -> str:
    user_id = (session.user_id or '').strip()
    if not user_id:
        raise RuntimeError('No active VRChat user. Sign in first.')
    kwargs: dict[str, Any] = {}
    if status is not None:
        normalized = status.strip().lower()
        if normalized not in VALID_STATUS:
            raise ValueError(f'Invalid status: {status!r}')
        kwargs['status'] = normalized
    if status_description is not None:
        kwargs['status_description'] = status_description.strip()[:_MAX_STATUS_DESCRIPTION]
    if not kwargs:
        return 'Nothing to update.'
    request = UpdateUserRequest(**kwargs)
    with make_api_client(session) as api_client:
        api = users_api.UsersApi(api_client)
        try:
            api.update_user(user_id, update_user_request=request)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(api_error_text(exc)) from exc
    return 'Status updated.'
