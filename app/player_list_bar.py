from __future__ import annotations
import webbrowser
from dataclasses import replace
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from .api_action_worker import ApiActionWorker
from .image_loader import RemoteImageLabel
from .logging_setup import get_logger
from .services.session_manager import SessionManager
from .theme import dark_theme, themed_menu
from .vrchat_api import InstanceInfo, InstancePlayer, apply_cached_thumbnails, close_instance, enrich_instance_players, force_clone_player_avatar, get_current_instance, get_current_instance_log_only, moderate_player, thumbnail_url_for_size, unmoderate_player, unfriend_user, user_profile_url
from .vrchat_auth import VRChatSession
logger = get_logger('player_list')
_ROW_AVATAR = 28
_ROW_HEIGHT = 34
_THUMB_SIZE = 64
_WORLD_HINT = 'Join a VRChat world to see players'
_REFRESH_TIMEOUT_MS = 20000
_LOG_ENRICH_REQUESTS = 0
_FAST_ENRICH_REQUESTS = 0
_ENRICH_BATCH_MAX = 40
_ENRICH_BATCH_INTERVAL_MS = 350
_POPULATE_BATCH = 16
_POPULATE_INTERVAL_MS = 16
_FORCE_CLONE_TIMEOUT_SEC = 25.0
_SCROLL_LOAD_MARGIN_ROWS = 3
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
            self.finished_error.emit(self.generation, str(exc) or 'Failed to load players.')

class PlayerRow(QFrame):
    action_requested = pyqtSignal(str, str, str)

    def __init__(self, player: InstancePlayer, parent: QWidget | None=None):
        super().__init__(parent)
        self.player = player
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
        self.name_label = QLabel(player.display_name)
        self.name_label.setObjectName('playerName')
        self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.name_label, 1)
        meta = QHBoxLayout()
        meta.setSpacing(4)
        meta.setContentsMargins(0, 0, 0, 0)
        if player.trust is not None:
            trust_badge = QLabel(player.trust.short)
            trust_badge.setObjectName('trustBadge')
            trust_badge.setToolTip(player.trust.label)
            trust_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            trust_badge.setFixedHeight(16)
            trust_badge.setStyleSheet(f'color: {player.trust.color}; font-size: 7pt; font-weight: 700; border: 1px solid {player.trust.color}; border-radius: 4px; padding: 0 5px; background-color: rgba(255, 255, 255, 0.04);')
            meta.addWidget(trust_badge)
        if player.is_friend:
            friend_badge = QLabel('★')
            friend_badge.setObjectName('friendBadge')
            friend_badge.setToolTip('Friend')
            meta.addWidget(friend_badge)
        layout.addLayout(meta)
        if player.thumbnail_url:
            self.load_thumbnail()

    def update_player(self, player: InstancePlayer) -> None:
        previous_url = self.player.thumbnail_url
        if not player.thumbnail_url and previous_url:
            player = replace(player, thumbnail_url=previous_url)
        self.player = player
        self.is_friend = player.is_friend
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

    def _show_context_menu(self, pos) -> None:
        menu = themed_menu(self)
        clone = menu.addAction('Force Clone')
        clone.setToolTip('Wear their avatar even if cloning is disabled (uses VRChat log avatar ID)')
        clone.triggered.connect(lambda: self.action_requested.emit('force_clone', self.player.user_id, self.player.display_name))
        menu.addSeparator()
        block = menu.addAction('Block User')
        block.triggered.connect(lambda: self.action_requested.emit('block', self.player.user_id, ''))
        unblock = menu.addAction('Unblock User')
        unblock.triggered.connect(lambda: self.action_requested.emit('unblock', self.player.user_id, ''))
        hide = menu.addAction('Hide Avatar')
        hide.triggered.connect(lambda: self.action_requested.emit('hide_avatar', self.player.user_id, ''))
        unhide = menu.addAction('Unhide Avatar')
        unhide.triggered.connect(lambda: self.action_requested.emit('unhide_avatar', self.player.user_id, ''))
        mute = menu.addAction('Mute User')
        mute.triggered.connect(lambda: self.action_requested.emit('mute', self.player.user_id, ''))
        unmute = menu.addAction('Unmute User')
        unmute.triggered.connect(lambda: self.action_requested.emit('unmute', self.player.user_id, ''))
        if self.is_friend:
            menu.addSeparator()
            unfriend = menu.addAction('Unfriend')
            unfriend.triggered.connect(lambda: self.action_requested.emit('unfriend', self.player.user_id, ''))
        menu.addSeparator()
        profile = menu.addAction('Open VRChat Profile')
        profile.triggered.connect(lambda: self.action_requested.emit('open_profile', self.player.user_id, ''))
        copy_id = menu.addAction('Copy User ID')
        copy_id.triggered.connect(lambda: self.action_requested.emit('copy_id', self.player.user_id, ''))
        menu.exec(self.mapToGlobal(pos))

