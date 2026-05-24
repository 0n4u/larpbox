from __future__ import annotations

import webbrowser

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .image_loader import RemoteImageLabel
from .logging_setup import get_logger
from .status_indicator import StatusIndicator
from .ui_layout import PREVIEW_PANEL_HEIGHT, PREVIEW_PANEL_WIDTH
from .theme import dark_theme, themed_menu
from .ui_animations import fade_in_widget, pop_in_widget, stagger_pop_in
from .vrchat_api import CurrentUserProfile, get_current_user_profile, user_profile_url
from .vrchat_auth import VRChatSession

logger = get_logger('account_info')

PANEL_WIDTH = PREVIEW_PANEL_WIDTH
_PROFILE_SIZE = 36
_BADGE_SIZE = 15
_MAX_BADGES = 4
_HINT = 'Loading account…'


class AccountProfileWorker(QThread):
    finished_ok = pyqtSignal(object)
    finished_error = pyqtSignal(str)

    def __init__(self, session: VRChatSession):
        super().__init__()
        self.session = session

    def run(self) -> None:
        try:
            profile = get_current_user_profile(self.session)
            self.finished_ok.emit(profile)
        except Exception as exc:
            logger.warning('Account profile fetch failed', exc_info=True)
            self.finished_error.emit(str(exc) or 'Failed to load account.')


def _normalize_whitespace(text: str) -> str:
    return ' '.join(text.split())


