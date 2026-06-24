from __future__ import annotations
import json
import time
from datetime import datetime
from typing import Any
from vrchatapi.api import friends_api, invite_api, notifications_api
from vrchatapi.exceptions import ApiException, UnauthorizedException
from ..logging_setup import get_logger
from ..vrchat_auth import VRChatSession
from .core import _api_error_message, make_api_client

logger = get_logger('vrchat.notifications')

ACTIONABLE_TYPES = frozenset({'friendRequest', 'invite', 'requestInvite', 'inviteResponse', 'requestInviteResponse'})


def _coerce_ts(value: Any) -> float:
    if isinstance(value, datetime):
        try:
            return value.timestamp()
        except (OverflowError, OSError, ValueError):
            return time.time()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        for fmt in ('%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ'):
            try:
                return datetime.strptime(value, fmt).timestamp()
            except ValueError:
                continue
    return time.time()


def _serialize_notification(notif: Any) -> dict[str, Any]:
    notif_type = str(getattr(getattr(notif, 'type', None), 'value', None) or getattr(notif, 'type', '') or '')
    details_raw = getattr(notif, 'details', None)
    details: dict[str, Any] = {}
    if details_raw:
        try:
            parsed = json.loads(details_raw) if isinstance(details_raw, str) else details_raw
            if isinstance(parsed, dict):
                details = parsed
        except (ValueError, TypeError):
            details = {}
    return {
        'id': str(getattr(notif, 'id', '') or ''),
        'type': notif_type,
        'sender_user_id': str(getattr(notif, 'sender_user_id', '') or ''),
        'sender_username': str(getattr(notif, 'sender_username', '') or ''),
        'message': str(getattr(notif, 'message', '') or ''),
        'details': details,
        'seen': bool(getattr(notif, 'seen', False)),
        'created_at': _coerce_ts(getattr(notif, 'created_at', None)),
    }


def fetch_notifications(session: VRChatSession, *, limit: int = 100) -> list[dict[str, Any]]:
    with make_api_client(session) as api_client:
        api = notifications_api.NotificationsApi(api_client)
        try:
            batch = api.get_notifications() or []
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    items = [_serialize_notification(notif) for notif in batch]
    return items[:limit]


def decline_notification(session: VRChatSession, notification_id: str) -> str:
    if not notification_id:
        raise ValueError('Notification ID is required.')
    with make_api_client(session) as api_client:
        api = notifications_api.NotificationsApi(api_client)
        try:
            api.delete_notification(notification_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return 'Notification dismissed.'


def mark_notification_seen(session: VRChatSession, notification_id: str) -> str:
    if not notification_id:
        raise ValueError('Notification ID is required.')
    with make_api_client(session) as api_client:
        api = notifications_api.NotificationsApi(api_client)
        try:
            api.mark_notification_as_read(notification_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return 'Marked as read.'


def clear_all_notifications(session: VRChatSession) -> str:
    with make_api_client(session) as api_client:
        api = notifications_api.NotificationsApi(api_client)
        try:
            api.clear_notifications()
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return 'Notifications cleared.'


def send_friend_request(session: VRChatSession, user_id: str) -> str:
    if not user_id:
        raise ValueError('User ID is required.')
    with make_api_client(session) as api_client:
        api = friends_api.FriendsApi(api_client)
        try:
            api.friend(user_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return 'Friend request sent.'


def cancel_friend_request(session: VRChatSession, user_id: str) -> str:
    if not user_id:
        raise ValueError('User ID is required.')
    with make_api_client(session) as api_client:
        api = friends_api.FriendsApi(api_client)
        try:
            api.delete_friend_request(user_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return 'Friend request cancelled.'


def request_invite(session: VRChatSession, user_id: str, *, message_slot: int | None = None) -> str:
    if not user_id:
        raise ValueError('User ID is required.')
    from vrchatapi.models.request_invite_request import RequestInviteRequest
    request = RequestInviteRequest(message_slot=message_slot) if message_slot is not None else None
    with make_api_client(session) as api_client:
        api = invite_api.InviteApi(api_client)
        try:
            if request is not None:
                api.request_invite(user_id, request_invite_request=request)
            else:
                api.request_invite(user_id)
        except (UnauthorizedException, ApiException) as exc:
            raise RuntimeError(_api_error_message(exc)) from exc
    return 'Invite requested.'
