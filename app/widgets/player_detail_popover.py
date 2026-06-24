from __future__ import annotations

import webbrowser
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from ..avatar_search_providers import PERFORMANCE_COLORS
from ..image_loader import RemoteImageLabel
from ..status_indicator import StatusIndicator
from ..theme import dark_theme
from ..vrchat.models import InstancePlayer
from ..vrchat_log_players import lookup_player_avatar_info
from ..vrchat_api import preview_force_clone_player, resolve_known_avatar_id, user_profile_url
from ..ui_animations import fade_out_widget, slide_fade_in_widget
from ..vrchat_auth import VRChatSession


class _DetailResolveWorker(QThread):
    finished_ok = pyqtSignal(str, object, object)

    def __init__(
        self,
        session: VRChatSession | None,
        player: InstancePlayer,
    ) -> None:
        super().__init__()
        self._session = session
        self._player = player

    def run(self) -> None:
        player = self._player
        resolved = resolve_known_avatar_id(
            player.user_id,
            player.display_name,
            avatar_id=player.avatar_id,
            session=self._session,
        ) or ''
        live_info = lookup_player_avatar_info(player.user_id, player.display_name)
        preview = preview_force_clone_player(
            self._session,
            player.user_id,
            display_name=player.display_name,
            avatar_id=player.avatar_id,
            status=player.status,
        )
        self.finished_ok.emit(resolved, live_info, preview)


