from __future__ import annotations
import time
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget
from ..config import get_bool, load_config_cached
from ..logging_setup import debug_event, get_logger, truncate_for_log
from ..safe_runtime import safe_call
from ..services.notifications import NotificationBus
from ..services.vrchat_window import find_vrchat_window, is_vrchat_foreground
from ..theme import dark_theme
from ..ui_animations import fade_in_widget, fade_out_widget, stop_widget_animations
logger = get_logger('notification_overlay')
_TOAST_WIDTH = 340
_MARGIN = 16
_GAP = 10
_MAX_TOASTS = 4
_DISPLAY_MS = 3200
_ERROR_DISPLAY_MS = 4500
_TRACK_MS = 100
_ANIM_MS = 220
_LEVEL_META = {'info': ('ℹ', '#4ea3ff', 'info'), 'warning': ('⚠', '#e8a040', 'warning'), 'error': ('✕', '#e07070', 'error')}

class _Toast(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, title: str, body: str, level: str, action: str, parent: QWidget):
        super().__init__(parent)
        self._action = action or ''
        self._expires_at = 0.0
        self._total_ms = _DISPLAY_MS
        self._dismissing = False
        self.setObjectName('notifyToast')
        self.setFixedWidth(_TOAST_WIDTH)
        if self._action:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip('Click to open larpbox')
        icon_text, accent, _ = _LEVEL_META.get(level, _LEVEL_META['info'])
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 11, 12, 8)
        root.setSpacing(10)
        icon = QLabel(icon_text)
        icon.setFixedSize(22, 22)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setObjectName('notifyIcon')
        root.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)
        title_label = QLabel(title.strip() or 'Notification')
        title_label.setObjectName('notifyTitle')
        text_col.addWidget(title_label)
        if body:
            body_label = QLabel(body.strip())
            body_label.setObjectName('notifyBody')
            body_label.setWordWrap(True)
            body_label.setMaximumHeight(40)
            text_col.addWidget(body_label)
        root.addLayout(text_col, 1)
        self._progress = QProgressBar()
        self._progress.setObjectName('notifyProgress')
        self._progress.setFixedHeight(3)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 1000)
        self._progress.setValue(1000)
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(root)
        outer.addWidget(self._progress)
        wrapper = QWidget(self)
        wrapper.setLayout(outer)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(wrapper)
        self.setStyleSheet(dark_theme(f'\n            QFrame#notifyToast {{\n                background-color: rgba(24, 24, 26, 0.96);\n                border: 1px solid rgba(255, 255, 255, 0.08);\n                border-left: 3px solid {accent};\n                border-radius: 10px;\n            }}\n            QLabel#notifyIcon {{\n                color: {accent};\n                font-size: 11pt;\n                font-weight: 700;\n                background-color: rgba(255, 255, 255, 0.04);\n                border-radius: 11px;\n            }}\n            QLabel#notifyTitle {{\n                color: #f4f4f5;\n                font-weight: 600;\n                font-size: 10pt;\n            }}\n            QLabel#notifyBody {{\n                color: #a1a1aa;\n                font-size: 8.5pt;\n            }}\n            QProgressBar#notifyProgress {{\n                background-color: rgba(255, 255, 255, 0.06);\n                border: none;\n                border-bottom-left-radius: 10px;\n                border-bottom-right-radius: 10px;\n            }}\n            QProgressBar#notifyProgress::chunk {{\n                background-color: {accent};\n                border-bottom-left-radius: 10px;\n                border-bottom-right-radius: 10px;\n            }}\n        '))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._action and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._action)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def begin_lifetime(self, *, total_ms: int) -> None:
        self._expires_at = time.monotonic() + total_ms / 1000.0
        self._progress.setValue(1000)

    def remaining_fraction(self) -> float:
        if self._expires_at <= 0:
            return 0.0
        remaining = self._expires_at - time.monotonic()
        if remaining <= 0:
            return 0.0
        total = max(self._total_ms / 1000.0, 0.001)
        return min(1.0, remaining / total)

    def set_total_ms(self, ms: int) -> None:
        self._total_ms = ms

    def is_expired(self) -> bool:
        return self._expires_at > 0 and time.monotonic() >= self._expires_at

    def mark_dismissing(self) -> bool:
        if self._dismissing:
            return False
        self._dismissing = True
        return True

