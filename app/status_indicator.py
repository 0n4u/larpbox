from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from .user_status import UserStatusInfo, status_dot_style


class StatusIndicator(QWidget):
    def __init__(self, parent: QWidget | None = None, *, dot_size: int = 8, compact: bool = False):
        super().__init__(parent)
        self._dot_size = dot_size
        self._compact = compact
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4 if compact else 5)

        self.dot = QLabel(self)
        self.dot.setFixedSize(dot_size, dot_size)
        self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self.label = QLabel('')
        self.label.setObjectName('statusIndicatorLabel')
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.label, 1)

    def apply(self, status: UserStatusInfo) -> None:
        self.dot.setStyleSheet(status_dot_style(status.color, size=self._dot_size))
        self.dot.setToolTip(status.label)
        font_size = '7pt' if self._compact else '8pt'
        self.label.setStyleSheet(f'color: {status.color}; font-size: {font_size}; font-weight: 600;')
        self.label.setText(status.label)
        self.label.setToolTip(status.label)
        self.setToolTip(status.label)
