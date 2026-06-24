from __future__ import annotations
import time
from typing import Any
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
from ..logging_setup import get_logger
from ..store import analytics_repo
from ..theme import COLOR_ACCENT, COLOR_BG_INPUT, COLOR_TEXT, COLOR_TEXT_MUTED, COLOR_TEXT_SUBTLE
from .panel_window import PanelWindow

logger = get_logger('ui')

_DAY_LABELS = ('Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat')
_PERIODS = (('All time', None), ('Last 7 days', 7), ('Last 30 days', 30), ('Last 90 days', 90))
_HEAT_EMPTY = QColor(COLOR_BG_INPUT)
_HEAT_BASE = QColor(COLOR_ACCENT)


def _format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f'{hours}h {minutes}m'
    if hours:
        return f'{hours}h'
    if minutes:
        return f'{minutes}m'
    return f'{seconds}s'


class _HeatmapWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._matrix = [[0] * 24 for _ in range(7)]
        self._max = 0
        self.setMinimumHeight(190)

    def set_data(self, buckets: list[dict[str, Any]]) -> None:
        self._matrix = [[0] * 24 for _ in range(7)]
        self._max = 0
        for row in buckets:
            weekday = int(row.get('weekday') or 0) % 7
            hour = int(row.get('hour') or 0) % 24
            count = int(row.get('count') or 0)
            self._matrix[weekday][hour] = count
            self._max = max(self._max, count)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        left_pad = 34
        top_pad = 4
        bottom_pad = 16
        grid_w = self.width() - left_pad - 6
        grid_h = self.height() - top_pad - bottom_pad
        if grid_w <= 0 or grid_h <= 0:
            return
        cell_w = grid_w / 24.0
        cell_h = grid_h / 7.0
        label_font = QFont()
        label_font.setPointSize(8)
        painter.setFont(label_font)
        for day in range(7):
            y = top_pad + day * cell_h
            painter.setPen(QPen(QColor(COLOR_TEXT_SUBTLE)))
            painter.drawText(QRectF(0, y, left_pad - 4, cell_h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _DAY_LABELS[day])
            for hour in range(24):
                x = left_pad + hour * cell_w
                count = self._matrix[day][hour]
                painter.fillRect(QRectF(x + 1, y + 1, cell_w - 2, cell_h - 2), QBrush(self._color_for(count)))
        painter.setPen(QPen(QColor(COLOR_TEXT_MUTED)))
        for hour in (0, 6, 12, 18):
            x = left_pad + hour * cell_w
            painter.drawText(QRectF(x, top_pad + 7 * cell_h, cell_w * 4, bottom_pad), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f'{hour:02d}h')
        painter.end()

    def _color_for(self, count: int) -> QColor:
        if count <= 0 or self._max <= 0:
            return _HEAT_EMPTY
        ratio = count / self._max
        alpha = int(60 + ratio * 195)
        return QColor(_HEAT_BASE.red(), _HEAT_BASE.green(), _HEAT_BASE.blue(), max(0, min(255, alpha)))


class _BarsWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._rows: list[tuple[str, float, int]] = []
        self._max = 0.0
        self.setMinimumHeight(60)

    def set_data(self, rows: list[tuple[str, float, int]]) -> None:
        self._rows = rows
        self._max = max((seconds for _, seconds, _ in rows), default=0.0)
        self.setMinimumHeight(max(60, len(rows) * 30 + 6))
        self.update()

    def paintEvent(self, _event) -> None:
        if not self._rows:
            painter = QPainter(self)
            painter.setPen(QPen(QColor(COLOR_TEXT_MUTED)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'No playtime recorded yet')
            painter.end()
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        row_h = 28.0
        label_w = min(220.0, self.width() * 0.4)
        bar_x = label_w + 8
        bar_max_w = self.width() - bar_x - 90
        for index, (label, seconds, visits) in enumerate(self._rows):
            y = index * row_h + 2
            painter.setPen(QPen(QColor(COLOR_TEXT)))
            painter.drawText(QRectF(0, y, label_w, row_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._elide(painter, label, label_w))
            ratio = (seconds / self._max) if self._max > 0 else 0
            width = max(2.0, bar_max_w * ratio)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(COLOR_ACCENT)))
            painter.drawRoundedRect(QRectF(bar_x, y + 5, width, row_h - 12), 3, 3)
            painter.setPen(QPen(QColor(COLOR_TEXT_SUBTLE)))
            painter.drawText(QRectF(bar_x + width + 6, y, 84, row_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f'{_format_duration(seconds)} · {visits}x')
        painter.end()

    def _elide(self, painter: QPainter, text: str, width: float) -> str:
        metrics = painter.fontMetrics()
        return metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(width))


class AnalyticsWindow(PanelWindow):
    def __init__(self):
        super().__init__('Analytics', width=660, height=700)
        header = QHBoxLayout()
        title = QLabel('Playtime & Activity')
        title.setObjectName('panelSectionHeader')
        self._period = QComboBox()
        for label, _ in _PERIODS:
            self._period.addItem(label)
        self._period.setCurrentIndex(2)
        self._period.currentIndexChanged.connect(self.reload)
        refresh = QPushButton('Refresh')
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.clicked.connect(self.reload)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel('Period:'))
        header.addWidget(self._period)
        header.addWidget(refresh)
        self.content_layout.addLayout(header)

        self._summary = QLabel('')
        self._summary.setObjectName('panelHint')
        self._summary.setWordWrap(True)
        self.content_layout.addWidget(self._summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 4, 0, 0)
        body_layout.setSpacing(10)
        body_layout.addWidget(self._section_label('Activity heatmap (local time)'))
        self._heatmap = _HeatmapWidget()
        body_layout.addWidget(self._heatmap)
        body_layout.addWidget(self._section_label('Top worlds by playtime'))
        self._bars = _BarsWidget()
        body_layout.addWidget(self._bars)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        self.content_layout.addWidget(scroll, 1)
        self.reload()

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName('panelSectionHeader')
        return label

    def _since_ts(self) -> float | None:
        days = _PERIODS[self._period.currentIndex()][1]
        if not days:
            return None
        return time.time() - days * 86400

    def reload(self) -> None:
        try:
            since = self._since_ts()
            total = analytics_repo.total_playtime_seconds(since_ts=since)
            sessions = analytics_repo.session_count(since_ts=since)
            worlds = analytics_repo.playtime_by_world(since_ts=since, limit=12)
            heatmap = analytics_repo.activity_heatmap(since_ts=since)
            unique_worlds = len(analytics_repo.playtime_by_world(since_ts=since, limit=10000))
            busiest = self._busiest_hour(heatmap)
            self._summary.setText(
                f'Total playtime: {_format_duration(total)}    ·    Sessions: {sessions}'
                f'    ·    Unique worlds: {unique_worlds}    ·    Busiest hour: {busiest}'
            )
            self._heatmap.set_data(heatmap)
            self._bars.set_data([
                (str(row.get('world_name') or row.get('world_id') or 'Unknown'), float(row.get('seconds') or 0), int(row.get('visits') or 0))
                for row in worlds
            ])
        except Exception:
            logger.warning('Failed to load analytics', exc_info=True)
            self._summary.setText('Analytics unavailable.')

    def _busiest_hour(self, buckets: list[dict[str, Any]]) -> str:
        totals: dict[int, int] = {}
        for row in buckets:
            hour = int(row.get('hour') or 0) % 24
            totals[hour] = totals.get(hour, 0) + int(row.get('count') or 0)
        if not totals:
            return '—'
        best = max(totals, key=lambda h: totals[h])
        return f'{best:02d}:00'
