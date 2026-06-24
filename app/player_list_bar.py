from __future__ import annotations
import time
import webbrowser
from dataclasses import replace
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from .api_action_worker import ApiActionWorker
from .safe_runtime import widget_is_valid
from .image_loader import RemoteImageLabel
from .logging_setup import get_logger
from .services.action_runner import ActionRunner
from .services.errors import report_worker_error
from .services.session_manager import SessionManager
from .status_indicator import StatusIndicator
from .theme import dark_theme, themed_menu, TOOLBAR_BUTTON
from .ui_animations import animate_list_items, flash_widget, pulse_widget, reveal_content, stop_pulse
from .ui_perf import list_populate_batch, list_populate_delay_ms, should_animate_lists
from .avatar_search_providers import PERFORMANCE_COLORS
from .widgets.player_detail_popover import PlayerDetailPopover
from .vrchat_api import InstanceInfo, InstancePlayer, apply_cached_thumbnails, close_instance, enrich_instance_players, force_clone_player_avatar, get_current_instance, get_current_instance_log_only, invite_user_to_instance, moderate_player, thumbnail_url_for_size, unmoderate_player, unfriend_user, user_profile_url
from .vrchat_log_players import log_players_for_current_room
from .refresh_debounce import RefreshDebouncer
from .ui_scroll import ScrollDebouncer, visible_widgets_in_layout
from .api_startup import in_startup_window, startup_delay_ms
from .api_rate_limit import api_budget_pressure, api_budget_saturated
from .vrchat_auth import VRChatSession
logger = get_logger('player_list')
_ROW_AVATAR = 28
_ROW_HEIGHT = 42
_THUMB_SIZE = 64
_WORLD_HINT = 'Join a VRChat world to see players'
_REFRESH_TIMEOUT_MS = 20000
_POLL_INTERVAL_MS = 15000
_LOG_POLL_INTERVAL_MS = 10000
_LOG_ENRICH_REQUESTS = 0
_FAST_ENRICH_REQUESTS = 0
_ENRICH_BATCH_MAX = 40
_ENRICH_BATCH_INTERVAL_MS = 350
_POPULATE_BATCH = 12
_POPULATE_INTERVAL_MS = 24
_FORCE_CLONE_TIMEOUT_SEC = 60.0
_API_ENRICH_MIN_INTERVAL_SEC = 45.0
_SCROLL_LOAD_MARGIN_ROWS = 3
_SCROLL_LOAD_MAX_PER_TICK = 8
_SCROLL_ENRICH_IDLE_MS = 150
PLAYER_LIST_HEIGHT = 200

class LogPlayersWorker(QThread):
    finished_ok = pyqtSignal(int, object)

    def __init__(self, session: VRChatSession | None, generation: int, *, enrich: bool=False):
        super().__init__()
        self.session = session
        self.generation = generation
        self.enrich = enrich

    def run(self) -> None:
        try:
            self.finished_ok.emit(self.generation, get_current_instance_log_only(self.session, enrich=self.enrich, max_enrich_requests=_LOG_ENRICH_REQUESTS))
        except Exception:
            logger.warning('Log player fetch failed', exc_info=True)
            self.finished_ok.emit(self.generation, None)

class QuickLogCheckWorker(QThread):
    finished_ok = pyqtSignal(object)

    def run(self) -> None:
        try:
            players, _location = log_players_for_current_room()
            self.finished_ok.emit({player.user_id for player in players})
        except Exception:
            logger.debug('Quick log check failed', exc_info=True)
            self.finished_ok.emit(None)

class ThumbnailEnrichWorker(QThread):
    finished_ok = pyqtSignal(int, object)

    def __init__(self, session: VRChatSession, players: list[InstancePlayer], enrich_generation: int, *, max_requests: int, priority_user_ids: set[str] | None=None):
        super().__init__()
        self.session = session
        self.players = players
        self.enrich_generation = enrich_generation
        self.max_requests = max_requests
        self.priority_user_ids = priority_user_ids

    def run(self) -> None:
        try:
            enriched = enrich_instance_players(self.session, self.players, max_requests=self.max_requests, priority_user_ids=self.priority_user_ids)
            self.finished_ok.emit(self.enrich_generation, enriched)
        except Exception:
            logger.warning('Thumbnail enrich failed', exc_info=True)
            self.finished_ok.emit(self.enrich_generation, self.players)

class ApiInstanceWorker(QThread):
    finished_ok = pyqtSignal(int, object)
    finished_error = pyqtSignal(int, str)

    def __init__(self, session: VRChatSession, generation: int):
        super().__init__()
        self.session = session
        self.generation = generation

    def run(self) -> None:
        try:
            info = get_current_instance(self.session, max_enrich_requests=_FAST_ENRICH_REQUESTS, friend_players_cache_only=True)
            self.finished_ok.emit(self.generation, info)
        except Exception as exc:
            logger.warning('Instance API fetch failed', exc_info=True)
            report_worker_error('Player list', exc)
            self.finished_error.emit(self.generation, str(exc) or 'Failed to load players.')

