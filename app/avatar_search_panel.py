from __future__ import annotations
import webbrowser
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QApplication, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from .api_action_worker import ApiActionWorker
from .avatar_search_providers import PERFORMANCE_COLORS, default_filter_for, filter_label, get_provider, search_with_provider
from .config import save_config
from .image_loader import RemoteImageLabel
from .logging_setup import get_logger, is_debug_mode
from .services.session_manager import SessionManager
from .theme import dark_theme, themed_menu
from .ui_animations import flash_widget, pop_in_widget, pulse_widget, stagger_pop_in, stop_pulse, stop_widget_animations
from .vrchat_api import AvatarResult, avatar_profile_url, enrich_avatar_description, favorite_avatar, select_avatar
from .vrchat_auth import VRChatSession
logger = get_logger('avatar_search')
_THUMB_SIZE = 56
_CARD_HEIGHT = 68
_PAGE_SIZE = 40

class AvatarCard(QFrame):
    action_requested = pyqtSignal(str, str)

    def __init__(self, avatar: AvatarResult, *, session: VRChatSession | None, parent: QWidget | None=None):
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
        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        self.name_label = QLabel(self._elide(avatar.name))
        self.name_label.setObjectName('avatarName')
        title_row.addWidget(self.name_label, 1)
        if avatar.performance:
            color = PERFORMANCE_COLORS.get(avatar.performance, '#888')
            perf = QLabel(avatar.performance[:3].upper())
            perf.setObjectName('perfBadge')
            perf.setToolTip(avatar.performance)
            perf.setFixedHeight(14)
            perf.setStyleSheet(f'color: {color}; border: 1px solid {color}; border-radius: 3px; font-size: 7pt; font-weight: 700; padding: 0 4px;')
            title_row.addWidget(perf)
        text_col.addLayout(title_row)
        self.desc_label = QLabel(self._subtitle(avatar))
        self.desc_label.setObjectName('avatarDesc')
        self.desc_label.setWordWrap(True)
        text_col.addWidget(self.desc_label)
        text_col.addStretch()
        layout.addLayout(text_col, 1)

    @staticmethod
    def _elide(text: str, max_width: int=170) -> str:
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
        return ' · '.join(parts) if parts else 'No description'

    def set_description(self, text: str) -> None:
        self.avatar = AvatarResult(id=self.avatar.id, name=self.avatar.name, description=text, author_name=self.avatar.author_name, image_url=self.avatar.image_url, performance=self.avatar.performance, author_id=self.avatar.author_id)
        self.desc_label.setText(self._subtitle(self.avatar))

    def _show_context_menu(self, pos) -> None:
        menu = None
        try:
            menu = themed_menu(self)
            if self.session and self.avatar.id:
                wear = menu.addAction('Wear Avatar')
                wear.triggered.connect(lambda: self.action_requested.emit('wear', self.avatar.id))
                favorite = menu.addAction('Favorite Avatar')
                favorite.triggered.connect(lambda: self.action_requested.emit('favorite', self.avatar.id))
                menu.addSeparator()
            if self.avatar.author_id:
                author = menu.addAction('Browse author avatars')
                author.triggered.connect(lambda: self.action_requested.emit('author', self.avatar.author_id))
            if self.avatar.id:
                copy_id = menu.addAction('Copy Avatar ID')
                copy_id.triggered.connect(lambda: self.action_requested.emit('copy_id', self.avatar.id))
                open_profile = menu.addAction('Open on VRChat.com')
                open_profile.triggered.connect(lambda: self.action_requested.emit('open', self.avatar.id))
            if menu.isEmpty():
                return
            menu.exec(self.mapToGlobal(pos))
        except Exception as e:
            from .logging_setup import get_logger
            logger = get_logger('avatar_search')
            logger.error('Context menu failed for avatar %s: %s', self.avatar.name, e, exc_info=True)
        finally:
            if menu is not None:
                menu.deleteLater()