class AccountInfoPanel(QWidget):
    def __init__(self, session: VRChatSession | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: AccountProfileWorker | None = None
        self._profile: CurrentUserProfile | None = None
        self._trust_badge: QFrame | None = None
        self.setFixedSize(PREVIEW_PANEL_WIDTH, PREVIEW_PANEL_HEIGHT)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(20000)
        self._poll_timer.timeout.connect(self.refresh)
        self._build_ui()
        self._show_hint(_HINT)
        if self.session is not None:
            RemoteImageLabel.set_session(self.session)
            self._poll_timer.start()
            QTimer.singleShot(300, self.refresh)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.frame = QWidget()
        self.frame.setObjectName('previewContainer')
        self.frame.setFixedSize(PREVIEW_PANEL_WIDTH, PREVIEW_PANEL_HEIGHT)
        self.frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.frame.customContextMenuRequested.connect(self._show_context_menu)
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(10, 10, 10, 10)
        frame_layout.setSpacing(6)

        header = QLabel('Account')
        header.setObjectName('previewHeader')
        frame_layout.addWidget(header)

        top_row = QHBoxLayout()
        top_row.setSpacing(7)
        top_row.setContentsMargins(0, 0, 0, 0)

        self.avatar = RemoteImageLabel(_PROFILE_SIZE, self)
        top_row.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(1)
        meta_col.setContentsMargins(0, 0, 0, 0)

        self.name_label = QLabel('')
        self.name_label.setObjectName('accountName')
        self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        meta_col.addWidget(self.name_label)

        self.status_indicator = StatusIndicator(compact=True)
        meta_col.addWidget(self.status_indicator)

        self.instance_label = QLabel('')
        self.instance_label.setObjectName('accountInstance')
        self.instance_label.setWordWrap(True)
        meta_col.addWidget(self.instance_label)

        self.badges_host = QWidget()
        badges_layout = QHBoxLayout(self.badges_host)
        badges_layout.setContentsMargins(0, 2, 0, 0)
        badges_layout.setSpacing(4)
        badges_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._badges_layout = badges_layout
        meta_col.addWidget(self.badges_host)

        top_row.addLayout(meta_col, 1)
        frame_layout.addLayout(top_row, 0)

        self.bio_text = QTextEdit()
        self.bio_text.setObjectName('accountBioText')
        self.bio_text.setReadOnly(True)
        self.bio_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.bio_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.bio_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bio_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.bio_text.setMinimumHeight(52)
        self.bio_text.document().setDocumentMargin(1)
        frame_layout.addWidget(self.bio_text, 1)

        root.addWidget(self.frame, 0, Qt.AlignmentFlag.AlignTop)

        self.setStyleSheet(dark_theme("""
            QLabel#previewHeader {
                font-weight: bold;
                font-size: 9pt;
                margin-bottom: 0;
            }
            QLabel#accountName {
                color: #f0f0f0;
                font-size: 9pt;
                font-weight: 600;
            }
            QLabel#accountInstance {
                color: #7a8a7a;
                font-size: 8pt;
            }
            QLabel#statusIndicatorLabel {
                background: transparent;
                border: none;
            }
            QTextEdit#accountBioText {
                background-color: #232323;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
                padding: 3px 4px;
                font-family: 'Segoe UI';
                font-size: 8pt;
                color: #b8b8b8;
            }
            QFrame#trustBadgePill {
                background-color: rgba(255, 255, 255, 0.04);
                border-radius: 4px;
                min-width: 24px;
                max-width: 32px;
            }
            QLabel#trustBadgeLabel {
                font-size: 7pt;
                font-weight: 700;
                padding: 0 2px;
            }
        """))

    def _elide_label(self, label: QLabel, text: str, width: int) -> None:
        metrics = QFontMetrics(label.font())
        label.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(width, 40)))
        label.setToolTip(text if label.text() != text else '')

    def _meta_text_width(self) -> int:
        return PANEL_WIDTH - 20 - _PROFILE_SIZE - 7

    def _show_context_menu(self, pos) -> None:
        if self._profile is None:
            return
        menu = themed_menu(self)
        profile = menu.addAction('Open VRChat Profile')
        profile.triggered.connect(
            lambda: webbrowser.open(user_profile_url(self._profile.user_id))
        )
        menu.exec(self.frame.mapToGlobal(pos))

    def _show_hint(self, text: str) -> None:
        self._profile = None
        self.avatar.load('')
        self.name_label.setText('Account')
        self.name_label.setToolTip('')
        self.status_indicator.label.setText('')
        self.status_indicator.dot.setStyleSheet('background: transparent; border: none;')
        self.instance_label.setText(text)
        self.instance_label.setToolTip('')
        self._clear_badges()
        self.badges_host.hide()
        self.bio_text.setPlainText('')

    def _clear_badges(self) -> None:
        if self._trust_badge is not None:
            self._trust_badge.deleteLater()
            self._trust_badge = None
        while self._badges_layout.count():
            item = self._badges_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_trust_badge(self, profile: CurrentUserProfile) -> None:
        trust = profile.trust
        pill = QFrame(self.badges_host)
        pill.setObjectName('trustBadgePill')
        pill.setFixedHeight(_BADGE_SIZE)
        pill.setToolTip(trust.label)
        pill.setStyleSheet(
            f'QFrame#trustBadgePill {{ border: 1px solid {trust.color}; '
            f'background-color: rgba(255, 255, 255, 0.04); border-radius: 4px; }}'
        )
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(4, 0, 4, 0)
        pill_layout.setSpacing(0)
        label = QLabel(trust.short)
        label.setObjectName('trustBadgeLabel')
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(f'color: {trust.color}; background: transparent; border: none;')
        pill_layout.addWidget(label)
        self._badges_layout.addWidget(pill)
        self._trust_badge = pill

    def _apply_badges(self, profile: CurrentUserProfile) -> None:
        self._clear_badges()
        self._add_trust_badge(profile)
        visible_badges = [badge for badge in profile.badges if badge.image_url][:_MAX_BADGES]
        for badge in visible_badges:
            label = RemoteImageLabel(_BADGE_SIZE, self.badges_host)
            label.setToolTip(badge.name)
            label.load(badge.image_url)
            self._badges_layout.addWidget(label)
        self._badges_layout.addStretch(1)
        self.badges_host.show()

    def _format_instance_text(self, profile: CurrentUserProfile) -> str:
        if profile.world_name:
            return profile.world_name
        if profile.status.in_world:
            if profile.status.key == 'private':
                return 'Private world'
            if profile.instance_label and profile.instance_label not in ('Offline', 'Private'):
                return 'In a world'
        if profile.instance_label in ('Offline', 'Private'):
            return profile.instance_label
        if profile.instance_label:
            return 'In a world'
        return 'Not in a world'

    def _instance_tooltip(self, profile: CurrentUserProfile) -> str:
        parts: list[str] = []
        if profile.world_name:
            parts.append(profile.world_name)
        if profile.instance_label and profile.instance_label not in ('Offline', 'Private'):
            parts.append(profile.instance_label)
        return ' · '.join(parts)

    def refresh(self) -> None:
        if self.session is None:
            self._show_hint('Not logged in')
            return
        if self._worker and self._worker.isRunning():
            return
        self.instance_label.setText('Updating…')
        self._worker = AccountProfileWorker(self.session)
        self._worker.finished_ok.connect(self._on_loaded)
        self._worker.finished_error.connect(self._on_error)
        self._worker.start()

    def _on_error(self, message: str) -> None:
        logger.debug('Account panel error: %s', message)
        if self.session is not None:
            self._elide_label(self.name_label, self.session.display_name or 'Account', self._meta_text_width())
            self.instance_label.setText(message[:60])
            self.bio_text.setPlainText('')
        else:
            self._show_hint(message[:60])

    def _on_loaded(self, profile: object) -> None:
        if not isinstance(profile, CurrentUserProfile):
            self._show_hint('Could not load account')
            return
        self._profile = profile
        self._elide_label(self.name_label, profile.display_name, self._meta_text_width())
        self.status_indicator.apply(profile.status)
        instance_text = self._format_instance_text(profile)
        self._elide_label(self.instance_label, instance_text, self._meta_text_width())
        tooltip = self._instance_tooltip(profile)
        if tooltip and tooltip != instance_text:
            self.instance_label.setToolTip(tooltip)
        else:
            self.instance_label.setToolTip('')
        self.avatar.load(profile.image_url)
        self._apply_badges(profile)
        bio = _normalize_whitespace(profile.bio) if profile.bio else 'No description'
        self.bio_text.setPlainText(bio)
        self.bio_text.verticalScrollBar().setValue(0)
        pop_in_widget(self.avatar, duration=280)
        stagger_pop_in(
            [self.name_label, self.status_indicator, self.instance_label, self.bio_text],
            duration=240,
            step_ms=45,
        )

    def cleanup(self) -> None:
        self._poll_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
