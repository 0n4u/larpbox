from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

_EASE_IN = QEasingCurve.Type.OutCubic
_EASE_OUT = QEasingCurve.Type.InCubic
_EASE_POP = QEasingCurve.Type.OutBack
_EASE_SMOOTH = QEasingCurve.Type.OutQuart


def _stop_fade(widget: QWidget) -> None:
    anim = getattr(widget, '_fade_animation', None)
    if anim is not None:
        anim.stop()
        widget._fade_animation = None
    pulse = getattr(widget, '_pulse_animation', None)
    if pulse is not None:
        pulse.stop()
        widget._pulse_animation = None
    widget.setGraphicsEffect(None)


def _opacity_effect(widget: QWidget, opacity: float) -> QGraphicsOpacityEffect:
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(opacity)
    widget.setGraphicsEffect(effect)
    return effect


def fade_in_widget(
    widget: QWidget,
    *,
    duration: int = 240,
    delay_ms: int = 0,
    start_opacity: float = 0.0,
    end_opacity: float = 1.0,
    easing: QEasingCurve.Type = _EASE_IN,
) -> QPropertyAnimation | None:
    if widget is None:
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


def fade_out_widget(
    widget: QWidget,
    *,
    duration: int = 180,
    delay_ms: int = 0,
    end_opacity: float = 0.0,
    easing: QEasingCurve.Type = _EASE_OUT,
    on_finished: Callable[[], None] | None = None,
    hide_after: bool = True,
) -> QPropertyAnimation | None:
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


def pop_in_widget(
    widget: QWidget,
    *,
    duration: int = 260,
    delay_ms: int = 0,
    start_opacity: float = 0.0,
    end_opacity: float = 1.0,
) -> QPropertyAnimation | None:
    return fade_in_widget(
        widget,
        duration=duration,
        delay_ms=delay_ms,
        start_opacity=start_opacity,
        end_opacity=end_opacity,
        easing=_EASE_POP,
    )


def stagger_fade_in(
    widgets: list[QWidget],
    *,
    duration: int = 200,
    step_ms: int = 28,
    easing: QEasingCurve.Type = _EASE_SMOOTH,
) -> None:
    for index, widget in enumerate(widgets):
        if widget is None:
            continue
        fade_in_widget(
            widget,
            duration=duration,
            delay_ms=index * step_ms,
            easing=easing,
        )


def stagger_pop_in(
    widgets: list[QWidget],
    *,
    duration: int = 220,
    step_ms: int = 24,
) -> None:
    for index, widget in enumerate(widgets):
        if widget is None:
            continue
        pop_in_widget(widget, duration=duration, delay_ms=index * step_ms)


def reveal_content(
    hide: QWidget | None,
    show: QWidget,
    *,
    duration: int = 260,
    pop: bool = True,
) -> None:
    def _show() -> None:
        if hide is not None:
            hide.hide()
        show.show()
        if pop:
            pop_in_widget(show, duration=duration)
        else:
            fade_in_widget(show, duration=duration, easing=_EASE_SMOOTH)

    if hide is not None and hide.isVisible():
        fade_out_widget(
            hide,
            duration=max(120, duration // 2),
            on_finished=_show,
            hide_after=True,
        )
        return
    _show()


def pulse_widget(
    widget: QWidget,
    *,
    duration: int = 1100,
    min_opacity: float = 0.42,
    max_opacity: float = 1.0,
) -> QPropertyAnimation | None:
    if widget is None:
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
    anim.start()
    return anim


def stop_pulse(widget: QWidget) -> None:
    pulse = getattr(widget, '_pulse_animation', None)
    if pulse is not None:
        pulse.stop()
        widget._pulse_animation = None
    if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
        widget.setGraphicsEffect(None)