class NotificationOverlay(QWidget):
    friend_filter_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet('background: transparent;')
        self._toasts: list[_Toast] = []
        self._move_anims: dict[_Toast, QPropertyAnimation] = {}
        self._hidden_since: float | None = None
        NotificationBus.instance().notify.connect(self._on_notify)
        self._track_timer = QTimer(self)
        self._track_timer.setInterval(_TRACK_MS)
        self._track_timer.timeout.connect(self._tick)
        self._track_timer.start()

    def _notifications_enabled(self) -> bool:
        return get_bool(load_config_cached().get('enable_notifications', True))

    def _vrchat_available(self) -> bool:
        return find_vrchat_window() is not None

    def _should_show(self) -> bool:
        if not self._notifications_enabled():
            return False
        if not self._vrchat_available():
            return False
        return is_vrchat_foreground()

    def _tick(self) -> None:
        safe_call(self._sync_frame, context='notification overlay tick')

    def _sync_frame(self) -> None:
        now = time.monotonic()
        for toast in list(self._toasts):
            if toast.is_expired():
                self._dismiss_toast(toast)
                continue
            if not toast._dismissing:
                toast._progress.setValue(int(toast.remaining_fraction() * 1000))
        if not self._toasts:
            self.hide()
            self._hidden_since = None
            return
        if not self._should_show():
            if self._hidden_since is None:
                self._hidden_since = now
            if now - self._hidden_since > 2.0:
                for toast in list(self._toasts):
                    self._dismiss_toast(toast, immediate=True)
                return
            self.hide()
            return
        self._hidden_since = None
        self._apply_window_geometry()
        self.show()
        self._layout_toasts(animate=True)

    def _apply_window_geometry(self) -> None:
        info = find_vrchat_window()
        if info is None:
            return
        _, rect = info
        stack_height = self._stack_height()
        height = min(rect.height - _MARGIN * 2, max(stack_height, 56))
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
            if animate:
                self._animate_toast_move(toast, 0, y)
            else:
                toast.move(0, y)
            y += toast.height() + _GAP

    def _animate_toast_move(self, toast: _Toast, x: int, y: int) -> None:
        current = self._move_anims.pop(toast, None)
        if current is not None:
            current.stop()
        if toast.pos() == QPoint(x, y):
            return
        anim = QPropertyAnimation(toast, b'pos', self)
        anim.setDuration(_ANIM_MS)
        anim.setStartValue(toast.pos())
        anim.setEndValue(QPoint(x, y))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: self._move_anims.pop(toast, None))
        self._move_anims[toast] = anim
        anim.start()

    def _display_ms_for(self, level: str) -> int:
        return _ERROR_DISPLAY_MS if level == 'error' else _DISPLAY_MS

    def _on_notify(self, title: str, body: str, level: str, action: str='') -> None:
        if not self._notifications_enabled():
            return
        debug_event(logger, 'notify queued', title=truncate_for_log(title), level=level)
        if not self._vrchat_available():
            return
        while len(self._toasts) >= _MAX_TOASTS:
            self._dismiss_toast(self._toasts[-1], immediate=True)
        toast = _Toast(title, body, level, action, self)
        toast.clicked.connect(self._on_toast_action)
        toast.adjustSize()
        toast.setFixedWidth(_TOAST_WIDTH)
        display_ms = self._display_ms_for(level)
        toast.set_total_ms(display_ms)
        toast.move(_TOAST_WIDTH + 20, 0)
        toast.show()
        self._toasts.insert(0, toast)
        toast.begin_lifetime(total_ms=display_ms)
        fade_in_widget(toast, duration=_ANIM_MS, easing=QEasingCurve.Type.OutCubic)
        self._sync_frame()

    def _on_toast_action(self, action: str) -> None:
        if action == 'show_friends_joinable':
            self.friend_filter_requested.emit('join_me')
        elif action == 'show_friends':
            self.friend_filter_requested.emit('online')

    def _dismiss_toast(self, toast: _Toast, *, immediate: bool=False) -> None:
        if toast not in self._toasts:
            return
        if not toast.mark_dismissing():
            return
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
                self._hidden_since = None
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
        fade_out_widget(toast, duration=_ANIM_MS, hide_after=True, on_finished=_remove, easing=QEasingCurve.Type.InCubic)

    def reposition(self) -> None:
        self._sync_frame()

    def cleanup(self) -> None:
        self._track_timer.stop()
        for toast in list(self._toasts):
            stop_widget_animations(toast)
        self._toasts.clear()
        self._move_anims.clear()
        self.hide()
        self.close()
