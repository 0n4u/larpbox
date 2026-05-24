from __future__ import annotations

import webbrowser

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .api_action_worker import ApiActionWorker
from .avatar_search_providers import (
    default_filter_for,
    filter_label,
    get_provider,
    search_with_provider,
)
from .config import save_config
from .image_loader import RemoteImageLabel
from .logging_setup import get_logger, is_debug_mode
from .theme import dark_theme, themed_menu
from .ui_animations import pop_in_widget, pulse_widget, stagger_fade_in, stop_pulse
from .vrchat_api import (
    AvatarResult,
    avatar_profile_url,
    enrich_avatar_description,
    favorite_avatar,
    select_avatar,
)
from .vrchat_auth import VRChatSession

logger = get_logger('avatar_search')

_THUMB_SIZE = 56
_CARD_HEIGHT = 68


class AvatarCard(QFrame):
    action_requested = pyqtSignal(str, str)

    def __init__(
        self,
        avatar: AvatarResult,
        *,
        session: VRChatSession | None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.avatar = avatar
        self.session = session
        self.setObjectName('avatarCard')
        self.setFixedHeight(_CARD_HEIGHT)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self.thumb = RemoteImageLabel(_THUMB_SIZE, self)
        self.thumb.load(avatar.image_url)
        layout.addWidget(self.thumb)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        self.name_label = QLabel(self._elide(avatar.name))
        self.name_label.setObjectName('avatarName')
        self.name_label.setWordWrap(False)
        self.desc_label = QLabel(self._subtitle(avatar))
        self.desc_label.setObjectName('avatarDesc')
        self.desc_label.setWordWrap(True)
        text_col.addWidget(self.name_label)
        text_col.addWidget(self.desc_label)
        text_col.addStretch()
        layout.addLayout(text_col, 1)

    @staticmethod
    def _elide(text: str, max_width: int = 190) -> str:
        font = QFont('Segoe UI', 9)
        font.setWeight(QFont.Weight.DemiBold)
        return QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, max_width)

    @staticmethod
    def _subtitle(avatar: AvatarResult) -> str:
        parts: list[str] = []
        if avatar.description:
            parts.append(avatar.description)
        elif avatar.author_name:
            parts.append(f'by {avatar.author_name}')
        if avatar.performance:
            parts.append(avatar.performance)
        return ' · '.join(parts) if parts else 'No description'

    def set_description(self, text: str) -> None:
        updated = AvatarResult(
            id=self.avatar.id,
            name=self.avatar.name,
            description=text,
            author_name=self.avatar.author_name,
            image_url=self.avatar.image_url,
            performance=self.avatar.performance,
        )
        self.avatar = updated
        self.desc_label.setText(self._subtitle(updated))

    def _show_context_menu(self, pos) -> None:
        menu = themed_menu(self)
        if self.session and self.avatar.id:
            wear = menu.addAction('Wear Avatar')
            wear.setToolTip('Switch into this avatar via VRChat API')
            wear.triggered.connect(lambda: self.action_requested.emit('wear', self.avatar.id))
            favorite = menu.addAction('Favorite Avatar')
            favorite.triggered.connect(lambda: self.action_requested.emit('favorite', self.avatar.id))
            menu.addSeparator()
        if self.avatar.id:
            copy_id = menu.addAction('Copy Avatar ID')
            copy_id.triggered.connect(lambda: self.action_requested.emit('copy_id', self.avatar.id))
            open_profile = menu.addAction('Open on VRChat.com')
            open_profile.triggered.connect(lambda: self.action_requested.emit('open', self.avatar.id))
        if menu.isEmpty():
            return
        menu.exec(self.mapToGlobal(pos))


class AvatarSearchWorker(QThread):
    finished_ok = pyqtSignal(list, str)
    finished_error = pyqtSignal(str)

    def __init__(
        self,
        session: VRChatSession | None,
        provider_id: str,
        query: str,
        filter_id: str,
    ):
        super().__init__()
        self.session = session
        self.provider_id = provider_id
        self.query = query
        self.filter_id = filter_id

    def run(self) -> None:
        try:
            outcome = search_with_provider(
                self.provider_id,
                session=self.session,
                query=self.query,
                filter_id=self.filter_id,
            )
            self.finished_ok.emit(outcome.results, outcome.notice or '')
        except Exception as exc:
            logger.warning('Avatar search failed: %s', exc, exc_info=is_debug_mode())
            self.finished_error.emit(str(exc) or 'Search failed.')


