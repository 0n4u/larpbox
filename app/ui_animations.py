from __future__ import annotations
from collections.abc import Callable
from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QTimer
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget
_EASE_IN = QEasingCurve.Type.OutCubic
_EASE_OUT = QEasingCurve.Type.InCubic
_EASE_POP = QEasingCurve.Type.OutBack
_EASE_SMOOTH = QEasingCurve.Type.OutQuart
_EASE_SPRING = QEasingCurve.Type.OutQuint
_SIZE_UNBOUND = 16777215

def _stop_fade(widget: QWidget) -> None:
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

def _opacity_effect(widget: QWidget, opacity: float) -> QGraphicsOpacityEffect:
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(opacity)
    widget.setGraphicsEffect(effect)
    return effect

def _ancestor_has_fade(widget: QWidget) -> bool:
    parent = widget.parentWidget()
    while parent is not None:
        if getattr(parent, '_fade_animation', None) is not None:
            return True
        if getattr(parent, '_pulse_animation', None) is not None:
            return True
        if isinstance(parent.graphicsEffect(), QGraphicsOpacityEffect):
            return True
        parent = parent.parentWidget()
    return False

def fade_in_widget(widget: QWidget, *, duration: int=280, delay_ms: int=0, start_opacity: float=0.0, end_opacity: float=1.0, easing: QEasingCurve.Type=_EASE_SMOOTH) -> QPropertyAnimation | None:
    if widget is None:
        return None

    def _begin() -> QPropertyAnimation | None:
        if widget is None or _ancestor_has_fade(widget):
            return None
        _stop_fade(widget)
        effect = _opacity_effect(widget, start_opacity)
        anim = QPropertyAnimation(effect, b'opacity', widget)
        anim.setDuration(duration)
        anim.setStartValue(start_opacity)
        anim.setEndValue(end_opacity)
        anim.setEasingCurve(easing)

        def _cleanup() -> None:
            if getattr(widget, '_fade_animation', None) is anim:
                widget._fade_animation = None
            widget.setGraphicsEffect(None)
        anim.finished.connect(_cleanup)
        widget._fade_animation = anim
        anim.start()
        return anim
    if delay_ms > 0:
        QTimer.singleShot(delay_ms, _begin)
    else:
        QTimer.singleShot(0, _begin)
    return None

def fade_out_widget(widget: QWidget, *, duration: int=200, delay_ms: int=0, end_opacity: float=0.0, easing: QEasingCurve.Type=_EASE_OUT, on_finished: Callable[[], None] | None=None, hide_after: bool=True) -> QPropertyAnimation | None:
    if widget is None or not widget.isVisible():
        if on_finished is not None:
            on_finished()
        return None
    _stop_fade(widget)
    effect = _opacity_effect(widget, 1.0)
    anim = QPropertyAnimation(effect, b'opacity', widget)
    anim.setDuration(duration)
    anim.setStartValue(effect.opacity())
    anim.setEndValue(end_opacity)
    anim.setEasingCurve(easing)

    def _finish() -> None:
        if getattr(widget, '_fade_animation', None) is anim:
            widget._fade_animation = None
        if hide_after:
            widget.hide()
        widget.setGraphicsEffect(None)
        if on_finished is not None:
            on_finished()
    anim.finished.connect(_finish)

    def start() -> None:
        if getattr(widget, '_fade_animation', None) is not anim:
            return
        anim.start()
    widget._fade_animation = anim
    if delay_ms > 0:
        QTimer.singleShot(delay_ms, start)
    else:
        start()
    return anim

def pop_in_widget(widget: QWidget, *, duration: int=300, delay_ms: int=0, start_opacity: float=0.0, end_opacity: float=1.0) -> QPropertyAnimation | None:
    return fade_in_widget(widget, duration=duration, delay_ms=delay_ms, start_opacity=start_opacity, end_opacity=end_opacity, easing=_EASE_POP)

def slide_fade_in_widget(widget: QWidget, *, offset_y: int=12, duration: int=300, delay_ms: int=0, easing: QEasingCurve.Type=_EASE_SPRING) -> QPropertyAnimation | None:
    if widget is None:
        return None

    def _begin() -> None:
        if widget is None:
            return
        end_pos = widget.pos()
        start_pos = QPoint(end_pos.x(), end_pos.y() + offset_y)
        widget.move(start_pos)
        fade_in_widget(widget, duration=duration, easing=_EASE_SMOOTH)
        slide = QPropertyAnimation(widget, b'pos', widget)
        slide.setDuration(duration)
        slide.setStartValue(start_pos)
        slide.setEndValue(end_pos)
        slide.setEasingCurve(easing)
        widget._slide_animation = slide
        slide.finished.connect(lambda: setattr(widget, '_slide_animation', None))
        slide.start()
    if delay_ms > 0:
        QTimer.singleShot(delay_ms, _begin)
    else:
        QTimer.singleShot(0, _begin)
    return None

def window_fade_in(widget: QWidget, *, duration: int=340, delay_ms: int=0) -> QPropertyAnimation | None:
    if widget is None:
        return None
    widget.setWindowOpacity(0.0)

    def _begin() -> QPropertyAnimation:
        anim = QPropertyAnimation(widget, b'windowOpacity', widget)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(_EASE_SMOOTH)
        anim.start()
        widget._window_fade = anim
        return anim
    if delay_ms > 0:
        QTimer.singleShot(delay_ms, _begin)
        return None
    return _begin()

