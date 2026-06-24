from __future__ import annotations
from typing import Any, Callable
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton, QScrollArea,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)
from ..config import load_config, save_config
from ..logging_setup import get_logger
from ..store import analytics_repo, favorites_repo, feed_repo, gamelog_repo, notifications_repo
from ..theme import DASHBOARD_CARD_STYLE, STATUS_COLORS
from .feed_format import format_relative_time
from .panel_window import PanelWindow

logger = get_logger('ui')

WIDGET_DEFS: dict[str, str] = {
    'playtime': 'Playtime',
    'recent_activity': 'Recent activity',
    'notifications': 'Notifications',
    'favorites': 'Local favorites',
    'recent_worlds': 'Recent worlds',
    'media': 'Recent media',
    'server_status': 'VRChat status',
}

_STATUS_COLORS = STATUS_COLORS

_CARD_STYLE = DASHBOARD_CARD_STYLE


def _format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f'{hours}h {minutes}m'
    if minutes:
        return f'{minutes}m'
    return f'{seconds}s'


class _CardContent(QWidget):
    def __init__(self):
        super().__init__()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _line(self, text: str, object_name: str = 'cardLine') -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setWordWrap(False)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    def refresh(self) -> None:              
        pass


class _PlaytimeContent(_CardContent):
    def refresh(self) -> None:
        self._clear()
        try:
            import time
            week = analytics_repo.total_playtime_seconds(since_ts=time.time() - 7 * 86400)
            total = analytics_repo.total_playtime_seconds()
            sessions = analytics_repo.session_count(since_ts=time.time() - 7 * 86400)
        except Exception:
            self._layout.addWidget(self._line('Unavailable', 'cardMuted'))
            return
        self._layout.addWidget(self._line(_format_duration(week), 'cardBig'))
        self._layout.addWidget(self._line('in the last 7 days', 'cardMuted'))
        self._layout.addWidget(self._line(f'All time: {_format_duration(total)}'))
        self._layout.addWidget(self._line(f'Sessions (7d): {sessions}', 'cardMuted'))


class _RecentActivityContent(_CardContent):
    def refresh(self) -> None:
        self._clear()
        rows = _safe(lambda: feed_repo.recent_unified(limit=6), [])
        if not rows:
            self._layout.addWidget(self._line('No recent activity', 'cardMuted'))
            return
        for row in rows:
            primary = str(row.get('primary_text') or '')
            when = format_relative_time(float(row.get('ts') or 0))
            self._layout.addWidget(self._line(f'{primary}', 'cardLine'))
            self._layout.addWidget(self._line(f'{_kind_label(row.get("kind"))} · {when}', 'cardMuted'))


class _NotificationsContent(_CardContent):
    def refresh(self) -> None:
        self._clear()
        unseen = _safe(notifications_repo.unseen_count, 0)
        self._layout.addWidget(self._line(str(unseen), 'cardBig'))
        self._layout.addWidget(self._line('unread', 'cardMuted'))
        rows = _safe(lambda: notifications_repo.list_notifications(limit=3), [])
        for row in rows:
            sender = str(row.get('sender_username') or 'VRChat')
            message = str(row.get('message') or row.get('type') or '')
            self._layout.addWidget(self._line(f'{sender}: {message}'.strip(': '), 'cardLine'))
        if not rows:
            self._layout.addWidget(self._line('No notifications yet', 'cardMuted'))


class _FavoritesContent(_CardContent):
    def refresh(self) -> None:
        self._clear()
        friends = _safe(lambda: favorites_repo.count(kind=favorites_repo.FRIEND), 0)
        worlds = _safe(lambda: favorites_repo.count(kind=favorites_repo.WORLD), 0)
        avatars = _safe(lambda: favorites_repo.count(kind=favorites_repo.AVATAR), 0)
        self._layout.addWidget(self._line(f'{friends + worlds + avatars}', 'cardBig'))
        self._layout.addWidget(self._line('local favorites', 'cardMuted'))
        self._layout.addWidget(self._line(f'Friends: {friends}   Worlds: {worlds}   Avatars: {avatars}'))


class _RecentWorldsContent(_CardContent):
    def refresh(self) -> None:
        self._clear()
        rows = _safe(lambda: gamelog_repo.recent_locations(limit=6), [])
        if not rows:
            self._layout.addWidget(self._line('No world history yet', 'cardMuted'))
            return
        for row in rows:
            name = str(row.get('world_name') or row.get('location') or 'Unknown')
            when = format_relative_time(float(row.get('ts') or 0))
            self._layout.addWidget(self._line(name, 'cardLine'))
            self._layout.addWidget(self._line(when, 'cardMuted'))


