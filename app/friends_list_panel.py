from __future__ import annotations
import time
import webbrowser
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication, QInputDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from .api_action_worker import ApiActionWorker
from .image_loader import RemoteImageLabel
from .status_indicator import StatusIndicator
from .user_status import status_dot_style
from .logging_setup import get_logger
from .player_list_bar import PLAYER_LIST_HEIGHT
from .services.action_runner import ActionRunner
from .services.auth_errors import is_rate_limit_error
from .services.session_manager import SessionManager
from .theme import TOOLBAR_BUTTON, dark_theme, themed_menu
from .ui_animations import animate_list_items, flash_widget, pulse_widget, reveal_content, stop_pulse
from .ui_layout import FRIEND_CARD_HEIGHT, FRIEND_CARD_WIDTH, FRIEND_FOOTER_HEIGHT, FRIEND_GRID_COLUMNS, FRIEND_IMAGE_HEIGHT, FRIENDS_PANEL_WIDTH
from .vrchat_api import FriendEntry, force_clone_player_avatar, get_friends_list, invite_user_to_instance, join_player_instance, moderate_player, unmoderate_player, unfriend_user, user_profile_url
from .config import get_bool, load_config, save_config
from .api_startup import startup_delay_ms
from .safe_runtime import widget_is_valid
from .services.notifications import NotificationBus
from .vrchat_auth import VRChatSession
logger = get_logger('friends_list')
_HINT = 'Log in to see friends'
_MAX_BADGE_ICONS = 1
_BADGE_ICON = 12
_POPULATE_BATCH = 10
_POPULATE_INTERVAL_MS = 32
_OFFLINE_HIDE_THRESHOLD = 100
_FORCE_CLONE_TIMEOUT_SEC = 60.0
_POLL_INTERVAL_MS = 15000
_FULL_POLL_INTERVAL_MS = 45000

class FriendsWorker(QThread):
    finished_ok = pyqtSignal(object)
    finished_error = pyqtSignal(object)

    def __init__(self, session: VRChatSession, *, include_offline: bool=True, enrich_worlds: bool=True):
        super().__init__()
        self.session = session
        self.include_offline = include_offline
        self.enrich_worlds = enrich_worlds

    def run(self) -> None:
        try:
            friends = get_friends_list(self.session, include_offline=self.include_offline, enrich_worlds=self.enrich_worlds)
            self.finished_ok.emit(friends)
        except Exception as exc:
            logger.warning('Friends fetch failed', exc_info=True)
            self.finished_error.emit(exc)