class PlayerDetailPopover(QFrame):

    def __init__(self, parent: QWidget | None=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName('playerDetailPopover')
        self._session: VRChatSession | None = None
        self._player: InstancePlayer | None = None
        self._resolve_worker: _DetailResolveWorker | None = None
        self._resolve_generation = 0
        self._resolved_avatar_id: str | None = None
        self._action_callback = None
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)
        top = QHBoxLayout()
        self.thumb = RemoteImageLabel(48, self)
        top.addWidget(self.thumb)
        col = QVBoxLayout()
        self.name_label = QLabel('')
        self.name_label.setObjectName('playerName')
        col.addWidget(self.name_label)
        self.status_indicator = StatusIndicator(self, dot_size=6, compact=True)
        col.addWidget(self.status_indicator)
        top.addLayout(col, 1)
        root.addLayout(top)
        self.trust_label = QLabel('')
        self.trust_label.setObjectName('playerMeta')
        root.addWidget(self.trust_label)
        self.avatar_label = QLabel('')
        self.avatar_label.setObjectName('playerMeta')
        self.avatar_label.setWordWrap(True)
        avatar_row = QHBoxLayout()
        avatar_row.setContentsMargins(0, 0, 0, 0)
        avatar_row.setSpacing(4)
        avatar_row.addWidget(self.avatar_label, 1)
        self.copy_avatar_btn = QPushButton('Copy')
        self.copy_avatar_btn.setFixedHeight(20)
        self.copy_avatar_btn.setToolTip('Copy avatar ID')
        self.copy_avatar_btn.clicked.connect(self._copy_avatar_id)
        avatar_row.addWidget(self.copy_avatar_btn)
        root.addLayout(avatar_row)
        self.perf_label = QLabel('')
        root.addWidget(self.perf_label)
        btn_row = QHBoxLayout()
        self.clone_btn = QPushButton('Force Clone')
        self.clone_btn.clicked.connect(lambda: self._emit('force_clone'))
        btn_row.addWidget(self.clone_btn)
        profile_btn = QPushButton('Profile')
        profile_btn.clicked.connect(self._open_profile)
        btn_row.addWidget(profile_btn)
        root.addLayout(btn_row)
        self.setStyleSheet(dark_theme('\n            QFrame#playerDetailPopover { background-color: #1e1e1e; border: 1px solid #4ea3ff; border-radius: 8px; }\n            QLabel#playerName { font-weight: 600; color: #f0f0f0; }\n            QLabel#playerMeta { color: #9aa0a6; font-size: 8pt; }\n        '))
        self.setFixedWidth(260)

    def bind_actions(self, callback) -> None:
        self._action_callback = callback

    def show_player(self, player: InstancePlayer, *, session: VRChatSession | None, global_pos) -> None:
        self._resolve_generation += 1
        generation = self._resolve_generation
        if self._resolve_worker and self._resolve_worker.isRunning():
            self._resolve_worker.wait(200)
        self._player = player
        self._session = session
        self.name_label.setText(player.display_name)
        if player.status is not None:
            self.status_indicator.apply(player.status)
            self.status_indicator.show()
        else:
            self.status_indicator.hide()
        trust_text = player.trust.label if player.trust else 'Unknown trust'
        self.trust_label.setText(f'Trust: {trust_text}')
        avatar_display = player.avatar_id or 'Resolving…'
        self.avatar_label.setText(f'Avatar: {avatar_display}')
        self._resolved_avatar_id = player.avatar_id
        self.copy_avatar_btn.setEnabled(True)
        self.copy_avatar_btn.setToolTip('Resolve current avatar ID from log')
        if player.avatar_performance:
            color = PERFORMANCE_COLORS.get(player.avatar_performance, '#888')
            self.perf_label.setText(f'Performance: {player.avatar_performance}')
            self.perf_label.setStyleSheet(f'color: {color}; font-size: 8pt; font-weight: 600;')
            self.perf_label.show()
        else:
            self.perf_label.hide()
        if player.thumbnail_url:
            self.thumb.load(player.thumbnail_url)
        else:
            self.thumb.setText('?')
        self.clone_btn.setEnabled(False)
        self.clone_btn.setToolTip('Checking clone availability…')
        self.adjustSize()
        self.move(global_pos)
        self.show()
        self.raise_()
        slide_fade_in_widget(self, offset_y=10, duration=280)
        self._resolve_worker = _DetailResolveWorker(session, player)
        self._resolve_worker.finished_ok.connect(
            lambda resolved, live_info, preview, gen=generation: self._on_resolved(gen, resolved, live_info, preview)
        )
        self._resolve_worker.start()

    def _on_resolved(self, generation: int, resolved: str, live_info: object, preview: object) -> None:
        if generation != self._resolve_generation or self._player is None:
            return
        player = self._player
        self._resolved_avatar_id = resolved or None
        if resolved:
            avatar_display = resolved
        elif getattr(live_info, 'avatar_name', None):
            avatar_display = f'{live_info.avatar_name} (use Copy to resolve)'
        else:
            avatar_display = player.avatar_id or 'Unknown'
        self.avatar_label.setText(f'Avatar: {avatar_display}')
        self.copy_avatar_btn.setToolTip(resolved or 'Resolve current avatar ID from log')
        available = bool(getattr(preview, 'available', False))
        message = str(getattr(preview, 'message', '') or '')
        self.clone_btn.setEnabled(available)
        self.clone_btn.setToolTip(message)

    def _emit(self, action: str) -> None:
        if self._player is None or self._action_callback is None:
            return
        player = self._player

        def _done() -> None:
            self._action_callback(action, player.user_id, player.display_name)
        fade_out_widget(self, duration=160, hide_after=True, on_finished=_done)

    def _copy_avatar_id(self) -> None:
        if self._player is None:
            return
        avatar_id = self._resolved_avatar_id
        if not avatar_id:
            self.copy_avatar_btn.setText('…')
            self._resolve_worker = _DetailResolveWorker(self._session, self._player)
            generation = self._resolve_generation

            def _on_copy_resolve(resolved: str, _live_info: object, _preview: object, gen=generation) -> None:
                if gen != self._resolve_generation:
                    return
                if resolved:
                    self._resolved_avatar_id = resolved
                    self._apply_copied_avatar(resolved)
                else:
                    self.copy_avatar_btn.setText('N/A')
                    QTimer.singleShot(1200, lambda: self.copy_avatar_btn.setText('Copy'))

            self._resolve_worker.finished_ok.connect(_on_copy_resolve)
            self._resolve_worker.start()
            return
        self._apply_copied_avatar(avatar_id)

    def _apply_copied_avatar(self, avatar_id: str) -> None:
        QApplication.clipboard().setText(avatar_id)
        self.avatar_label.setText(f'Avatar: {avatar_id}')
        self.copy_avatar_btn.setText('Copied')
        QTimer.singleShot(1200, lambda: self.copy_avatar_btn.setText('Copy'))

    def _open_profile(self) -> None:
        if self._player is None:
            return
        webbrowser.open(user_profile_url(self._player.user_id))
        self.hide()

    @staticmethod
    def dismiss_later(popover: 'PlayerDetailPopover', ms: int=100) -> None:
        QTimer.singleShot(ms, popover.hide)
