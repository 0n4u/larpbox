from __future__ import annotations
from collections.abc import Callable
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetricsF, QTextOption
from PyQt6.QtWidgets import QLabel, QTextEdit
from .chatbox_layout import MAX_PAYLOAD_CHARS, ChatboxLayout, VRCHAT_WRAP_WIDTH_PX, layout_chatbox_payload
from .ui_animations import flash_widget
_BASE_FONT_PT = 10.0
_MIN_FONT_PT = 5.0

class ChatboxPreview:

    def __init__(self, preview_text: QTextEdit, preview_meta: QLabel, format_message: Callable[[str], str]):
        self.preview_text = preview_text
        self.preview_meta = preview_meta
        self._format_message = format_message
        self._layout_font = QFont('Segoe UI', int(_BASE_FONT_PT))
        self._font_metrics: QFontMetricsF | None = None
        self._last_render_key: tuple[str, float, int] | None = None
        self._last_meta: str | None = None
        self._last_formatted: str = ''
        self._last_label: str = ''
        self.preview_text.setFont(self._layout_font)
        if isinstance(self.preview_text, QTextEdit):
            self.preview_text.document().setDocumentMargin(2)
            option = QTextOption()
            option.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview_text.document().setDefaultTextOption(option)

    def _font_metrics_for_layout(self) -> QFontMetricsF:
        if self._font_metrics is None:
            self._font_metrics = QFontMetricsF(self._layout_font)
        return self._font_metrics

    def _measure(self) -> Callable[[str], float]:
        fm = self._font_metrics_for_layout()

        def measure(text: str) -> float:
            return fm.horizontalAdvance(text)
        return measure

    def _layout_payload(self, formatted: str) -> ChatboxLayout:
        return layout_chatbox_payload(formatted, max_width_units=VRCHAT_WRAP_WIDTH_PX, measure=self._measure())

    def _viewport_size(self) -> tuple[int, int]:
        width = self.preview_text.viewport().width()
        height = self.preview_text.viewport().height()
        try:
            width = int(width)
        except (TypeError, ValueError):
            width = 0
        try:
            height = int(height)
        except (TypeError, ValueError):
            height = 0
        if width <= 0:
            width = max(120, self.preview_text.width() - 14)
        if height <= 0:
            height = max(80, self.preview_text.height() - 14)
        return (width, height)

    def _base_line_height_px(self) -> float:
        return self._font_metrics_for_layout().lineSpacing()

    def _preview_scale(self, layout: ChatboxLayout, viewport_w: int, viewport_h: int) -> float:
        scale_x = viewport_w / VRCHAT_WRAP_WIDTH_PX
        base_line_h = self._base_line_height_px()
        content_h = max(base_line_h, layout.line_count * base_line_h)
        scale_y = viewport_h / content_h if content_h > 0 else scale_x
        return min(scale_x, scale_y, 1.0)

    def _display_font(self, scale: float) -> QFont:
        font = QFont(self._layout_font)
        font.setPointSizeF(max(_MIN_FONT_PT, _BASE_FONT_PT * scale))
        return font

    def _vertical_margin(self, layout: ChatboxLayout, scale: float, viewport_h: int) -> int:
        display_font = self._display_font(scale)
        line_h = QFontMetricsF(display_font).lineSpacing()
        content_h = max(line_h, layout.line_count * line_h)
        if layout.mode == 'extreme_height':
            return max(0, int(viewport_h - content_h))
        return max(0, int((viewport_h - content_h) / 2))

    def _render_text(self, layout: ChatboxLayout) -> str:
        return '\n'.join(layout.lines)

    def _apply_layout(self, formatted: str, label: str) -> None:
        self._last_formatted = formatted
        self._last_label = label
        layout = self._layout_payload(formatted)
        viewport_w, viewport_h = self._viewport_size()
        scale = self._preview_scale(layout, viewport_w, viewport_h)
        rendered = self._render_text(layout)
        top_margin = self._vertical_margin(layout, scale, viewport_h)
        render_key = (rendered, round(scale, 4), top_margin)
        meta = f'{layout.payload_length}/{MAX_PAYLOAD_CHARS}'
        if label:
            meta = f'{label} • {meta}'
        if render_key != self._last_render_key:
            self.preview_text.blockSignals(True)
            self.preview_text.setFont(self._display_font(scale))
            self.preview_text.setViewportMargins(0, top_margin, 0, 0)
            self.preview_text.setPlainText(rendered)
            self.preview_text.blockSignals(False)
            self._last_render_key = render_key
            flash_widget(self.preview_text, duration=240, dip=0.82)
        if meta != self._last_meta:
            self.preview_meta.setText(meta)
            self._last_meta = meta
            flash_widget(self.preview_meta, duration=200, dip=0.55)

    def refresh_geometry(self) -> None:
        if self._last_formatted:
            self._last_render_key = None
            self._apply_layout(self._last_formatted, self._last_label)

    def set_message(self, raw_message: str, label: str='') -> None:
        try:
            formatted = self._format_message(raw_message)
            self._apply_layout(formatted, label)
        except Exception:
            pass

    def set_payload(self, formatted_payload: str, label: str='') -> None:
        try:
            self._apply_layout(formatted_payload, label)
        except Exception:
            pass
