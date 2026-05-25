from __future__ import annotations
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from ..theme import dark_theme
from ..vrchat.models import InstanceInfo

class InstanceInfoBar(QWidget):

    def __init__(self, parent: QWidget | None=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.frame = QFrame()
        self.frame.setObjectName('instanceInfoBar')
        layout = QHBoxLayout(self.frame)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)
        self.world_label = QLabel('Not in a world')
        self.world_label.setObjectName('instanceWorld')
        self.world_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.world_label, 1)
        self.meta_label = QLabel('')
        self.meta_label.setObjectName('instanceMeta')
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.meta_label)
        root.addWidget(self.frame)
        self.setStyleSheet(dark_theme('\n            QFrame#instanceInfoBar {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n            }\n            QLabel#instanceWorld {\n                color: #e8e8e8;\n                font-size: 9pt;\n                font-weight: 600;\n            }\n            QLabel#instanceMeta {\n                color: #7a8a7a;\n                font-size: 8pt;\n            }\n        '))

    def apply(self, info: InstanceInfo | None) -> None:
        if info is None:
            self.world_label.setText('Not in a world')
            self.meta_label.setText('')
            return
        world_name = (info.world_name or '').strip()
        self.world_label.setText(world_name or 'Not in a world')
        parts: list[str] = [f'{info.player_count} players']
        if info.region:
            parts.append(info.region.upper())
        if info.instance_type:
            parts.append(info.instance_type)
        short_id = info.instance_id.split('~')[0] if info.instance_id else ''
        if short_id and len(short_id) <= 12:
            parts.append(short_id)
        self.meta_label.setText(' · '.join(parts))

    def set_hint(self, text: str) -> None:
        self.world_label.setText(text)
        self.meta_label.setText('')
