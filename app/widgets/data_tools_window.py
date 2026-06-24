from __future__ import annotations
from typing import Callable
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QTabWidget, QVBoxLayout, QWidget
from ..logging_setup import get_logger
from ..services import data_io
from ..store import favorites_repo
from .feed_format import format_relative_time
from .panel_window import PanelWindow

logger = get_logger('ui')

_KIND_FILTERS = (('All', None), ('Friends', favorites_repo.FRIEND), ('Worlds', favorites_repo.WORLD), ('Avatars', favorites_repo.AVATAR))


class DataToolsWindow(PanelWindow):
    def __init__(self):
        super().__init__('Data & Favorites', width=560, height=640)
        self._status = QLabel('')
        self._status.setObjectName('panelHint')
        tabs = QTabWidget()
        self.content_layout.addWidget(tabs, 1)
        tabs.addTab(self._build_favorites_tab(), 'Favorites')
        tabs.addTab(self._build_backup_tab(), 'Backup & Export')
        self.content_layout.addWidget(self._status)
        self.reload_favorites()

    def _build_favorites_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self._kind_combo = QComboBox()
        for label, _ in _KIND_FILTERS:
            self._kind_combo.addItem(label)
        self._kind_combo.currentIndexChanged.connect(self.reload_favorites)
        remove_btn = QPushButton('Remove selected')
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(self._remove_selected)
        top.addWidget(QLabel('Show:'))
        top.addWidget(self._kind_combo)
        top.addStretch()
        top.addWidget(remove_btn)
        layout.addLayout(top)
        self._fav_list = QListWidget()
        self._fav_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self._fav_list, 1)
        self._fav_count = QLabel('')
        self._fav_count.setObjectName('panelHint')
        layout.addWidget(self._fav_count)
        return tab

    def _build_backup_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._make_section_label('Favorites'))
        layout.addWidget(self._action_button('Export favorites (JSON)', self._export_favorites))
        layout.addWidget(self._action_button('Import favorites (JSON)', self._import_favorites))
        layout.addWidget(self._make_section_label('History'))
        layout.addWidget(self._action_button('Export game log (CSV)', self._export_gamelog_csv))
        layout.addWidget(self._action_button('Export friend feed (CSV)', self._export_feed_csv))
        layout.addWidget(self._action_button('Export full history (JSON)', self._export_history_json))
        layout.addWidget(self._make_section_label('Configuration'))
        layout.addWidget(self._action_button('Export config backup (JSON)', self._export_config_backup))
        layout.addWidget(self._action_button('Import config backup (JSON)', self._import_config_backup))
        layout.addStretch(1)
        hint = QLabel('Backups never include your login token or password.')
        hint.setObjectName('panelHint')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return tab

    def _make_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName('panelSectionHeader')
        return label

    def _action_button(self, text: str, handler: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(handler)
        return button

    def _current_kind(self):
        return _KIND_FILTERS[self._kind_combo.currentIndex()][1]

    def reload_favorites(self) -> None:
        kind = self._current_kind()
        rows = favorites_repo.list_favorites(kind=kind)
        self._fav_list.clear()
        for row in rows:
            name = str(row.get('name') or '') or str(row.get('target_id') or '')
            label = f"{name}  ·  {row.get('kind')}  ·  {format_relative_time(float(row.get('added_ts') or 0))}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (str(row.get('kind') or ''), str(row.get('target_id') or '')))
            self._fav_list.addItem(item)
        if not rows:
            placeholder = QListWidgetItem('No favorites yet. Right-click a friend to add one.')
            placeholder.setForeground(QColor('#777777'))
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._fav_list.addItem(placeholder)
        self._fav_count.setText(f'{len(rows)} favorite(s)')

    def _remove_selected(self) -> None:
        removed = 0
        for item in self._fav_list.selectedItems():
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data:
                continue
            kind, target_id = data
            favorites_repo.remove_favorite(kind=kind, target_id=target_id)
            removed += 1
        if removed:
            self.reload_favorites()
            self._set_status(f'Removed {removed} favorite(s)')

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def _save_path(self, title: str, default_name: str, filt: str) -> str:
        path, _ = QFileDialog.getSaveFileName(self, title, default_name, filt)
        return path

    def _open_path(self, title: str, filt: str) -> str:
        path, _ = QFileDialog.getOpenFileName(self, title, '', filt)
        return path

    def _guard(self, action: Callable[[], str]) -> None:
        try:
            self._set_status(action())
        except Exception as exc:
            logger.warning('Data tool action failed', exc_info=True)
            self._set_status(f'Error: {exc}')

    def _export_favorites(self) -> None:
        path = self._save_path('Export Favorites', 'larpbox_favorites.json', 'JSON (*.json)')
        if path:
            self._guard(lambda: f'Exported {data_io.export_favorites(path)} favorite(s)')

    def _import_favorites(self) -> None:
        path = self._open_path('Import Favorites', 'JSON (*.json)')
        if path:
            def _do() -> str:
                count = data_io.import_favorites(path)
                self.reload_favorites()
                return f'Imported {count} favorite(s)'
            self._guard(_do)

    def _export_gamelog_csv(self) -> None:
        path = self._save_path('Export Game Log', 'larpbox_gamelog.csv', 'CSV (*.csv)')
        if path:
            self._guard(lambda: f'Exported {data_io.export_gamelog_csv(path)} game-log row(s)')

    def _export_feed_csv(self) -> None:
        path = self._save_path('Export Friend Feed', 'larpbox_friend_feed.csv', 'CSV (*.csv)')
        if path:
            self._guard(lambda: f'Exported {data_io.export_feed_csv(path)} feed row(s)')

    def _export_history_json(self) -> None:
        path = self._save_path('Export History', 'larpbox_history.json', 'JSON (*.json)')
        if path:
            self._guard(lambda: 'Exported history: ' + ', '.join(f'{k}={v}' for k, v in data_io.export_history_json(path).items()))

    def _export_config_backup(self) -> None:
        path = self._save_path('Export Config Backup', 'larpbox_backup.json', 'JSON (*.json)')
        if path:
            def _do() -> str:
                data_io.export_config_backup(path)
                return 'Config backup saved'
            self._guard(_do)

    def _import_config_backup(self) -> None:
        path = self._open_path('Import Config Backup', 'JSON (*.json)')
        if path:
            self._guard(lambda: 'Restored: ' + ', '.join(f'{k}={v}' for k, v in data_io.import_config_backup(path).items()))
