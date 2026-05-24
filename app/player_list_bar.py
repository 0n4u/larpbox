from __future__ import annotations

import webbrowser

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .api_action_worker import ApiActionWorker
from .image_loader import RemoteImageLabel
from .logging_setup import get_logger
from .theme import dark_theme, themed_menu
from .ui_animations import stop_pulse
from .vrchat_api import (
    InstanceInfo,
    InstancePlayer,
    close_instance,
    force_clone_player_avatar,
    get_current_instance,
    get_current_instance_log_only,
    moderate_player,
    user_profile_url,
)
from .vrchat_auth import VRChatSession

logger = get_logger('player_list')

_ROW_AVATAR = 28
_ROW_HEIGHT = 34
_WORLD_HINT = 'Join a VRChat world to see players'
_REFRESH_TIMEOUT_MS = 12000
_FAST_ENRICH_REQUESTS = 4
_POPULATE_BATCH = 12
_POPULATE_INTERVAL_MS = 24
_API_ENRICH_DELAY_MS = 400

PLAYER_LIST_HEIGHT = 200


class LogPlayersWorker(QThread):
    finished_ok = pyqtSignal(int, object)

    def __init__(self, session: VRChatSession | None, generation: int):
        super().__init__()
        self.session = session
        self.generation = generation

    def run(self) -> None:
        try:
            self.finished_ok.emit(
                self.generation,
                get_current_instance_log_only(self.session, enrich=False),
            )
        except Exception:
            logger.warning('Log player fetch failed', exc_info=True)
            self.finished_ok.emit(self.generation, None)


class ApiInstanceWorker(QThread):
    finished_ok = pyqtSignal(int, object)
    finished_error = pyqtSignal(int, str)

    def __init__(self, session: VRChatSession, generation: int):
        super().__init__()
        self.session = session
        self.generation = generation

    def run(self) -> None:
        try:
            info = get_current_instance(
                self.session,
                max_enrich_requests=_FAST_ENRICH_REQUESTS,
                friend_players_cache_only=True,
            )
            self.finished_ok.emit(self.generation, info)
        except Exception as exc:
            logger.warning('Instance API fetch failed', exc_info=True)
            self.finished_error.emit(self.generation, str(exc) or 'Failed to load players.')


class PlayerRow(QFrame):
    action_requested = pyqtSignal(str, str, str)

    def __init__(self, player: InstancePlayer, parent: QWidget | None = None):
        super().__init__(parent)
        self.player = player
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
            trust_badge.setStyleSheet(
                f'color: {player.trust.color}; font-size: 7pt; font-weight: 700; '
                f'border: 1px solid {player.trust.color}; border-radius: 4px; '
                f'padding: 0 5px; background-color: rgba(255, 255, 255, 0.04);'
            )
            meta.addWidget(trust_badge)

        if player.is_friend:
            friend_badge = QLabel('★')
            friend_badge.setObjectName('friendBadge')
            friend_badge.setToolTip('Friend')
            meta.addWidget(friend_badge)

        layout.addLayout(meta)

    def load_thumbnail(self) -> None:
        self.thumb.load(self.player.thumbnail_url)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        metrics = QFontMetrics(self.name_label.font())
        available = max(40, self.name_label.width())
        elided = metrics.elidedText(
            self.player.display_name,
            Qt.TextElideMode.ElideRight,
            available,
        )
        self.name_label.setText(elided)
        self.name_label.setToolTip(
            self.player.display_name if elided != self.player.display_name else ''
        )

    def _show_context_menu(self, pos) -> None:
        menu = themed_menu(self)
        clone = menu.addAction('Force Clone')
        clone.setToolTip('Wear their avatar even if cloning is disabled (uses VRChat log avatar ID)')
        clone.triggered.connect(
            lambda: self.action_requested.emit('force_clone', self.player.user_id, self.player.display_name)
        )
        menu.addSeparator()
        block = menu.addAction('Block User')
        block.triggered.connect(lambda: self.action_requested.emit('block', self.player.user_id, ''))
        hide = menu.addAction('Hide Avatar')
        hide.triggered.connect(lambda: self.action_requested.emit('hide_avatar', self.player.user_id, ''))
        mute = menu.addAction('Mute User')
        mute.triggered.connect(lambda: self.action_requested.emit('mute', self.player.user_id, ''))
        menu.addSeparator()
        profile = menu.addAction('Open VRChat Profile')
        profile.triggered.connect(lambda: self.action_requested.emit('open_profile', self.player.user_id, ''))
        copy_id = menu.addAction('Copy User ID')
        copy_id.triggered.connect(lambda: self.action_requested.emit('copy_id', self.player.user_id, ''))
        menu.exec(self.mapToGlobal(pos))


