from __future__ import annotations
import webbrowser
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget
from .api_action_worker import ApiActionWorker
from .avatar_search_providers import PERFORMANCE_COLORS
from .image_loader import RemoteImageLabel
from .logging_setup import get_logger
from .api_startup import in_startup_window
from .services.session_manager import SessionManager
from .theme import TOOLBAR_BUTTON, dark_theme, themed_menu
from .ui_animations import animate_list_items, flash_widget, pop_in_widget, pulse_widget, stop_pulse
from .vrchat_api import AvatarResult, avatar_profile_url, enrich_wardrobe_avatar, list_favorite_avatar_ids, select_avatar, wardrobe_avatar_placeholder
from .vrchat_auth import VRChatSession
from .wardrobe_cache import flush_cache
logger = get_logger('wardrobe')
_THUMB_SIZE = 56
_CARD_HEIGHT = 72
_POPULATE_BATCH = 14
_POPULATE_INTERVAL_MS = 12
_SCROLL_LOAD_MARGIN_PX = 96
_WEAR_BUTTON = TOOLBAR_BUTTON + '\nQPushButton#wardrobeWearBtn {\n    color: #7db87d;\n    border-color: #3d6640;\n    font-weight: 600;\n    font-size: 8pt;\n}\nQPushButton#wardrobeWearBtn:hover {\n    color: #9ed89e;\n    border-color: #7db87d;\n    background-color: rgba(125, 184, 125, 0.12);\n}\n'

def _needs_enrichment(avatar: AvatarResult) -> bool:
    if not avatar.image_url:
        return True
    name = (avatar.name or '').strip()
    return name.startswith('Avatar …') or name == 'Favorite avatar'

class WardrobeListWorker(QThread):
    finished_ok = pyqtSignal(object)
    finished_error = pyqtSignal(str)

    def __init__(self, session: VRChatSession):
        super().__init__()
        self.session = session

    def run(self) -> None:
        try:
            self.finished_ok.emit(list_favorite_avatar_ids(self.session))
        except Exception as exc:
            logger.warning('Wardrobe list failed', exc_info=True)
            self.finished_error.emit(str(exc) or 'Failed to load wardrobe.')

class WardrobeEnrichWorker(QThread):
    avatar_enriched = pyqtSignal(str, object)

    def __init__(self, session: VRChatSession, avatar_ids: list[str], *, vrc_fallback: bool=False):
        super().__init__()
        self.session = session
        self.avatar_ids = avatar_ids
        self.vrc_fallback = vrc_fallback

    def run(self) -> None:
        for avatar_id in self.avatar_ids:
            if self.isInterruptionRequested():
                return
            try:
                result = enrich_wardrobe_avatar(self.session, avatar_id, allow_vrc_fallback=self.vrc_fallback)
                self.avatar_enriched.emit(avatar_id, result)
            except Exception:
                logger.debug('Could not enrich wardrobe avatar %s', avatar_id, exc_info=True)