class FriendCard(QFrame):
    action_requested = pyqtSignal(str, str, str)

    def __init__(self, friend: FriendEntry, parent: QWidget | None=None, *, session: VRChatSession | None=None):
        super().__init__(parent)
        self.friend = friend
        self.session = session
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
        self.thumb.setStyleSheet('QLabel#avatarThumb { background-color: #232323; border: none; border-radius: 0px; color: #666; }')
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
        trust_badge.setStyleSheet(f'color: {friend.trust.color}; font-size: 6pt; font-weight: 700; border: 1px solid {friend.trust.color}; border-radius: 3px; padding: 0 4px; background-color: rgba(255, 255, 255, 0.04);')
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
        elided = metrics.elidedText(self.friend.display_name, Qt.TextElideMode.ElideRight, available)
        self.name_label.setText(elided)
        self.name_label.setToolTip(self.friend.display_name if elided != self.friend.display_name else '')
        if self.friend.status.key == 'join_me' and self.world_label.isVisible():
            world_text = self._world_line_text(self.friend)
            world_metrics = QFontMetrics(self.world_label.font())
            world_available = max(24, self.world_label.width())
            elided_world = world_metrics.elidedText(world_text, Qt.TextElideMode.ElideRight, world_available)
            self.world_label.setText(elided_world)
            self.world_label.setToolTip(world_text if elided_world != world_text else '')

    def _show_context_menu(self, pos) -> None:
        menu = None
        try:
            menu = themed_menu(self)
            clone = menu.addAction('Force Clone')
            clone.setEnabled(True)
            clone.setToolTip('Wear their avatar (may require log data)')
            clone.triggered.connect(lambda: self.action_requested.emit('force_clone', self.friend.user_id, self.friend.display_name))
            menu.addSeparator()
            if self.friend.can_join and self.friend.join_location:
                join = menu.addAction('Join Player')
                join.setToolTip('Launch VRChat and join their instance')
                join.triggered.connect(lambda: self.action_requested.emit('join', self.friend.user_id, self.friend.join_location or ''))
                menu.addSeparator()
            profile = menu.addAction('Open VRChat Profile')
            profile.triggered.connect(lambda: self.action_requested.emit('open_profile', self.friend.user_id, ''))
            invite = menu.addAction('Invite to My Instance')
            invite.triggered.connect(lambda: self.action_requested.emit('invite', self.friend.user_id, self.friend.display_name))
            copy_id = menu.addAction('Copy User ID')
            copy_id.triggered.connect(lambda: self.action_requested.emit('copy_id', self.friend.user_id, ''))
            copy_avatar_id = menu.addAction('Copy Avatar ID')
            copy_avatar_id.setEnabled(True)
            copy_avatar_id.setToolTip('Copy avatar ID (may require log data)')
            copy_avatar_id.triggered.connect(lambda: self.action_requested.emit('copy_avatar_id', self.friend.user_id, self.friend.display_name))
            groups = load_config().get('friend_groups', {})
            if isinstance(groups, dict) and groups:
                menu.addSeparator()
                group_menu = menu.addMenu('Add to group')
                for name in sorted(groups.keys(), key=str.casefold):
                    group_menu.addAction(name).triggered.connect(lambda _checked=False, g=name: self.action_requested.emit('add_group', self.friend.user_id, g))
            menu.addSeparator()
            block = menu.addAction('Block User')
            block.triggered.connect(lambda: self.action_requested.emit('block', self.friend.user_id, ''))
            unblock = menu.addAction('Unblock User')
            unblock.triggered.connect(lambda: self.action_requested.emit('unblock', self.friend.user_id, ''))
            hide = menu.addAction('Hide Avatar')
            hide.triggered.connect(lambda: self.action_requested.emit('hide_avatar', self.friend.user_id, ''))
            unhide = menu.addAction('Unhide Avatar')
            unhide.triggered.connect(lambda: self.action_requested.emit('unhide_avatar', self.friend.user_id, ''))
            mute = menu.addAction('Mute User')
            mute.triggered.connect(lambda: self.action_requested.emit('mute', self.friend.user_id, ''))
            unmute = menu.addAction('Unmute User')
            unmute.triggered.connect(lambda: self.action_requested.emit('unmute', self.friend.user_id, ''))
            unfriend = menu.addAction('Unfriend')
            unfriend.triggered.connect(lambda: self.action_requested.emit('unfriend', self.friend.user_id, ''))
            try:
                menu.exec(self.mapToGlobal(pos))
            except Exception as e:
                from .logging_setup import get_logger
                logger = get_logger('friends_list')
                logger.error('Context menu execution failed for %s: %s', self.friend.display_name, e, exc_info=True)
        except Exception as e:
            from .logging_setup import get_logger
            logger = get_logger('friends_list')
            logger.error('Context menu creation failed for %s: %s', self.friend.display_name, e, exc_info=True)
        finally:
            if menu is not None:
                menu.deleteLater()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            webbrowser.open(user_profile_url(self.friend.user_id))
        super().mouseReleaseEvent(event)