class PlayerRow(QFrame):

    def __init__(self, player: InstancePlayer, parent: QWidget | None=None, *, session: VRChatSession | None=None, list_bar: PlayerListBar | None=None):
        try:
            super().__init__(parent)
            self.player = player
            self.session = session
            self._list_bar = list_bar
            self.is_friend = player.is_friend
            self._thumb_url = ''
            self._thumb_loaded = False
            self.setObjectName('playerRow')
            self.setFixedHeight(_ROW_HEIGHT)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(self._show_context_menu)
            layout = QHBoxLayout(self)
            layout.setContentsMargins(6, 4, 8, 4)
            layout.setSpacing(8)
            self.thumb = RemoteImageLabel(_ROW_AVATAR, self)
            layout.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignVCenter)
            name_col = QVBoxLayout()
            name_col.setContentsMargins(0, 0, 0, 0)
            name_col.setSpacing(0)
            self.name_label = QLabel(player.display_name)
            self.name_label.setObjectName('playerName')
            self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            name_col.addWidget(self.name_label)
            self.status_indicator = StatusIndicator(self, dot_size=6, compact=True)
            if player.status is not None:
                self.status_indicator.apply(player.status)
            else:
                self.status_indicator.hide()
            name_col.addWidget(self.status_indicator)
            name_host = QWidget(self)
            name_host.setLayout(name_col)
            name_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            layout.addWidget(name_host, 1)
            meta = QHBoxLayout()
            meta.setSpacing(4)
            meta.setContentsMargins(0, 0, 0, 0)
            self._trust_badge: QLabel | None = None
            self._friend_badge: QLabel | None = None
            if player.trust is not None:
                trust_badge = QLabel(player.trust.short)
                trust_badge.setObjectName('trustBadge')
                trust_badge.setToolTip(player.trust.label)
                trust_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                trust_badge.setFixedHeight(16)
                trust_badge.setStyleSheet(f'color: {player.trust.color}; font-size: 7pt; font-weight: 700; border: 1px solid {player.trust.color}; border-radius: 4px; padding: 0 5px; background-color: rgba(255, 255, 255, 0.04);')
                meta.addWidget(trust_badge)
                self._trust_badge = trust_badge
            if player.is_friend:
                friend_badge = QLabel('★')
                friend_badge.setObjectName('friendBadge')
                friend_badge.setToolTip('Friend')
                meta.addWidget(friend_badge)
                self._friend_badge = friend_badge
            self._perf_badge: QLabel | None = None
            if player.avatar_performance:
                color = PERFORMANCE_COLORS.get(player.avatar_performance, '#888')
                perf_badge = QLabel(player.avatar_performance[:3].upper())
                perf_badge.setObjectName('perfBadge')
                perf_badge.setToolTip(player.avatar_performance)
                perf_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                perf_badge.setFixedHeight(16)
                perf_badge.setStyleSheet(f'color: {color}; font-size: 7pt; font-weight: 700; border: 1px solid {color}; border-radius: 4px; padding: 0 5px;')
                meta.addWidget(perf_badge)
                self._perf_badge = perf_badge
            layout.addLayout(meta)
            if player.thumbnail_url:
                self.load_thumbnail()
        except Exception as e:
            logger.error('Failed to initialize player row for %s: %s', player.display_name, e, exc_info=True)

    def set_session(self, session: VRChatSession | None) -> None:
        self.session = session

    def update_player(self, player: InstancePlayer) -> None:
        previous_url = self.player.thumbnail_url
        if not player.thumbnail_url and previous_url:
            player = replace(player, thumbnail_url=previous_url)
        self.player = player
        self.is_friend = player.is_friend
        if player.status is not None:
            self.status_indicator.apply(player.status)
            self.status_indicator.show()
        else:
            self.status_indicator.hide()
        if self._trust_badge is not None:
            if player.trust is not None:
                self._trust_badge.setText(player.trust.short)
                self._trust_badge.setToolTip(player.trust.label)
                self._trust_badge.setStyleSheet(f'color: {player.trust.color}; font-size: 7pt; font-weight: 700; border: 1px solid {player.trust.color}; border-radius: 4px; padding: 0 5px; background-color: rgba(255, 255, 255, 0.04);')
                self._trust_badge.show()
            else:
                self._trust_badge.hide()
        if self._friend_badge is not None:
            self._friend_badge.setVisible(player.is_friend)
        if self._perf_badge is not None:
            if player.avatar_performance:
                color = PERFORMANCE_COLORS.get(player.avatar_performance, '#888')
                self._perf_badge.setText(player.avatar_performance[:3].upper())
                self._perf_badge.setToolTip(player.avatar_performance)
                self._perf_badge.setStyleSheet(f'color: {color}; font-size: 7pt; font-weight: 700; border: 1px solid {color}; border-radius: 4px; padding: 0 5px;')
                self._perf_badge.show()
            else:
                self._perf_badge.hide()
        metrics = QFontMetrics(self.name_label.font())
        available = max(40, self.name_label.width())
        elided = metrics.elidedText(player.display_name, Qt.TextElideMode.ElideRight, available)
        self.name_label.setText(elided)
        self.name_label.setToolTip(player.display_name if elided != player.display_name else '')
        if player.thumbnail_url != previous_url:
            self._thumb_loaded = False
            self._thumb_url = ''
            self.thumb.setText('…')
            if player.thumbnail_url:
                self.load_thumbnail()

    def needs_thumbnail(self) -> bool:
        if not self.player.thumbnail_url:
            return False
        url = self._thumbnail_load_url()
        if self._thumb_loaded and self._thumb_url == url:
            pixmap = self.thumb.pixmap()
            if pixmap is not None and (not pixmap.isNull()):
                return False
        return True

    def _thumbnail_load_url(self) -> str:
        return thumbnail_url_for_size(self.player.thumbnail_url, _THUMB_SIZE)

    def load_thumbnail(self) -> None:
        url = self._thumbnail_load_url()
        if not url:
            self.thumb.setText('?')
            return
        if self._thumb_loaded and self._thumb_url == url:
            pixmap = self.thumb.pixmap()
            if pixmap is not None and (not pixmap.isNull()):
                return
        self._thumb_url = url
        self._thumb_loaded = True
        self.thumb.load(url)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        metrics = QFontMetrics(self.name_label.font())
        available = max(40, self.name_label.width())
        elided = metrics.elidedText(self.player.display_name, Qt.TextElideMode.ElideRight, available)
        self.name_label.setText(elided)
        self.name_label.setToolTip(self.player.display_name if elided != self.player.display_name else '')

    def mouseReleaseEvent(self, event) -> None:
        try:
            if event.button() == Qt.MouseButton.LeftButton and self._list_bar is not None:
                self._list_bar.show_player_detail(self.player, self.mapToGlobal(event.pos()))
            super().mouseReleaseEvent(event)
        except Exception as e:
            logger.error('Mouse release event failed for player %s: %s', self.player.display_name, e, exc_info=True)
            super().mouseReleaseEvent(event)

    def _show_context_menu(self, pos) -> None:
        try:
            if self._list_bar is not None and self.player is not None:
                global_pos = self.mapToGlobal(pos)
                self._list_bar.show_player_context_menu(self.player, global_pos)
        except Exception as e:
            logger.error('Context menu trigger failed for player %s: %s', self.player.display_name if self.player else 'unknown', e, exc_info=True)