class DescriptionWorker(QThread):
    finished_ok = pyqtSignal(str, str)

    def __init__(self, session: VRChatSession, avatar_id: str):
        super().__init__()
        self.session = session
        self.avatar_id = avatar_id

    def run(self) -> None:
        text = enrich_avatar_description(self.session, self.avatar_id)
        self.finished_ok.emit(self.avatar_id, text)


class AvatarSearchPanel(QWidget):
    def __init__(self, session: VRChatSession | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._provider = get_provider()
        self._filter_id = default_filter_for(self._provider)
        self._worker: AvatarSearchWorker | None = None
        self._desc_workers: list[DescriptionWorker] = []
        self._action_worker: ApiActionWorker | None = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(400)
        self._search_timer.timeout.connect(self._run_search)
        if session is not None:
            RemoteImageLabel.set_session(session)
        self.setFixedWidth(308)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._build_ui()
        self._refresh_status_hint()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.container = QWidget()
        self.container.setObjectName('roundContainer')
        inner = QVBoxLayout(self.container)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(0)

        group = QGroupBox('Avatar Search')
        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(10, 10, 10, 8)
        group_layout.setSpacing(6)

        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(self._search_placeholder())
        self.search_input.setFixedHeight(28)
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._run_search)
        self.filter_btn = QPushButton('⛃')
        self.filter_btn.setFixedSize(28, 28)
        self.filter_btn.setToolTip('Search filter')
        self.filter_btn.clicked.connect(self._show_filter_menu)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.filter_btn)
        group_layout.addLayout(search_row)

        self.status_label = QLabel('')
        self.status_label.setObjectName('panelStatus')
        self.status_label.setWordWrap(True)
        group_layout.addWidget(self.status_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')
        self.results_host = QWidget()
        self.results_layout = QVBoxLayout(self.results_host)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(6)
        self.empty_label = QLabel('')
        self.empty_label.setObjectName('emptyState')
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.results_layout.addWidget(self.empty_label, 1)
        self.scroll.setWidget(self.results_host)
        group_layout.addWidget(self.scroll, 1)

        group.setLayout(group_layout)
        inner.addWidget(group)
        layout.addWidget(self.container)

        self.setStyleSheet(dark_theme("""
            QGroupBox {
                color: #dcdcdc;
            }
            QLabel#panelStatus {
                color: #7a8a7a;
                font-size: 8pt;
            }
            QLabel#emptyState {
                color: #6a7a6a;
                font-size: 9pt;
                padding: 24px 12px;
            }
            QFrame#avatarCard {
                background-color: #232323;
                border: 1px solid #3a3a3a;
                border-radius: 8px;
            }
            QFrame#avatarCard:hover {
                border-color: #4ea3ff;
            }
            QLabel#avatarName {
                color: #f0f0f0;
                font-weight: 600;
                font-size: 9pt;
            }
            QLabel#avatarDesc {
                color: #9aa0a6;
                font-size: 8pt;
            }
        """))

    def apply_settings(self) -> None:
        previous_provider = self._provider.id
        self._provider = get_provider()
        if self._provider.id != previous_provider:
            self._filter_id = self._provider.filters[0].id
            save_config({'avatar_search_filter': self._filter_id})
        else:
            self._filter_id = default_filter_for(self._provider)
        self.search_input.setPlaceholderText(self._search_placeholder())
        self._clear_results()
        self._refresh_status_hint()
        if self._provider.requires_query:
            if len(self.search_input.text().strip()) >= self._provider.min_query_len:
                self._run_search()
        else:
            self._run_search()

    def _search_placeholder(self) -> str:
        if self._provider.requires_query:
            return f'Search avatars ({self._provider.min_query_len}+ chars)...'
        return 'Optional name filter...'

    def _empty_message(self) -> str:
        if self._provider.requires_query:
            return f'Type at least {self._provider.min_query_len} characters to search avatars.'
        if self._provider.requires_login and self.session is None:
            return 'Login required for this avatar search API.'
        return 'Press Enter or change the filter to load avatars.'

    def _refresh_status_hint(self) -> None:
        filter_text = filter_label(self._provider, self._filter_id)
        self.status_label.setText(f'{self._provider.label} · {filter_text}')
        self._show_empty_state(self._empty_message())

    def _show_empty_state(self, message: str) -> None:
        self.empty_label.setText(message)
        self.empty_label.show()
        pop_in_widget(self.empty_label, duration=220)

    def _show_filter_menu(self) -> None:
        menu = themed_menu(self)
        for item in self._provider.filters:
            action = menu.addAction(item.label)
            action.setCheckable(True)
            action.setChecked(self._filter_id == item.id)
            action.triggered.connect(lambda _checked, filter_id=item.id: self._set_filter(filter_id))
        menu.exec(self.filter_btn.mapToGlobal(self.filter_btn.rect().bottomLeft()))

    def _set_filter(self, filter_id: str) -> None:
        self._filter_id = filter_id
        save_config({'avatar_search_filter': filter_id})
        self._refresh_status_hint()
        self._run_search()

    def _on_search_changed(self) -> None:
        if not self._provider.requires_query:
            self._search_timer.start()
            return
        if len(self.search_input.text().strip()) >= self._provider.min_query_len:
            self._search_timer.start()

    def _run_search(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        query = self.search_input.text().strip()
        if self._provider.requires_login and self.session is None:
            self.status_label.setText(f'Login required for {self._provider.label}.')
            self._clear_results()
            self._show_empty_state('Login required for this avatar search API.')
            return
        if self._provider.requires_query and len(query) < self._provider.min_query_len:
            self.status_label.setText(self._empty_message())
            self._clear_results()
            self._show_empty_state(self._empty_message())
            return
        self.status_label.setText(f'Searching {self._provider.label}...')
        pulse_widget(self.status_label, duration=1100)
        self._worker = AvatarSearchWorker(
            self.session,
            self._provider.id,
            query,
            self._filter_id,
        )
        self._worker.finished_ok.connect(self._on_results)
        self._worker.finished_error.connect(self._on_error)
        self._worker.start()

    def _clear_results(self) -> None:
        self._desc_workers.clear()
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _on_error(self, message: str) -> None:
        stop_pulse(self.status_label)
        self.status_label.setText(message)
        self._clear_results()
        self._show_empty_state(message)

    def _on_results(self, results: list, notice: str = '') -> None:
        stop_pulse(self.status_label)
        self._clear_results()
        if not results:
            self.status_label.setText('No avatars found.')
            self._show_empty_state('No avatars found.\nTry a different search term or filter.')
            return
        self.empty_label.hide()
        filter_text = filter_label(self._provider, self._filter_id)
        status = f'{len(results)} result(s) · {self._provider.label} · {filter_text}'
        if notice:
            status = f'{notice} · {status}'
        self.status_label.setText(status)
        cards: list[AvatarCard] = []
        for avatar in results[:40]:
            card = AvatarCard(avatar, session=self.session, parent=self.results_host)
            card.action_requested.connect(self._on_card_action)
            self.results_layout.insertWidget(self.results_layout.count() - 1, card)
            cards.append(card)
            if self.session and avatar.id and not avatar.description:
                worker = DescriptionWorker(self.session, avatar.id)
                worker.finished_ok.connect(self._on_description)
                worker.start()
                self._desc_workers.append(worker)
        stagger_fade_in(cards, duration=220, step_ms=26)

    def _on_description(self, avatar_id: str, description: str) -> None:
        if not description:
            return
        for i in range(self.results_layout.count() - 1):
            item = self.results_layout.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, AvatarCard) and widget.avatar.id == avatar_id:
                widget.set_description(description)
                break

    def _on_card_action(self, action: str, avatar_id: str) -> None:
        if action == 'copy_id':
            QApplication.clipboard().setText(avatar_id)
            self.status_label.setText('Avatar ID copied.')
            return
        if action == 'open':
            webbrowser.open(avatar_profile_url(avatar_id))
            self.status_label.setText('Opened avatar page in browser.')
            return
        if self.session is None:
            self.status_label.setText('Login required for this action.')
            return
        if self._action_worker and self._action_worker.isRunning():
            self.status_label.setText('Please wait for the current action…')
            return
        if action == 'wear':
            self.status_label.setText('Switching avatar…')
            self._action_worker = ApiActionWorker(
                lambda: select_avatar(self.session, avatar_id),
                'Avatar selected — switch applies in VRChat.',
            )
        elif action == 'favorite':
            self.status_label.setText('Favoriting avatar…')
            self._action_worker = ApiActionWorker(
                lambda: favorite_avatar(self.session, avatar_id),
            )
        else:
            return
        self._action_worker.finished_ok.connect(self._on_action_ok)
        self._action_worker.finished_error.connect(self._on_action_error)
        self._action_worker.start()

    def _on_action_ok(self, message: str) -> None:
        self.status_label.setText(message)

    def _on_action_error(self, message: str) -> None:
        self.status_label.setText(message)

    def cleanup(self) -> None:
        self._search_timer.stop()
        if self._action_worker and self._action_worker.isRunning():
            self._action_worker.wait(2000)
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        for worker in self._desc_workers:
            if worker.isRunning():
                worker.wait(1000)
