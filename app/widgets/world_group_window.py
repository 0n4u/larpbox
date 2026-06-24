from __future__ import annotations
import webbrowser
from typing import Any, Callable
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)
from ..api_action_worker import ApiActionWorker
from ..config import load_config, save_config
from ..logging_setup import get_logger
from ..store import favorites_repo
from ..vrchat_auth import VRChatSession
from .panel_window import PanelWindow

logger = get_logger('ui')


class _FnWorker(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.loaded.emit(self._fn())
        except Exception as exc:
            self.failed.emit(str(exc) or 'Request failed.')


class WorldGroupWindow(PanelWindow):
    def __init__(self, session: VRChatSession | None = None):
        super().__init__('Worlds, Groups & Status', width=620, height=720)
        self._session = session
        self._workers: set[QThread] = set()
        self._action_workers: set[ApiActionWorker] = set()
        self._groups_loaded = False
        tabs = QTabWidget()
        self.content_layout.addWidget(tabs, 1)
        tabs.addTab(self._build_worlds_tab(), 'Worlds')
        tabs.addTab(self._build_groups_tab(), 'Groups')
        tabs.addTab(self._build_status_tab(), 'Status')
        self._tabs = tabs

    def set_session(self, session: VRChatSession | None) -> None:
        self._session = session

    def present(self) -> None:
        super().present()
        self._refresh_status_presets()
        if self._session is not None and not self._groups_loaded:
            self._load_groups()

                        
    def _build_worlds_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        bar = QHBoxLayout()
        self._world_search = QLineEdit()
        self._world_search.setPlaceholderText('Search worlds…')
        self._world_search.returnPressed.connect(self._search_worlds)
        self._world_sort = QComboBox()
        for label, value in (('Popularity', 'popularity'), ('Heat', 'heat'), ('Recently updated', 'updated'), ('Random', 'random')):
            self._world_sort.addItem(label, value)
        search_btn = QPushButton('Search')
        search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        search_btn.clicked.connect(self._search_worlds)
        bar.addWidget(self._world_search, 1)
        bar.addWidget(self._world_sort)
        bar.addWidget(search_btn)
        layout.addLayout(bar)
        self._world_status = QLabel('Search for worlds or browse featured.')
        self._world_status.setObjectName('panelHint')
        layout.addWidget(self._world_status)
        self._world_list = QListWidget()
        self._world_list.setWordWrap(True)
        layout.addWidget(self._world_list, 1)
        return tab

    def _search_worlds(self) -> None:
        if self._session is None:
            self._world_status.setText('Sign in to search worlds.')
            return
        query = self._world_search.text().strip()
        sort = self._world_sort.currentData()
        self._world_status.setText('Searching…')
        session = self._session

        def _run() -> list[dict[str, Any]]:
            from ..vrchat.worlds import search_worlds
            return search_worlds(session, query, sort=sort, limit=40)

        self._run_worker(_run, self._on_worlds_loaded, self._world_status)

    def _on_worlds_loaded(self, worlds: list[dict[str, Any]]) -> None:
        self._world_list.clear()
        for world in worlds:
            item = QListWidgetItem()
            row = _WorldRow(world, on_changed=self._refresh_world_row)
            item.setSizeHint(row.sizeHint())
            self._world_list.addItem(item)
            self._world_list.setItemWidget(item, row)
        self._world_status.setText(f'{len(worlds)} world(s)' if worlds else 'No worlds found.')

    def _refresh_world_row(self, _world_id: str) -> None:
        pass

                        
    def _build_groups_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        left = QVBoxLayout()
        self._groups_status = QLabel('Loading groups…')
        self._groups_status.setObjectName('panelHint')
        self._group_list = QListWidget()
        self._group_list.itemSelectionChanged.connect(self._on_group_selected)
        left.addWidget(self._groups_status)
        left.addWidget(self._group_list, 1)
        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setFixedWidth(220)
        self._group_detail = QPlainTextEdit()
        self._group_detail.setReadOnly(True)
        self._group_detail.setPlaceholderText('Select a group to see details, instances, and recent posts.')
        layout.addWidget(left_box)
        layout.addWidget(self._group_detail, 1)
        return tab

    def _load_groups(self) -> None:
        if self._session is None:
            self._groups_status.setText('Sign in to view groups.')
            return
        session = self._session
        self._groups_loaded = True
        self._groups_status.setText('Loading groups…')

        def _run() -> list[dict[str, Any]]:
            from ..vrchat.groups import get_user_groups
            return get_user_groups(session, session.user_id)

        self._run_worker(_run, self._on_groups_loaded, self._groups_status)

    def _on_groups_loaded(self, groups: list[dict[str, Any]]) -> None:
        self._group_list.clear()
        for group in groups:
            item = QListWidgetItem(f"{group.get('name')}  ({group.get('member_count', 0)})")
            item.setData(Qt.ItemDataRole.UserRole, group)
            self._group_list.addItem(item)
        self._groups_status.setText(f'{len(groups)} group(s)' if groups else 'No groups.')

    def _on_group_selected(self) -> None:
        items = self._group_list.selectedItems()
        if not items or self._session is None:
            return
        group = items[0].data(Qt.ItemDataRole.UserRole)
        if not isinstance(group, dict):
            return
        group_id = str(group.get('id') or '')
        header = (
            f"{group.get('name')}\n"
            f"{group.get('short_code')}.{group.get('discriminator')}   ·   {group.get('member_count', 0)} members   ·   {group.get('privacy') or 'group'}\n\n"
            f"{group.get('description') or ''}\n\n"
            'Loading instances and posts…'
        )
        self._group_detail.setPlainText(header)
        session = self._session

        def _run() -> dict[str, Any]:
            from ..vrchat.groups import get_group_instances, get_group_posts
            return {
                'instances': _safe_call(lambda: get_group_instances(session, group_id)),
                'posts': _safe_call(lambda: get_group_posts(session, group_id, limit=8)),
            }

        def _done(data: dict[str, Any]) -> None:
            self._render_group_detail(group, data.get('instances') or [], data.get('posts') or [])

        self._run_worker(_run, _done, None)

    def _render_group_detail(self, group: dict[str, Any], instances: list[dict], posts: list[dict]) -> None:
        lines = [
            str(group.get('name') or 'Group'),
            f"{group.get('short_code')}.{group.get('discriminator')}   ·   {group.get('member_count', 0)} members   ·   {group.get('privacy') or 'group'}",
            '',
        ]
        if group.get('description'):
            lines += [str(group.get('description')), '']
        lines.append(f'Active instances ({len(instances)}):')
        if instances:
            for inst in instances:
                name = inst.get('world_name') or inst.get('world_id') or inst.get('location') or 'Instance'
                lines.append(f"  • {name} — {inst.get('member_count', 0)} here")
        else:
            lines.append('  (none right now)')
        lines.append('')
        lines.append(f'Recent posts ({len(posts)}):')
        if posts:
            for post in posts:
                title = post.get('title') or 'Post'
                text = post.get('text') or ''
                lines.append(f"  • {title}: {text}".rstrip(': '))
        else:
            lines.append('  (no posts)')
        self._group_detail.setPlainText('\n'.join(lines))

                        
    def _build_status_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._section('Quick presets'))
        self._presets_row = QHBoxLayout()
        self._presets_row.setSpacing(6)
        presets_box = QWidget()
        presets_box.setLayout(self._presets_row)
        layout.addWidget(presets_box)
        layout.addWidget(self._section('Status'))
        self._status_combo = QComboBox()
        from ..vrchat.profile import STATUS_LABELS, VALID_STATUS
        for value in VALID_STATUS:
            self._status_combo.addItem(STATUS_LABELS.get(value, value), value)
        layout.addWidget(self._status_combo)
        layout.addWidget(self._section('Status description (max 32 chars)'))
        self._status_desc = QLineEdit()
        self._status_desc.setMaxLength(32)
        layout.addWidget(self._status_desc)
        buttons = QHBoxLayout()
        save_btn = QPushButton('Apply status')
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._apply_status)
        save_preset_btn = QPushButton('Save as preset')
        save_preset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_preset_btn.clicked.connect(self._save_current_preset)
        buttons.addWidget(save_btn)
        buttons.addWidget(save_preset_btn)
        buttons.addStretch()
        layout.addLayout(buttons)
        self._status_msg = QLabel('')
        self._status_msg.setObjectName('panelHint')
        self._status_msg.setWordWrap(True)
        layout.addWidget(self._status_msg)
        layout.addStretch(1)
        self._prefill_status()
        return tab

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName('panelSectionHeader')
        return label

    def _refresh_status_presets(self) -> None:
        while self._presets_row.count():
            item = self._presets_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        presets = load_config().get('status_presets')
        if not isinstance(presets, list):
            presets = []
        for preset in presets:
            if not isinstance(preset, dict):
                continue
            button = QPushButton(str(preset.get('label') or preset.get('status') or 'Preset'))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, p=preset: self._apply_preset(p))
            self._presets_row.addWidget(button)
        self._presets_row.addStretch()

    def _apply_preset(self, preset: dict[str, Any]) -> None:
        status = str(preset.get('status') or 'active')
        index = self._status_combo.findData(status)
        if index >= 0:
            self._status_combo.setCurrentIndex(index)
        self._status_desc.setText(str(preset.get('description') or ''))
        self._apply_status()

    def _save_current_preset(self) -> None:
        presets = load_config().get('status_presets')
        if not isinstance(presets, list):
            presets = []
        label = (self._status_desc.text().strip() or self._status_combo.currentText())[:24]
        presets.append({'label': label, 'status': self._status_combo.currentData(), 'description': self._status_desc.text().strip()})
        save_config({'status_presets': presets})
        self._refresh_status_presets()
        self._status_msg.setText('Preset saved.')

    def _prefill_status(self) -> None:
        if self._session is None:
            return
        session = self._session

        def _run() -> Any:
            from ..vrchat.core import get_current_user_profile
            return get_current_user_profile(session)

        def _done(profile: Any) -> None:
            if profile is None:
                return
            api_status = str(getattr(getattr(profile, 'status', None), 'key', '') or '').replace('_', ' ')
            index = self._status_combo.findData(api_status)
            if index >= 0:
                self._status_combo.setCurrentIndex(index)
            self._status_desc.setText(str(getattr(profile, 'status_description', '') or ''))

        self._run_worker(_run, _done, None)

    def _apply_status(self) -> None:
        if self._session is None:
            self._status_msg.setText('Sign in to change your status.')
            return
        session = self._session
        status = self._status_combo.currentData()
        description = self._status_desc.text().strip()

        def _run() -> str:
            from ..vrchat.profile import update_status
            return update_status(session, status=status, status_description=description)

        worker = ApiActionWorker(_run, success_message='Status updated.', timeout_sec=15.0, context='update status')
        worker.finished_ok.connect(lambda msg: self._status_msg.setText(msg))
        worker.finished_error.connect(lambda msg: self._status_msg.setText(msg[:120]))
        worker.finished_ok.connect(lambda _m, w=worker: self._action_workers.discard(w))
        worker.finished_error.connect(lambda _m, w=worker: self._action_workers.discard(w))
        worker.finished_cancelled.connect(lambda w=worker: self._action_workers.discard(w))
        self._action_workers.add(worker)
        worker.start()

                                 
    def _run_worker(self, fn: Callable[[], Any], on_ok: Callable[[Any], None], status_label: QLabel | None) -> None:
        worker = _FnWorker(fn)
        worker.loaded.connect(on_ok)
        if status_label is not None:
            worker.failed.connect(lambda msg: status_label.setText(msg[:120]))
        else:
            worker.failed.connect(lambda msg: logger.debug('worker failed: %s', msg))
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        self._workers.add(worker)
        worker.start()

    def closeEvent(self, event):
        for worker in list(self._workers):
            if worker.isRunning():
                worker.wait(1500)
        for worker in list(self._action_workers):
            if worker.isRunning():
                worker.request_cancel()
                worker.wait(1500)
        super().closeEvent(event)


