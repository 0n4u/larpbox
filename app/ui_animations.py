from __future__ import annotations
from collections.abc import Callable
from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QTimer
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget
from .logging_setup import debug_event, get_logger, is_debug_mode
from .safe_runtime import widget_is_valid
from .theme import motion_enabled
logger = get_logger('ui_animations')
_EASE_IN = QEasingCurve.Type.OutCubic
_EASE_OUT = QEasingCurve.Type.InCubic
_EASE_POP = QEasingCurve.Type.OutBack
_EASE_SMOOTH = QEasingCurve.Type.OutQuart
_EASE_SPRING = QEasingCurve.Type.OutQuint
_SIZE_UNBOUND = 16777215
_MAX_STAGGER_ITEMS = 8

def _motion_on() -> bool:
    return motion_enabled()

def _widget_alive(widget: QWidget | None) -> bool:
    return widget_is_valid(widget)

def _safe_widget_call(widget: QWidget | None, fn: Callable[[], None], *, context: str) -> None:
    if not _widget_alive(widget):
        if is_debug_mode():
            debug_event(logger, 'skipped callback — widget deleted', context=context)
        return
    try:
        fn()
    except RuntimeError:
        if is_debug_mode():
            debug_event(logger, 'skipped callback — widget deleted mid-call', context=context)

def _stop_fade(widget: QWidget) -> None:
    if not _widget_alive(widget):
        return
    try:
        anim = getattr(widget, '_fade_animation', None)
        if anim is not None:
            anim.stop()
            widget._fade_animation = None
        pulse = getattr(widget, '_pulse_animation', None)
        if pulse is not None:
            pulse.stop()
            widget._pulse_animation = None
        expand = getattr(widget, '_expand_animation', None)
        if expand is not None:
            expand.stop()
            widget._expand_animation = None
        slide = getattr(widget, '_slide_animation', None)
        if slide is not None:
            slide.stop()
            widget._slide_animation = None
        widget.setGraphicsEffect(None)
    except RuntimeError:
        pass

def _opacity_effect(widget: QWidget, opacity: float) -> QGraphicsOpacityEffect | None:
    if not _widget_alive(widget):
        return None
    try:
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(opacity)
        widget.setGraphicsEffect(effect)
        return effect
    except RuntimeError:
        return None

def _ancestor_has_fade(widget: QWidget) -> bool:
    if not _widget_alive(widget):
        return True
    try:
        parent = widget.parentWidget()
        while parent is not None:
            if not _widget_alive(parent):
                return True
            if getattr(parent, '_fade_animation', None) is not None:
                return True
            if getattr(parent, '_pulse_animation', None) is not None:
                return True
            if isinstance(parent.graphicsEffect(), QGraphicsOpacityEffect):
                return True
            parent = parent.parentWidget()
    except RuntimeError:
        return True
    return False

def fade_in_widget(widget: QWidget, *, duration: int=280, delay_ms: int=0, start_opacity: float=0.0, end_opacity: float=1.0, easing: QEasingCurve.Type=_EASE_SMOOTH) -> QPropertyAnimation | None:
    if not _widget_alive(widget):
        return None
    if not _motion_on():
        return None

    def _begin() -> QPropertyAnimation | None:
        if not _widget_alive(widget) or _ancestor_has_fade(widget):
            return None
        _stop_fade(widget)
        effect = _opacity_effect(widget, start_opacity)
        if effect is None:
            return None
        anim = QPropertyAnimation(effect, b'opacity', widget)
        anim.setDuration(duration)
        anim.setStartValue(start_opacity)
        anim.setEndValue(end_opacity)
        anim.setEasingCurve(easing)

        def _cleanup() -> None:
            if not _widget_alive(widget):
                return
            if getattr(widget, '_fade_animation', None) is anim:
                widget._fade_animation = None
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
        anim.finished.connect(_cleanup)
        widget._fade_animation = anim
        anim.start()
        return anim
    if delay_ms > 0:
        QTimer.singleShot(delay_ms, lambda: _safe_widget_call(widget, _begin, context='fade_in_widget'))
    else:
        QTimer.singleShot(0, lambda: _safe_widget_call(widget, _begin, context='fade_in_widget'))
    return None