class _MediaContent(_CardContent):
    def refresh(self) -> None:
        self._clear()
        rows = _safe(lambda: gamelog_repo.recent_media(limit=6), [])
        if not rows:
            self._layout.addWidget(self._line('No media detected yet', 'cardMuted'))
            return
        for row in rows:
            title = str(row.get('title') or row.get('url') or '')
            when = format_relative_time(float(row.get('ts') or 0))
            self._layout.addWidget(self._line(title, 'cardLine'))
            self._layout.addWidget(self._line(when, 'cardMuted'))


class _ServerStatusContent(_CardContent):
    def refresh(self) -> None:
        self._clear()
        summary = _safe(self._fetch_status, None)
        if summary is None:
            self._layout.addWidget(self._line('Status unavailable', 'cardMuted'))
            return
        indicator = str(summary.get('indicator') or 'unknown')
        color = _STATUS_COLORS.get(indicator, _STATUS_COLORS['unknown'])
        dot = self._line('\u25cf', 'cardBig')
        dot.setStyleSheet(f'color: {color};')
        self._layout.addWidget(dot)
        self._layout.addWidget(self._line(str(summary.get('description') or 'Unknown'), 'cardLine'))
        components = summary.get('components') or []
        for comp in components[:4]:
            name = str(comp.get('name') or '')
            status = str(comp.get('status') or '').replace('_', ' ')
            self._layout.addWidget(self._line(f'{name}: {status}', 'cardMuted'))

    def _fetch_status(self) -> dict[str, Any] | None:
        from ..services.server_status import ServerStatusService
        return ServerStatusService.instance().last()


_CONTENT_FACTORY: dict[str, Callable[[], _CardContent]] = {
    'playtime': _PlaytimeContent,
    'recent_activity': _RecentActivityContent,
    'notifications': _NotificationsContent,
    'favorites': _FavoritesContent,
    'recent_worlds': _RecentWorldsContent,
    'media': _MediaContent,
    'server_status': _ServerStatusContent,
}