def _safe_call(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception:
        logger.debug('group sub-call failed', exc_info=True)
        return []


class _WorldRow(QFrame):
    def __init__(self, world: dict[str, Any], *, on_changed: Callable[[str], None]):
        super().__init__()
        self._world = world
        self._on_changed = on_changed
        world_id = str(world.get('id') or '')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        name = QLabel(str(world.get('name') or 'Unknown'))
        name.setStyleSheet('font-weight: 600;')
        author = QLabel(f"by {world.get('author_name') or 'unknown'}   ·   {world.get('occupants', 0)}/{world.get('capacity', 0)} here   ·   ♥ {world.get('favorites', 0)}")
        author.setStyleSheet('color: #9aa0a6; font-size: 9pt;')
        text_col.addWidget(name)
        text_col.addWidget(author)
        layout.addLayout(text_col, 1)
        self._fav_btn = QPushButton()
        self._fav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fav_btn.clicked.connect(self._toggle_fav)
        self._sync_fav_label()
        open_btn = QPushButton('Open')
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setToolTip('Open world page in browser')
        open_btn.clicked.connect(lambda: self._open_page(world_id))
        layout.addWidget(self._fav_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet('QFrame { border-bottom: 1px solid #2a2a2a; }')

    def _sync_fav_label(self) -> None:
        world_id = str(self._world.get('id') or '')
        on = favorites_repo.is_favorite(kind=favorites_repo.WORLD, target_id=world_id)
        self._fav_btn.setText('★ Saved' if on else '☆ Save')

    def _toggle_fav(self) -> None:
        world_id = str(self._world.get('id') or '')
        if not world_id:
            return
        favorites_repo.toggle_favorite(
            kind=favorites_repo.WORLD, target_id=world_id,
            name=str(self._world.get('name') or ''), image_url=str(self._world.get('image_url') or ''),
        )
        self._sync_fav_label()
        self._on_changed(world_id)

    def _open_page(self, world_id: str) -> None:
        from ..vrchat.worlds import world_page_url
        if world_id:
            webbrowser.open(world_page_url(world_id))
