from __future__ import annotations
from typing import Any
from PyQt6.QtCore import QObject
from ..logging_setup import get_logger
from ..store import analytics_repo, feed_repo, gamelog_repo, notifications_repo
from .event_bus import VrcEventBus

logger = get_logger('store')

_MEDIA_DEDUP_WINDOW_SEC = 120.0


class VrcStoreRecorder(QObject):
    """Subscribes to the VrcEventBus and persists events into the SQLite store.

    This is the single bridge between live events (log watcher + pipeline) and
    durable history. Every handler is defensive: a write failure must never
    propagate back into the producer thread.
    """

    def __init__(self, bus: VrcEventBus | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._bus = bus or VrcEventBus.instance()
        self._connect()

    def _connect(self) -> None:
        bus = self._bus
        bus.world_join.connect(self._on_world_join)
        bus.player_join.connect(self._on_player_join)
        bus.player_leave.connect(self._on_player_leave)
        bus.video_url.connect(self._on_video_url)
        bus.notification.connect(self._on_notification)
        bus.friend_online.connect(self._on_friend_online)
        bus.friend_offline.connect(self._on_friend_offline)
        bus.friend_location.connect(self._on_friend_location)
        bus.friend_status.connect(self._on_friend_status)
        bus.friend_update.connect(self._on_friend_update)
        bus.friend_added.connect(self._on_friend_added)
        bus.friend_removed.connect(self._on_friend_removed)

    def _on_world_join(self, payload: dict[str, Any]) -> None:
        try:
            location = str(payload.get('location') or '')
            if not location:
                return
            world_name = str(payload.get('world_name') or '')
            last = gamelog_repo.last_location()
            if last and last.get('location') == location:
                if world_name and not last.get('world_name'):
                    gamelog_repo.update_last_location_name(location, world_name)
                return
            ts = float(payload.get('ts') or 0) or None
            gamelog_repo.record_location(
                world_id=str(payload.get('world_id') or ''),
                instance_id=str(payload.get('instance_id') or ''),
                location=location,
                world_name=world_name,
                region=str(payload.get('region') or ''),
                instance_type=str(payload.get('instance_type') or ''),
                group_id=str(payload.get('group_id') or ''),
                ts=ts,
            )
            analytics_repo.open_visit(world_id=str(payload.get('world_id') or ''), location=location, joined_ts=ts)
            feed_repo.add_feed(type='gps_self', display_name=world_name or location, payload=payload, ts=ts)
        except Exception:
            logger.debug('Failed to persist world join', exc_info=True)

    def _on_player_join(self, payload: dict[str, Any]) -> None:
        self._record_join_leave(gamelog_repo.JOIN, payload)

    def _on_player_leave(self, payload: dict[str, Any]) -> None:
        self._record_join_leave(gamelog_repo.LEAVE, payload)

    def _record_join_leave(self, kind: str, payload: dict[str, Any]) -> None:
        try:
            user_id = str(payload.get('user_id') or '')
            display_name = str(payload.get('display_name') or '')
            if not user_id and not display_name:
                return
            location = ''
            last = gamelog_repo.last_location()
            if last:
                location = str(last.get('location') or '')
            gamelog_repo.record_join_leave(
                kind=kind, user_id=user_id, display_name=display_name,
                location=str(payload.get('location') or location), ts=float(payload.get('ts') or 0) or None,
            )
        except Exception:
            logger.debug('Failed to persist join/leave', exc_info=True)

    def _on_video_url(self, payload: dict[str, Any]) -> None:
        try:
            url = str(payload.get('url') or '')
            if not url:
                return
            ts = float(payload.get('ts') or 0) or None
            if gamelog_repo.media_exists(url, since_ts=(ts or 0) - _MEDIA_DEDUP_WINDOW_SEC):
                return
            location = ''
            last = gamelog_repo.last_location()
            if last:
                location = str(last.get('location') or '')
            gamelog_repo.record_media(
                url=url, title=str(payload.get('title') or ''), location=location,
                source=str(payload.get('source') or ''), ts=ts,
            )
        except Exception:
            logger.debug('Failed to persist media url', exc_info=True)

    def _on_notification(self, payload: dict[str, Any]) -> None:
        try:
            vrc_id = str(payload.get('id') or payload.get('vrc_id') or '')
            if not vrc_id:
                return
            notifications_repo.upsert_notification(
                vrc_id=vrc_id,
                type=str(payload.get('type') or ''),
                sender_user_id=str(payload.get('senderUserId') or payload.get('sender_user_id') or ''),
                sender_username=str(payload.get('senderUsername') or payload.get('sender_username') or ''),
                message=str(payload.get('message') or ''),
                details=payload.get('details') if isinstance(payload.get('details'), dict) else None,
                ts=float(payload.get('ts') or 0) or None,
                seen=bool(payload.get('seen', False)),
            )
        except Exception:
            logger.debug('Failed to persist notification', exc_info=True)

    def _friend_log(self, log_type: str, payload: dict[str, Any]) -> None:
        try:
            user_id = str(payload.get('user_id') or payload.get('userId') or '')
            if not user_id:
                return
            feed_repo.add_friend_log(
                type=log_type,
                user_id=user_id,
                display_name=str(payload.get('display_name') or payload.get('displayName') or ''),
                prev=str(payload.get('prev') or ''),
                current=str(payload.get('current') or ''),
                ts=float(payload.get('ts') or 0) or None,
            )
        except Exception:
            logger.debug('Failed to persist friend log', exc_info=True)

    def _on_friend_online(self, payload: dict[str, Any]) -> None:
        self._friend_log(feed_repo.ONLINE, payload)

    def _on_friend_offline(self, payload: dict[str, Any]) -> None:
        self._friend_log(feed_repo.OFFLINE, payload)

    def _on_friend_location(self, payload: dict[str, Any]) -> None:
        self._friend_log(feed_repo.LOCATION, payload)

    def _on_friend_status(self, payload: dict[str, Any]) -> None:
        self._friend_log(feed_repo.STATUS, payload)

    def _on_friend_update(self, payload: dict[str, Any]) -> None:
        self._friend_log(str(payload.get('log_type') or feed_repo.STATUS), payload)

    def _on_friend_added(self, payload: dict[str, Any]) -> None:
        self._friend_log(feed_repo.ADDED, payload)

    def _on_friend_removed(self, payload: dict[str, Any]) -> None:
        self._friend_log(feed_repo.REMOVED, payload)
