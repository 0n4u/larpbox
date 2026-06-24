from __future__ import annotations
import json
from vrchatapi.api import friends_api, instances_api, playermoderation_api, users_api
from vrchatapi.exceptions import ApiException, UnauthorizedException
from vrchatapi.models.moderate_user_request import ModerateUserRequest
from ..logging_setup import get_logger
from ..vrchat_auth import VRChatSession
from .core import make_api_client
from .models import TrustRank, trust_rank_from_tags
logger = get_logger('vrchat.moderation')
MODERATION_TYPES = frozenset({'block', 'mute', 'hideAvatar', 'interactOff', 'avatarInteraction'})

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

def moderate_player(session: VRChatSession, user_id: str, moderation_type: str) -> str:
    if not user_id:
        raise ValueError('User ID is required.')
    request = ModerateUserRequest(moderated=user_id, type=moderation_type)
    with make_api_client(session) as api_client:
        api = playermoderation_api.PlayermoderationApi(api_client)
        try:
            api.moderate_user(moderate_user_request=request)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return f'Applied {moderation_type} to player.'

def unmoderate_player(session: VRChatSession, user_id: str, moderation_type: str) -> str:
    if not user_id:
        raise ValueError('User ID is required.')
    request = ModerateUserRequest(moderated=user_id, type=moderation_type)
    with make_api_client(session) as api_client:
        api = playermoderation_api.PlayermoderationApi(api_client)
        try:
            api.unmoderate_user(moderate_user_request=request)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return f'Removed {moderation_type} from player.'

def unfriend_user(session: VRChatSession, user_id: str) -> str:
    if not user_id:
        raise ValueError('User ID is required.')
    with make_api_client(session) as api_client:
        api = friends_api.FriendsApi(api_client)
        try:
            api.delete_friend(user_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return 'Removed friend.'

def close_instance(session: VRChatSession, world_id: str, instance_id: str, *, hard_close: bool=True) -> str:
    if not world_id or not instance_id:
        raise ValueError('World and instance ID are required.')
    with make_api_client(session) as api_client:
        api = instances_api.InstancesApi(api_client)
        try:
            api.close_instance(world_id, instance_id, hard_close=hard_close)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return 'Instance closed.'

def get_user_tags(session: VRChatSession, user_id: str) -> TrustRank:
    with make_api_client(session) as api_client:
        api = users_api.UsersApi(api_client)
        try:
            user = api.get_user(user_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return trust_rank_from_tags(list(getattr(user, 'tags', None) or []))
