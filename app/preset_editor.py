from __future__ import annotations
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QGroupBox, QHBoxLayout, QInputDialog, QLabel, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from .frameless_chrome import apply_frameless_chrome
from .preset_storage import load_presets, save_presets
from .theme import dark_theme
from .title_bar import TitleBar
from .ui_animations import pop_in_widget

class PresetManagerWindow(QWidget):
    presets_saved = pyqtSignal()

    def __init__(self, parent: QWidget | None=None):
        super().__init__(parent)
        self.setWindowTitle('')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(560, 420)
        self.presets: dict[str, list[str]] = {}
        self.current_preset: str | None = None
        self._dirty = False
        self._intro_animated = False
        self.init_ui()
        self.load_presets()
        self.apply_theme()

    def init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)
        self.container = QWidget()
        self.container.setObjectName('roundContainer')
        inner = QVBoxLayout(self.container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)
        self.title_bar = TitleBar(self, title='Preset Manager')
        inner.addWidget(self.title_bar)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(6)
        group = QGroupBox('Edit Presets')
        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(10, 10, 10, 8)
        group_layout.setSpacing(6)
        body = QHBoxLayout()
        body.setSpacing(8)
        list_col = QVBoxLayout()
        list_col.setSpacing(4)
        list_label = QLabel('Presets')
        list_label.setObjectName('sectionHeader')
        list_col.addWidget(list_label)
        self.preset_list = QListWidget()
        self.preset_list.setMinimumWidth(170)
        self.preset_list.itemSelectionChanged.connect(self.on_preset_selected)
        list_col.addWidget(self.preset_list, 1)
        body.addLayout(list_col, 0)
        editor_col = QVBoxLayout()
        editor_col.setSpacing(4)
        editor_header = QHBoxLayout()
        self.editor_label = QLabel('Frames')
        self.editor_label.setObjectName('sectionHeader')
        editor_header.addWidget(self.editor_label)
        editor_header.addStretch()
        self.frame_count_label = QLabel('')
        self.frame_count_label.setObjectName('mutedHint')
        editor_header.addWidget(self.frame_count_label)
        editor_col.addLayout(editor_header)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.btn_new = QPushButton('New')
        self.btn_duplicate = QPushButton('Duplicate')
        self.btn_rename = QPushButton('Rename')
        self.btn_delete = QPushButton('Delete')
        self.btn_save = QPushButton('Save All')
        self.btn_save.setObjectName('primaryAction')
        for btn in (self.btn_new, self.btn_duplicate, self.btn_rename, self.btn_delete, self.btn_save):
            btn.setFixedHeight(26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_new.clicked.connect(self.create_preset)
        self.btn_duplicate.clicked.connect(self.duplicate_preset)
        self.btn_rename.clicked.connect(self.rename_preset)
        self.btn_delete.clicked.connect(self.delete_preset)
        self.btn_save.clicked.connect(self.save_presets)
        toolbar.addWidget(self.btn_new)
        toolbar.addWidget(self.btn_duplicate)
        toolbar.addWidget(self.btn_rename)
        toolbar.addWidget(self.btn_delete)
        toolbar.addStretch()
        toolbar.addWidget(self.btn_save)
        editor_col.addLayout(toolbar)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText('One line per frame in the preset animation…')
        self.editor.textChanged.connect(self.on_editor_changed)
        editor_col.addWidget(self.editor, 1)
        body.addLayout(editor_col, 1)
        group_layout.addLayout(body)
        self.status_label = QLabel('Select a preset or create a new one.')
        self.status_label.setObjectName('mutedHint')
        self.status_label.setWordWrap(True)
        group_layout.addWidget(self.status_label)
        group.setLayout(group_layout)
        content_layout.addWidget(group)
        inner.addWidget(content)
        outer.addWidget(self.container)
        self._update_action_states()

    def apply_theme(self) -> None:
        self.setStyleSheet(dark_theme("\n            QLabel#sectionHeader {\n                font-weight: 600;\n                color: #dcdcdc;\n            }\n            QLabel#mutedHint {\n                color: #7a8a7a;\n                font-size: 9pt;\n            }\n            QPlainTextEdit {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n                padding: 6px;\n                font-family: 'Consolas', 'Cascadia Mono', monospace;\n                font-size: 9pt;\n            }\n            QPlainTextEdit:focus {\n                border-color: #4ea3ff;\n            }\n            QPushButton#primaryAction {\n                background-color: #2f3542;\n                border: 1px solid #4ea3ff;\n                font-weight: 600;\n            }\n            QPushButton#primaryAction:hover {\n                background-color: #3a4558;\n            }\n            "))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_frameless_chrome(self)
        if not self._intro_animated:
            self._intro_animated = True
            pop_in_widget(self.container, duration=260)

    def closeEvent(self, event) -> None:
        event.accept()

    def _update_action_states(self) -> None:
        has_selection = self.current_preset is not None
        self.btn_rename.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)
        self.btn_duplicate.setEnabled(has_selection)

    def _update_frame_count(self) -> None:
        text = self.editor.toPlainText()
        lines = [line for line in text.splitlines() if line.strip()]
        count = len(lines) if lines else 1 if text else 0
        self.frame_count_label.setText(f'{count} frame(s)')

    def load_presets(self) -> None:
        self.presets = load_presets()
        self._dirty = False
        self.refresh_preset_list()
        self.status_label.setText(f'{len(self.presets)} preset(s) loaded.')

    def refresh_preset_list(self) -> None:
        selected = self.current_preset
        self.preset_list.clear()
        for name in sorted(self.presets.keys()):
            self.preset_list.addItem(name)
        if selected:
            items = self.preset_list.findItems(selected, Qt.MatchFlag.MatchExactly)
            if items:
                self.preset_list.setCurrentItem(items[0])
                return
        self.current_preset = None
        self.editor.blockSignals(True)
        self.editor.clear()
        self.editor.blockSignals(False)
        self._update_frame_count()
        self._update_action_states()

    def on_preset_selected(self) -> None:
        items = self.preset_list.selectedItems()
        if not items:
            self.current_preset = None
            self.editor.blockSignals(True)
            self.editor.clear()
            self.editor.blockSignals(False)
            self.editor_label.setText('Frames')
            self._update_frame_count()
            self._update_action_states()
            return
        name = items[0].text()
        self.current_preset = name
        frames = self.presets.get(name, [])
        self.editor.blockSignals(True)
        self.editor.setPlainText('\n'.join(frames))
        self.editor.blockSignals(False)
        self.editor_label.setText(f'Frames — {name}')
        self._update_frame_count()
        self._update_action_states()

    def on_editor_changed(self) -> None:
        if not self.current_preset:
            return
        text = self.editor.toPlainText()
        self.presets[self.current_preset] = text.split('\n') if text else ['']
        self._dirty = True
        self._update_frame_count()
        self.status_label.setText('Unsaved changes — click Save All when ready.')

    def _unique_name(self, base: str) -> str:
        if base not in self.presets:
            return base
        index = 2
        while f'{base} ({index})' in self.presets:
            index += 1
        return f'{base} ({index})'

    def create_preset(self) -> None:
        name, ok = QInputDialog.getText(self, 'New Preset', 'Preset name:')
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.presets:
            QMessageBox.warning(self, 'Preset Exists', 'A preset with that name already exists.')
            return
        self.presets[name] = ['']
        self._dirty = True
        self.refresh_preset_list()
        items = self.preset_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self.preset_list.setCurrentItem(items[0])
        self.status_label.setText(f"Created '{name}' — add frames and save.")

    def duplicate_preset(self) -> None:
        if not self.current_preset:
            return
        source = self.current_preset
        name = self._unique_name(f'{source} copy')
        self.presets[name] = list(self.presets.get(source, ['']))
        self._dirty = True
        self.refresh_preset_list()
        items = self.preset_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self.preset_list.setCurrentItem(items[0])
        self.status_label.setText(f"Duplicated '{source}' as '{name}'.")

    def rename_preset(self) -> None:
        if not self.current_preset:
            return
        old_name = self.current_preset
        new_name, ok = QInputDialog.getText(self, 'Rename Preset', 'New name:', text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        new_name = new_name.strip()
        if new_name in self.presets:
            QMessageBox.warning(self, 'Preset Exists', 'A preset with that name already exists.')
            return
        self.presets[new_name] = self.presets.pop(old_name, [])
        self.current_preset = new_name
        self._dirty = True
        self.refresh_preset_list()
        self.status_label.setText(f"Renamed '{old_name}' to '{new_name}'.")

    def delete_preset(self) -> None:
        if not self.current_preset:
            return
        name = self.current_preset
        reply = QMessageBox.question(self, 'Delete Preset', f"Delete preset '{name}'?\nThis cannot be undone until you save.")
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.presets.pop(name, None)
        self.current_preset = None
        self._dirty = True
        self.refresh_preset_list()
        self.status_label.setText(f"Deleted '{name}' — save to write presets.json.")

    def save_presets(self) -> None:
        try:
            save_presets(self.presets)
            self._dirty = False
            self.status_label.setText('Presets saved to presets.json.')
            self.presets_saved.emit()
        except Exception as exc:
            QMessageBox.critical(self, 'Save Error', str(exc))