def _safe(fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except Exception:
        logger.debug('dashboard card query failed', exc_info=True)
        return default


def _kind_label(kind: Any) -> str:
    text = str(kind or '')
    if text.startswith('friend:'):
        return text.split(':', 1)[1].capitalize()
    return text.capitalize() or 'Event'


class _DashboardCard(QFrame):
    def __init__(self, widget_id: str, *, on_move: Callable[[str, int], None], on_remove: Callable[[str], None]):
        super().__init__()
        self.widget_id = widget_id
        self.setObjectName('dashboardCard')
        self.setStyleSheet(_CARD_STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel(WIDGET_DEFS.get(widget_id, widget_id))
        title.setObjectName('cardTitle')
        up = self._tool('▲', 'Move up', lambda: on_move(widget_id, -1))
        down = self._tool('▼', 'Move down', lambda: on_move(widget_id, 1))
        close = self._tool('✕', 'Remove widget', lambda: on_remove(widget_id))
        header.addWidget(title)
        header.addStretch()
        header.addWidget(up)
        header.addWidget(down)
        header.addWidget(close)
        outer.addLayout(header)
        self._content = _CONTENT_FACTORY.get(widget_id, _CardContent)()
        outer.addWidget(self._content)

    def _tool(self, text: str, tip: str, handler: Callable[[], None]) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoRaise(True)
        button.clicked.connect(handler)
        return button

    def refresh(self) -> None:
        self._content.refresh()


class _DashboardPage(QWidget):
    def __init__(self, ids: list[str], on_changed: Callable[[], None]):
        super().__init__()
        self._ids = [wid for wid in ids if wid in WIDGET_DEFS]
        self._on_changed = on_changed
        self._cards: list[_DashboardCard] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        self._cards_layout = QVBoxLayout(container)
        self._cards_layout.setContentsMargins(2, 2, 2, 2)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll)
        self.render()

    def ids(self) -> list[str]:
        return list(self._ids)

    def render(self) -> None:
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards = []
        for wid in self._ids:
            card = _DashboardCard(wid, on_move=self._move, on_remove=self._remove)
            card.refresh()
            self._cards_layout.addWidget(card)
            self._cards.append(card)
        self._cards_layout.addStretch(1)
        if not self._ids:
            empty = QLabel('No widgets on this page. Use “Add widget”.')
            empty.setObjectName('panelHint')
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._cards_layout.insertWidget(0, empty)

    def refresh(self) -> None:
        for card in self._cards:
            card.refresh()

    def add_widget(self, widget_id: str) -> None:
        if widget_id in WIDGET_DEFS and widget_id not in self._ids:
            self._ids.append(widget_id)
            self.render()
            self._on_changed()

    def available_widgets(self) -> list[str]:
        return [wid for wid in WIDGET_DEFS if wid not in self._ids]

    def _move(self, widget_id: str, delta: int) -> None:
        if widget_id not in self._ids:
            return
        index = self._ids.index(widget_id)
        new_index = index + delta
        if new_index < 0 or new_index >= len(self._ids):
            return
        self._ids[index], self._ids[new_index] = self._ids[new_index], self._ids[index]
        self.render()
        self._on_changed()

    def _remove(self, widget_id: str) -> None:
        if widget_id in self._ids:
            self._ids.remove(widget_id)
            self.render()
            self._on_changed()


class DashboardWindow(PanelWindow):
    def __init__(self):
        super().__init__('Dashboard', width=440, height=620)
        self._loading = True
        header = QHBoxLayout()
        title = QLabel('Dashboard')
        title.setObjectName('panelSectionHeader')
        add_widget_btn = QPushButton('Add widget')
        add_widget_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_widget_btn.clicked.connect(self._show_add_widget_menu)
        add_page_btn = QPushButton('Add page')
        add_page_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_page_btn.clicked.connect(self._add_page)
        refresh_btn = QPushButton('Refresh')
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh_all)
        self._add_widget_btn = add_widget_btn
        header.addWidget(title)
        header.addStretch()
        header.addWidget(add_widget_btn)
        header.addWidget(add_page_btn)
        header.addWidget(refresh_btn)
        self.content_layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_page)
        self.tabs.tabBarDoubleClicked.connect(self._rename_page)
        self.tabs.currentChanged.connect(lambda _i: self._update_add_state())
        self.content_layout.addWidget(self.tabs, 1)

        hint = QLabel('Double-click a tab to rename. Use ▲▼ to reorder, ✕ to remove.')
        hint.setObjectName('panelHint')
        self.content_layout.addWidget(hint)

        self._load_layout()
        self._loading = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(800)
        self._refresh_timer.timeout.connect(self.refresh_all)
        self._connect_bus()

    def _connect_bus(self) -> None:
        try:
            from ..services.event_bus import VrcEventBus
            VrcEventBus.instance().event.connect(self._on_bus_event)
        except Exception:
            logger.debug('Dashboard could not connect to event bus', exc_info=True)

    def _on_bus_event(self, _kind: str, _payload: dict) -> None:
        if self.isVisible():
            self._refresh_timer.start()

    def _default_layout(self) -> list[dict[str, Any]]:
        return [{'name': 'Overview', 'widgets': ['playtime', 'recent_activity', 'notifications', 'favorites']}]

    def _load_layout(self) -> None:
        layout = load_config().get('dashboard_layout')
        if not isinstance(layout, list) or not layout:
            layout = self._default_layout()
        for page in layout:
            if not isinstance(page, dict):
                continue
            name = str(page.get('name') or 'Page')
            widgets = page.get('widgets')
            ids = [str(w) for w in widgets] if isinstance(widgets, list) else []
            self.tabs.addTab(_DashboardPage(ids, self._save_layout), name)
        if self.tabs.count() == 0:
            self.tabs.addTab(_DashboardPage(['playtime'], self._save_layout), 'Overview')
        self._update_add_state()

    def _save_layout(self) -> None:
        if self._loading:
            return
        layout = []
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, _DashboardPage):
                layout.append({'name': self.tabs.tabText(index), 'widgets': page.ids()})
        save_config({'dashboard_layout': layout})
        self._update_add_state()

    def _current_page(self) -> _DashboardPage | None:
        page = self.tabs.currentWidget()
        return page if isinstance(page, _DashboardPage) else None

    def _update_add_state(self) -> None:
        page = self._current_page()
        self._add_widget_btn.setEnabled(bool(page and page.available_widgets()))

    def _show_add_widget_menu(self) -> None:
        page = self._current_page()
        if page is None:
            return
        menu = QMenu(self)
        for wid in page.available_widgets():
            menu.addAction(WIDGET_DEFS[wid]).triggered.connect(lambda _checked=False, w=wid: page.add_widget(w))
        if menu.isEmpty():
            menu.addAction('All widgets added').setEnabled(False)
        menu.exec(self._add_widget_btn.mapToGlobal(self._add_widget_btn.rect().bottomLeft()))

    def _add_page(self) -> None:
        name, ok = QInputDialog.getText(self, 'Add page', 'Page name:')
        if ok and name.strip():
            index = self.tabs.addTab(_DashboardPage([], self._save_layout), name.strip())
            self.tabs.setCurrentIndex(index)
            self._save_layout()

    def _close_page(self, index: int) -> None:
        if self.tabs.count() <= 1:
            return
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()
        self._save_layout()

    def _rename_page(self, index: int) -> None:
        if index < 0:
            return
        current = self.tabs.tabText(index)
        name, ok = QInputDialog.getText(self, 'Rename page', 'Page name:', text=current)
        if ok and name.strip():
            self.tabs.setTabText(index, name.strip())
            self._save_layout()

    def refresh_all(self) -> None:
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, _DashboardPage):
                page.refresh()

    def present(self) -> None:
        self.refresh_all()
        super().present()

    def closeEvent(self, event):
        try:
            from ..services.event_bus import VrcEventBus
            VrcEventBus.instance().event.disconnect(self._on_bus_event)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)
