from __future__ import annotations
from typing import Any, Callable
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QTabWidget, QVBoxLayout, QWidget
from ..logging_setup import get_logger
from ..services.event_bus import VrcEventBus
from ..store import feed_repo, gamelog_repo
from .feed_format import format_abs_time, format_instance_type, format_relative_time
from .panel_window import PanelWindow

logger = get_logger('ui')

_PAGE_SIZE = 200
_COLORS = {
    'online': QColor('#7db87d'),
    'offline': QColor('#9a9a9a'),
    'join': QColor('#7db87d'),
    'leave': QColor('#e0a070'),
    'location': QColor('#4ea3ff'),
    'media': QColor('#b08ad0'),
}
_FRIEND_ICONS = {
    'online': '●', 'offline': '○', 'status': '◆', 'location': '➜',
    'avatar': '☻', 'bio': '✎', 'displayName': '✎', 'trustLevel': '⬆',
    'friendAdded': '✚', 'friendRemoved': '✖',
}


class _FeedTab(QWidget):
    """One scrollable list backed by a store query, with incremental paging."""

    def __init__(self, loader: Callable[[int], list[dict[str, Any]]], renderer: Callable[[dict[str, Any]], tuple[str, str, QColor | None]], empty_hint: str, *, on_activate: Callable[[dict[str, Any]], None] | None = None):
        super().__init__()
        self._loader = loader
        self._renderer = renderer
        self._on_activate = on_activate
        self._limit = _PAGE_SIZE
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(6)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        if on_activate is not None:
            self.list.itemDoubleClicked.connect(self._handle_activate)
        layout.addWidget(self.list, 1)
        footer = QHBoxLayout()
        self.count_label = QLabel('')
        self.count_label.setObjectName('panelHint')
        self.more_btn = QPushButton('Load older')
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.clicked.connect(self._load_more)
        footer.addWidget(self.count_label)
        footer.addStretch()
        footer.addWidget(self.more_btn)
        layout.addLayout(footer)
        self._empty_hint = empty_hint

    def _handle_activate(self, item: QListWidgetItem) -> None:
        if self._on_activate is None:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(row, dict):
            self._on_activate(row)

    def _load_more(self) -> None:
        self._limit += _PAGE_SIZE
        self.reload()

    def reload(self) -> None:
        try:
            rows = self._loader(self._limit)
        except Exception:
            logger.debug('Feed tab load failed', exc_info=True)
            rows = []
        self.list.clear()
        for row in rows:
            text, tooltip, color = self._renderer(row)
            item = QListWidgetItem(text)
            if tooltip:
                item.setToolTip(tooltip)
            if color is not None:
                item.setForeground(color)
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.list.addItem(item)
        if not rows:
            placeholder = QListWidgetItem(self._empty_hint)
            placeholder.setForeground(QColor('#777777'))
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(placeholder)
        self.count_label.setText(f'{len(rows)} entr{"y" if len(rows) == 1 else "ies"}')
        self.more_btn.setVisible(len(rows) >= self._limit)