class PlayerListBar(QWidget):
    instance_updated = pyqtSignal(object)

    def __init__(self, session: VRChatSession | None=None, parent: QWidget | None=None):
        super().__init__(parent)
        self.session = session
        self._worker: LogPlayersWorker | None = None
        self._api_worker: ApiInstanceWorker | None = None
        self._fallback_worker: LogPlayersWorker | None = None
        self._action_worker: ApiActionWorker | None = None
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
        self.setFixedHeight(PLAYER_LIST_HEIGHT)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(20000)
        self._poll_timer.timeout.connect(self.refresh)
        self._refresh_timeout = QTimer(self)
        self._refresh_timeout.setSingleShot(True)
        self._refresh_timeout.timeout.connect(self._on_refresh_timeout)
        self._build_ui()
        self._show_world_hint(animate=False)
        if self.session is not None:
            RemoteImageLabel.set_session(self.session)
            self._poll_timer.start()
            QTimer.singleShot(2000, self.refresh)

    def set_session(self, session: VRChatSession | None) -> None:
        self.session = session
        self._refresh_generation += 1
        if self._worker and self._worker.isRunning():
            self._worker.wait(1500)
        if session is None:
            self._poll_timer.stop()
            self._current_instance = None
            self._show_world_hint(animate=False)
            return
        RemoteImageLabel.set_session(session)
        RemoteImageLabel.clear_failed_urls()
        if not self._poll_timer.isActive():
            self._poll_timer.start()
        QTimer.singleShot(2000, self.refresh)

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
        header_row.addWidget(title)
        header_row.addStretch(1)
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
        self.scroll.verticalScrollBar().valueChanged.connect(self._load_visible_thumbnails)
        frame_layout.addWidget(self.list_box, 1)
        root.addWidget(self.frame)
        self.setStyleSheet(dark_theme('\n            QLabel#previewHeader {\n                font-weight: bold;\n                font-size: 9pt;\n            }\n            QFrame#playerListBox {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n            }\n            QLabel#playerHint {\n                color: #6a7a6a;\n                font-size: 8pt;\n                padding: 12px 8px;\n            }\n            QScrollArea {\n                background: transparent;\n                border: none;\n            }\n            QFrame#playerRow {\n                background-color: transparent;\n                border: none;\n                border-bottom: 1px solid #2e2e2e;\n            }\n            QFrame#playerRow:hover {\n                background-color: rgba(78, 163, 255, 0.08);\n            }\n            QLabel#playerName {\n                color: #f0f0f0;\n                font-size: 8pt;\n            }\n            QLabel#friendBadge {\n                color: #4ea3ff;\n                font-size: 8pt;\n            }\n        '))

    def _set_world_label(self, text: str, *, is_error: bool=False) -> None:
        _ = (text, is_error)

    def _show_instance_menu(self, pos) -> None:
        if self._current_instance is None or not self._current_instance.can_close_instance:
            return
        menu = themed_menu(self)
        close_action = menu.addAction('Force Close Instance')
        close_action.setToolTip('Hard-close this instance (instance owner only, like VRCX)')
        close_action.triggered.connect(self._force_close_instance)
        menu.exec(self.frame.mapToGlobal(pos))

    def _force_close_instance(self) -> None:
        info = self._current_instance
        if info is None or self.session is None or (not info.can_close_instance):
            return
        if self._action_worker and self._action_worker.isRunning():
            return
        self._set_world_label('Closing instance…')
        self._action_worker = ApiActionWorker(lambda: close_instance(self.session, info.world_id, info.instance_id, hard_close=True), 'Instance force-closed.')
        self._action_worker.finished_ok.connect(self._on_action_ok)
        self._action_worker.finished_error.connect(self._on_action_error)
        self._action_worker.start()

    def _on_player_action(self, action: str, user_id: str, display_name: str) -> None:
        if action == 'copy_id':
            QApplication.clipboard().setText(user_id)
            self._set_world_label('User ID copied.')
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
            avatar_id = None
            if self._current_instance is not None:
                for player in self._current_instance.players:
                    if player.user_id == user_id:
                        avatar_id = player.avatar_id
                        display_name = display_name or player.display_name
                        break
            self._set_world_label('Force cloning avatar…')
            self._action_worker = ApiActionWorker(lambda: force_clone_player_avatar(self.session, user_id, display_name=display_name or None, avatar_id=avatar_id), 'Avatar selected — switch applies in VRChat.', timeout_sec=_FORCE_CLONE_TIMEOUT_SEC)
            self._action_worker.finished_ok.connect(self._on_action_ok)
            self._action_worker.finished_error.connect(self._on_action_error)
            self._action_worker.start()
            return
        mod_type = {'block': 'block', 'unblock': 'block', 'hide_avatar': 'hideAvatar', 'unhide_avatar': 'hideAvatar', 'mute': 'mute', 'unmute': 'mute'}.get(action)
        if mod_type is None and action != 'unfriend':
            return
        if action == 'unfriend':
            self._set_world_label('Removing friend…')
            self._action_worker = ApiActionWorker(lambda: unfriend_user(self.session, user_id), 'Removed friend.')
            self._action_worker.finished_ok.connect(self._on_action_ok)
            self._action_worker.finished_error.connect(self._on_action_error)
            self._action_worker.start()
            return
        is_undo = action.startswith('un')
        self._set_world_label(f"{('Removing' if is_undo else 'Applying')} {mod_type}…")
        fn = unmoderate_player if is_undo else moderate_player
        self._action_worker = ApiActionWorker(lambda: fn(self.session, user_id, mod_type), f"{('Removed' if is_undo else 'Applied')} {mod_type}.")
        self._action_worker.finished_ok.connect(self._on_action_ok)
        self._action_worker.finished_error.connect(self._on_action_error)
        self._action_worker.start()

    def _on_action_ok(self, message: str) -> None:
        if self._current_instance is not None:
            self._set_world_label(self._current_instance.world_name)
        self.refresh()

    def _on_action_error(self, message: str) -> None:
        if SessionManager.instance().try_handle_auth_failure(message):
            return
        if self._current_instance is not None:
            self._set_world_label(f'Error: {message}', is_error=True)
        else:
            self._set_world_label(message, is_error=True)

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
        self.scroll.hide()
        self.hint_label.setText(_WORLD_HINT)
        self.hint_label.show()
        self._set_world_label('')
        if animate:
            self.hint_label.show()

    def refresh(self) -> None:
        if SessionManager.instance().is_relogin_active():
            return
        if self.session is None:
            self._show_world_hint()
            return
        if self._worker and self._worker.isRunning():
            return
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._stop_populate()
        RemoteImageLabel.clear_failed_urls()
        if self._current_instance is None:
            self._set_world_label('Checking…')
        self._refresh_timeout.start(_REFRESH_TIMEOUT_MS)
        self._worker = LogPlayersWorker(self.session, generation, enrich=False)
        self._worker.finished_ok.connect(self._on_log_loaded)
        self._worker.start()
        self._start_api_enrich(generation)

    def _finish_checking_state(self) -> None:
        self._refresh_timeout.stop()

    def _start_api_enrich(self, generation: int) -> None:
        if generation != self._refresh_generation or self.session is None:
            return
        if self._api_worker and self._api_worker.isRunning():
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
            return
        if self._current_instance is None:
            self._show_world_hint()
            self._set_world_label('Could not read VRChat log', is_error=True)
            self._finish_checking_state()

    def _on_api_loaded(self, generation: int, info: object) -> None:
        if generation != self._refresh_generation:
            return
        if isinstance(info, InstanceInfo):
            self._apply_instance_info(info)
            self._finish_checking_state()
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
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.rows_layout.addStretch(1)

    def _on_error(self, generation: int, message: str) -> None:
        if generation != self._refresh_generation:
            return
        logger.debug('Player list error: %s', message)
        if self._fallback_worker and self._fallback_worker.isRunning():
            return
        if self._current_instance is not None:
            self._finish_checking_state()
            return
        self._start_log_only_fallback()

    def _same_player_roster(self, left: InstanceInfo, right: InstanceInfo) -> bool:
        return [player.user_id for player in left.players] == [player.user_id for player in right.players]

    def _players_missing_thumbnails(self, players: list[InstancePlayer]) -> int:
        return sum((1 for player in players if not player.thumbnail_url))

    def _enrich_batch_size(self) -> int:
        if self._current_instance is None:
            return _ENRICH_BATCH_MAX
        missing = self._players_missing_thumbnails(self._current_instance.players)
        return min(max(missing, 1), _ENRICH_BATCH_MAX)

    def _visible_user_ids(self) -> set[str]:
        if not self.scroll.isVisible():
            return set()
        viewport = self.scroll.viewport()
        if viewport is None:
            return set()
        margin = _ROW_HEIGHT * _SCROLL_LOAD_MARGIN_ROWS
        view_height = viewport.height()
        visible: set[str] = set()
        for index in range(self.rows_layout.count() - 1):
            widget = self.rows_layout.itemAt(index).widget()
            if not isinstance(widget, PlayerRow):
                continue
            row_top = widget.mapTo(viewport, widget.rect().topLeft()).y()
            row_bottom = row_top + widget.height()
            if row_bottom < -margin or row_top > view_height + margin:
                continue
            if widget.player.user_id:
                visible.add(widget.player.user_id)
        return visible

    def _begin_thumbnail_enrich(self) -> None:
        if self.session is None or self._current_instance is None:
            return
        if self._players_missing_thumbnails(self._current_instance.players) <= 0:
            return
        if self._enrich_worker and self._enrich_worker.isRunning():
            return
        QTimer.singleShot(0, self._start_thumbnail_enrich)

    def _schedule_thumbnail_enrich(self) -> None:
        if self.session is None or self._current_instance is None:
            return
        if self._players_missing_thumbnails(self._current_instance.players) <= 0:
            return
        if self._enrich_worker and self._enrich_worker.isRunning():
            return
        self._enrich_timer.start(_ENRICH_BATCH_INTERVAL_MS)

    def _start_thumbnail_enrich(self) -> None:
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
            updated = by_id.get(widget.player.user_id)
            if updated is not None:
                widget.update_player(updated)
        self._load_pending_thumbnails()

    def _load_pending_thumbnails(self) -> None:
        for index in range(self.rows_layout.count() - 1):
            widget = self.rows_layout.itemAt(index).widget()
            if not isinstance(widget, PlayerRow):
                continue
            if widget.needs_thumbnail():
                widget.load_thumbnail()

    def _load_visible_thumbnails(self) -> None:
        self._load_pending_thumbnails()

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
            if thumb == player.thumbnail_url and trust == player.trust and (player.is_friend == prior.is_friend) and (player.avatar_id == prior.avatar_id):
                merged.append(player)
                continue
            merged.append(replace(player, thumbnail_url=thumb, trust=trust, is_friend=player.is_friend or prior.is_friend, avatar_id=player.avatar_id or prior.avatar_id))
        return replace(incoming, players=merged)

    def _apply_instance_info(self, info: InstanceInfo) -> None:
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
            self.scroll.hide()
            self.hint_label.show()
            return
        self.hint_label.hide()
        self.scroll.show()
        self._pending_players = list(info.players)
        self._populate_index = 0
        self._populate_gen = self._populate_generation
        self._populate_next_batch()
        self._begin_thumbnail_enrich()

    def _on_loaded(self, generation: int, info: object) -> None:
        self._on_api_loaded(generation, info)

    def _populate_next_batch(self) -> None:
        if self._populate_generation != self._populate_gen:
            return
        players = self._pending_players
        start = self._populate_index
        end = min(start + _POPULATE_BATCH, len(players))
        for player in players[start:end]:
            row = PlayerRow(player, self.rows_host)
            row.action_requested.connect(self._on_player_action)
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
            if player.thumbnail_url:
                row.load_thumbnail()
        self._populate_index = end
        self._load_visible_thumbnails()
        if end < len(players):
            QTimer.singleShot(_POPULATE_INTERVAL_MS, self._populate_next_batch)
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
        self._stop_populate()
        self._poll_timer.stop()
        self._refresh_timeout.stop()
        if self._enrich_worker and self._enrich_worker.isRunning():
            self._enrich_worker.wait(2000)
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        if self._api_worker and self._api_worker.isRunning():
            self._api_worker.wait(2000)
        if self._fallback_worker and self._fallback_worker.isRunning():
            self._fallback_worker.wait(2000)
        if self._action_worker and self._action_worker.isRunning():
            self._action_worker.wait(2000)
