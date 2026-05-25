from __future__ import annotations
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt, QPoint, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget
from ..config import get_bool, load_config
from ..logging_setup import get_logger
from ..services.notifications import NotificationBus
from ..services.vrchat_window import find_vrchat_window, is_vrchat_foreground
from ..theme import dark_theme
from ..ui_animations import fade_in_widget, fade_out_widget, stop_widget_animations
logger = get_logger('notification_overlay')
_TOAST_WIDTH = 320
_MARGIN = 14
_GAP = 8
_MAX_TOASTS = 5
_DISPLAY_MS = 4500
_TRACK_MS = 150
_ANIM_MS = 260

class _Toast(QFrame):
    dismiss_requested = pyqtSignal(object)

    def __init__(self, title: str, body: str, level: str, parent: QWidget):
        super().__init__(parent)
        self.setObjectName('notifyToast')
        self.setFixedWidth(_TOAST_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName('notifyTitle')
        layout.addWidget(title_label)
        if body:
            body_label = QLabel(body)
            body_label.setObjectName('notifyBody')
            body_label.setWordWrap(True)
            layout.addWidget(body_label)
        accent = {'info': '#4ea3ff', 'warning': '#e8a040', 'error': '#e07070'}.get(level, '#4ea3ff')
        self.setStyleSheet(dark_theme(f'\n            QFrame#notifyToast {{\n                background-color: #1e1e1e;\n                border: 1px solid #3a3a3a;\n                border-left: 3px solid {accent};\n                border-radius: 8px;\n            }}\n            QLabel#notifyTitle {{\n                color: #f0f0f0;\n                font-weight: 600;\n                font-size: 10pt;\n            }}\n            QLabel#notifyBody {{\n                color: #9aa0a6;\n                font-size: 9pt;\n            }}\n        '))
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(lambda: self.dismiss_requested.emit(self))

    def start_lifetime(self, ms: int=_DISPLAY_MS) -> None:
        self._dismiss_timer.start(ms)

    def stop_lifetime(self) -> None:
        self._dismiss_timer.stop()

class NotificationOverlay(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet('background: transparent;')
        self._toasts: list[_Toast] = []
        self._move_anims: dict[_Toast, QPropertyAnimation] = {}
        NotificationBus.instance().notify.connect(self._on_notify)
        self._track_timer = QTimer(self)
        self._track_timer.setInterval(_TRACK_MS)
        self._track_timer.timeout.connect(self._sync_with_vrchat)
        self._track_timer.start()

    def _notifications_enabled(self) -> bool:
        return get_bool(load_config().get('enable_notifications', True))

    def _should_show(self) -> bool:
        return self._notifications_enabled() and find_vrchat_window() is not None and is_vrchat_foreground()

    def _sync_with_vrchat(self) -> None:
        if not self._toasts:
            self.hide()
            return
        if not self._should_show():
            self.hide()
            return
        self._apply_window_geometry()
        self.show()
        self._layout_toasts(animate=True)

    def _apply_window_geometry(self) -> None:
        info = find_vrchat_window()
        if info is None:
            return
        _, rect = info
        stack_height = self._stack_height()
        height = min(rect.height - _MARGIN * 2, max(stack_height, 48))
        x = rect.right - _MARGIN - _TOAST_WIDTH
        y = rect.top + _MARGIN
        self.setGeometry(x, y, _TOAST_WIDTH, height)

    def _stack_height(self) -> int:
        if not self._toasts:
            return 0
        total = sum((toast.sizeHint().height() for toast in self._toasts))
        total += _GAP * max(0, len(self._toasts) - 1)
        return total

    def _layout_toasts(self, *, animate: bool) -> None:
        y = 0
        for toast in self._toasts:
            target_x = 0
            target_y = y
            if animate:
                self._animate_toast_move(toast, target_x, target_y)
            else:
                toast.move(target_x, target_y)
            y += toast.height() + _GAP

    def _animate_toast_move(self, toast: _Toast, x: int, y: int) -> None:
        current = self._move_anims.pop(toast, None)
        if current is not None:
            current.stop()
        anim = QPropertyAnimation(toast, b'pos', self)
        anim.setDuration(_ANIM_MS)
        anim.setStartValue(toast.pos())
        anim.setEndValue(QPoint(x, y))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: self._move_anims.pop(toast, None))
        self._move_anims[toast] = anim
        anim.start()

    def _on_notify(self, title: str, body: str, level: str) -> None:
        if not self._notifications_enabled():
            return
        if not self._should_show():
            return
        while len(self._toasts) >= _MAX_TOASTS:
            oldest = self._toasts[-1]
            self._dismiss_toast(oldest, immediate=True)
        toast = _Toast(title, body, level, self)
        toast.dismiss_requested.connect(self._dismiss_toast)
        toast.adjustSize()
        toast.setFixedWidth(_TOAST_WIDTH)
        toast.move(_TOAST_WIDTH + 24, 0)
        toast.show()
        self._toasts.insert(0, toast)
        self._apply_window_geometry()
        self.show()
        fade_in_widget(toast, duration=_ANIM_MS, easing=QEasingCurve.Type.OutCubic)
        toast.start_lifetime()
        self._layout_toasts(animate=True)

    def _dismiss_toast(self, toast: _Toast, *, immediate: bool=False) -> None:
        if toast not in self._toasts:
            return
        toast.stop_lifetime()
        move_anim = self._move_anims.pop(toast, None)
        if move_anim is not None:
            move_anim.stop()

        def _remove() -> None:
            if toast in self._toasts:
                self._toasts.remove(toast)
            stop_widget_animations(toast)
            toast.deleteLater()
            if not self._toasts:
                self.hide()
                return
            if self._should_show():
                self._apply_window_geometry()
                self._layout_toasts(animate=True)
                self.show()
            else:
                self.hide()
        if immediate:
            _remove()
            return
        slide = QPropertyAnimation(toast, b'pos', self)
        slide.setDuration(_ANIM_MS)
        slide.setStartValue(toast.pos())
        slide.setEndValue(QPoint(toast.x() + 48, toast.y()))
        slide.setEasingCurve(QEasingCurve.Type.InCubic)
        fade_out_widget(toast, duration=_ANIM_MS, hide_after=False)
        slide.finished.connect(_remove)
        slide.start()

    def reposition(self) -> None:
        self._sync_with_vrchat()

    def cleanup(self) -> None:
        self._track_timer.stop()
        for toast in list(self._toasts):
            toast.stop_lifetime()
            stop_widget_animations(toast)
        self._toasts.clear()
        self._move_anims.clear()
        self.hide()
        self.close()
