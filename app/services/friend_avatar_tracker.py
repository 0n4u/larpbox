from __future__ import annotations
from typing import Any
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from ..logging_setup import get_logger
from .event_bus import VrcEventBus

logger = get_logger('friend_avatar')


class _ResolveWorker(QThread):
    finished = pyqtSignal(str, object)

    def __init__(self, session: Any, user_id: str, user: dict[str, Any]) -> None:
        super().__init__()
        self._session = session
        self._user_id = user_id
        self._user = user

    def run(self) -> None:
        resolved: str | None = None
        try:
            from ..vrchat.core import resolve_avatar_from_pipeline_user
            resolved = resolve_avatar_from_pipeline_user(self._session, self._user_id, self._user)
        except Exception:
            logger.debug('Pipeline friend avatar resolve failed for %s', self._user_id, exc_info=True)
        self.finished.emit(self._user_id, resolved)


class FriendAvatarTracker(QObject):
    """Resolve friend avatars when the VRChat websocket pipeline reports thumbnail changes."""

    _instance: 'FriendAvatarTracker | None' = None

    @classmethod
    def instance(cls) -> 'FriendAvatarTracker':
        if cls._instance is None:
            cls._instance = FriendAvatarTracker()
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self._started = False
        self._workers: set[_ResolveWorker] = set()

    def start(self) -> None:
        if self._started:
            return
        bus = VrcEventBus.instance()
        bus.friend_update.connect(self._on_friend_event)
        bus.friend_online.connect(self._on_friend_event)
        self._started = True
        logger.debug('Friend avatar tracker listening for pipeline events')

    def _on_friend_event(self, payload: dict[str, Any]) -> None:
        user = payload.get('user')
        if not isinstance(user, dict):
            return
        user_id = str(user.get('id') or payload.get('user_id') or '').strip()
        if not user_id:
            return
        thumb = str(
            user.get('currentAvatarThumbnailImageUrl')
            or user.get('currentAvatarImageUrl')
            or payload.get('current_avatar_thumbnail_url')
            or ''
        ).strip()
        if not thumb:
            return
        from .session_manager import SessionManager
        session = SessionManager.instance().session
        if session is None:
            return
        worker = _ResolveWorker(session, user_id, user)
        self._workers.add(worker)
        worker.finished.connect(self._on_resolve_done)
        worker.finished.connect(lambda *_: self._workers.discard(worker))
        worker.start()

    def _on_resolve_done(self, user_id: str, resolved: object) -> None:
        if isinstance(resolved, str) and resolved.startswith('avtr_'):
            logger.debug('Pipeline resolved avatar for %s -> %s', user_id, resolved)