class PlayerListBar(QWidget):
    def __init__(self, session: VRChatSession | None = None, parent: QWidget | None = None):
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
            QTimer.singleShot(800, self.refresh)

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
        self.count_label = QLabel('')
        self.count_label.setObjectName('playerCountBadge')
        header_row.addWidget(self.count_label)
        frame_layout.addLayout(header_row)

        self.world_label = QLabel('')
        self.world_label.setObjectName('worldLabel')
        self.world_label.setWordWrap(True)
        self.world_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        frame_layout.addWidget(self.world_label)

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

        frame_layout.addWidget(self.list_box, 1)
        root.addWidget(self.frame)

        self.setStyleSheet(dark_theme("""
            QLabel#previewHeader {
                font-weight: bold;
                font-size: 9pt;
            }
            QLabel#worldLabel {
                color: #7a8a7a;
                font-size: 8pt;
            }
            QLabel#worldLabelError {
                color: #e07070;
                font-size: 7pt;
            }
            QLabel#playerCountBadge {
                color: #4ea3ff;
                font-size: 8pt;
                font-weight: 600;
                border: 1px solid #355a80;
                border-radius: 8px;
                padding: 1px 7px;
                background-color: rgba(78, 163, 255, 0.08);
            }
            QFrame#playerListBox {
                background-color: #232323;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
            }
            QLabel#playerHint {
                color: #6a7a6a;
                font-size: 8pt;
                padding: 12px 8px;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QFrame#playerRow {
                background-color: transparent;
                border: none;
                border-bottom: 1px solid #2e2e2e;
            }
            QFrame#playerRow:hover {
                background-color: rgba(78, 163, 255, 0.08);
            }
            QLabel#playerName {
                color: #f0f0f0;
                font-size: 8pt;
            }
            QLabel#friendBadge {
                color: #4ea3ff;
                font-size: 8pt;
            }
        """))

    def _set_world_label(self, text: str, *, is_error: bool = False) -> None:
        self.world_label.setText(text)
        self.world_label.setToolTip(text if len(text) > 48 else '')
        self.world_label.setObjectName('worldLabelError' if is_error else 'worldLabel')
        self.world_label.style().unpolish(self.world_label)
        self.world_label.style().polish(self.world_label)

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
        if info is None or self.session is None or not info.can_close_instance:
            return
        if self._action_worker and self._action_worker.isRunning():
            return
        self._set_world_label('Closing instance…')
        self._action_worker = ApiActionWorker(
            lambda: close_instance(
                self.session,
                info.world_id,
                info.instance_id,
                hard_close=True,
            ),
            'Instance force-closed.',
        )
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
            self._action_worker = ApiActionWorker(
                lambda: force_clone_player_avatar(
                    self.session,
                    user_id,
                    display_name=display_name or None,
                    avatar_id=avatar_id,
                ),
                'Avatar selected — switch applies in VRChat.',
            )
            self._action_worker.finished_ok.connect(self._on_action_ok)
            self._action_worker.finished_error.connect(self._on_action_error)
            self._action_worker.start()
            return
        mod_type = {
            'block': 'block',
            'hide_avatar': 'hideAvatar',
            'mute': 'mute',
        }.get(action)
        if mod_type is None:
            return
        self._set_world_label(f'Applying {mod_type}…')
        self._action_worker = ApiActionWorker(
            lambda: moderate_player(self.session, user_id, mod_type),
            f'Applied {mod_type}.',
        )
        self._action_worker.finished_ok.connect(self._on_action_ok)
        self._action_worker.finished_error.connect(self._on_action_error)
        self._action_worker.start()

    def _on_action_ok(self, message: str) -> None:
        if self._current_instance is not None:
            self._set_world_label(self._current_instance.world_name)
        self.refresh()

    def _on_action_error(self, message: str) -> None:
        if self._current_instance is not None:
            self._set_world_label(f'Error: {message}', is_error=True)
        else:
            self._set_world_label(message, is_error=True)

    def _stop_populate(self) -> None:
        self._populate_generation += 1
        self._pending_players = []
        self._populate_index = 0

    def _show_world_hint(self, *, animate: bool = True) -> None:
        self._stop_populate()
        stop_pulse(self.world_label)
        self._current_instance = None
        self._clear_rows()
        self.scroll.hide()
        self.hint_label.setText(_WORLD_HINT)
        self.hint_label.show()
        self._set_world_label('')
        self.count_label.setText('')
        if animate:
            self.hint_label.show()

    def refresh(self) -> None:
        if self.session is None:
            self._show_world_hint()
            return
        if self._worker and self._worker.isRunning():
            return
        if self._api_worker and self._api_worker.isRunning():
            return
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._stop_populate()
        self._set_world_label('Checking…')
        self._refresh_timeout.start(_REFRESH_TIMEOUT_MS)
        self._worker = LogPlayersWorker(self.session, generation)
        self._worker.finished_ok.connect(self._on_log_loaded)
        self._worker.start()

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
        QTimer.singleShot(_API_ENRICH_DELAY_MS, lambda: self._start_api_enrich(generation))

    def _on_api_loaded(self, generation: int, info: object) -> None:
        if generation != self._refresh_generation:
            return
        self._refresh_timeout.stop()
        stop_pulse(self.world_label)
        if isinstance(info, InstanceInfo):
            self._apply_instance_info(info)
            return
        if self._current_instance is None:
            self._show_world_hint()

    def _start_log_only_fallback(self) -> None:
        if self.session is None:
            return
        if self._fallback_worker and self._fallback_worker.isRunning():
            return
        self._fallback_worker = LogPlayersWorker(self.session, self._refresh_generation)
        self._fallback_worker.finished_ok.connect(self._on_log_only_loaded)
        self._fallback_worker.start()

    def _on_log_only_loaded(self, generation: int, fallback: object) -> None:
        if generation != self._refresh_generation:
            return
        self._refresh_timeout.stop()
        if isinstance(fallback, InstanceInfo):
            self._apply_instance_info(fallback)
            return
        self._show_world_hint()
        self._set_world_label('Could not load players from log', is_error=True)

    def _on_refresh_timeout(self) -> None:
        if not (self._worker and self._worker.isRunning()):
            return
        if self._current_instance is not None:
            logger.info('Player list API refresh timed out — keeping displayed players')
            self._refresh_timeout.stop()
            stop_pulse(self.world_label)
            return
        logger.warning('Player list refresh timed out — showing log-only players')
        self._start_log_only_fallback()

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
        self._refresh_timeout.stop()
        logger.debug('Player list error: %s', message)
        if self._fallback_worker and self._fallback_worker.isRunning():
            return
        if self._current_instance is not None:
            stop_pulse(self.world_label)
            return
        self._start_log_only_fallback()

    def _same_player_roster(self, left: InstanceInfo, right: InstanceInfo) -> bool:
        return [player.user_id for player in left.players] == [player.user_id for player in right.players]

    def _apply_instance_info(self, info: InstanceInfo) -> None:
        previous = self._current_instance
        if previous is not None and self._same_player_roster(previous, info):
            self._current_instance = info
            self._set_world_label(info.world_name)
            self.count_label.setText(str(info.player_count))
            stop_pulse(self.world_label)
            return

        self._current_instance = info
        self._stop_populate()
        self._clear_rows()
        self._set_world_label(info.world_name)
        self.count_label.setText(str(info.player_count))
        stop_pulse(self.world_label)

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
            row.load_thumbnail()
        self._populate_index = end
        if end < len(players):
            QTimer.singleShot(_POPULATE_INTERVAL_MS, self._populate_next_batch)
        else:
            self._pending_players = []

    def cleanup(self) -> None:
        self._stop_populate()
        self._poll_timer.stop()
        self._refresh_timeout.stop()
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        if self._api_worker and self._api_worker.isRunning():
            self._api_worker.wait(2000)
        if self._fallback_worker and self._fallback_worker.isRunning():
            self._fallback_worker.wait(2000)
        if self._action_worker and self._action_worker.isRunning():
            self._action_worker.wait(2000)