class FriendsListPanel(QWidget):

    def __init__(self, session: VRChatSession | None=None, parent: QWidget | None=None):
        super().__init__(parent)
        self.session = session
        self._worker: FriendsWorker | None = None
        self._action_runner = ActionRunner(self)
        self._populate_generation = 0
        self._populate_gen = 0
        self._pending_friends: list[FriendEntry] = []
        self._populate_index = 0
        self._prev_friend_status: dict[str, str] = {}
        self._prev_friend_joinable: dict[str, bool] = {}
        self._fetch_generation = 0
        self._all_friends: list[FriendEntry] = []
        self._status_pinned_until = 0.0
        self._filter_id = str(load_config().get('friend_filter', 'all'))
        self.setFixedSize(FRIENDS_PANEL_WIDTH, PLAYER_LIST_HEIGHT)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(lambda: self.refresh(full=False))
        self._full_poll_timer = QTimer(self)
        self._full_poll_timer.setInterval(_FULL_POLL_INTERVAL_MS)
        self._full_poll_timer.timeout.connect(lambda: self.refresh(full=True))
        self._build_ui()
        self._show_hint(_HINT)
        if self.session is not None:
            RemoteImageLabel.set_session(self.session)
            self._poll_timer.start()
            self._full_poll_timer.start()
            QTimer.singleShot(startup_delay_ms('friends'), lambda: self.refresh(full=False))

    def set_session(self, session: VRChatSession | None) -> None:
        self.session = session
        self._fetch_generation += 1
        if self._worker and self._worker.isRunning():
            self._worker.wait(1500)
        self._stop_populate()
        if session is None:
            self._poll_timer.stop()
            self._show_hint('Not logged in')
            return
        RemoteImageLabel.set_session(session)
        if not self._poll_timer.isActive():
            self._poll_timer.start()
        if not self._full_poll_timer.isActive():
            self._full_poll_timer.start()
        QTimer.singleShot(startup_delay_ms('friends'), lambda: self.refresh(full=False))

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
        self.refresh_btn = QPushButton('↻')
        self.refresh_btn.setFixedSize(24, 24)
        self.refresh_btn.setStyleSheet(TOOLBAR_BUTTON)
        self.refresh_btn.setToolTip('Refresh friends list')
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._manual_refresh)
        header_row.addWidget(self.refresh_btn)
        header_row.addStretch(1)
        frame_layout.addLayout(header_row)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        self._filter_buttons: dict[str, QPushButton] = {}
        for filter_id, label in (('all', 'All'), ('join_me', 'Join Me'), ('online', 'Online')):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(20)
            btn.clicked.connect(lambda checked, fid=filter_id: self._set_filter(fid))
            self._filter_buttons[filter_id] = btn
            filter_row.addWidget(btn)
        self.group_btn = QPushButton('Groups')
        self.group_btn.setFixedHeight(20)
        self.group_btn.clicked.connect(self._show_groups_menu)
        filter_row.addWidget(self.group_btn)
        filter_row.addStretch(1)
        frame_layout.addLayout(filter_row)
        self._sync_filter_buttons()
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
        self.setStyleSheet(dark_theme('\n            QLabel#previewHeader {\n                font-weight: bold;\n                font-size: 9pt;\n            }\n            QLabel#friendCountBadge {\n                color: #7db87d;\n                font-size: 8pt;\n                font-weight: 600;\n                border: 1px solid #3d6640;\n                border-radius: 8px;\n                padding: 1px 7px;\n                background-color: rgba(125, 184, 125, 0.1);\n            }\n            QLabel#friendStatusLabel {\n                color: #7a8a7a;\n                font-size: 8pt;\n            }\n            QFrame#friendListBox {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n            }\n            QLabel#friendHint {\n                color: #6a7a6a;\n                font-size: 8pt;\n                padding: 12px 8px;\n            }\n            QScrollArea {\n                background: transparent;\n                border: none;\n            }\n            QFrame#friendCard {\n                background-color: #1c1c1c;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n            }\n            QFrame#friendCard:hover {\n                border-color: #4ea3ff;\n            }\n            QFrame#friendImageHost {\n                background-color: #232323;\n                border-top-left-radius: 6px;\n                border-top-right-radius: 6px;\n            }\n            QFrame#friendCardFooter {\n                background-color: #1a1a1a;\n                border-bottom-left-radius: 6px;\n                border-bottom-right-radius: 6px;\n            }\n            QLabel#friendName {\n                color: #f0f0f0;\n                font-size: 7pt;\n                font-weight: 600;\n            }\n            QLabel#friendWorld {\n                color: #7db87d;\n                font-size: 6pt;\n            }\n            QLabel#statusIndicatorLabel {\n                background: transparent;\n                border: none;\n                font-size: 6pt;\n            }\n        '))

    def _stop_populate(self) -> None:
        self._populate_generation += 1
        self._pending_friends = []
        self._populate_index = 0

    def _manual_refresh(self) -> None:
        pulse_widget(self.refresh_btn, duration=700, min_opacity=0.55)
        self.refresh(full=True)

    def _show_hint(self, text: str) -> None:
        self._stop_populate()
        self._clear_grid()
        self.hint_label.setText(text)
        self.count_label.setText('')
        self.status_label.setText('')
        reveal_content(self.scroll if self.scroll.isVisible() else None, self.hint_label, duration=220, pop=False)

    def _clear_grid(self) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _set_status_message(self, text: str, *, error: bool=False, pin_sec: float=0) -> None:
        self.status_label.setText(text)
        self.status_label.setToolTip(text if error else '')
        if pin_sec > 0:
            self._status_pinned_until = time.monotonic() + pin_sec
        elif text:
            self._status_pinned_until = 0.0
        if error:
            self.status_label.setStyleSheet('color: #e07070; font-size: 8pt;')
        else:
            self.status_label.setStyleSheet('')

    def _format_friends_summary(self, friends: list[FriendEntry], *, hidden_offline: int=0) -> str:
        if not friends:
            return ''
        counts: dict[str, int] = {}
        for friend in friends:
            counts[friend.status.key] = counts.get(friend.status.key, 0) + 1
        parts: list[str] = []
        for key, label in (('join_me', 'join me'), ('active', 'online'), ('ask_me', 'ask me'), ('busy', 'do not disturb'), ('offline', 'offline'), ('invisible', 'invisible'), ('private', 'private')):
            count = counts.get(key, 0)
            if not count:
                continue
            if key == 'offline' and hidden_offline > 0:
                parts.append(f'{count} offline hidden')
            else:
                parts.append(f'{count} {label}')
        return ' · '.join(parts[:4])

    def _start_action_worker(self, worker: ApiActionWorker) -> None:
        self._action_runner.run(worker, on_ok=self._on_action_ok, on_error=self._on_action_error, on_cancelled=self._on_action_cancelled)

    def _on_card_action(self, action: str, user_id: str, detail: str) -> None:
        if action == 'copy_id':
            QApplication.clipboard().setText(user_id)
            self._set_status_message('User ID copied.', pin_sec=2.5)
            QTimer.singleShot(2500, self.refresh)
            return
        if action == 'copy_avatar_id':
            friend = next((entry for entry in self._all_friends if entry.user_id == user_id), None)
            avatar_id = friend.avatar_id if friend is not None else None
            if avatar_id:
                QApplication.clipboard().setText(avatar_id)
                self._set_status_message('Avatar ID copied.', pin_sec=2.5)
            else:
                self._set_status_message('No avatar ID available', error=True, pin_sec=3.0)
            return
        if action == 'open_profile':
            webbrowser.open(user_profile_url(user_id))
            return
        if self.session is None:
            return
        if self._action_runner.is_running():
            return
        if action == 'force_clone':
            friend = next((entry for entry in self._all_friends if entry.user_id == user_id), None)
            status = friend.status if friend is not None else None
            self._set_status_message('Force cloning avatar…')
            self._start_action_worker(ApiActionWorker(lambda: force_clone_player_avatar(self.session, user_id, display_name=detail or None, status=status), 'Avatar selected — switch applies in VRChat.', timeout_sec=_FORCE_CLONE_TIMEOUT_SEC, context='force clone'))
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
            return
        if action == 'invite':
            window = self.window()
            player_list = getattr(window, 'player_list', None)
            instance = getattr(player_list, '_current_instance', None) if player_list is not None else None
            if instance is None or not instance.world_id or not instance.instance_id:
                self._set_status_message('Join a VRChat instance first.', error=True)
                return
            self._set_status_message(f'Inviting {detail or user_id}…')
            self._start_action_worker(ApiActionWorker(lambda: invite_user_to_instance(self.session, user_id, world_id=instance.world_id, instance_id=instance.instance_id), f'Invite sent to {detail or user_id}.'))
            return
        if action == 'add_group':
            if detail:
                self._add_friend_to_group(user_id, detail)
            return
        mod_map = {'block': ('block', False), 'unblock': ('block', True), 'hide_avatar': ('hideAvatar', False), 'unhide_avatar': ('hideAvatar', True), 'mute': ('mute', False), 'unmute': ('mute', True)}
        if action in mod_map:
            mod_type, undo = mod_map[action]
            fn = unmoderate_player if undo else moderate_player
            self._start_action_worker(ApiActionWorker(lambda: fn(self.session, user_id, mod_type)))
            return
        if action == 'unfriend':
            self._start_action_worker(ApiActionWorker(lambda: unfriend_user(self.session, user_id)))

    def _on_action_ok(self, message: str) -> None:
        if not widget_is_valid(self):
            return
        self._set_status_message(message, pin_sec=6.0)
        self.refresh()

    def _on_action_error(self, message: str) -> None:
        if not widget_is_valid(self):
            return
        if SessionManager.instance().try_handle_auth_failure(message):
            self._set_status_message('Session expired — sign in again', error=True, pin_sec=12.0)
            return
        self._set_status_message(f'Error: {message}', error=True, pin_sec=12.0)

    def _on_action_cancelled(self) -> None:
        if not widget_is_valid(self):
            return
        self._set_status_message('')

    def _emit_friend_notifications(self, friends: list[FriendEntry]) -> None:
        bus = NotificationBus.instance()
        for friend in friends:
            prev = self._prev_friend_status.get(friend.user_id)
            current = friend.status.key
            prev_joinable = self._prev_friend_joinable.get(friend.user_id, False)
            if prev is None:
                self._prev_friend_status[friend.user_id] = current
                self._prev_friend_joinable[friend.user_id] = friend.can_join
                continue
            if prev in ('offline', 'invisible') and current in ('active', 'join_me', 'ask_me'):
                bus.friend_online(friend.display_name)
            if friend.can_join and not prev_joinable:
                bus.friend_joinable(friend.display_name, friend.world_name or 'a world')
            self._prev_friend_status[friend.user_id] = current
            self._prev_friend_joinable[friend.user_id] = friend.can_join

    def _friends_for_display(self, friends: list[FriendEntry]) -> tuple[list[FriendEntry], int]:
        friends = self._apply_friend_filter(friends)
        if len(friends) <= _OFFLINE_HIDE_THRESHOLD:
            return (friends, 0)
        visible = [friend for friend in friends if friend.status.key != 'offline']
        hidden_offline = len(friends) - len(visible)
        if hidden_offline <= 0:
            return (friends, 0)
        return (visible, hidden_offline)

    def _apply_friend_filter(self, friends: list[FriendEntry]) -> list[FriendEntry]:
        filter_id = self._filter_id
        if filter_id == 'join_me':
            return [friend for friend in friends if friend.status.key == 'join_me']
        if filter_id == 'online':
            return [friend for friend in friends if friend.is_online]
        if filter_id.startswith('group:'):
            group_name = filter_id[6:]
            groups = load_config().get('friend_groups', {})
            ids = set(groups.get(group_name, []) if isinstance(groups, dict) else [])
            return [friend for friend in friends if friend.user_id in ids]
        return friends

    def apply_filter(self, filter_id: str) -> None:
        self._set_filter(filter_id)

    def _set_filter(self, filter_id: str) -> None:
        self._filter_id = filter_id
        save_config({'friend_filter': filter_id})
        self._sync_filter_buttons()
        if self._all_friends:
            self._render_friends(self._all_friends)

    def _sync_filter_buttons(self) -> None:
        base = self._filter_id.split(':', 1)[0] if self._filter_id.startswith('group:') else self._filter_id
        for fid, btn in self._filter_buttons.items():
            btn.setChecked(fid == base and not self._filter_id.startswith('group:'))
        self.group_btn.setStyleSheet('font-weight: 600;' if self._filter_id.startswith('group:') else '')

    def _friend_groups(self) -> dict[str, list[str]]:
        raw = load_config().get('friend_groups', {})
        if not isinstance(raw, dict):
            return {}
        return {str(name): [str(uid) for uid in ids if isinstance(ids, list)] for name, ids in raw.items()}

    def _show_groups_menu(self, *_args) -> None:
        menu = themed_menu(self.group_btn)
        groups = self._friend_groups()
        if not groups:
            empty = menu.addAction('No groups yet')
            empty.setEnabled(False)
        else:
            for name in sorted(groups.keys(), key=str.casefold):
                action = menu.addAction(name)
                action.triggered.connect(lambda _checked=False, group=name: self._set_filter(f'group:{group}'))
        menu.addSeparator()
        manage = menu.addAction('Manage groups…')
        manage.triggered.connect(self._manage_groups_hint)
        create = menu.addAction('New group…')
        create.triggered.connect(self._create_group)
        menu.exec(self.group_btn.mapToGlobal(self.group_btn.rect().bottomLeft()))

    def _create_group(self) -> None:
        name, ok = QInputDialog.getText(self, 'New Friend Group', 'Group name:')
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        groups = self._friend_groups()
        groups.setdefault(name, [])
        save_config({'friend_groups': groups})
        self._set_status_message(f'Created group {name}')

    def _manage_groups_hint(self) -> None:
        self._set_status_message('Right-click a friend → Add to group')

    def _add_friend_to_group(self, user_id: str, group_name: str) -> None:
        groups = self._friend_groups()
        members = list(groups.get(group_name, []))
        if user_id not in members:
            members.append(user_id)
        groups[group_name] = members
        save_config({'friend_groups': groups})
        self._set_status_message(f'Added to {group_name}')

    def _render_friends(self, friends: list[FriendEntry]) -> None:
        if get_bool(load_config().get('enable_notifications', True)):
            self._emit_friend_notifications(friends)
        display_friends, hidden_offline = self._friends_for_display(friends)
        self._stop_populate()
        self._clear_grid()
        self.count_label.setText(str(len(friends)))
        if time.monotonic() >= self._status_pinned_until:
            self._set_status_message(self._format_friends_summary(friends, hidden_offline=hidden_offline))
        if not display_friends:
            self.hint_label.setText('No friends match this filter')
            reveal_content(self.scroll if self.scroll.isVisible() else None, self.hint_label, duration=220, pop=False)
            return
        self._pending_friends = display_friends
        self._populate_index = 0
        self._populate_gen = self._populate_generation

        def _show_friends() -> None:
            RemoteImageLabel.prefetch([friend.thumbnail_url for friend in display_friends if friend.thumbnail_url])
            self._populate_next_batch()

        if self.scroll.isVisible():
            _show_friends()
        else:
            reveal_content(self.hint_label, self.scroll, duration=260, pop=True)
            QTimer.singleShot(120, _show_friends)
        self._push_thumbnails_to_player_list()

    def refresh(self, *, full: bool=False) -> None:
        if SessionManager.instance().is_relogin_active():
            return
        if self.session is None:
            self._show_hint('Not logged in')
            return
        self._fetch_generation += 1
        generation = self._fetch_generation
        if self._worker and self._worker.isRunning():
            return
        self._stop_populate()
        if not self.scroll.isVisible():
            self.hint_label.setText('Loading friends…')
            self.hint_label.show()
            self.scroll.hide()
            self._set_status_message('Loading…')
            pulse_widget(self.status_label, duration=1200, min_opacity=0.45)
        self._worker = FriendsWorker(self.session, include_offline=full, enrich_worlds=full)
        self._worker.finished_ok.connect(lambda friends: self._on_loaded(friends, generation))
        self._worker.finished_error.connect(lambda err: self._on_error(err, generation))
        self._worker.start()

    def _on_error(self, message: object, generation: int) -> None:
        if generation != self._fetch_generation or self.session is None:
            return
        logger.debug('Friends list error: %s', message)
        stop_pulse(self.status_label)
        if is_rate_limit_error(message):
            self._show_hint('Rate limited — wait a few minutes')
            QTimer.singleShot(60000, self.refresh)
            return
        if SessionManager.instance().try_handle_auth_failure(message):
            self._show_hint('Session expired — sign in again')
            return
        self._show_hint('Could not load friends')

    def _on_loaded(self, friends_obj: object, generation: int) -> None:
        if generation != self._fetch_generation or self.session is None:
            return
        stop_pulse(self.status_label)
        if not isinstance(friends_obj, list):
            self._show_hint('Could not load friends')
            return
        friends: list[FriendEntry] = [item for item in friends_obj if isinstance(item, FriendEntry)]
        self._all_friends = friends
        self._render_friends(friends)
        flash_widget(self.status_label)

    def _push_thumbnails_to_player_list(self) -> None:
        window = self.window()
        player_list = getattr(window, 'player_list', None)
        if player_list is not None and hasattr(player_list, 'reapply_cached_thumbnails'):
            player_list.reapply_cached_thumbnails()

    def _populate_next_batch(self) -> None:
        if self._populate_generation != self._populate_gen:
            return
        friends = self._pending_friends
        start = self._populate_index
        end = min(start + _POPULATE_BATCH, len(friends))
        new_cards: list[FriendCard] = []
        for index in range(start, end):
            friend = friends[index]
            card = FriendCard(friend, self.grid_host, session=self.session)
            card.action_requested.connect(self._on_card_action)
            row = index // FRIEND_GRID_COLUMNS
            col = index % FRIEND_GRID_COLUMNS
            self.grid_layout.addWidget(card, row, col)
            card.load_images()
            new_cards.append(card)
        animate_list_items(new_cards, pop=True, duration=200, step_ms=20)
        self._populate_index = end
        if end < len(friends):
            QTimer.singleShot(_POPULATE_INTERVAL_MS, self._populate_next_batch)
        else:
            self._pending_friends = []

    def cleanup(self) -> None:
        self._action_runner.cleanup()
        self._stop_populate()
        self._poll_timer.stop()
        self._full_poll_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