class ActivityFeedWindow(PanelWindow):
    def __init__(self):
        super().__init__('Activity', width=580, height=760)
        self._data_window = None
        header = QHBoxLayout()
        title = QLabel('Activity & History')
        title.setObjectName('panelSectionHeader')
        data_btn = QPushButton('Data…')
        data_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        data_btn.setToolTip('Favorites, backup & export')
        data_btn.clicked.connect(self._open_data_tools)
        refresh = QPushButton('Refresh')
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.clicked.connect(self.reload_all)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(data_btn)
        header.addWidget(refresh)
        self.content_layout.addLayout(header)

        self.tabs = QTabWidget()
        self.content_layout.addWidget(self.tabs, 1)
        self._feed_tab = _FeedTab(self._load_feed, self._render_feed, 'No activity recorded yet.')
        self._worlds_tab = _FeedTab(self._load_worlds, self._render_world, 'No world history yet.')
        self._players_tab = _FeedTab(self._load_players, self._render_player, 'No players recorded yet.')
        self._media_tab = _FeedTab(self._load_media, self._render_media, 'No videos/media detected yet.\nMedia played in worlds will appear here.', on_activate=self._open_media)
        self._friends_tab = _FeedTab(self._load_friends, self._render_friend, 'No friend events yet.\nEnable the VRChat pipeline in Settings for live friend tracking.')
        self.tabs.addTab(self._feed_tab, 'Feed')
        self.tabs.addTab(self._worlds_tab, 'Worlds')
        self.tabs.addTab(self._players_tab, 'Players')
        self.tabs.addTab(self._media_tab, 'Media')
        self.tabs.addTab(self._friends_tab, 'Friends')

        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(400)
        self._reload_timer.timeout.connect(self.reload_all)
        VrcEventBus.instance().event.connect(self._on_bus_event)
        self.reload_all()

    def _open_data_tools(self) -> None:
        if self._data_window is None:
            from .data_tools_window import DataToolsWindow
            self._data_window = DataToolsWindow()
        self._data_window.reload_favorites()
        self._data_window.present()

    def _on_bus_event(self, _kind: str, _payload: dict) -> None:
        if self.isVisible():
            self._reload_timer.start()

    def reload_all(self) -> None:
        for tab in (self._feed_tab, self._worlds_tab, self._players_tab, self._media_tab, self._friends_tab):
            tab.reload()

    def _load_feed(self, limit: int) -> list[dict[str, Any]]:
        return feed_repo.recent_unified(limit=limit)

    def _load_worlds(self, limit: int) -> list[dict[str, Any]]:
        return gamelog_repo.recent_locations(limit=limit)

    def _load_players(self, limit: int) -> list[dict[str, Any]]:
        return gamelog_repo.recent_join_leave(limit=limit)

    def _load_friends(self, limit: int) -> list[dict[str, Any]]:
        return feed_repo.recent_friend_log(limit=limit)

    def _load_media(self, limit: int) -> list[dict[str, Any]]:
        return gamelog_repo.recent_media(limit=limit)

    def _open_media(self, row: dict[str, Any]) -> None:
        url = str(row.get('url') or '')
        if url.startswith('http'):
            import webbrowser
            webbrowser.open(url)

    def _render_feed(self, row: dict[str, Any]) -> tuple[str, str, QColor | None]:
        ts = float(row.get('ts') or 0)
        kind = str(row.get('kind') or '')
        primary = str(row.get('primary_text') or '')
        secondary = str(row.get('secondary_text') or '')
        if kind.startswith('friend:'):
            sub = kind.split(':', 1)[1]
            icon = _FRIEND_ICONS.get(sub, '•')
            label = f'{primary} {sub}' + (f' — {secondary}' if secondary else '')
            color = _COLORS.get(sub)
        elif kind == 'location':
            icon = '🌐'
            label = primary + (f'  [{format_instance_type(secondary)}]' if secondary else '')
            color = _COLORS['location']
        elif kind == 'media':
            icon = '▶'
            label = primary
            color = _COLORS['media']
        else:
            icon = '•'
            label = primary
            color = None
        return (f'{format_relative_time(ts):>8}  {icon}  {label}', format_abs_time(ts), color)

    def _render_world(self, row: dict[str, Any]) -> tuple[str, str, QColor | None]:
        ts = float(row.get('ts') or 0)
        name = str(row.get('world_name') or '') or str(row.get('location') or '')
        itype = format_instance_type(str(row.get('instance_type') or ''))
        region = str(row.get('region') or '').upper()
        bits = [b for b in (itype, region) if b]
        suffix = f'  [{" · ".join(bits)}]' if bits else ''
        return (f'{format_relative_time(ts):>8}  🌐  {name}{suffix}', format_abs_time(ts), _COLORS['location'])

    def _render_player(self, row: dict[str, Any]) -> tuple[str, str, QColor | None]:
        ts = float(row.get('ts') or 0)
        kind = str(row.get('kind') or '')
        name = str(row.get('display_name') or '') or str(row.get('user_id') or '')
        icon = '➕' if kind == 'join' else '➖'
        return (f'{format_relative_time(ts):>8}  {icon}  {name}', format_abs_time(ts), _COLORS.get(kind))

    def _render_media(self, row: dict[str, Any]) -> tuple[str, str, QColor | None]:
        ts = float(row.get('ts') or 0)
        url = str(row.get('url') or '')
        title = str(row.get('title') or '')
        source = str(row.get('source') or '')
        label = title or url
        suffix = '  (added by user)' if source == 'user_added' else ''
        tooltip = f'{url}\n{format_abs_time(ts)}\nDouble-click to open'
        return (f'{format_relative_time(ts):>8}  ▶  {label}{suffix}', tooltip, _COLORS['media'])

    def _render_friend(self, row: dict[str, Any]) -> tuple[str, str, QColor | None]:
        ts = float(row.get('ts') or 0)
        ftype = str(row.get('type') or '')
        name = str(row.get('display_name') or '')
        current = str(row.get('current') or '')
        icon = _FRIEND_ICONS.get(ftype, '•')
        label = f'{name} {ftype}' + (f' — {current}' if current else '')
        return (f'{format_relative_time(ts):>8}  {icon}  {label}', format_abs_time(ts), _COLORS.get(ftype))

    def closeEvent(self, event):
        try:
            VrcEventBus.instance().event.disconnect(self._on_bus_event)
        except (TypeError, RuntimeError):
            pass
        if self._data_window is not None:
            self._data_window.close()
        super().closeEvent(event)
