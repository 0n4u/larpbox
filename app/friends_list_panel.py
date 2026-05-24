from __future__ import annotations

import webbrowser

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .api_action_worker import ApiActionWorker
from .image_loader import RemoteImageLabel
from .status_indicator import StatusIndicator
from .user_status import status_dot_style
from .logging_setup import get_logger
from .player_list_bar import PLAYER_LIST_HEIGHT
from .theme import dark_theme, themed_menu
from .ui_animations import stop_pulse
from .ui_layout import (
    FRIEND_CARD_HEIGHT,
    FRIEND_CARD_WIDTH,
    FRIEND_FOOTER_HEIGHT,
    FRIEND_GRID_COLUMNS,
    FRIEND_IMAGE_HEIGHT,
    FRIENDS_PANEL_WIDTH,
)
from .vrchat_api import (
    FriendEntry,
    force_clone_player_avatar,
    get_friends_list,
    join_player_instance,
    user_profile_url,
)
from .vrchat_auth import VRChatSession

logger = get_logger('friends_list')

_HINT = 'Log in to see friends'
_MAX_BADGE_ICONS = 1
_BADGE_ICON = 12
_POPULATE_BATCH = 10
_POPULATE_INTERVAL_MS = 32
_OFFLINE_HIDE_THRESHOLD = 100
_FORCE_CLONE_TIMEOUT_SEC = 25.0


class FriendsWorker(QThread):
    finished_ok = pyqtSignal(object)
    finished_error = pyqtSignal(str)

    def __init__(self, session: VRChatSession):
        super().__init__()
        self.session = session

    def run(self) -> None:
        try:
            friends = get_friends_list(self.session)
            self.finished_ok.emit(friends)
        except Exception as exc:
            logger.warning('Friends fetch failed', exc_info=True)
            self.finished_error.emit(str(exc) or 'Failed to load friends.')