class AvatarSearchWorker(QThread):
    finished_ok = pyqtSignal(int, object)
    finished_error = pyqtSignal(int, str)

    def __init__(self, session: VRChatSession | None, provider_id: str, query: str, filter_id: str, offset: int, generation: int):
        super().__init__()
        self.session = session
        self.provider_id = provider_id
        self.query = query
        self.filter_id = filter_id
        self.offset = offset
        self.generation = generation

    def run(self) -> None:
        try:
            outcome = search_with_provider(self.provider_id, session=self.session, query=self.query, filter_id=self.filter_id, limit=_PAGE_SIZE, offset=self.offset)
            self.finished_ok.emit(self.generation, outcome)
        except Exception as exc:
            logger.warning('Avatar search failed: %s', exc, exc_info=is_debug_mode())
            self.finished_error.emit(self.generation, str(exc) or 'Search failed.')

class DescriptionWorker(QThread):
    finished_ok = pyqtSignal(int, str, str)

    def __init__(self, session: VRChatSession, avatar_id: str, generation: int):
        super().__init__()
        self.session = session
        self.avatar_id = avatar_id
        self.generation = generation

    def run(self) -> None:
        text = enrich_avatar_description(self.session, self.avatar_id)
        self.finished_ok.emit(self.generation, self.avatar_id, text)

