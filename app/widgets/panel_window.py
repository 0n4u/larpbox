from __future__ import annotations
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from ..frameless_chrome import apply_frameless_chrome
from ..theme import PANEL_STYLE, dark_theme
from ..title_bar import TitleBar
from ..ui_animations import window_fade_in

_PANEL_STYLE = PANEL_STYLE


class PanelWindow(QWidget):
    """Reusable frameless, themed window with a title bar and content area.

    Shared by the activity feed, notification center, and analytics views so
    they all match the existing larpbox window chrome.
    """

    def __init__(self, title: str, *, width: int = 560, height: int = 720, extra_style: str = ''):
        super().__init__()
        self.setWindowTitle('')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(0.0)
        self.resize(width, height)
        self.setMinimumSize(420, 460)
        self._fade_started = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)
        self.container = QWidget()
        self.container.setObjectName('roundContainer')
        outer.addWidget(self.container)
        inner = QVBoxLayout(self.container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)
        self.title_bar = TitleBar(self, title=title)
        inner.addWidget(self.title_bar)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(10, 8, 10, 10)
        self.content_layout.setSpacing(6)
        inner.addWidget(self.content, 1)
        self.setStyleSheet(dark_theme(_PANEL_STYLE, extra_style))

    def showEvent(self, event):
        super().showEvent(event)
        apply_frameless_chrome(self)
        if not self._fade_started:
            self._fade_started = True
            window_fade_in(self, duration=300)

    def present(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
