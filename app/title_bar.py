from PyQt6.QtCore import Qt, QPoint
from .theme import TITLE_CHROME_BTN_SIZE
from .ui_animations import fade_out_widget, slide_fade_in_widget
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget
_CHROME_BTN = '\n    QPushButton {\n        background-color: transparent;\n        border: none;\n        padding: 0px;\n    }\n'
_CLOSE_BTN = _CHROME_BTN + '\n    QPushButton {\n        color: #e57373;\n        font-size: 11px;\n    }\n    QPushButton:hover {\n        background-color: #7b1e1e;\n        color: #ffffff;\n    }\n    QPushButton:pressed {\n        background-color: #5a1515;\n    }\n'
_WINDOW_BTN = _CHROME_BTN + '\n    QPushButton {\n        color: #c0c0c0;\n        font-size: 11px;\n    }\n    QPushButton:hover {\n        background-color: #404040;\n    }\n    QPushButton:pressed {\n        background-color: #303030;\n    }\n'
_MAX_BTN = _CHROME_BTN + '\n    QPushButton {\n        color: #c0c0c0;\n        font-size: 10px;\n    }\n    QPushButton:hover {\n        background-color: #404040;\n    }\n    QPushButton:pressed {\n        background-color: #303030;\n    }\n'
_HELP_BTN = _CHROME_BTN + '\n    QPushButton {\n        color: #9aa0a6;\n        font-size: 11px;\n    }\n    QPushButton:hover {\n        background-color: #404040;\n        color: #4ea3ff;\n    }\n    QPushButton:pressed {\n        background-color: #303030;\n    }\n    QPushButton[helpActive="true"] {\n        background-color: #404040;\n        color: #4ea3ff;\n    }\n'
PRIVACY_HELP_LINES = (('Local data only', 'Settings, presets, and caches are stored on your PC as JSON files. Login tokens are saved locally only if you enable Remember login.'), ('Open source', 'This project is open source.'))

class _HelpPopover(QFrame):

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName('helpPopover')
        self.setFixedWidth(248)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.caret = QLabel('▲')
        self.caret.setObjectName('helpPopoverCaret')
        self.caret.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.caret.setContentsMargins(0, 0, 14, 0)
        outer.addWidget(self.caret)
        self.card = QFrame()
        self.card.setObjectName('helpPopoverCard')
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)
        for title, detail in PRIVACY_HELP_LINES:
            row = QFrame()
            row.setObjectName('helpPopoverRow')
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            bullet = QLabel('●')
            bullet.setObjectName('helpPopoverBullet')
            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(1)
            line_title = QLabel(title)
            line_title.setObjectName('helpPopoverLineTitle')
            line_detail = QLabel(detail)
            line_detail.setObjectName('helpPopoverLineDetail')
            line_detail.setWordWrap(True)
            text_col.addWidget(line_title)
            text_col.addWidget(line_detail)
            row_layout.addWidget(bullet, 0, Qt.AlignmentFlag.AlignTop)
            row_layout.addLayout(text_col, 1)
            card_layout.addWidget(row)
        outer.addWidget(self.card)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 140))
        self.card.setGraphicsEffect(shadow)
        self.setStyleSheet('\n            QFrame#helpPopover {\n                background: transparent;\n                border: none;\n            }\n            QLabel#helpPopoverCaret {\n                color: #3a3a3a;\n                font-size: 10px;\n                background: transparent;\n                margin-bottom: -5px;\n            }\n            QFrame#helpPopoverCard {\n                background-color: #1e1e1e;\n                border: 1px solid #3a3a3a;\n                border-radius: 10px;\n            }\n            QFrame#helpPopoverRow {\n                background: transparent;\n                border: none;\n            }\n            QLabel#helpPopoverBullet {\n                color: #4ea3ff;\n                font-size: 7px;\n                margin-top: 4px;\n                background: transparent;\n            }\n            QLabel#helpPopoverLineTitle {\n                color: #dcdcdc;\n                font-size: 9pt;\n                font-weight: 600;\n                background: transparent;\n            }\n            QLabel#helpPopoverLineDetail {\n                color: #9aa0a6;\n                font-size: 8pt;\n                background: transparent;\n            }\n        ')
        self.hide()

    def reposition(self, anchor: QWidget) -> None:
        anchor_bottom_right = anchor.mapTo(self.parentWidget(), QPoint(anchor.width(), anchor.height()))
        self.adjustSize()
        x = anchor_bottom_right.x() - self.width() + 8
        y = anchor_bottom_right.y() + 2
        self.move(max(8, x), y)