def fade_out_widget(widget: QWidget, *, duration: int=200, delay_ms: int=0, end_opacity: float=0.0, easing: QEasingCurve.Type=_EASE_OUT, on_finished: Callable[[], None] | None=None, hide_after: bool=True) -> QPropertyAnimation | None:
    if not _widget_alive(widget):
        if on_finished is not None:
            on_finished()
        return None
    if not _motion_on():
        try:
            if hide_after:
                widget.hide()
        except RuntimeError:
            pass
        if on_finished is not None:
            on_finished()
        return None
    try:
        visible = widget.isVisible()
    except RuntimeError:
        if on_finished is not None:
            on_finished()
        return None
    if not visible:
        if on_finished is not None:
            on_finished()
        return None
    _stop_fade(widget)
    effect = _opacity_effect(widget, 1.0)
    if effect is None:
        if on_finished is not None:
            on_finished()
        return None
    anim = QPropertyAnimation(effect, b'opacity', widget)
    anim.setDuration(duration)
    anim.setStartValue(effect.opacity())
    anim.setEndValue(end_opacity)
    anim.setEasingCurve(easing)

    def _finish() -> None:
        if not _widget_alive(widget):
            if on_finished is not None:
                on_finished()
            return
        if getattr(widget, '_fade_animation', None) is anim:
            widget._fade_animation = None
        try:
            if hide_after:
                widget.hide()
            widget.setGraphicsEffect(None)
        except RuntimeError:
            pass
        if on_finished is not None:
            on_finished()
    anim.finished.connect(_finish)

    def start() -> None:
        if not _widget_alive(widget):
            return
        if getattr(widget, '_fade_animation', None) is not anim:
            return
        anim.start()
    widget._fade_animation = anim
    if delay_ms > 0:
        QTimer.singleShot(delay_ms, lambda: _safe_widget_call(widget, start, context='fade_out_widget'))
    else:
        start()
    return anim

def pop_in_widget(widget: QWidget, *, duration: int=300, delay_ms: int=0, start_opacity: float=0.0, end_opacity: float=1.0) -> QPropertyAnimation | None:
    return fade_in_widget(widget, duration=duration, delay_ms=delay_ms, start_opacity=start_opacity, end_opacity=end_opacity, easing=_EASE_POP)

def slide_fade_in_widget(widget: QWidget, *, offset_y: int=12, duration: int=300, delay_ms: int=0, easing: QEasingCurve.Type=_EASE_SPRING) -> QPropertyAnimation | None:
    if not _widget_alive(widget) or not _motion_on():
        return None

    def _begin() -> None:
        if not _widget_alive(widget):
            return
        try:
            end_pos = widget.pos()
            start_pos = QPoint(end_pos.x(), end_pos.y() + offset_y)
            widget.move(start_pos)
        except RuntimeError:
            return
        fade_in_widget(widget, duration=duration, easing=_EASE_SMOOTH)
        if not _widget_alive(widget):
            return
        try:
            slide = QPropertyAnimation(widget, b'pos', widget)
            slide.setDuration(duration)
            slide.setStartValue(start_pos)
            slide.setEndValue(end_pos)
            slide.setEasingCurve(easing)
            widget._slide_animation = slide

            def _cleanup() -> None:
                if _widget_alive(widget):
                    widget._slide_animation = None
            slide.finished.connect(_cleanup)
            slide.start()
        except RuntimeError:
            pass
    if delay_ms > 0:
        QTimer.singleShot(delay_ms, lambda: _safe_widget_call(widget, _begin, context='slide_fade_in_widget'))
    else:
        QTimer.singleShot(0, lambda: _safe_widget_call(widget, _begin, context='slide_fade_in_widget'))
    return None

def window_fade_in(widget: QWidget, *, duration: int=340, delay_ms: int=0) -> QPropertyAnimation | None:
    if not _widget_alive(widget):
        return None
    if not _motion_on():
        try:
            widget.setWindowOpacity(1.0)
        except RuntimeError:
            pass
        return None
    try:
        widget.setWindowOpacity(0.0)
    except RuntimeError:
        return None

    def _begin() -> QPropertyAnimation | None:
        if not _widget_alive(widget):
            return None
        try:
            anim = QPropertyAnimation(widget, b'windowOpacity', widget)
            anim.setDuration(duration)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(_EASE_SMOOTH)
            anim.start()
            widget._window_fade = anim
            return anim
        except RuntimeError:
            return None
    if delay_ms > 0:
        QTimer.singleShot(delay_ms, lambda: _safe_widget_call(widget, _begin, context='window_fade_in'))
        return None
    return _begin()