def expand_widget(widget: QWidget, *, duration: int=280) -> QPropertyAnimation | None:
    if widget is None:
        return None
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
        if getattr(widget, '_expand_animation', None) is anim:
            widget._expand_animation = None
        widget.setMaximumHeight(_SIZE_UNBOUND)
    anim.finished.connect(_cleanup)
    widget._expand_animation = anim
    anim.start()
    return anim

def collapse_widget(widget: QWidget, *, duration: int=220, on_finished: Callable[[], None] | None=None, hide_after: bool=True) -> QPropertyAnimation | None:
    if widget is None or not widget.isVisible():
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
        if getattr(widget, '_expand_animation', None) is anim:
            widget._expand_animation = None
        if hide_after:
            widget.hide()
        widget.setMaximumHeight(_SIZE_UNBOUND)
        if on_finished is not None:
            on_finished()
    anim.finished.connect(_finish)
    widget._expand_animation = anim
    anim.start()
    return anim

def toggle_expand_widget(widget: QWidget, visible: bool, *, duration: int=280) -> QPropertyAnimation | None:
    if visible:
        return expand_widget(widget, duration=duration)
    return collapse_widget(widget, duration=max(180, duration - 60))

def stagger_fade_in(widgets: list[QWidget], *, duration: int=220, step_ms: int=24, easing: QEasingCurve.Type=_EASE_SMOOTH) -> None:
    for index, widget in enumerate(widgets):
        if widget is None:
            continue
        fade_in_widget(widget, duration=duration, delay_ms=index * step_ms, easing=easing)

def stagger_pop_in(widgets: list[QWidget], *, duration: int=240, step_ms: int=22) -> None:
    for index, widget in enumerate(widgets):
        if widget is None:
            continue
        pop_in_widget(widget, duration=duration, delay_ms=index * step_ms)

def reveal_content(hide: QWidget | None, show: QWidget, *, duration: int=280, pop: bool=True) -> None:

    def _show() -> None:
        if hide is not None:
            hide.hide()
        show.show()
        if pop:
            pop_in_widget(show, duration=duration)
        else:
            fade_in_widget(show, duration=duration, easing=_EASE_SMOOTH)
    if hide is not None and hide.isVisible():
        fade_out_widget(hide, duration=max(140, duration // 2), on_finished=_show, hide_after=True)
        return
    _show()

def pulse_widget(widget: QWidget, *, duration: int=1100, min_opacity: float=0.42, max_opacity: float=1.0) -> QPropertyAnimation | None:
    if widget is None or _ancestor_has_fade(widget):
        return None
    pulse = getattr(widget, '_pulse_animation', None)
    if pulse is not None:
        pulse.stop()
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = _opacity_effect(widget, max_opacity)
    anim = QPropertyAnimation(effect, b'opacity', widget)
    anim.setDuration(duration)
    anim.setStartValue(max_opacity)
    anim.setEndValue(min_opacity)
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)
    anim.setLoopCount(-1)
    widget._pulse_animation = anim
    QTimer.singleShot(0, anim.start)
    return anim

def stop_pulse(widget: QWidget) -> None:
    pulse = getattr(widget, '_pulse_animation', None)
    if pulse is not None:
        pulse.stop()
        widget._pulse_animation = None
    if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
        widget.setGraphicsEffect(None)

def stop_widget_animations(widget: QWidget | None) -> None:
    if widget is None:
        return
    stop_pulse(widget)
    fade = getattr(widget, '_fade_animation', None)
    if fade is not None:
        try:
            fade.stop()
        except Exception:
            pass
        widget._fade_animation = None
    expand = getattr(widget, '_expand_animation', None)
    if expand is not None:
        try:
            expand.stop()
        except Exception:
            pass
        widget._expand_animation = None
    slide = getattr(widget, '_slide_animation', None)
    if slide is not None:
        try:
            slide.stop()
        except Exception:
            pass
        widget._slide_animation = None
    if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
        widget.setGraphicsEffect(None)

def flash_widget(widget: QWidget, *, duration: int=240, dip: float=0.35) -> QPropertyAnimation | None:
    if widget is None or not widget.isVisible():
        return None
    stop_pulse(widget)
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = _opacity_effect(widget, 1.0)
    anim = QPropertyAnimation(effect, b'opacity', widget)
    anim.setDuration(duration)
    anim.setStartValue(1.0)
    anim.setKeyValueAt(0.45, dip)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)

    def _cleanup() -> None:
        if getattr(widget, '_fade_animation', None) is anim:
            widget._fade_animation = None
        widget.setGraphicsEffect(None)

    anim.finished.connect(_cleanup)
    widget._fade_animation = anim
    anim.start()
    return anim

def animate_list_items(widgets: list[QWidget], *, pop: bool=True, duration: int=220, step_ms: int=20) -> None:
    visible = [widget for widget in widgets if widget is not None]
    if not visible:
        return
    if pop:
        stagger_pop_in(visible, duration=duration, step_ms=step_ms)
    else:
        stagger_fade_in(visible, duration=duration, step_ms=step_ms)