class AvatarSearchPanel(QWidget):

    def __init__(self, session: VRChatSession | None=None, parent: QWidget | None=None, *, tabbed: bool=False):
        super().__init__(parent)
        self.session = session
        self._tabbed = tabbed
        self._provider = get_provider()
        self._filter_id = default_filter_for(self._provider)
        self._worker: AvatarSearchWorker | None = None
        self._desc_workers: list[DescriptionWorker] = []
        self._action_worker: ApiActionWorker | None = None
        self._search_generation = 0
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(400)
        self._search_timer.timeout.connect(lambda: self._run_search(reset=True))
        self._query = ''
        self._offset = 0
        self._has_more = False
        self._loading_more = False
        if session is not None:
            RemoteImageLabel.set_session(session)
        if not self._tabbed:
            self.setFixedWidth(308)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()
        self._refresh_status_hint()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.container = QWidget()
        if self._tabbed:
            inner = QVBoxLayout(self.container)
            inner.setContentsMargins(4, 4, 4, 4)
            inner.setSpacing(6)
            group_layout = inner
        else:
            self.container.setObjectName('roundContainer')
            inner = QVBoxLayout(self.container)
            inner.setContentsMargins(8, 8, 8, 8)
            inner.setSpacing(6)
            group = QGroupBox('Avatar Search')
            group_layout = QVBoxLayout()
            group_layout.setContentsMargins(10, 10, 10, 8)
            group_layout.setSpacing(6)
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('Search, avtr_… or usr_…')
        self.search_input.setFixedHeight(28)
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(lambda: self._run_search(reset=True))
        self.filter_btn = QPushButton('⛃')
        self.filter_btn.setFixedSize(28, 28)
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
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
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
        if not self._tabbed:
            group.setLayout(group_layout)
            inner.addWidget(group)
        layout.addWidget(self.container)
        self.setStyleSheet(dark_theme('\n            QLabel#panelStatus { color: #7a8a7a; font-size: 8pt; }\n            QLabel#emptyState { color: #6a7a6a; font-size: 9pt; padding: 24px 12px; }\n            QFrame#avatarCard {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 8px;\n            }\n            QFrame#avatarCard:hover { border-color: #4ea3ff; }\n            QLabel#avatarName { color: #f0f0f0; font-weight: 600; font-size: 9pt; }\n            QLabel#avatarDesc { color: #9aa0a6; font-size: 8pt; }\n        '))

    def apply_settings(self) -> None:
        self._cancel_active_search()
        previous_provider = self._provider.id
        self._provider = get_provider()
        if self._provider.id != previous_provider:
            self._filter_id = self._provider.filters[0].id
            save_config({'avatar_search_filter': self._filter_id})
        else:
            self._filter_id = default_filter_for(self._provider)
        self._clear_results()
        self._refresh_status_hint()

    def set_session(self, session: VRChatSession | None) -> None:
        self.session = session
        if session is not None:
            RemoteImageLabel.set_session(session)
        self.apply_settings()

    def _cancel_active_search(self, *, increment: bool=True) -> int:
        if increment:
            self._search_generation += 1
        generation = self._search_generation
        self._search_timer.stop()
        self._loading_more = False
        self._has_more = False
        stop_pulse(self.status_label)
        for worker in list(self._desc_workers):
            self._disconnect_desc_worker(worker)
        self._desc_workers.clear()
        if self._worker is not None:
            worker = self._worker
            self._worker = None
            try:
                worker.finished_ok.disconnect(self._on_results)
                worker.finished_error.disconnect(self._on_error)
            except Exception:
                pass
        return generation

    @staticmethod
    def _disconnect_desc_worker(worker: DescriptionWorker) -> None:
        try:
            worker.finished_ok.disconnect()
        except Exception:
            pass

    def _search_placeholder(self) -> str:
        return 'Search name, avtr_… or usr_…'

    def _empty_message(self) -> str:
        if self._provider.requires_query:
            return f'Type at least {self._provider.min_query_len} characters, or paste an avatar/user ID.'
        return 'Press Enter or change the filter to load avatars.'

    def _refresh_status_hint(self) -> None:
        filter_text = filter_label(self._provider, self._filter_id)
        self.status_label.setText(f'{self._provider.label} · {filter_text}')

    def _show_empty_state(self, message: str) -> None:
        self.empty_label.setText(message)
        if self.empty_label.isVisible():
            flash_widget(self.empty_label, duration=220, dip=0.6)
        else:
            self.empty_label.show()
            pop_in_widget(self.empty_label, duration=260)

    def _show_filter_menu(self) -> None:
        menu = themed_menu(self)
        for item in self._provider.filters:
            action = menu.addAction(item.label)
            action.setCheckable(True)
            action.setChecked(self._filter_id == item.id)
            action.triggered.connect(lambda _c, fid=item.id: self._set_filter(fid))
        menu.exec(self.filter_btn.mapToGlobal(self.filter_btn.rect().bottomLeft()))

    def _set_filter(self, filter_id: str) -> None:
        self._filter_id = filter_id
        save_config({'avatar_search_filter': filter_id})
        self._run_search(reset=True)

    def _on_search_changed(self) -> None:
        self._search_timer.start()

    def _on_scroll(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        if not self._has_more or self._loading_more:
            return
        if value >= bar.maximum() - 40:
            self._run_search(reset=False)

    def _run_search(self, *, reset: bool) -> None:
        if not reset and self._worker and self._worker.isRunning():
            return
        query = self.search_input.text().strip()
        if reset:
            generation = self._cancel_active_search(increment=True)
            self._offset = 0
            self._has_more = False
            self._clear_results()
        else:
            generation = self._search_generation
            self._loading_more = True
            self._offset += _PAGE_SIZE
        if self._provider.requires_login and self.session is None:
            self.status_label.setText(f'Login required for {self._provider.label}.')
            self._show_empty_state('Login required for this avatar search API.')
            return
        min_len = self._provider.min_query_len
        is_id = query.startswith('avtr_') or query.startswith('usr_')
        if self._provider.requires_query and len(query) < min_len and (not is_id):
            self._refresh_status_hint()
            self._show_empty_state(self._empty_message())
            return
        self._query = query
        label = 'Searching…' if reset else 'Loading more…'
        self.status_label.setText(f'{label} {self._provider.label}')
        if reset:
            pulse_widget(self.status_label, duration=1100)
        self._worker = AvatarSearchWorker(self.session, self._provider.id, query, self._filter_id, self._offset, generation)
        self._worker.finished_ok.connect(self._on_results)
        self._worker.finished_error.connect(self._on_error)
        self._worker.start()

    def _clear_results(self) -> None:
        for worker in list(self._desc_workers):
            self._disconnect_desc_worker(worker)
        self._desc_workers.clear()
        stop_widget_animations(self.empty_label)
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                stop_widget_animations(widget)
                widget.deleteLater()

    def _on_error(self, generation: int, message: str) -> None:
        if generation != self._search_generation:
            return
        try:
            self._loading_more = False
            stop_pulse(self.status_label)
            self.status_label.setText(message)
            if self._offset == 0:
                self._clear_results()
                self._show_empty_state(message)
        except Exception:
            logger.debug('Avatar search error UI update failed', exc_info=is_debug_mode())

    def _on_results(self, generation: int, outcome: object) -> None:
        if generation != self._search_generation:
            return
        try:
            from .avatar_search_providers import AvatarSearchOutcome
            self._loading_more = False
            stop_pulse(self.status_label)
            if not isinstance(outcome, AvatarSearchOutcome):
                return
            results = outcome.results
            self._has_more = outcome.has_more
            if self._offset == 0 and (not results):
                self._clear_results()
                self.status_label.setText('No avatars found.')
                self._show_empty_state('No avatars found.\nTry a different term, ID, or filter.')
                return
            if self._offset == 0:
                self._clear_results()
            self.empty_label.hide()
            filter_text = filter_label(self._provider, self._filter_id)
            count = self.results_layout.count() - 1 + len(results)
            status = f'{count} result(s) · {self._provider.label} · {filter_text}'
            if outcome.notice:
                status = f'{outcome.notice} · {status}'
            if self._has_more:
                status += ' · scroll for more'
            self.status_label.setText(status)
            cards: list[AvatarCard] = []
            active_generation = self._search_generation
            for avatar in results:
                card = AvatarCard(avatar, session=self.session, parent=self.results_host)
                card.action_requested.connect(self._on_card_action)
                self.results_layout.insertWidget(self.results_layout.count() - 1, card)
                cards.append(card)
                if self.session and avatar.id and (not avatar.description):
                    worker = DescriptionWorker(self.session, avatar.id, active_generation)
                    worker.finished_ok.connect(self._on_description)
                    worker.start()
                    self._desc_workers.append(worker)
            if cards:
                stagger_pop_in(cards, duration=200, step_ms=16)
        except Exception as exc:
            logger.warning('Avatar search results UI failed: %s', exc, exc_info=is_debug_mode())
            self._on_error(generation, str(exc) or 'Failed to show search results.')

    def _on_description(self, generation: int, avatar_id: str, description: str) -> None:
        if generation != self._search_generation or not description:
            return
        try:
            for i in range(self.results_layout.count() - 1):
                item = self.results_layout.itemAt(i)
                widget = item.widget() if item else None
                if isinstance(widget, AvatarCard) and widget.avatar.id == avatar_id:
                    widget.set_description(description)
                    break
        except Exception:
            logger.debug('Avatar description UI update failed', exc_info=is_debug_mode())

    def _on_card_action(self, action: str, value: str) -> None:
        if action == 'copy_id':
            QApplication.clipboard().setText(value)
            self.status_label.setText('Avatar ID copied.')
            return
        if action == 'open':
            webbrowser.open(avatar_profile_url(value))
            return
        if action == 'author':
            self.search_input.setText(value)
            self._run_search(reset=True)
            return
        if self.session is None:
            self.status_label.setText('Login required for this action.')
            return
        if self._action_worker and self._action_worker.isRunning():
            return
        if action == 'wear':
            self.status_label.setText('Switching avatar…')
            self._action_worker = ApiActionWorker(lambda: select_avatar(self.session, value))
        elif action == 'favorite':
            self.status_label.setText('Favoriting…')
            self._action_worker = ApiActionWorker(lambda: favorite_avatar(self.session, value))
        else:
            return
        self._action_worker.finished_ok.connect(self._on_action_ok)
        self._action_worker.finished_error.connect(self._on_action_error)
        self._action_worker.start()

    def _on_action_ok(self, message: str) -> None:
        self.status_label.setText(message)

    def _on_action_error(self, message: str) -> None:
        if SessionManager.instance().try_handle_auth_failure(message):
            self.status_label.setText('Session expired — sign in again.')
            return
        self.status_label.setText(message)

    def cleanup(self) -> None:
        self._cancel_active_search(increment=False)
        if self._action_worker and self._action_worker.isRunning():
            self._action_worker.wait(2000)
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        for worker in self._desc_workers:
            if worker.isRunning():
                worker.wait(1000)
