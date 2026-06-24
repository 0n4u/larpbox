from __future__ import annotations
import json
import random
import threading
from typing import Any, Callable
from urllib.parse import quote
from PyQt6.QtCore import QThread
from ..logging_setup import get_logger
from ..version import __version__
from . import event_bus as ev
from .event_bus import VrcEventBus

logger = get_logger('pipeline')

USER_AGENT = f'larpbox/{__version__}'
PIPELINE_URL = 'wss://pipeline.vrchat.cloud/'
_MAX_BACKOFF_SEC = 60.0

_FRIEND_TYPE_MAP = {
    'friend-online': ev.FRIEND_ONLINE,
    'friend-active': ev.FRIEND_ONLINE,
    'friend-offline': ev.FRIEND_OFFLINE,
    'friend-location': ev.FRIEND_LOCATION,
    'friend-update': ev.FRIEND_UPDATE,
    'friend-add': ev.FRIEND_ADDED,
    'friend-delete': ev.FRIEND_REMOVED,
}


def _coerce_content(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _world_name(content: dict[str, Any]) -> str:
    world = content.get('world')
    if isinstance(world, dict):
        return str(world.get('name') or '')
    return ''


def _friend_events(msg_type: str, content: dict[str, Any]) -> list[dict[str, Any]]:
    kind = _FRIEND_TYPE_MAP[msg_type]
    user = content.get('user') if isinstance(content.get('user'), dict) else {}
    user_id = str(content.get('userId') or user.get('id') or '')
    if not user_id:
        return []
    status = str(user.get('status') or '')
    location = str(content.get('location') or user.get('location') or '')
    payload: dict[str, Any] = {
        'kind': kind,
        'user_id': user_id,
        'display_name': str(user.get('displayName') or content.get('displayName') or ''),
        'status': status,
        'location': location,
        'world_name': _world_name(content),
    }
    thumb = str(user.get('currentAvatarThumbnailImageUrl') or user.get('currentAvatarImageUrl') or '')
    if thumb:
        payload['current_avatar_thumbnail_url'] = thumb
    if user:
        payload['user'] = user
    if kind == ev.FRIEND_LOCATION:
        payload['current'] = location
    elif kind in (ev.FRIEND_ONLINE, ev.FRIEND_UPDATE):
        payload['current'] = status
    else:
        payload['current'] = ''
    return [payload]


def _notification_events(content: dict[str, Any]) -> list[dict[str, Any]]:
    vrc_id = str(content.get('id') or '')
    if not vrc_id:
        return []
    details = content.get('details')
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except (ValueError, TypeError):
            details = {}
    if not isinstance(details, dict):
        details = {}
    return [{
        'kind': ev.NOTIFICATION,
        'id': vrc_id,
        'type': str(content.get('type') or ''),
        'senderUserId': str(content.get('senderUserId') or ''),
        'senderUsername': str(content.get('senderUsername') or ''),
        'message': str(content.get('message') or ''),
        'details': details,
    }]


def map_pipeline_message(text: str) -> list[dict[str, Any]]:
    """Translate a raw VRChat pipeline frame into bus event payloads (pure)."""
    try:
        outer = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(outer, dict):
        return []
    msg_type = str(outer.get('type') or '')
    content = _coerce_content(outer.get('content'))
    if msg_type in _FRIEND_TYPE_MAP:
        return _friend_events(msg_type, content)
    if msg_type == 'user-location':
        user_id = str(content.get('userId') or '')
        return [{
            'kind': ev.USER_LOCATION,
            'user_id': user_id,
            'location': str(content.get('location') or ''),
            'world_name': _world_name(content),
        }]
    if msg_type in ('notification', 'notification-v2'):
        return _notification_events(content)
    return []


class VrcPipeline(QThread):
    """Websocket client for the VRChat event pipeline.

    Connects to the official pipeline endpoint using the session auth token,
    reconnects with exponential backoff + jitter, and maps frames onto the
    VrcEventBus. Requires the optional ``websocket-client`` dependency; if it is
    not installed the thread exits cleanly and the app falls back to logs+REST.
    The auth token is never logged.
    """

    def __init__(self, token_provider: Callable[[], str | None], bus: VrcEventBus | None = None, parent=None):
        super().__init__(parent)
        self._token_provider = token_provider
        self._bus = bus or VrcEventBus.instance()
        self._stop = threading.Event()
        self._ws: Any = None
        self._lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _current_token(self) -> str:
        try:
            return (self._token_provider() or '').strip()
        except Exception:
            return ''

    def run(self) -> None:
        try:
            import websocket
        except ImportError:
            logger.warning('websocket-client not installed — VRChat pipeline disabled (logs + REST only)')
            return
        app_factory = getattr(websocket, 'WebSocketApp', None)
        if app_factory is None:
            logger.warning("Installed 'websocket' package lacks WebSocketApp — install 'websocket-client'")
            return
        logger.info('VRChat pipeline thread started')
        backoff = 1.0
        while not self._stop.is_set():
            token = self._current_token()
            if not token:
                self._stop.wait(5.0)
                continue
            connected = {'ok': False}
            self._connect_once(app_factory, token, connected)
            if self._stop.is_set():
                break
            if connected['ok']:
                backoff = 1.0
            delay = min(backoff, _MAX_BACKOFF_SEC) + random.uniform(0.0, 1.0)
            logger.debug('Reconnecting VRChat pipeline in %.1fs', delay)
            self._stop.wait(delay)
            backoff = min(backoff * 2.0, _MAX_BACKOFF_SEC)
        logger.info('VRChat pipeline thread stopped')

    def _connect_once(self, app_factory: Any, token: str, connected: dict[str, bool]) -> None:
        url = f'{PIPELINE_URL}?authToken={quote(token, safe="")}'

        def on_open(_ws: Any) -> None:
            connected['ok'] = True
            logger.info('VRChat pipeline connected')

        def on_message(_ws: Any, message: Any) -> None:
            try:
                for payload in map_pipeline_message(message if isinstance(message, str) else str(message)):
                    kind = payload.pop('kind')
                    self._bus.emit_event(kind, payload)
            except Exception:
                logger.debug('Pipeline message handling failed', exc_info=True)

        def on_error(_ws: Any, error: Any) -> None:
            logger.debug('VRChat pipeline error: %s', type(error).__name__)

        def on_close(_ws: Any, *_args: Any) -> None:
            logger.debug('VRChat pipeline connection closed')

        try:
            ws = app_factory(url, header=[f'User-Agent: {USER_AGENT}'], on_open=on_open,
                             on_message=on_message, on_error=on_error, on_close=on_close)
            with self._lock:
                self._ws = ws
            ws.run_forever(ping_interval=60, ping_timeout=10)
        except Exception:
            logger.debug('VRChat pipeline run_forever failed', exc_info=True)
        finally:
            with self._lock:
                self._ws = None
