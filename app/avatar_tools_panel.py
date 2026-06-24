from __future__ import annotations
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget
from PyQt6.QtCore import QTimer
from .api_startup import startup_delay_ms
from .avatar_search_panel import AvatarSearchPanel
from .theme import dark_theme
from .ui_animations import flash_widget
from .vrchat_auth import VRChatSession
from .wardrobe_panel import WardrobePanel

class AvatarToolsPanel(QWidget):

    def __init__(self, session: VRChatSession | None=None, parent: QWidget | None=None):
        super().__init__(parent)
        self.session = session
        self.setFixedWidth(308)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.container = QWidget()
        self.container.setObjectName('roundContainer')
        inner = QVBoxLayout(self.container)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(6)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('avatarToolsTabs')
        self.search = AvatarSearchPanel(session=self.session, parent=self, tabbed=True)
        self.wardrobe = WardrobePanel(session=self.session, parent=self)
        self.tabs.addTab(self.search, 'Search')
        self.tabs.addTab(self.wardrobe, 'My Wardrobe')
        self.tabs.currentChanged.connect(self._on_tab_changed)
        inner.addWidget(self.tabs)
        layout.addWidget(self.container)
        self.setStyleSheet(dark_theme('\n            QTabWidget::pane { border: 1px solid #3a3a3a; border-radius: 6px; background: #232323; }\n            QTabBar::tab { background: #1c1c1c; color: #9aa0a6; padding: 6px 10px; border-top-left-radius: 4px; border-top-right-radius: 4px; }\n            QTabBar::tab:selected { background: #232323; color: #e8e8e8; }\n        '))

    def _on_tab_changed(self, index: int) -> None:
        flash_widget(self.tabs, duration=200, dip=0.82)
        if index == 1 and self.session is not None:
            if not self.wardrobe._all_avatars:
                QTimer.singleShot(startup_delay_ms('wardrobe'), self.wardrobe.refresh)

    def set_session(self, session: VRChatSession | None) -> None:
        self.session = session
        self.search.set_session(session)
        self.wardrobe.set_session(session)

    def apply_settings(self) -> None:
        self.search.apply_settings()

    def cleanup(self) -> None:
        self.search.cleanup()
        self.wardrobe.cleanup()
