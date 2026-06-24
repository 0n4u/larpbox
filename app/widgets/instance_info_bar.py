from __future__ import annotations
import webbrowser
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from ..theme import TOOLBAR_BUTTON, dark_theme, themed_menu
from ..ui_animations import flash_widget
from ..vrchat.core import vrchat_join_url
from ..vrchat.models import InstanceInfo

class InstanceInfoBar(QWidget):
    world_tools_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None=None):
        super().__init__(parent)
        self._info: InstanceInfo | None = None
        self._last_world_text = ''
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.frame = QFrame()
        self.frame.setObjectName('instanceInfoBar')
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.world_label = QLabel('Not in a world')
        self.world_label.setObjectName('instanceWorld')
        self.world_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(self.world_label, 1)
        self.copy_btn = QPushButton('⧉')
        self.copy_btn.setFixedSize(22, 22)
        self.copy_btn.setToolTip('Copy instance link')
        self.copy_btn.clicked.connect(self._copy_link)
        top.addWidget(self.copy_btn)
        self.open_btn = QPushButton('↗')
        self.open_btn.setFixedSize(22, 22)
        self.open_btn.setToolTip('Open instance in browser')
        self.open_btn.clicked.connect(self._open_link)
        top.addWidget(self.open_btn)
        self.world_btn = QPushButton('🌐')
        self.world_btn.setFixedSize(22, 22)
        self.world_btn.setStyleSheet(TOOLBAR_BUTTON)
        self.world_btn.setToolTip('Worlds, groups & status')
        self.world_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.world_btn.clicked.connect(self.world_tools_requested.emit)
        top.addWidget(self.world_btn)
        layout.addLayout(top)
        self.detail_label = QLabel('')
        self.detail_label.setObjectName('instanceDetail')
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)
        self.meta_label = QLabel('')
        self.meta_label.setObjectName('instanceMeta')
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.meta_label)
        root.addWidget(self.frame)
        self.setStyleSheet(dark_theme('\n            QFrame#instanceInfoBar {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n            }\n            QLabel#instanceWorld {\n                color: #e8e8e8;\n                font-size: 9pt;\n                font-weight: 600;\n            }\n            QLabel#instanceDetail {\n                color: #9aa0a6;\n                font-size: 8pt;\n            }\n            QLabel#instanceMeta {\n                color: #7a8a7a;\n                font-size: 8pt;\n            }\n        '))

    def apply(self, info: InstanceInfo | None) -> None:
        self._info = info
        if info is None:
            self.world_label.setText('Not in a world')
            self.detail_label.setText('')
            self.meta_label.setText('')
            self.copy_btn.setEnabled(False)
            self.open_btn.setEnabled(False)
            return
        world_name = (info.world_name or '').strip()
        world_text = world_name or 'Not in a world'
        if world_text != self._last_world_text:
            self._last_world_text = world_text
            flash_widget(self.frame, duration=260, dip=0.72)
        self.world_label.setText(world_text)
        detail_parts: list[str] = []
        if info.instance_type:
            detail_parts.append(info.instance_type.title())
        if info.owner_display_name:
            detail_parts.append(f'Owner: {info.owner_display_name}')
        elif info.owner_id:
            detail_parts.append(f'Owner: {info.owner_id[:12]}…')
        if info.region:
            detail_parts.append(info.region.upper())
        self.detail_label.setText(' · '.join(detail_parts))
        meta_parts: list[str] = []
        if info.max_players:
            meta_parts.append(f'{info.player_count}/{info.max_players} players')
        else:
            meta_parts.append(f'{info.player_count} players')
        short_id = info.instance_id.split('~')[0] if info.instance_id else ''
        if short_id and len(short_id) <= 16:
            meta_parts.append(short_id)
        self.meta_label.setText(' · '.join(meta_parts))
        has_link = bool(info.location or (info.world_id and info.instance_id))
        self.copy_btn.setEnabled(has_link)
        self.open_btn.setEnabled(has_link)

    def set_hint(self, text: str) -> None:
        self._info = None
        self.world_label.setText(text)
        self.detail_label.setText('')
        self.meta_label.setText('')
        self.copy_btn.setEnabled(False)
        self.open_btn.setEnabled(False)

    def _location(self) -> str:
        if self._info is None:
            return ''
        if self._info.location:
            return self._info.location
        if self._info.world_id and self._info.instance_id:
            return f'{self._info.world_id}:{self._info.instance_id}'
        return ''

    def _copy_link(self) -> None:
        location = self._location()
        url = vrchat_join_url(location) if location else ''
        if url:
            QApplication.clipboard().setText(url)

    def _open_link(self) -> None:
        location = self._location()
        url = vrchat_join_url(location) if location else ''
        if url:
            webbrowser.open(url)

    def _show_context_menu(self, pos) -> None:
        if self._info is None:
            return
        menu = themed_menu(self.frame)
        copy = menu.addAction('Copy instance link')
        copy.triggered.connect(self._copy_link)
        open_link = menu.addAction('Open in browser')
        open_link.triggered.connect(self._open_link)
        menu.exec(self.frame.mapToGlobal(pos))