class FriendCard(QFrame):
    action_requested = pyqtSignal(str, str, str)

    def __init__(self, friend: FriendEntry, parent: QWidget | None = None):
        super().__init__(parent)
        self.friend = friend
        self.setObjectName('friendCard')
        self.setFixedSize(FRIEND_CARD_WIDTH, FRIEND_CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        image_host = QFrame()
        image_host.setObjectName('friendImageHost')
        image_host.setFixedHeight(FRIEND_IMAGE_HEIGHT)
        image_layout = QVBoxLayout(image_host)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(0)

        self.thumb = RemoteImageLabel(FRIEND_CARD_WIDTH, image_host)
        self.thumb.setFixedSize(FRIEND_CARD_WIDTH, FRIEND_IMAGE_HEIGHT)
        self.thumb.setStyleSheet(
            'QLabel#avatarThumb { background-color: #232323; border: none; border-radius: 0px; color: #666; }'
        )
        image_layout.addWidget(self.thumb)
        layout.addWidget(image_host)

        dot = QLabel(image_host)
        dot.setFixedSize(9, 9)
        dot.setStyleSheet(status_dot_style(friend.status.color, size=9))
        dot.setToolTip(friend.status.label)
        dot.move(FRIEND_CARD_WIDTH - 13, 4)
        dot.raise_()

        footer = QFrame()
        footer.setObjectName('friendCardFooter')
        footer.setFixedHeight(FRIEND_FOOTER_HEIGHT)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(4, 2, 4, 3)
        footer_layout.setSpacing(0)

        self.name_label = QLabel(friend.display_name)
        self.name_label.setObjectName('friendName')
        footer_layout.addWidget(self.name_label)

        self.status_indicator = StatusIndicator(footer, dot_size=7, compact=True)
        self.status_indicator.apply(friend.status)
        footer_layout.addWidget(self.status_indicator)

        self.world_label = QLabel('')
        self.world_label.setObjectName('friendWorld')
        if friend.status.key == 'join_me':
            self.world_label.setText(self._world_line_text(friend))
            footer_layout.addWidget(self.world_label)
        else:
            self.world_label.hide()

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(3)

        trust_badge = QLabel(friend.trust.short)
        trust_badge.setObjectName('friendTrustBadge')
        trust_badge.setToolTip(friend.trust.label)
        trust_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        trust_badge.setFixedHeight(14)
        trust_badge.setStyleSheet(
            f'color: {friend.trust.color}; font-size: 6pt; font-weight: 700; '
            f'border: 1px solid {friend.trust.color}; border-radius: 3px; '
            f'padding: 0 4px; background-color: rgba(255, 255, 255, 0.04);'
        )
        meta.addWidget(trust_badge)

        self._badge_images: list[tuple[RemoteImageLabel, str]] = []
        for badge in [item for item in friend.badges if item.image_url][:_MAX_BADGE_ICONS]:
            badge_label = RemoteImageLabel(_BADGE_ICON, footer)
            badge_label.setToolTip(badge.name)
            self._badge_images.append((badge_label, badge.image_url))
            meta.addWidget(badge_label)

        meta.addStretch(1)
        footer_layout.addLayout(meta)
        layout.addWidget(footer)

    def load_images(self) -> None:
        self.thumb.load(self.friend.thumbnail_url)
        for badge_label, image_url in self._badge_images:
            badge_label.load(image_url)

    @staticmethod
    def _world_line_text(friend: FriendEntry) -> str:
        if friend.world_name and friend.player_count is not None:
            return f'{friend.world_name} · {friend.player_count} players'
        if friend.world_name:
            return friend.world_name
        if friend.status.location:
            return 'In a joinable world'
        return ''

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        metrics = QFontMetrics(self.name_label.font())
        available = max(24, self.name_label.width())
        elided = metrics.elidedText(
            self.friend.display_name,
            Qt.TextElideMode.ElideRight,
            available,
        )
        self.name_label.setText(elided)
        self.name_label.setToolTip(
            self.friend.display_name if elided != self.friend.display_name else ''
        )
        if self.friend.status.key == 'join_me' and self.world_label.isVisible():
            world_text = self._world_line_text(self.friend)
            world_metrics = QFontMetrics(self.world_label.font())
            world_available = max(24, self.world_label.width())
            elided_world = world_metrics.elidedText(
                world_text,
                Qt.TextElideMode.ElideRight,
                world_available,
            )
            self.world_label.setText(elided_world)
            self.world_label.setToolTip(world_text if elided_world != world_text else '')

    def _show_context_menu(self, pos) -> None:
        menu = themed_menu(self)
        clone = menu.addAction('Force Clone')
        clone.setToolTip('Wear their avatar even if cloning is disabled (uses VRChat log avatar ID)')
        clone.triggered.connect(
            lambda: self.action_requested.emit(
                'force_clone',
                self.friend.user_id,
                self.friend.display_name,
            )
        )
        menu.addSeparator()
        if self.friend.can_join and self.friend.join_location:
            join = menu.addAction('Join Player')
            join.setToolTip('Launch VRChat and join their instance')
            join.triggered.connect(
                lambda: self.action_requested.emit(
                    'join',
                    self.friend.user_id,
                    self.friend.join_location or '',
                )
            )
            menu.addSeparator()
        profile = menu.addAction('Open VRChat Profile')
        profile.triggered.connect(
            lambda: self.action_requested.emit('open_profile', self.friend.user_id, '')
        )
        copy_id = menu.addAction('Copy User ID')
        copy_id.triggered.connect(
            lambda: self.action_requested.emit('copy_id', self.friend.user_id, '')
        )
        menu.exec(self.mapToGlobal(pos))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            webbrowser.open(user_profile_url(self.friend.user_id))
        super().mouseReleaseEvent(event)


class FriendsListPanel(QWidget):
    def __init__(self, session: VRChatSession | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: FriendsWorker | None = None
        self._action_worker: ApiActionWorker | None = None
        self._populate_generation = 0
        self._populate_gen = 0
        self._pending_friends: list[FriendEntry] = []
        self._populate_index = 0
        self.setFixedSize(FRIENDS_PANEL_WIDTH, PLAYER_LIST_HEIGHT)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(60000)
        self._poll_timer.timeout.connect(self.refresh)
        self._build_ui()
        self._show_hint(_HINT)
        if self.session is not None:
            RemoteImageLabel.set_session(self.session)
            self._poll_timer.start()
            QTimer.singleShot(3500, self.refresh)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.frame = QWidget()
        self.frame.setObjectName('previewContainer')
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(10, 8, 10, 8)
        frame_layout.setSpacing(5)

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        title = QLabel('Friends')
        title.setObjectName('previewHeader')
        header_row.addWidget(title)
        self.count_label = QLabel('')
        self.count_label.setObjectName('friendCountBadge')
        header_row.addWidget(self.count_label)
        header_row.addStretch(1)
        frame_layout.addLayout(header_row)

        self.status_label = QLabel('')
        self.status_label.setObjectName('friendStatusLabel')
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        frame_layout.addWidget(self.status_label)

        self.list_box = QFrame()
        self.list_box.setObjectName('friendListBox')
        list_box_layout = QVBoxLayout(self.list_box)
        list_box_layout.setContentsMargins(0, 0, 0, 0)
        list_box_layout.setSpacing(0)

        self.hint_label = QLabel(_HINT)
        self.hint_label.setObjectName('friendHint')
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        list_box_layout.addWidget(self.hint_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.hide()

        self.grid_host = QWidget()
        self.grid_layout = QGridLayout(self.grid_host)
        self.grid_layout.setContentsMargins(2, 2, 2, 2)
        self.grid_layout.setHorizontalSpacing(6)
        self.grid_layout.setVerticalSpacing(6)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.grid_host)
        list_box_layout.addWidget(self.scroll, 1)

        frame_layout.addWidget(self.list_box, 1)
        root.addWidget(self.frame)

        self.setStyleSheet(dark_theme("""
            QLabel#previewHeader {
                font-weight: bold;
                font-size: 9pt;
            }
            QLabel#friendCountBadge {
                color: #7db87d;
                font-size: 8pt;
                font-weight: 600;
                border: 1px solid #3d6640;
                border-radius: 8px;
                padding: 1px 7px;
                background-color: rgba(125, 184, 125, 0.1);
            }
            QLabel#friendStatusLabel {
                color: #7a8a7a;
                font-size: 8pt;
            }
            QFrame#friendListBox {
                background-color: #232323;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
            }
            QLabel#friendHint {
                color: #6a7a6a;
                font-size: 8pt;
                padding: 12px 8px;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QFrame#friendCard {
                background-color: #1c1c1c;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
            }
            QFrame#friendCard:hover {
                border-color: #4ea3ff;
            }
            QFrame#friendImageHost {
                background-color: #232323;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QFrame#friendCardFooter {
                background-color: #1a1a1a;
                border-bottom-left-radius: 6px;
                border-bottom-right-radius: 6px;
            }
            QLabel#friendName {
                color: #f0f0f0;
                font-size: 7pt;
                font-weight: 600;
            }
            QLabel#friendWorld {
                color: #7db87d;
                font-size: 6pt;
            }
            QLabel#statusIndicatorLabel {
                background: transparent;
                border: none;
                font-size: 6pt;
            }
        """))

    def _stop_populate(self) -> None:
        self._populate_generation += 1
        self._pending_friends = []
        self._populate_index = 0

    def _show_hint(self, text: str) -> None:
        self._stop_populate()
        self._clear_grid()
        self.scroll.hide()
        self.hint_label.setText(text)
        self.hint_label.show()
        self.count_label.setText('')
        self.status_label.setText('')

    def _clear_grid(self) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _set_status_message(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setToolTip(text if error else '')
        if error:
            self.status_label.setStyleSheet('color: #e07070; font-size: 8pt;')
        else:
            self.status_label.setStyleSheet('')

    def _format_friends_summary(self, friends: list[FriendEntry], *, hidden_offline: int = 0) -> str:
        if not friends:
            return ''
        counts: dict[str, int] = {}
        for friend in friends:
            counts[friend.status.key] = counts.get(friend.status.key, 0) + 1
        parts: list[str] = []
        for key, label in (
            ('join_me', 'join me'),
            ('active', 'online'),
            ('ask_me', 'ask me'),
            ('busy', 'do not disturb'),
            ('offline', 'offline'),
            ('invisible', 'invisible'),
            ('private', 'private'),
        ):
            count = counts.get(key, 0)
            if not count:
                continue
            if key == 'offline' and hidden_offline > 0:
                parts.append(f'{count} offline hidden')
            else:
                parts.append(f'{count} {label}')
        return ' · '.join(parts[:4])

    def _on_card_action(self, action: str, user_id: str, detail: str) -> None:
        if action == 'copy_id':
            QApplication.clipboard().setText(user_id)
            self._set_status_message('User ID copied.')
            QTimer.singleShot(2500, self.refresh)
            return
        if action == 'open_profile':
            webbrowser.open(user_profile_url(user_id))
            return
        if self.session is None:
            return
        if self._action_worker and self._action_worker.isRunning():
            return
        if action == 'force_clone':
            self._set_status_message('Force cloning avatar…')
            self._action_worker = ApiActionWorker(
                lambda: force_clone_player_avatar(
                    self.session,
                    user_id,
                    display_name=detail or None,
                ),
                'Avatar selected — switch applies in VRChat.',
                timeout_sec=_FORCE_CLONE_TIMEOUT_SEC,
            )
            self._action_worker.finished_ok.connect(self._on_action_ok)
            self._action_worker.finished_error.connect(self._on_action_error)
            self._action_worker.start()
            return
        if action == 'join':
            if not detail:
                self._set_status_message('No instance to join.')
                return
            try:
                join_player_instance(detail)
                self._set_status_message('Opening VRChat to join player…')
            except Exception as exc:
                self._set_status_message(str(exc), error=True)

    def _on_action_ok(self, _message: str) -> None:
        self.refresh()

    def _on_action_error(self, message: str) -> None:
        self._set_status_message(f'Error: {message}', error=True)

    def _friends_for_display(self, friends: list[FriendEntry]) -> tuple[list[FriendEntry], int]:
        if len(friends) <= _OFFLINE_HIDE_THRESHOLD:
            return friends, 0
        visible = [friend for friend in friends if friend.status.key != 'offline']
        hidden_offline = len(friends) - len(visible)
        if hidden_offline <= 0:
            return friends, 0
        return visible, hidden_offline

    def refresh(self) -> None:
        if self.session is None:
            self._show_hint('Not logged in')
            return
        if self._worker and self._worker.isRunning():
            return
        self._stop_populate()
        self._set_status_message('Loading…')
        self._worker = FriendsWorker(self.session)
        self._worker.finished_ok.connect(self._on_loaded)
        self._worker.finished_error.connect(self._on_error)
        self._worker.start()

    def _on_error(self, message: str) -> None:
        logger.debug('Friends list error: %s', message)
        stop_pulse(self.status_label)
        self._show_hint('Could not load friends')

    def _on_loaded(self, friends_obj: object) -> None:
        stop_pulse(self.status_label)
        if not isinstance(friends_obj, list):
            self._show_hint('Could not load friends')
            return

        friends: list[FriendEntry] = [item for item in friends_obj if isinstance(item, FriendEntry)]
        display_friends, hidden_offline = self._friends_for_display(friends)
        self._stop_populate()
        self._clear_grid()
        self.count_label.setText(str(len(friends)))
        self._set_status_message(
            self._format_friends_summary(friends, hidden_offline=hidden_offline)
        )

        if not display_friends:
            self.hint_label.setText('No friends found')
            self.scroll.hide()
            self.hint_label.show()
            return

        self.hint_label.hide()
        self.scroll.show()
        self._pending_friends = display_friends
        self._populate_index = 0
        self._populate_gen = self._populate_generation
        self._populate_next_batch()

    def _populate_next_batch(self) -> None:
        if self._populate_generation != self._populate_gen:
            return
        friends = self._pending_friends
        start = self._populate_index
        end = min(start + _POPULATE_BATCH, len(friends))
        for index in range(start, end):
            friend = friends[index]
            card = FriendCard(friend, self.grid_host)
            card.action_requested.connect(self._on_card_action)
            row = index // FRIEND_GRID_COLUMNS
            col = index % FRIEND_GRID_COLUMNS
            self.grid_layout.addWidget(card, row, col)
            if friend.status.key != 'offline':
                card.load_images()
        self._populate_index = end
        if end < len(friends):
            QTimer.singleShot(_POPULATE_INTERVAL_MS, self._populate_next_batch)
        else:
            self._pending_friends = []

    def cleanup(self) -> None:
        self._stop_populate()
        self._poll_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        if self._action_worker and self._action_worker.isRunning():
            self._action_worker.wait(2000)