class WardrobeCard(QFrame):
    wear_requested = pyqtSignal(str)

    def __init__(self, avatar: AvatarResult, *, session: VRChatSession | None, parent: QWidget | None=None):
        super().__init__(parent)
        self.avatar = avatar
        self.session = session
        self._perf_badge: QLabel | None = None
        self.setObjectName('wardrobeCard')
        self.setFixedHeight(_CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(8)
        self.thumb = RemoteImageLabel(_THUMB_SIZE, self)
        self._load_thumb(avatar)
        layout.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignVCenter)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        self.title_row = QHBoxLayout()
        self.title_row.setSpacing(4)
        self.name_label = QLabel(self._elide(avatar.name or 'Favorite avatar'))
        self.name_label.setObjectName('avatarName')
        self.title_row.addWidget(self.name_label, 1)
        self._set_perf_badge(avatar.performance)
        text_col.addLayout(self.title_row)
        self.desc_label = QLabel(self._subtitle(avatar))
        self.desc_label.setObjectName('avatarDesc')
        self.desc_label.setWordWrap(True)
        text_col.addWidget(self.desc_label)
        text_col.addStretch()
        layout.addLayout(text_col, 1)
        self.wear_btn = QPushButton('Wear')
        self.wear_btn.setObjectName('wardrobeWearBtn')
        self.wear_btn.setFixedSize(46, 26)
        self.wear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wear_btn.clicked.connect(lambda: self.wear_requested.emit(self.avatar.id))
        layout.addWidget(self.wear_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def _load_thumb(self, avatar: AvatarResult) -> None:
        if avatar.image_url:
            self.thumb.load(avatar.image_url)
        else:
            self.thumb.setText('…')

    def _set_perf_badge(self, performance: str | None) -> None:
        if self._perf_badge is not None:
            self.title_row.removeWidget(self._perf_badge)
            self._perf_badge.deleteLater()
            self._perf_badge = None
        if not performance:
            return
        color = PERFORMANCE_COLORS.get(performance, '#888')
        perf = QLabel(performance[:3].upper())
        perf.setObjectName('perfBadge')
        perf.setToolTip(performance)
        perf.setFixedHeight(14)
        perf.setStyleSheet(f'color: {color}; border: 1px solid {color}; border-radius: 3px; font-size: 7pt; font-weight: 700; padding: 0 4px;')
        self.title_row.addWidget(perf)
        self._perf_badge = perf

    @staticmethod
    def _elide(text: str, max_width: int=150) -> str:
        font = QFont('Segoe UI', 9)
        font.setWeight(QFont.Weight.DemiBold)
        return QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, max_width)

    @staticmethod
    def _subtitle(avatar: AvatarResult) -> str:
        if avatar.author_name:
            return f'by {avatar.author_name}'
        if avatar.description:
            return avatar.description
        if _needs_enrichment(avatar):
            return 'Loading details…'
        return avatar.id[:18] + '…' if len(avatar.id) > 20 else avatar.id

    def update_avatar(self, avatar: AvatarResult) -> None:
        self.avatar = avatar
        self.name_label.setText(self._elide(avatar.name or 'Favorite avatar'))
        self.desc_label.setText(self._subtitle(avatar))
        self._set_perf_badge(avatar.performance)
        self._load_thumb(avatar)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.wear_requested.emit(self.avatar.id)
        super().mouseReleaseEvent(event)

    def _show_context_menu(self, pos) -> None:
        menu = None
        try:
            menu = themed_menu(self)
            wear = menu.addAction('Wear Avatar')
            wear.triggered.connect(lambda: self.wear_requested.emit(self.avatar.id))
            menu.addSeparator()
            copy_id = menu.addAction('Copy Avatar ID')
            copy_id.triggered.connect(lambda: QApplication.clipboard().setText(self.avatar.id))
            if self.avatar.id:
                open_profile = menu.addAction('Open on VRChat.com')
                open_profile.triggered.connect(lambda: webbrowser.open(avatar_profile_url(self.avatar.id)))
            menu.exec(self.mapToGlobal(pos))
        except Exception as e:
            from .logging_setup import get_logger
            logger = get_logger('wardrobe')
            logger.error('Context menu failed for avatar %s: %s', self.avatar.name, e, exc_info=True)
        finally:
            if menu is not None:
                menu.deleteLater()

