from __future__ import annotations
from typing import Any
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout
from ..api_action_worker import ApiActionWorker
from ..logging_setup import get_logger
from ..services.session_manager import SessionManager
from ..store import notifications_repo
from ..theme import NOTIFICATION_ROW_STYLE
from ..vrchat_auth import VRChatSession
from .feed_format import format_abs_time, format_relative_time
from .panel_window import PanelWindow

logger = get_logger('ui')

_TYPE_LABELS = {
    'friendRequest': 'Friend Request',
    'invite': 'Instance Invite',
    'requestInvite': 'Invite Request',
    'inviteResponse': 'Invite Response',
    'requestInviteResponse': 'Request Response',
}


class _NotifFetchWorker(QThread):
    loaded = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, session: VRChatSession):
        super().__init__()
        self._session = session

    def run(self) -> None:
        try:
            from ..vrchat.notifications import fetch_notifications
            self.loaded.emit(fetch_notifications(self._session, limit=100))
        except Exception as exc:
            self.failed.emit(str(exc))


class _NotificationRow(QFrame):
    accept_clicked = pyqtSignal(str)
    decline_clicked = pyqtSignal(str)

    def __init__(self, row: dict[str, Any]):
        super().__init__()
        self.setObjectName('notifRow')
        vrc_id = str(row.get('vrc_id') or row.get('id') or '')
        ntype = str(row.get('type') or '')
        sender = str(row.get('sender_username') or 'Someone')
        message = str(row.get('message') or '')
        ts = float(row.get('ts') or 0)
        seen = bool(row.get('seen'))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title = QLabel(f'{_TYPE_LABELS.get(ntype, ntype or "Notification")} · {sender}')
        title.setObjectName('notifTitle')
        title.setProperty('unread', 'false' if seen else 'true')
        title.style().unpolish(title)
        title.style().polish(title)
        sub_text = message or format_abs_time(ts)
        subtitle = QLabel(sub_text)
        subtitle.setObjectName('notifSubtitle')
        subtitle.setWordWrap(True)
        when = QLabel(format_relative_time(ts))
        when.setObjectName('notifWhen')
        text_col.addWidget(title)
        text_col.addWidget(subtitle)
        text_col.addWidget(when)
        layout.addLayout(text_col, 1)
        if ntype == 'friendRequest':
            accept = QPushButton('Accept')
            accept.setCursor(Qt.CursorShape.PointingHandCursor)
            accept.clicked.connect(lambda: self.accept_clicked.emit(vrc_id))
            layout.addWidget(accept, 0, Qt.AlignmentFlag.AlignVCenter)
        decline = QPushButton('Dismiss' if ntype != 'friendRequest' else 'Decline')
        decline.setCursor(Qt.CursorShape.PointingHandCursor)
        decline.clicked.connect(lambda: self.decline_clicked.emit(vrc_id))
        layout.addWidget(decline, 0, Qt.AlignmentFlag.AlignVCenter)


class NotificationCenterWindow(PanelWindow):
    def __init__(self, session: VRChatSession | None = None):
        super().__init__('Notifications', width=560, height=680, extra_style=NOTIFICATION_ROW_STYLE)
        self._session = session
        self._fetch_worker: _NotifFetchWorker | None = None
        self._action_workers: set[ApiActionWorker] = set()
        header = QHBoxLayout()
        title = QLabel('Notifications')
        title.setObjectName('panelSectionHeader')
        self._status = QLabel('')
        self._status.setObjectName('panelHint')
        mark_all = QPushButton('Mark all read')
        mark_all.setCursor(Qt.CursorShape.PointingHandCursor)
        mark_all.clicked.connect(self._mark_all_read)
        refresh = QPushButton('Refresh')
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.clicked.connect(self.sync_from_server)
        header.addWidget(title)
        header.addWidget(self._status)
        header.addStretch()
        header.addWidget(mark_all)
        header.addWidget(refresh)
        self.content_layout.addLayout(header)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.content_layout.addWidget(self.list, 1)
        self.reload()

    def set_session(self, session: VRChatSession | None) -> None:
        self._session = session

    def reload(self) -> None:
        try:
            rows = notifications_repo.list_notifications(limit=100)
        except Exception:
            logger.debug('Notification list load failed', exc_info=True)
            rows = []
        self.list.clear()
        for row in rows:
            item = QListWidgetItem()
            widget = _NotificationRow(row)
            widget.accept_clicked.connect(self._accept_friend)
            widget.decline_clicked.connect(self._decline)
            widget.setMinimumWidth(self.list.viewport().width() - 8)
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
        unseen = notifications_repo.unseen_count()
        self._status.setText(f'{unseen} unread' if unseen else 'All read')

    def _mark_all_read(self) -> None:
        notifications_repo.mark_all_seen()
        self.reload()

    def sync_from_server(self) -> None:
        if self._session is None:
            self._status.setText('Login required to sync')
            return
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            return
        self._status.setText('Syncing…')
        self._fetch_worker = _NotifFetchWorker(self._session)
        self._fetch_worker.loaded.connect(self._on_fetched)
        self._fetch_worker.failed.connect(self._on_fetch_failed)
        self._fetch_worker.finished.connect(lambda: setattr(self, '_fetch_worker', None))
        self._fetch_worker.start()

    def _on_fetched(self, items: object) -> None:
        if not isinstance(items, list):
            self.reload()
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            notifications_repo.upsert_notification(
                vrc_id=str(item.get('id') or ''),
                type=str(item.get('type') or ''),
                sender_user_id=str(item.get('sender_user_id') or ''),
                sender_username=str(item.get('sender_username') or ''),
                message=str(item.get('message') or ''),
                details=item.get('details') if isinstance(item.get('details'), dict) else None,
                ts=float(item.get('created_at') or 0),
                seen=bool(item.get('seen')),
            )
        self.reload()

    def _on_fetch_failed(self, message: str) -> None:
        if SessionManager.instance().try_handle_auth_failure(message):
            self._status.setText('Session expired — sign in again')
            return
        self._status.setText(message or 'Sync failed')
        self.reload()

    def _accept_friend(self, notification_id: str) -> None:
        if self._session is None:
            self._status.setText('Login required')
            return
        from ..vrchat.core import accept_friend_request
        worker = ApiActionWorker(lambda: accept_friend_request(self._session, notification_id), 'Friend request accepted.', context='accept friend request')
        self._run_action(worker, notification_id)

    def _decline(self, notification_id: str) -> None:
        if self._session is None:
            notifications_repo.mark_responded(notification_id)
            notifications_repo.delete_notification(notification_id)
            self.reload()
            return
        from ..vrchat.notifications import decline_notification
        worker = ApiActionWorker(lambda: decline_notification(self._session, notification_id), 'Notification dismissed.', context='decline notification')
        self._run_action(worker, notification_id)

    def _run_action(self, worker: ApiActionWorker, notification_id: str) -> None:
        self._action_workers.add(worker)

        def _cleanup() -> None:
            self._action_workers.discard(worker)

        def _ok(_message: str) -> None:
            notifications_repo.mark_responded(notification_id)
            notifications_repo.delete_notification(notification_id)
            self.reload()

        def _err(message: str) -> None:
            if SessionManager.instance().try_handle_auth_failure(message):
                self._status.setText('Session expired — sign in again')
            else:
                self._status.setText(message)
            _cleanup()

        worker.finished_ok.connect(_ok)
        worker.finished_error.connect(_err)
        worker.finished_cancelled.connect(_cleanup)
        worker.finished.connect(_cleanup)
        worker.start()