def expand_widget(widget: QWidget, *, duration: int=280) -> QPropertyAnimation | None:
    if not _widget_alive(widget):
        return None
    if not _motion_on():
        try:
            widget.setVisible(True)
            widget.setMaximumHeight(_SIZE_UNBOUND)
        except RuntimeError:
            pass
        return None
    try:
        expand = getattr(widget, '_expand_animation', None)
        if expand is not None:
            expand.stop()
        target = max(widget.sizeHint().height(), widget.minimumSizeHint().height())
        if target <= 0:
            widget.setVisible(True)
            return None
        widget.setVisible(True)
        widget.setMaximumHeight(0)
        anim = QPropertyAnimation(widget, b'maximumHeight', widget)
        anim.setDuration(duration)
        anim.setStartValue(0)
        anim.setEndValue(target)
        anim.setEasingCurve(_EASE_SMOOTH)

        def _cleanup() -> None:
            if not _widget_alive(widget):
                return
            if getattr(widget, '_expand_animation', None) is anim:
                widget._expand_animation = None
            try:
                widget.setMaximumHeight(_SIZE_UNBOUND)
            except RuntimeError:
                pass
        anim.finished.connect(_cleanup)
        widget._expand_animation = anim
        anim.start()
        return anim
    except RuntimeError:
        return None

def collapse_widget(widget: QWidget, *, duration: int=220, on_finished: Callable[[], None] | None=None, hide_after: bool=True) -> QPropertyAnimation | None:
    if not _widget_alive(widget):
        if on_finished is not None:
            on_finished()
        return None
    if not _motion_on():
        try:
            if hide_after:
                widget.hide()
            widget.setMaximumHeight(_SIZE_UNBOUND)
        except RuntimeError:
            pass
        if on_finished is not None:
            on_finished()
        return None
    try:
        if not widget.isVisible():
            if on_finished is not None:
                on_finished()
            return None
        expand = getattr(widget, '_expand_animation', None)
        if expand is not None:
            expand.stop()
        current = widget.height()
        anim = QPropertyAnimation(widget, b'maximumHeight', widget)
        anim.setDuration(duration)
        anim.setStartValue(current)
        anim.setEndValue(0)
        anim.setEasingCurve(_EASE_OUT)

        def _finish() -> None:
            if not _widget_alive(widget):
                if on_finished is not None:
                    on_finished()
                return
            if getattr(widget, '_expand_animation', None) is anim:
                widget._expand_animation = None
            try:
                if hide_after:
                    widget.hide()
                widget.setMaximumHeight(_SIZE_UNBOUND)
            except RuntimeError:
                pass
            if on_finished is not None:
                on_finished()
        anim.finished.connect(_finish)
        widget._expand_animation = anim
        anim.start()
        return anim
    except RuntimeError:
        if on_finished is not None:
            on_finished()
        return None

def toggle_expand_widget(widget: QWidget, visible: bool, *, duration: int=280) -> QPropertyAnimation | None:
    if visible:
        return expand_widget(widget, duration=duration)
    return collapse_widget(widget, duration=max(180, duration - 60))

def stagger_fade_in(widgets: list[QWidget], *, duration: int=220, step_ms: int=24, easing: QEasingCurve.Type=_EASE_SMOOTH) -> None:
    for index, widget in enumerate(widgets):
        if not _widget_alive(widget):
            continue
        fade_in_widget(widget, duration=duration, delay_ms=index * step_ms, easing=easing)

def stagger_pop_in(widgets: list[QWidget], *, duration: int=240, step_ms: int=22) -> None:
    if not _motion_on():
        return
    capped = [widget for widget in widgets if _widget_alive(widget)][:_MAX_STAGGER_ITEMS]
    for index, widget in enumerate(capped):
        pop_in_widget(widget, duration=duration, delay_ms=index * step_ms)

