from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QInputDialog, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from .preset_storage import load_presets, save_presets
from .theme import dark_theme

class PresetManagerWindow(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Preset Manager')
        self.setFixedSize(520, 380)
        self.presets = {}
        self.current_preset = None
        self.init_ui()
        self.load_presets()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.preset_list = QListWidget()
        self.preset_list.setMinimumWidth(160)
        self.preset_list.itemSelectionChanged.connect(self.on_preset_selected)
        layout.addWidget(self.preset_list)
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        options_layout = QHBoxLayout()
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(4)
        self.btn_new = QPushButton('New')
        self.btn_rename = QPushButton('Rename')
        self.btn_delete = QPushButton('Delete')
        self.btn_save = QPushButton('Save All')
        for btn in (self.btn_new, self.btn_rename, self.btn_delete, self.btn_save):
            btn.setFixedHeight(22)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_new.clicked.connect(self.create_preset)
        self.btn_rename.clicked.connect(self.rename_preset)
        self.btn_delete.clicked.connect(self.delete_preset)
        self.btn_save.clicked.connect(self.save_presets)
        options_layout.addStretch()
        options_layout.addWidget(self.btn_new)
        options_layout.addWidget(self.btn_rename)
        options_layout.addWidget(self.btn_delete)
        options_layout.addWidget(self.btn_save)
        right_layout.addLayout(options_layout)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText('One line per frame in the preset animation...')
        self.editor.textChanged.connect(self.on_editor_changed)
        right_layout.addWidget(self.editor)
        layout.addLayout(right_layout)
        self.setStyleSheet(dark_theme('\n            QWidget {\n                background-color: #1e1e1e;\n            }\n            QPlainTextEdit {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n            }\n            '))

    def load_presets(self):
        self.presets = load_presets()
        self.refresh_preset_list()

    def refresh_preset_list(self):
        self.preset_list.clear()
        for name in sorted(self.presets.keys()):
            self.preset_list.addItem(name)

    def on_preset_selected(self):
        items = self.preset_list.selectedItems()
        if not items:
            self.current_preset = None
            self.editor.blockSignals(True)
            self.editor.clear()
            self.editor.blockSignals(False)
            return
        name = items[0].text()
        self.current_preset = name
        frames = self.presets.get(name, [])
        self.editor.blockSignals(True)
        self.editor.setPlainText('\n'.join(frames))
        self.editor.blockSignals(False)

    def on_editor_changed(self):
        if not self.current_preset:
            return
        text = self.editor.toPlainText()
        self.presets[self.current_preset] = text.split('\n') if text else ['']

    def create_preset(self):
        name, ok = QInputDialog.getText(self, 'New Preset', 'Preset name:')
        if not ok or not name:
            return
        if name in self.presets:
            QMessageBox.warning(self, 'Preset Exists', 'A preset with that name already exists.')
            return
        self.presets[name] = ['']
        self.refresh_preset_list()
        items = self.preset_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self.preset_list.setCurrentItem(items[0])
            self.on_preset_selected()

    def rename_preset(self):
        if not self.current_preset:
            return
        old_name = self.current_preset
        new_name, ok = QInputDialog.getText(self, 'Rename Preset', 'New name:', text=old_name)
        if not ok or not new_name or new_name == old_name:
            return
        if new_name in self.presets:
            QMessageBox.warning(self, 'Preset Exists', 'A preset with that name already exists.')
            return
        self.presets[new_name] = self.presets.pop(old_name, [])
        self.refresh_preset_list()
        items = self.preset_list.findItems(new_name, Qt.MatchFlag.MatchExactly)
        if items:
            self.preset_list.setCurrentItem(items[0])
            self.on_preset_selected()

    def delete_preset(self):
        if not self.current_preset:
            return
        name = self.current_preset
        reply = QMessageBox.question(self, 'Delete Preset', f"Delete preset '{name}'?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.presets.pop(name, None)
        self.current_preset = None
        self.refresh_preset_list()
        self.editor.blockSignals(True)
        self.editor.clear()
        self.editor.blockSignals(False)

    def save_presets(self):
        try:
            save_presets(self.presets)
            QMessageBox.information(self, 'Presets Saved', 'Presets have been saved to presets.json.')
            parent = self.parent()
            if hasattr(parent, 'on_presets_changed_from_manager'):
                parent.on_presets_changed_from_manager()
        except Exception as e:
            QMessageBox.critical(self, 'Save Error', str(e))
