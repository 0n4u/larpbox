from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QLayout, QScrollArea, QWidget

W = TypeVar('W', bound=QWidget)

DEFAULT_SCROLL_DEBOUNCE_MS = 60
DEFAULT_VIEWPORT_MARGIN_PX = 42


class ScrollDebouncer(QObject):
    """Coalesce rapid scroll valueChanged events into a single callback."""

    def __init__(
        self,
        parent: QObject | None,
        callback: Callable[[], None],
        *,
        delay_ms: int = DEFAULT_SCROLL_DEBOUNCE_MS,
    ) -> None:
        super().__init__(parent)
        self._callback = callback
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._fire)

    def schedule(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.stop()

    def is_pending(self) -> bool:
        return self._timer.isActive()

    def _fire(self) -> None:
        self._callback()


def visible_widgets_in_layout(
    scroll: QScrollArea,
    layout: QLayout,
    widget_type: type[W],
    *,
    margin_px: int = DEFAULT_VIEWPORT_MARGIN_PX,
    skip_trailing_stretch: bool = True,
) -> list[W]:
    """Return layout child widgets of widget_type that intersect the scroll viewport."""
    if not scroll.isVisible():
        return []
    viewport = scroll.viewport()
    if viewport is None:
        return []
    view_height = viewport.height()
    visible: list[W] = []
    count = layout.count()
    end = count - 1 if skip_trailing_stretch and count > 0 else count
    for index in range(end):
        widget = layout.itemAt(index).widget()
        if not isinstance(widget, widget_type):
            continue
        row_top = widget.mapTo(viewport, widget.rect().topLeft()).y()
        row_bottom = row_top + widget.height()
        if row_bottom < -margin_px or row_top > view_height + margin_px:
            continue
        visible.append(widget)
    return visible


def visible_indices_in_layout(
    scroll: QScrollArea,
    layout: QLayout,
    widget_type: type,
    *,
    margin_px: int = DEFAULT_VIEWPORT_MARGIN_PX,
    skip_trailing_stretch: bool = True,
) -> list[int]:
    """Return layout indices for widgets of widget_type visible in the scroll viewport."""
    if not scroll.isVisible():
        return []
    viewport = scroll.viewport()
    if viewport is None:
        return []
    view_height = viewport.height()
    indices: list[int] = []
    count = layout.count()
    end = count - 1 if skip_trailing_stretch and count > 0 else count
    for index in range(end):
        widget = layout.itemAt(index).widget()
        if not isinstance(widget, widget_type):
            continue
        row_top = widget.mapTo(viewport, widget.rect().topLeft()).y()
        row_bottom = row_top + widget.height()
        if row_bottom < -margin_px or row_top > view_height + margin_px:
            continue
        indices.append(index)
    return indices


def visible_widgets_in_host(
    scroll: QScrollArea,
    host: QWidget,
    widget_type: type[W],
    *,
    margin_px: int = DEFAULT_VIEWPORT_MARGIN_PX,
) -> list[W]:
    """Return direct child widgets of host that intersect the scroll viewport."""
    if not scroll.isVisible():
        return []
    viewport = scroll.viewport()
    if viewport is None:
        return []
    view_height = viewport.height()
    visible: list[W] = []
    for child in host.children():
        if not isinstance(child, widget_type):
            continue
        widget = child
        row_top = widget.mapTo(viewport, widget.rect().topLeft()).y()
        row_bottom = row_top + widget.height()
        if row_bottom < -margin_px or row_top > view_height + margin_px:
            continue
        visible.append(widget)
    return visible