def reveal_content(hide: QWidget | None, show: QWidget, *, duration: int=280, pop: bool=True) -> None:

    def _show() -> None:
        if hide is not None and _widget_alive(hide):
            try:
                hide.hide()
            except RuntimeError:
                pass
        if not _widget_alive(show):
            return
        try:
            show.show()
        except RuntimeError:
            return
        if _motion_on():
            if pop:
                pop_in_widget(show, duration=duration)
            else:
                fade_in_widget(show, duration=duration, easing=_EASE_SMOOTH)
    if hide is not None and _widget_alive(hide):
        try:
            hide_visible = hide.isVisible()
        except RuntimeError:
            hide_visible = False
        if hide_visible and _motion_on():
            fade_out_widget(hide, duration=max(140, duration // 2), on_finished=_show, hide_after=True)
            return
    _show()

def pulse_widget(widget: QWidget, *, duration: int=1100, min_opacity: float=0.42, max_opacity: float=1.0) -> QPropertyAnimation | None:
    if not _widget_alive(widget) or not _motion_on() or _ancestor_has_fade(widget):
        return None
    try:
        pulse = getattr(widget, '_pulse_animation', None)
        if pulse is not None:
            pulse.stop()
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = _opacity_effect(widget, max_opacity)
            if effect is None:
                return None
        anim = QPropertyAnimation(effect, b'opacity', widget)
        anim.setDuration(duration)
        anim.setStartValue(max_opacity)
        anim.setEndValue(min_opacity)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        anim.setLoopCount(-1)
        widget._pulse_animation = anim
        QTimer.singleShot(0, lambda: _safe_widget_call(widget, anim.start, context='pulse_widget'))
        return anim
    except RuntimeError:
        return None

def stop_pulse(widget: QWidget) -> None:
    if not _widget_alive(widget):
        return
    try:
        pulse = getattr(widget, '_pulse_animation', None)
        if pulse is not None:
            pulse.stop()
            widget._pulse_animation = None
        if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
            widget.setGraphicsEffect(None)
    except RuntimeError:
        pass

def stop_widget_animations(widget: QWidget | None) -> None:
    if not _widget_alive(widget):
        return
    stop_pulse(widget)
    fade = getattr(widget, '_fade_animation', None)
    if fade is not None:
        try:
            fade.stop()
        except Exception:
            pass
        try:
            widget._fade_animation = None
        except RuntimeError:
            pass
    expand = getattr(widget, '_expand_animation', None)
    if expand is not None:
        try:
            expand.stop()
        except Exception:
            pass
        try:
            widget._expand_animation = None
        except RuntimeError:
            pass
    slide = getattr(widget, '_slide_animation', None)
    if slide is not None:
        try:
            slide.stop()
        except Exception:
            pass
        try:
            widget._slide_animation = None
        except RuntimeError:
            pass
    try:
        if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
            widget.setGraphicsEffect(None)
    except RuntimeError:
        pass

def flash_widget(widget: QWidget, *, duration: int=240, dip: float=0.35) -> QPropertyAnimation | None:
    if not _widget_alive(widget) or not _motion_on():
        return None
    try:
        if not widget.isVisible():
            return None
        stop_pulse(widget)
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = _opacity_effect(widget, 1.0)
            if effect is None:
                return None
        anim = QPropertyAnimation(effect, b'opacity', widget)
        anim.setDuration(duration)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.45, dip)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        def _cleanup() -> None:
            if not _widget_alive(widget):
                return
            if getattr(widget, '_fade_animation', None) is anim:
                widget._fade_animation = None
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
        anim.finished.connect(_cleanup)
        widget._fade_animation = anim
        anim.start()
        return anim
    except RuntimeError:
        return None

def animate_list_items(widgets: list[QWidget], *, pop: bool=True, duration: int=220, step_ms: int=20) -> None:
    visible = [widget for widget in widgets if _widget_alive(widget)]
    if not visible or not _motion_on():
        return
    capped = visible[:_MAX_STAGGER_ITEMS]
    if pop:
        stagger_pop_in(capped, duration=duration, step_ms=step_ms)
    else:
        stagger_fade_in(capped, duration=duration, step_ms=step_ms)