class PlayerListBar(QWidget):
    instance_updated = pyqtSignal(object)

    def __init__(self, session: VRChatSession | None=None, parent: QWidget | None=None):
        super().__init__(parent)
        self.session = session
        self._worker: LogPlayersWorker | None = None
        self._quick_log_worker: QuickLogCheckWorker | None = None
        self._api_worker: ApiInstanceWorker | None = None
        self._fallback_worker: LogPlayersWorker | None = None
        self._action_runner = ActionRunner(self)
        self._current_instance: InstanceInfo | None = None
        self._refresh_generation = 0
        self._populate_generation = 0
        self._populate_gen = 0
        self._pending_players: list[InstancePlayer] = []
        self._populate_index = 0
        self._enrich_generation = 0
        self._enrich_worker: ThumbnailEnrichWorker | None = None
        self._enrich_timer = QTimer(self)
        self._enrich_timer.setSingleShot(True)
        self._enrich_timer.timeout.connect(self._start_thumbnail_enrich)
        self._context_menu_open = False
        self._deferred_instance_info: InstanceInfo | None = None
        self._worker_started_at = 0.0
        self._last_api_enrich_at = 0.0
        self._last_api_roster_key: tuple[str, ...] = ()
        self._refresh_pending = False
        self._status_pinned_until = 0.0
        self._refresh_debouncer = RefreshDebouncer(self, self._run_refresh, delay_ms=300)
        self._scroll_thumb_debouncer = ScrollDebouncer(self, self._load_visible_thumbnails, delay_ms=60)
        self._scroll_idle_timer = QTimer(self)
        self._scroll_idle_timer.setSingleShot(True)
        self._scroll_idle_timer.setInterval(_SCROLL_ENRICH_IDLE_MS)
        self._scroll_idle_timer.timeout.connect(self._on_scroll_idle)
        self._scrolling = False
        self._detail_popover = PlayerDetailPopover(self)
        self._detail_popover.bind_actions(self._on_player_action)
        self.setFixedHeight(PLAYER_LIST_HEIGHT)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.refresh)
        self._log_poll_timer = QTimer(self)
        self._log_poll_timer.setInterval(_LOG_POLL_INTERVAL_MS)
        self._log_poll_timer.timeout.connect(self._quick_log_check)
        self._refresh_timeout = QTimer(self)
        self._refresh_timeout.setSingleShot(True)
        self._refresh_timeout.timeout.connect(self._on_refresh_timeout)
        self._build_ui()
        self._show_world_hint(animate=False)
        if self.session is not None:
            RemoteImageLabel.set_session(self.session)
            self._poll_timer.start()
            self._log_poll_timer.start()
            QTimer.singleShot(startup_delay_ms('player'), self.refresh)

    def set_session(self, session: VRChatSession | None) -> None:
        self.session = session
        self._refresh_generation += 1
        self._stop_thumbnail_enrich()
        if session is None:
            self._poll_timer.stop()
            self._current_instance = None
            self._show_world_hint(animate=False)
            return
        RemoteImageLabel.set_session(session)
        if not self._poll_timer.isActive():
            self._poll_timer.start()
        if not self._log_poll_timer.isActive():
            self._log_poll_timer.start()
        QTimer.singleShot(startup_delay_ms('player'), self.refresh)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.frame = QWidget()
        self.frame.setObjectName('previewContainer')
        self.frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.frame.customContextMenuRequested.connect(self._show_instance_menu)
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(10, 8, 10, 8)
        frame_layout.setSpacing(5)
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        title = QLabel('Player List')
        title.setObjectName('previewHeader')
        header_row.addWidget(title, 0)
        self.refresh_btn = QPushButton('↻')
        self.refresh_btn.setFixedSize(24, 24)
        self.refresh_btn.setStyleSheet(TOOLBAR_BUTTON)
        self.refresh_btn.setToolTip('Refresh player list')
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._manual_refresh)
        header_row.addWidget(self.refresh_btn, 0)
        header_row.addStretch(1)
        self.status_label = QLabel('')
        self.status_label.setObjectName('playerStatusLabel')
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header_row.addWidget(self.status_label, 1)
        frame_layout.addLayout(header_row)
        self.list_box = QFrame()
        self.list_box.setObjectName('playerListBox')
        list_box_layout = QVBoxLayout(self.list_box)
        list_box_layout.setContentsMargins(0, 0, 0, 0)
        list_box_layout.setSpacing(0)
        self.hint_label = QLabel(_WORLD_HINT)
        self.hint_label.setObjectName('playerHint')
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        list_box_layout.addWidget(self.hint_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.hide()
        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(2)
        self.rows_layout.addStretch(1)
        self.scroll.setWidget(self.rows_host)
        list_box_layout.addWidget(self.scroll, 1)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)
        frame_layout.addWidget(self.list_box, 1)
        root.addWidget(self.frame)
        self.setStyleSheet(dark_theme('\n            QLabel#previewHeader {\n                font-weight: bold;\n                font-size: 9pt;\n            }\n            QLabel#playerStatusLabel {\n                color: #7a8a7a;\n                font-size: 8pt;\n            }\n            QFrame#playerListBox {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n            }\n            QLabel#playerHint {\n                color: #6a7a6a;\n                font-size: 8pt;\n                padding: 12px 8px;\n            }\n            QScrollArea {\n                background: transparent;\n                border: none;\n            }\n            QFrame#playerRow {\n                background-color: transparent;\n                border: none;\n                border-bottom: 1px solid #2e2e2e;\n            }\n            QFrame#playerRow:hover {\n                background-color: rgba(78, 163, 255, 0.08);\n            }\n            QLabel#playerName {\n                color: #f0f0f0;\n                font-size: 8pt;\n            }\n            QLabel#statusIndicatorLabel {\n                font-size: 7pt;\n            }\n            QLabel#friendBadge {\n                color: #4ea3ff;\n                font-size: 8pt;\n            }\n        '))

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

    def _format_players_summary(self, players: list[InstancePlayer]) -> str:
        if not players:
            return ''
        counts: dict[str, int] = {}
        for player in players:
            key = player.status.key if player.status is not None else 'active'
            counts[key] = counts.get(key, 0) + 1
        parts: list[str] = []
        for key, label in (('private', 'private'), ('invisible', 'invisible')):
            count = counts.get(key, 0)
            if count:
                parts.append(f'{count} {label}')
        return ' · '.join(parts)

    def _update_status_summary(self) -> None:
        if time.monotonic() < self._status_pinned_until:
            return
        if self._current_instance is None or not self._current_instance.players:
            self._set_status_message('')
            return
        self._set_status_message(self._format_players_summary(self._current_instance.players))

    def _set_world_label(self, text: str, *, is_error: bool=False) -> None:
        self._set_status_message(text, error=is_error)

    def show_player_context_menu(self, player: InstancePlayer, global_pos) -> None:
        user_id = player.user_id
        display_name = player.display_name
        is_friend = player.is_friend
        self._context_menu_open = True
        menu = None
        try:
            menu = themed_menu(self.frame)
            clone = menu.addAction('Force Clone')
            clone.setEnabled(True)
            clone.setToolTip('Wear their avatar (may require log data)')
            clone.triggered.connect(lambda: self._on_player_action('force_clone', user_id, display_name))
            menu.addSeparator()
            if is_friend:
                invite = menu.addAction('Invite to My Instance')
                invite.triggered.connect(lambda: self._on_player_action('invite', user_id, display_name))
                menu.addSeparator()
            block = menu.addAction('Block User')
            block.triggered.connect(lambda: self._on_player_action('block', user_id, ''))
            unblock = menu.addAction('Unblock User')
            unblock.triggered.connect(lambda: self._on_player_action('unblock', user_id, ''))
            hide = menu.addAction('Hide Avatar')
            hide.triggered.connect(lambda: self._on_player_action('hide_avatar', user_id, ''))
            unhide = menu.addAction('Unhide Avatar')
            unhide.triggered.connect(lambda: self._on_player_action('unhide_avatar', user_id, ''))
            mute = menu.addAction('Mute User')
            mute.triggered.connect(lambda: self._on_player_action('mute', user_id, ''))
            unmute = menu.addAction('Unmute User')
            unmute.triggered.connect(lambda: self._on_player_action('unmute', user_id, ''))
            if is_friend:
                menu.addSeparator()
                unfriend = menu.addAction('Unfriend')
                unfriend.triggered.connect(lambda: self._on_player_action('unfriend', user_id, ''))
            menu.addSeparator()
            profile = menu.addAction('Open VRChat Profile')
            profile.triggered.connect(lambda: self._on_player_action('open_profile', user_id, ''))
            copy_id = menu.addAction('Copy User ID')
            copy_id.triggered.connect(lambda: self._on_player_action('copy_id', user_id, ''))
            copy_avatar_id = menu.addAction('Copy Avatar ID')
            copy_avatar_id.setEnabled(True)
            copy_avatar_id.setToolTip('Copy avatar ID (may require log data)')
            copy_avatar_id.triggered.connect(lambda: self._on_player_action('copy_avatar_id', user_id, display_name))
            try:
                menu.exec(global_pos)
            except Exception as e:
                logger.error('Context menu execution failed for %s: %s', display_name, e, exc_info=True)
        except Exception as e:
            logger.error('Context menu creation failed for %s: %s', display_name, e, exc_info=True)
        finally:
            self._context_menu_open = False
            if menu is not None:
                menu.deleteLater()
            deferred = self._deferred_instance_info
            if deferred is not None:
                self._deferred_instance_info = None
                self._apply_instance_info(deferred)

    def _show_instance_menu(self, pos) -> None:
        if self._current_instance is None or not self._current_instance.can_close_instance:
            return
        menu = themed_menu(self)
        close_action = menu.addAction('Force Close Instance')
        close_action.setToolTip('Hard-close this instance (instance owner only)')
        close_action.triggered.connect(self._force_close_instance)
        menu.exec(self.frame.mapToGlobal(pos))

    def _start_action_worker(self, worker: ApiActionWorker) -> None:
        self._action_runner.run(worker, on_ok=self._on_action_ok, on_error=self._on_action_error, on_cancelled=self._on_action_cancelled)

    def _force_close_instance(self) -> None:
        info = self._current_instance
        if info is None or self.session is None or (not info.can_close_instance):
            return
        if self._action_runner.is_running():
            return
        self._set_world_label('Closing instance…')
        self._start_action_worker(ApiActionWorker(lambda: close_instance(self.session, info.world_id, info.instance_id, hard_close=True), 'Instance force-closed.'))

    def show_player_detail(self, player: InstancePlayer, global_pos) -> None:
        self._detail_popover.show_player(player, session=self.session, global_pos=global_pos)

    def _on_player_action(self, action: str, user_id: str, display_name: str) -> None:
        try:
            if action == 'copy_id':
                QApplication.clipboard().setText(user_id)
                self._set_world_label('User ID copied.', pin_sec=2.5)
                QTimer.singleShot(2500, self.refresh)
                return
            if action == 'copy_avatar_id':
                avatar_id = None
                if self._current_instance is not None:
                    for player in self._current_instance.players:
                        if player.user_id == user_id:
                            avatar_id = player.avatar_id
                            display_name = display_name or player.display_name
                            break
                if avatar_id:
                    QApplication.clipboard().setText(avatar_id)
                    self._set_world_label('Avatar ID copied.', pin_sec=2.5)
                else:
                    self._set_world_label('No avatar ID available', is_error=True, pin_sec=3.0)
                return
            if action == 'open_profile':
                webbrowser.open(user_profile_url(user_id))
                return
            if self.session is None:
                return
            if self._action_runner.is_running():
                return
            if action == 'force_clone':
                avatar_id = None
                player_status = None
                thumbnail_url = None
                if self._current_instance is not None:
                    for player in self._current_instance.players:
                        if player.user_id == user_id:
                            avatar_id = player.avatar_id
                            player_status = player.status
                            thumbnail_url = player.thumbnail_url
                            display_name = display_name or player.display_name
                            break
                self._set_world_label('Force cloning avatar…')
                self._start_action_worker(ApiActionWorker(lambda: force_clone_player_avatar(self.session, user_id, display_name=display_name or None, avatar_id=avatar_id, status=player_status, thumbnail_url=thumbnail_url), 'Avatar selected — switch applies in VRChat.', timeout_sec=_FORCE_CLONE_TIMEOUT_SEC, context='force clone'))
                return
            if action == 'invite':
                if self._current_instance is None or not self._current_instance.instance_id:
                    self._set_status_message('Join a VRChat instance first.', error=True)
                    return
                self._set_world_label(f'Inviting {display_name or user_id}…')
                inst = self._current_instance
                self._start_action_worker(ApiActionWorker(lambda: invite_user_to_instance(self.session, user_id, world_id=inst.world_id, instance_id=inst.instance_id), f'Invite sent to {display_name or user_id}.'))
                return
            mod_type = {'block': 'block', 'unblock': 'block', 'hide_avatar': 'hideAvatar', 'unhide_avatar': 'hideAvatar', 'mute': 'mute', 'unmute': 'mute'}.get(action)
            if mod_type is None and action != 'unfriend':
                return
            if action == 'unfriend':
                self._set_world_label('Removing friend…')
                self._start_action_worker(ApiActionWorker(lambda: unfriend_user(self.session, user_id), 'Removed friend.'))
                return
            is_undo = action.startswith('un')
            self._set_world_label(f"{('Removing' if is_undo else 'Applying')} {mod_type}…")
            fn = unmoderate_player if is_undo else moderate_player
            self._start_action_worker(ApiActionWorker(lambda: fn(self.session, user_id, mod_type), f"{('Removed' if is_undo else 'Applied')} {mod_type}."))
        except Exception as e:
            logger.error('Player action failed for %s: %s', action, e, exc_info=True)

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

    def _stop_thumbnail_enrich(self) -> None:
        self._enrich_generation += 1
        self._enrich_timer.stop()

    def _stop_populate(self) -> None:
        self._populate_generation += 1
        self._pending_players = []
        self._populate_index = 0

    def _show_world_hint(self, *, animate: bool=True) -> None:
        self._stop_populate()
        self._stop_thumbnail_enrich()
        self._current_instance = None
        self.instance_updated.emit(None)
        self._clear_rows()
        self.hint_label.setText(_WORLD_HINT)
        self._set_world_label('')
        if animate:
            reveal_content(self.scroll if self.scroll.isVisible() else None, self.hint_label, duration=240, pop=True)
        else:
            self.scroll.hide()
            self.hint_label.show()

    def _manual_refresh(self) -> None:
        pulse_widget(self.refresh_btn, duration=700, min_opacity=0.55)
        self.refresh()

    def _quick_log_check(self) -> None:
        if SessionManager.instance().is_relogin_active() or self.session is None:
            return
        if self._action_runner.is_running():
            return
        if self._worker and self._worker.isRunning():
            return
        if self._quick_log_worker and self._quick_log_worker.isRunning():
            return
        if self._context_menu_open:
            return
        self._quick_log_worker = QuickLogCheckWorker()
        self._quick_log_worker.finished_ok.connect(self._on_quick_log_checked)
        self._quick_log_worker.start()

    def _on_quick_log_checked(self, log_ids_obj: object) -> None:
        if not isinstance(log_ids_obj, set):
            return
        log_ids: set[str] = log_ids_obj
        current = self._current_instance
        current_ids = {player.user_id for player in current.players} if current is not None else set()
        if log_ids != current_ids and (log_ids or current_ids):
            logger.debug('Quick log check detected roster change (%d -> %d) — refreshing', len(current_ids), len(log_ids))
            self.refresh()

    def refresh(self) -> None:
        if SessionManager.instance().is_relogin_active():
            return
        if self.session is None:
            self._show_world_hint()
            return
        if self._context_menu_open:
            logger.debug('Skipping refresh - context menu is open')
            return
        worker_running = self._worker is not None and self._worker.isRunning()
        worker_stuck = worker_running and time.monotonic() - self._worker_started_at > 15.0
        if worker_running and not worker_stuck:
            self._refresh_pending = True
            return
        self._refresh_pending = False
        self._run_refresh()

    def _run_refresh(self) -> None:
        if SessionManager.instance().is_relogin_active() or self.session is None:
            return
        if self._context_menu_open:
            return
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._stop_populate()
        if self._current_instance is None:
            self._set_world_label('Checking…')
            pulse_widget(self.status_label, duration=1200, min_opacity=0.45)
        self._refresh_timeout.start(_REFRESH_TIMEOUT_MS)
        self._worker_started_at = time.monotonic()
        self._worker = LogPlayersWorker(self.session, generation, enrich=False)
        self._worker.finished_ok.connect(self._on_log_loaded)
        self._worker.start()
        self._start_api_enrich(generation)

    def _maybe_run_pending_refresh(self) -> None:
        if self._refresh_pending:
            self._refresh_pending = False
            self._refresh_debouncer.schedule()

    def _finish_checking_state(self) -> None:
        self._refresh_timeout.stop()

    def _start_api_enrich(self, generation: int) -> None:
        if generation != self._refresh_generation or self.session is None:
            return
        delay = startup_delay_ms('player_api')
        if delay > 0:
            QTimer.singleShot(delay, lambda gen=generation: self._start_api_enrich_now(gen))
            return
        self._start_api_enrich_now(generation)

    def _roster_key(self, info: InstanceInfo | None) -> tuple[str, ...]:
        if info is None:
            return ()
        return tuple((player.user_id for player in info.players))

    def _same_player_roster(self, previous: InstanceInfo, incoming: InstanceInfo) -> bool:
        if self._pending_players:
            return False
        return self._roster_key(previous) == self._roster_key(incoming)

    def _start_api_enrich_now(self, generation: int) -> None:
        if generation != self._refresh_generation or self.session is None:
            return
        if self._api_worker and self._api_worker.isRunning():
            return
        roster_key = self._roster_key(self._current_instance)
        now = time.monotonic()
        if roster_key and roster_key == self._last_api_roster_key and now - self._last_api_enrich_at < _API_ENRICH_MIN_INTERVAL_SEC:
            logger.debug('Skipping API enrich — roster unchanged (%.0fs ago)', now - self._last_api_enrich_at)
            return
        self._api_worker = ApiInstanceWorker(self.session, generation)
        self._api_worker.finished_ok.connect(self._on_api_loaded)
        self._api_worker.finished_error.connect(self._on_error)
        self._api_worker.start()

    def _on_log_loaded(self, generation: int, info: object) -> None:
        if generation != self._refresh_generation:
            return
        if isinstance(info, InstanceInfo):
            self._apply_instance_info(info)
            self._finish_checking_state()
            stop_pulse(self.status_label)
            if not self._action_runner.is_running():
                self._update_status_summary()
                flash_widget(self.status_label)
            self._maybe_run_pending_refresh()
            return
        if self._current_instance is None:
            self._show_world_hint()
            self._set_world_label('Could not read VRChat log', is_error=True)
            self._finish_checking_state()
        self._maybe_run_pending_refresh()

    def _on_api_loaded(self, generation: int, info: object) -> None:
        if generation != self._refresh_generation:
            return
        if isinstance(info, InstanceInfo):
            self._last_api_enrich_at = time.monotonic()
            self._last_api_roster_key = self._roster_key(info)
            self._apply_instance_info(info)
            self._finish_checking_state()
            stop_pulse(self.status_label)
            if not self._action_runner.is_running():
                self._update_status_summary()
                flash_widget(self.status_label)
            return
        if self._current_instance is None:
            self._show_world_hint()
        self._finish_checking_state()

    def _start_log_only_fallback(self) -> None:
        if self.session is None:
            return
        if self._fallback_worker and self._fallback_worker.isRunning():
            return
        self._fallback_worker = LogPlayersWorker(self.session, self._refresh_generation, enrich=False)
        self._fallback_worker.finished_ok.connect(self._on_log_only_loaded)
        self._fallback_worker.start()

    def _on_log_only_loaded(self, generation: int, fallback: object) -> None:
        if generation != self._refresh_generation:
            return
        if isinstance(fallback, InstanceInfo):
            self._apply_instance_info(fallback)
            self._finish_checking_state()
            if not self._action_runner.is_running():
                self._update_status_summary()
            return
        self._show_world_hint()
        self._set_world_label('Could not load players from log', is_error=True)
        self._finish_checking_state()

    def _on_refresh_timeout(self) -> None:
        if self._current_instance is not None:
            logger.info('Player list API refresh timed out — keeping displayed players')
            self._finish_checking_state()
            return
        if self._worker and self._worker.isRunning():
            logger.warning('Player list log read timed out — retrying')
            self._start_log_only_fallback()
            return
        self._finish_checking_state()
        if self._current_instance is None:
            self._show_world_hint()
            self._set_world_label('Player list refresh timed out', is_error=True)

    def _clear_rows(self) -> None:
        try:
            while self.rows_layout.count():
                item = self.rows_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self.rows_layout.addStretch(1)
        except Exception as e:
            logger.error('Failed to clear player rows: %s', e, exc_info=True)

    def _on_error(self, generation: int, message: str) -> None:
        if generation != self._refresh_generation:
            return
        logger.debug('Player list error: %s', message)
        if SessionManager.instance().try_handle_auth_failure(message):
            self._finish_checking_state()
            return
        report_worker_error('Player list', message)
        if self._fallback_worker and self._fallback_worker.isRunning():
            return
        if self._current_instance is not None:
            self._finish_checking_state()
            return
        self._start_log_only_fallback()

    def _on_scroll_value_changed(self, _value: int) -> None:
        self._scrolling = True
        self._scroll_idle_timer.start(_SCROLL_ENRICH_IDLE_MS)
        self._scroll_thumb_debouncer.schedule()

    def _on_scroll_idle(self) -> None:
        self._scrolling = False
        if self._current_instance is None:
            return
        if self._players_missing_thumbnails(self._current_instance.players) > 0:
            self._schedule_thumbnail_enrich()

    def _is_scrolling(self) -> bool:
        return self._scrolling or self._scroll_thumb_debouncer.is_pending()

    def _scroll_viewport_margin_px(self) -> int:
        return _ROW_HEIGHT * _SCROLL_LOAD_MARGIN_ROWS

    def _players_missing_thumbnails(self, players: list[InstancePlayer]) -> int:
        return sum((1 for player in players if not player.thumbnail_url))

    def _enrich_batch_size(self) -> int:
        if self._current_instance is None:
            return _ENRICH_BATCH_MAX
        missing = self._players_missing_thumbnails(self._current_instance.players)
        cap = 5 if in_startup_window() else _ENRICH_BATCH_MAX
        if self._action_runner.is_running() or api_budget_saturated():
            cap = min(cap, 6)
        pressure = api_budget_pressure()
        if pressure > 0.65:
            cap = min(cap, max(3, int(cap * (1.0 - pressure * 0.5))))
        return min(max(missing, 1), cap)

    def _enrich_batch_interval_ms(self) -> int:
        return 1500 if in_startup_window() else _ENRICH_BATCH_INTERVAL_MS

    def _visible_user_ids(self) -> set[str]:
        rows = visible_widgets_in_layout(
            self.scroll,
            self.rows_layout,
            PlayerRow,
            margin_px=self._scroll_viewport_margin_px(),
        )
        return {row.player.user_id for row in rows if row.player.user_id}

    def _begin_thumbnail_enrich(self) -> None:
        if self._is_scrolling():
            self._scroll_idle_timer.start(_SCROLL_ENRICH_IDLE_MS)
            return
        if self._action_runner.is_running():
            self._enrich_timer.start(self._enrich_batch_interval_ms())
            return
        if self.session is None or self._current_instance is None:
            return
        if self._players_missing_thumbnails(self._current_instance.players) <= 0:
            return
        if self._enrich_worker and self._enrich_worker.isRunning():
            return
        delay = startup_delay_ms('player_api') if in_startup_window() else 0
        QTimer.singleShot(delay, self._start_thumbnail_enrich)

    def _schedule_thumbnail_enrich(self) -> None:
        if self._is_scrolling():
            self._scroll_idle_timer.start(_SCROLL_ENRICH_IDLE_MS)
            return
        if self._action_runner.is_running():
            self._enrich_timer.start(self._enrich_batch_interval_ms())
            return
        if self.session is None or self._current_instance is None:
            return
        if self._players_missing_thumbnails(self._current_instance.players) <= 0:
            return
        if self._enrich_worker and self._enrich_worker.isRunning():
            return
        self._enrich_timer.start(self._enrich_batch_interval_ms())

    def _start_thumbnail_enrich(self) -> None:
        if self._is_scrolling():
            self._scroll_idle_timer.start(_SCROLL_ENRICH_IDLE_MS)
            return
        if self._action_runner.is_running():
            self._enrich_timer.start(self._enrich_batch_interval_ms())
            return
        if self.session is None or self._current_instance is None:
            return
        if SessionManager.instance().is_relogin_active():
            return
        if self._players_missing_thumbnails(self._current_instance.players) <= 0:
            return
        if self._enrich_worker and self._enrich_worker.isRunning():
            return
        enrich_generation = self._enrich_generation
        self._enrich_worker = ThumbnailEnrichWorker(self.session, list(self._current_instance.players), enrich_generation, max_requests=self._enrich_batch_size(), priority_user_ids=self._visible_user_ids())
        self._enrich_worker.finished_ok.connect(self._on_enrich_loaded)
        self._enrich_worker.start()

    def _on_enrich_loaded(self, enrich_generation: int, players_obj: object) -> None:
        if enrich_generation != self._enrich_generation:
            return
        if not isinstance(players_obj, list) or self._current_instance is None:
            return
        players = [item for item in players_obj if isinstance(item, InstancePlayer)]
        if not players:
            return
        self._current_instance = replace(self._current_instance, players=players)
        self._update_row_players(players)
        if not self._action_runner.is_running():
            self._update_status_summary()
        if self._players_missing_thumbnails(players) > 0:
            self._schedule_thumbnail_enrich()

    def _prepare_instance_players(self, info: InstanceInfo) -> InstanceInfo:
        players = apply_cached_thumbnails(info.players)
        return replace(info, players=players)

    def _update_row_players(self, players: list[InstancePlayer]) -> None:
        by_id = {player.user_id: player for player in players}
        for index in range(self.rows_layout.count() - 1):
            widget = self.rows_layout.itemAt(index).widget()
            if not isinstance(widget, PlayerRow):
                continue
            widget.set_session(self.session)
            updated = by_id.get(widget.player.user_id)
            if updated is not None:
                widget.update_player(updated)
        self._scroll_thumb_debouncer.schedule()

    def _load_thumbnails_for_rows(self, rows: list[PlayerRow], *, max_loads: int = _SCROLL_LOAD_MAX_PER_TICK) -> None:
        loaded = 0
        for row in rows:
            if loaded >= max_loads:
                break
            if row.needs_thumbnail():
                row.load_thumbnail()
                loaded += 1

    def _load_visible_thumbnails(self) -> None:
        rows = visible_widgets_in_layout(
            self.scroll,
            self.rows_layout,
            PlayerRow,
            margin_px=self._scroll_viewport_margin_px(),
        )
        self._load_thumbnails_for_rows(rows)

    def _merge_instance_players(self, previous: InstanceInfo, incoming: InstanceInfo) -> InstanceInfo:
        prev_by_id = {player.user_id: player for player in previous.players}
        merged: list[InstancePlayer] = []
        for player in incoming.players:
            prior = prev_by_id.get(player.user_id)
            if prior is None:
                merged.append(player)
                continue
            thumb = player.thumbnail_url or prior.thumbnail_url
            trust = player.trust or prior.trust
            status = player.status or prior.status
            if thumb == player.thumbnail_url and trust == player.trust and status == player.status and (player.is_friend == prior.is_friend) and (player.avatar_id == prior.avatar_id):
                merged.append(player)
                continue
            merged.append(replace(player, thumbnail_url=thumb, trust=trust, status=status, is_friend=player.is_friend or prior.is_friend, avatar_id=player.avatar_id or prior.avatar_id))
        return replace(incoming, players=merged)

    def _apply_instance_info(self, info: InstanceInfo) -> None:
        if self._context_menu_open:
            self._deferred_instance_info = info
            return
        info = self._prepare_instance_players(info)
        previous = self._current_instance
        if previous is not None and self._same_player_roster(previous, info):
            info = self._merge_instance_players(previous, info)
            merged_name = info.world_name or previous.world_name
            if merged_name != info.world_name:
                info = replace(info, world_name=merged_name)
            self._current_instance = info
            self.instance_updated.emit(info)
            self._update_row_players(info.players)
            self._begin_thumbnail_enrich()
            return
        self._current_instance = info
        self.instance_updated.emit(info)
        self._stop_populate()
        self._stop_thumbnail_enrich()
        self._clear_rows()
        if not info.players:
            self.hint_label.setText('No players listed for this instance')
            reveal_content(self.scroll if self.scroll.isVisible() else None, self.hint_label, duration=220, pop=False)
            return
        self._pending_players = list(info.players)
        self._populate_index = 0
        self._populate_gen = self._populate_generation

        def _show_players() -> None:
            self._populate_next_batch()
            self._begin_thumbnail_enrich()

        if self.scroll.isVisible():
            _show_players()
        else:
            reveal_content(self.hint_label, self.scroll, duration=260, pop=True)
            QTimer.singleShot(120, _show_players)

    def _populate_next_batch(self) -> None:
        if self._populate_generation != self._populate_gen:
            return
        players = self._pending_players
        start = self._populate_index
        end = min(start + list_populate_batch(normal=_POPULATE_BATCH), len(players))
        new_rows: list[PlayerRow] = []
        for player in players[start:end]:
            row = PlayerRow(player, self.rows_host, session=self.session, list_bar=self)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
            new_rows.append(row)
        if should_animate_lists() and new_rows:
            animate_list_items(new_rows, pop=True, duration=190, step_ms=18)

        def _load_batch_thumbs(rows: list[PlayerRow] = new_rows) -> None:
            for row in rows:
                if row.player.thumbnail_url:
                    row.load_thumbnail()

        QTimer.singleShot(0, _load_batch_thumbs)
        self._populate_index = end
        if end < len(players):
            delay = list_populate_delay_ms(animated=should_animate_lists())
            QTimer.singleShot(delay, self._populate_next_batch)
        else:
            self._pending_players = []
            QTimer.singleShot(0, self._load_visible_thumbnails)

    def reapply_cached_thumbnails(self) -> None:
        if self._current_instance is None:
            return
        players = apply_cached_thumbnails(self._current_instance.players)
        if not any((left.thumbnail_url != right.thumbnail_url for left, right in zip(self._current_instance.players, players))):
            return
        self._current_instance = replace(self._current_instance, players=players)
        self._update_row_players(players)
        self._begin_thumbnail_enrich()

    def cleanup(self) -> None:
        self._action_runner.cleanup()
        self._stop_populate()
        self._stop_thumbnail_enrich()
        self._poll_timer.stop()
        self._log_poll_timer.stop()
        self._refresh_timeout.stop()
        if self._enrich_worker and self._enrich_worker.isRunning():
            self._enrich_worker.wait(2000)
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        if self._api_worker and self._api_worker.isRunning():
            self._api_worker.wait(2000)
        if self._fallback_worker and self._fallback_worker.isRunning():
            self._fallback_worker.wait(2000)
        if self._quick_log_worker and self._quick_log_worker.isRunning():
            self._quick_log_worker.wait(1000)