class TitleBar(QWidget):

    def __init__(self, parent: QWidget | None=None, title: str='larpbox', *, show_help: bool=False):
        super().__init__(parent)
        self.parent_window = parent
        self.drag_position = None
        self.setFixedHeight(28)
        self._title = title
        self._show_help = show_help
        self._help_popover: _HelpPopover | None = None
        self.setup_ui()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(4)
        self.btn_close = QPushButton('✕')
        self.btn_close.setFixedSize(TITLE_CHROME_BTN_SIZE, TITLE_CHROME_BTN_SIZE)
        self.btn_close.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet(_CLOSE_BTN)
        self.btn_minimize = QPushButton('–')
        self.btn_minimize.setFixedSize(TITLE_CHROME_BTN_SIZE, TITLE_CHROME_BTN_SIZE)
        self.btn_minimize.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_minimize.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_minimize.setStyleSheet(_WINDOW_BTN)
        self.btn_maximize = QPushButton('□')
        self.btn_maximize.setFixedSize(TITLE_CHROME_BTN_SIZE, TITLE_CHROME_BTN_SIZE)
        self.btn_maximize.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_maximize.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_maximize.setStyleSheet(_MAX_BTN)
        self.btn_close.clicked.connect(self.close_window)
        self.btn_minimize.clicked.connect(self.minimize_window)
        self.btn_maximize.clicked.connect(self.maximize_window)
        self.title_label = QLabel(self._title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.title_label.setStyleSheet('\n            QLabel {\n                color: #dcdcdc;\n                font-size: 11px;\n                font-weight: 500;\n                background: transparent;\n            }\n        ')
        layout.addWidget(self.title_label)
        layout.addStretch()
        if self._show_help:
            self.btn_help = QPushButton('❓')
            self.btn_help.setFixedSize(TITLE_CHROME_BTN_SIZE, TITLE_CHROME_BTN_SIZE)
            self.btn_help.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.btn_help.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_help.setStyleSheet(_HELP_BTN)
            self.btn_help.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.btn_help.clicked.connect(self._toggle_help_popover)
            layout.addWidget(self.btn_help)
        self._trailing_host = QWidget()
        self._trailing_layout = QHBoxLayout(self._trailing_host)
        self._trailing_layout.setContentsMargins(0, 0, 4, 0)
        self._trailing_layout.setSpacing(4)
        layout.addWidget(self._trailing_host, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.btn_minimize)
        layout.addWidget(self.btn_maximize)
        layout.addWidget(self.btn_close)
        self.setStyleSheet('\n            TitleBar {\n                background-color: #202020;\n                border-bottom: 1px solid #3a3a3a;\n            }\n        ')

    def add_trailing_widget(self, widget: QWidget) -> None:
        self._trailing_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def _toggle_help_popover(self) -> None:
        if self.parent_window is None:
            return
        if self._help_popover is None:
            self._help_popover = _HelpPopover(self.parent_window)
        showing = not self._help_popover.isVisible()
        if showing:
            self._help_popover.reposition(self.btn_help)
            self._help_popover.show()
            self._help_popover.raise_()
            slide_fade_in_widget(self._help_popover, offset_y=8, duration=260)
        else:
            fade_out_widget(self._help_popover, duration=180, hide_after=True)
        self.btn_help.setProperty('helpActive', showing)
        self.btn_help.style().unpolish(self.btn_help)
        self.btn_help.style().polish(self.btn_help)

    def close_window(self):
        if self._help_popover is not None:
            self._help_popover.hide()
        if self.parent_window:
            self.parent_window.close()

    def minimize_window(self):
        if self.parent_window:
            self.parent_window.showMinimized()

    def maximize_window(self):
        if self.parent_window:
            if self.parent_window.isMaximized():
                self.parent_window.showNormal()
            else:
                self.parent_window.showMaximized()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.parent_window is not None:
            self.drag_position = event.globalPosition().toPoint() - self.parent_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_position is not None and (self.parent_window is not None):
            self.parent_window.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.drag_position = None
        event.accept()