class WardrobePanel(QWidget):

    def __init__(self, session: VRChatSession | None=None, parent: QWidget | None=None):
        super().__init__(parent)
        self.session = session
        self._list_worker: WardrobeListWorker | None = None
        self._enrich_worker: WardrobeEnrichWorker | None = None
        self._action_worker: ApiActionWorker | None = None
        self._all_avatars: list[AvatarResult] = []
        self._filtered_avatars: list[AvatarResult] = []
        self._cards_by_id: dict[str, WardrobeCard] = {}
        self._populate_index = 0
        self._populate_generation = 0
        self._enrich_queued: set[str] = set()
        self._enrich_pending: list[str] = []
        self._enrich_active = 0
        self._enrich_total = 0
        self._vrc_fallback_pending: list[str] = []
        self._vrc_fallback_scheduled = False
        self._build_ui()
        if session is not None:
            RemoteImageLabel.set_session(session)

    def set_session(self, session: VRChatSession | None) -> None:
        self.session = session
        if session is not None:
            RemoteImageLabel.set_session(session)
        if session is None:
            self._stop_workers()
            self._show_empty('Log in to see your wardrobe')
            return
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel('My Wardrobe')
        title.setObjectName('wardrobeTitle')
        header.addWidget(title)
        self.count_label = QLabel('')
        self.count_label.setObjectName('wardrobeCountBadge')
        header.addWidget(self.count_label)
        header.addStretch(1)
        self.refresh_btn = QPushButton('↻')
        self.refresh_btn.setFixedSize(24, 24)
        self.refresh_btn.setStyleSheet(TOOLBAR_BUTTON)
        self.refresh_btn.setToolTip('Refresh wardrobe')
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('Filter favorites…')
        self.search_input.setFixedHeight(28)
        self.search_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_input)
        self.status_label = QLabel('')
        self.status_label.setObjectName('panelStatus')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.empty_label = QLabel('Loading wardrobe…')
        self.empty_label.setObjectName('emptyState')
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.list_layout.addWidget(self.empty_label, 1)
        self.scroll.setWidget(self.list_host)
        layout.addWidget(self.scroll, 1)
        self.setStyleSheet(dark_theme(_WEAR_BUTTON + '\n            QLabel#wardrobeTitle {\n                font-weight: 600;\n                font-size: 9pt;\n                color: #e8e8e8;\n            }\n            QLabel#wardrobeCountBadge {\n                color: #7db87d;\n                font-size: 8pt;\n                font-weight: 600;\n                border: 1px solid #3d6640;\n                border-radius: 8px;\n                padding: 1px 7px;\n                background-color: rgba(125, 184, 125, 0.1);\n            }\n            QLabel#panelStatus {\n                color: #7a8a7a;\n                font-size: 8pt;\n            }\n            QLabel#emptyState {\n                color: #6a7a6a;\n                font-size: 9pt;\n                padding: 28px 12px;\n            }\n            QFrame#wardrobeCard {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 8px;\n            }\n            QFrame#wardrobeCard:hover {\n                border-color: #4ea3ff;\n                background-color: #262626;\n            }\n            QLabel#avatarName {\n                color: #f0f0f0;\n                font-weight: 600;\n                font-size: 9pt;\n            }\n            QLabel#avatarDesc {\n                color: #9aa0a6;\n                font-size: 8pt;\n            }\n        '))

    def _stop_workers(self) -> None:
        self._populate_generation += 1
        self._enrich_queued.clear()
        self._enrich_pending.clear()
        self._vrc_fallback_pending.clear()
        self._vrc_fallback_scheduled = False
        if self._enrich_worker and self._enrich_worker.isRunning():
            self._enrich_worker.requestInterruption()
            self._enrich_worker.wait(1500)
        if self._list_worker and self._list_worker.isRunning():
            self._list_worker.wait(1500)

    def _show_empty(self, message: str) -> None:
        self._clear_cards()
        self.count_label.setText('')
        self.status_label.setText('')
        stop_pulse(self.status_label)
        self.empty_label.setText(message)
        if self.empty_label.isVisible():
            flash_widget(self.empty_label, duration=220, dip=0.6)
        else:
            self.empty_label.show()
            pop_in_widget(self.empty_label, duration=260)

    def _clear_cards(self) -> None:
        self._cards_by_id.clear()
        self._populate_index = 0
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.empty_label:
                widget.deleteLater()

    def refresh(self) -> None:
        if self.session is None:
            self._show_empty('Log in to see your wardrobe')
            return
        if self._list_worker and self._list_worker.isRunning():
            return
        self._stop_workers()
        self._all_avatars = []
        self._filtered_avatars = []
        self._clear_cards()
        self.count_label.setText('')
        self.empty_label.setText('Loading favorites…')
        self.empty_label.show()
        self.status_label.setText('Fetching favorite list…')
        pulse_widget(self.status_label)
        self._list_worker = WardrobeListWorker(self.session)
        self._list_worker.finished_ok.connect(self._on_ids_loaded)
        self._list_worker.finished_error.connect(self._on_error)
        self._list_worker.start()

    def _on_error(self, message: str) -> None:
        stop_pulse(self.status_label)
        if SessionManager.instance().try_handle_auth_failure(message):
            self._show_empty('Session expired — sign in again')
            return
        self.status_label.setText('')
        self._show_empty(message)

    def _on_ids_loaded(self, ids_obj: object) -> None:
        stop_pulse(self.status_label)
        if not isinstance(ids_obj, list):
            self._show_empty('Could not load wardrobe')
            return
        avatar_ids = [str(item) for item in ids_obj if str(item).startswith('avtr_')]
        if not avatar_ids:
            self._show_empty('No favorite avatars yet.\nFavorite avatars in VRChat to see them here.')
            return
        self._all_avatars = [wardrobe_avatar_placeholder(avatar_id) for avatar_id in avatar_ids]
        RemoteImageLabel.prefetch([avatar.image_url for avatar in self._all_avatars if avatar.image_url])
        cached = sum(1 for avatar in self._all_avatars if not _needs_enrichment(avatar))
        self.count_label.setText(str(len(self._all_avatars)))
        self._apply_filter(initial=True)
        if cached == len(self._all_avatars):
            self.status_label.setText(f'{len(self._all_avatars)} favorites ready')
        else:
            self.status_label.setText(f'{len(self._all_avatars)} favorites · {cached} cached · loading as you scroll')

    def _apply_filter(self, *, initial: bool=False) -> None:
        query = self.search_input.text().strip().casefold()
        avatars = self._all_avatars
        if query:
            avatars = [avatar for avatar in avatars if query in (avatar.name or '').casefold() or query in avatar.id.casefold() or query in (avatar.author_name or '').casefold()]
        self._filtered_avatars = avatars
        self._populate_generation += 1
        self._populate_index = 0
        self._enrich_queued.clear()
        self._enrich_pending.clear()
        if self._enrich_worker and self._enrich_worker.isRunning():
            self._enrich_worker.requestInterruption()
        self._clear_cards()
        if not self._all_avatars:
            self._show_empty('No favorite avatars yet.\nFavorite avatars in VRChat to see them here.')
            self.status_label.setText('')
            return
        if not avatars:
            self.empty_label.setText('No favorites match your filter.')
            self.empty_label.show()
            self.status_label.setText(f'Showing 0 of {len(self._all_avatars)}')
            return
        self.empty_label.hide()
        shown = len(avatars)
        total = len(self._all_avatars)
        if shown != total:
            self.status_label.setText(f'Showing {shown} of {total}')
        elif not initial:
            self.status_label.setText(f'{total} favorites')
        self._populate_next_batch()

    def _populate_next_batch(self) -> None:
        generation = self._populate_generation
        if generation != self._populate_generation:
            return
        if self._populate_index >= len(self._filtered_avatars):
            return
        end = min(self._populate_index + _POPULATE_BATCH, len(self._filtered_avatars))
        enrich_ids: list[str] = []
        new_cards: list[WardrobeCard] = []
        for index in range(self._populate_index, end):
            avatar = self._filtered_avatars[index]
            card = WardrobeCard(avatar, session=self.session, parent=self.list_host)
            card.wear_requested.connect(self._wear)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
            self._cards_by_id[avatar.id] = card
            new_cards.append(card)
            if _needs_enrichment(avatar):
                enrich_ids.append(avatar.id)
        if new_cards:
            animate_list_items(new_cards, pop=True, duration=200, step_ms=18)
        self._populate_index = end
        self._queue_enrich(enrich_ids)
        if self._populate_index < len(self._filtered_avatars):
            QTimer.singleShot(_POPULATE_INTERVAL_MS, self._populate_next_batch)

    def _on_scroll(self, _value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() - bar.value() <= _SCROLL_LOAD_MARGIN_PX:
            self._populate_next_batch()

    def _queue_enrich(self, avatar_ids: list[str]) -> None:
        new_ids = [avatar_id for avatar_id in avatar_ids if avatar_id not in self._enrich_queued]
        if not new_ids:
            return
        for avatar_id in new_ids:
            self._enrich_queued.add(avatar_id)
        self._enrich_pending.extend(new_ids)
        self._enrich_total = len(self._enrich_queued)
        self._start_enrich_worker()

    def _start_enrich_worker(self) -> None:
        if self.session is None or not self._enrich_pending:
            return
        if self._enrich_worker and self._enrich_worker.isRunning():
            return
        batch_size = min(10, len(self._enrich_pending))
        batch = self._enrich_pending[:batch_size]
        self._enrich_pending = self._enrich_pending[batch_size:]
        self._enrich_worker = WardrobeEnrichWorker(self.session, batch, vrc_fallback=False)
        self._enrich_worker.avatar_enriched.connect(self._on_avatar_enriched)
        self._enrich_worker.finished.connect(self._on_enrich_batch_done)
        self._enrich_worker.start()
        if self._enrich_total:
            done = self._enrich_total - len(self._enrich_pending) - len(batch)
            self.status_label.setText(f'Loading details… {max(0, done)}/{self._enrich_total}')

    def _on_avatar_enriched(self, avatar_id: str, result_obj: object) -> None:
        if not isinstance(result_obj, AvatarResult):
            return
        for index, avatar in enumerate(self._all_avatars):
            if avatar.id == avatar_id:
                self._all_avatars[index] = result_obj
                break
        for index, avatar in enumerate(self._filtered_avatars):
            if avatar.id == avatar_id:
                self._filtered_avatars[index] = result_obj
                break
        card = self._cards_by_id.get(avatar_id)
        if card is not None:
            card.update_avatar(result_obj)

    def _on_enrich_batch_done(self) -> None:
        if self._enrich_pending:
            self._start_enrich_worker()
            return
        for avatar_id in list(self._enrich_queued):
            avatar = next((item for item in self._all_avatars if item.id == avatar_id), None)
            if avatar is not None and _needs_enrichment(avatar):
                if avatar_id not in self._vrc_fallback_pending:
                    self._vrc_fallback_pending.append(avatar_id)
        self._schedule_vrc_fallback()
        if not self._vrc_fallback_pending:
            self._finish_enrich_status()

    def _schedule_vrc_fallback(self) -> None:
        if not self._vrc_fallback_pending or self._vrc_fallback_scheduled:
            return
        if self._enrich_worker and self._enrich_worker.isRunning():
            return
        self._vrc_fallback_scheduled = True
        delay_ms = 8000 if in_startup_window() else 1500
        QTimer.singleShot(delay_ms, self._start_vrc_fallback_worker)

    def _start_vrc_fallback_worker(self) -> None:
        self._vrc_fallback_scheduled = False
        if self.session is None or not self._vrc_fallback_pending:
            self._finish_enrich_status()
            return
        if self._enrich_worker and self._enrich_worker.isRunning():
            return
        batch_size = min(3, len(self._vrc_fallback_pending))
        batch = self._vrc_fallback_pending[:batch_size]
        self._vrc_fallback_pending = self._vrc_fallback_pending[batch_size:]
        self._enrich_worker = WardrobeEnrichWorker(self.session, batch, vrc_fallback=True)
        self._enrich_worker.avatar_enriched.connect(self._on_avatar_enriched)
        self._enrich_worker.finished.connect(self._on_vrc_fallback_batch_done)
        self._enrich_worker.start()

    def _on_vrc_fallback_batch_done(self) -> None:
        if self._vrc_fallback_pending:
            QTimer.singleShot(1200, self._start_vrc_fallback_worker)
            return
        self._finish_enrich_status()

    def _finish_enrich_status(self) -> None:
        flush_cache()
        total = len(self._all_avatars)
        cached = sum(1 for avatar in self._all_avatars if not _needs_enrichment(avatar))
        if cached >= total:
            self.status_label.setText(f'{total} favorites ready')
        else:
            self.status_label.setText(f'{total} favorites · {cached} with details')

    def _wear(self, avatar_id: str) -> None:
        if self.session is None or (self._action_worker and self._action_worker.isRunning()):
            return
        self.status_label.setText('Switching avatar…')
        pulse_widget(self.status_label)
        self._action_worker = ApiActionWorker(lambda: select_avatar(self.session, avatar_id), 'Avatar selected — switch applies in VRChat.')
        self._action_worker.finished_ok.connect(self._on_wear_ok)
        self._action_worker.finished_error.connect(self._on_wear_error)
        self._action_worker.start()

    def _on_wear_ok(self, _message: str) -> None:
        stop_pulse(self.status_label)
        self.status_label.setText('Avatar selected — switch in VRChat')

    def _on_wear_error(self, message: str) -> None:
        stop_pulse(self.status_label)
        self.status_label.setText(f'Error: {message}')

    def cleanup(self) -> None:
        stop_pulse(self.status_label)
        self._stop_workers()
        flush_cache()
        if self._action_worker and self._action_worker.isRunning():
            self._action_worker.wait(2000)
