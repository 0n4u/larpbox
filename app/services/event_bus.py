from __future__ import annotations
import time
from typing import Any
from PyQt6.QtCore import QObject, pyqtSignal

                                                                          
                                                                             
WORLD_JOIN = 'world_join'
PLAYER_JOIN = 'player_join'
PLAYER_LEAVE = 'player_leave'
AVATAR_CHANGE = 'avatar_change'
VIDEO_URL = 'video_url'
USER_LOCATION = 'user_location'
FRIEND_ONLINE = 'friend_online'
FRIEND_OFFLINE = 'friend_offline'
FRIEND_LOCATION = 'friend_location'
FRIEND_STATUS = 'friend_status'
FRIEND_UPDATE = 'friend_update'
FRIEND_ADDED = 'friend_added'
FRIEND_REMOVED = 'friend_removed'
NOTIFICATION = 'notification'


class VrcEventBus(QObject):
    """Central, thread-safe dispatcher for VRChat events.

    Mirrors the NotificationBus singleton pattern. Producers (log watcher,
    websocket pipeline, REST reconciliation) call emit_event(); consumers
    (store recorder, UI panels, overlay) connect to the typed dict signals or
    the catch-all `event` signal. Cross-thread emissions are delivered as
    queued connections on the receiver's thread.
    """

    world_join = pyqtSignal(dict)
    player_join = pyqtSignal(dict)
    player_leave = pyqtSignal(dict)
    avatar_change = pyqtSignal(dict)
    video_url = pyqtSignal(dict)
    user_location = pyqtSignal(dict)
    friend_online = pyqtSignal(dict)
    friend_offline = pyqtSignal(dict)
    friend_location = pyqtSignal(dict)
    friend_status = pyqtSignal(dict)
    friend_update = pyqtSignal(dict)
    friend_added = pyqtSignal(dict)
    friend_removed = pyqtSignal(dict)
    notification = pyqtSignal(dict)
    event = pyqtSignal(str, dict)

    _instance: 'VrcEventBus | None' = None

    @classmethod
    def instance(cls) -> 'VrcEventBus':
        if cls._instance is None:
            cls._instance = VrcEventBus()
        return cls._instance

    def emit_event(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        data: dict[str, Any] = dict(payload or {})
        data.setdefault('ts', time.time())
        data.setdefault('kind', kind)
        signal = getattr(self, kind, None) if kind != 'event' else None
        if signal is not None:
            try:
                signal.emit(data)
            except (TypeError, RuntimeError):
                pass
        self.event.emit(kind, data)
