from __future__ import annotations
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from PyQt6.QtCore import QThread
from ..logging_setup import get_logger
from . import event_bus as ev
from .event_bus import VrcEventBus

logger = get_logger('vr_overlay')

_OVERLAY_KEY = 'larpbox.feed'
_OVERLAY_NAME = 'larpbox'
_MAX_LINES = 6
_MESSAGE_TTL_SEC = 10.0
_IMAGE_W = 512
_IMAGE_H = 256


def openvr_available() -> bool:
    try:
        import openvr              
        return True
    except Exception:
        return False


def format_overlay_message(kind: str, payload: dict[str, Any]) -> str | None:
    """Translate a bus event into a short overlay line (or None to ignore)."""
    if kind == ev.NOTIFICATION:
        sender = str(payload.get('senderUsername') or payload.get('sender_username') or 'VRChat')
        message = str(payload.get('message') or payload.get('type') or 'Notification')
        return f'[notify] {sender}: {message}'.strip()
    if kind == ev.WORLD_JOIN:
        name = str(payload.get('world_name') or payload.get('location') or '')
        return f'[world] {name}' if name else None
    if kind == ev.PLAYER_JOIN:
        name = str(payload.get('display_name') or '')
        return f'[join] {name}' if name else None
    if kind == ev.PLAYER_LEAVE:
        name = str(payload.get('display_name') or '')
        return f'[leave] {name}' if name else None
    if kind == ev.FRIEND_ONLINE:
        name = str(payload.get('display_name') or payload.get('displayName') or '')
        return f'[online] {name}' if name else None
    if kind == ev.VIDEO_URL:
        title = str(payload.get('title') or payload.get('url') or '')
        return f'[media] {title}' if title else None
    return None


class _MessageBuffer:
    """Thread-safe ring buffer of recent (ts, text) lines with TTL pruning."""

    def __init__(self, max_lines: int = _MAX_LINES, ttl_sec: float = _MESSAGE_TTL_SEC):
        self._lines: deque[tuple[float, str]] = deque(maxlen=max_lines)
        self._ttl = ttl_sec
        self._lock = threading.Lock()
        self._dirty = False

    def add(self, text: str, *, now: float | None = None) -> None:
        with self._lock:
            self._lines.append((now if now is not None else time.time(), text))
            self._dirty = True

    def current(self, *, now: float | None = None) -> list[str]:
        cutoff = (now if now is not None else time.time()) - self._ttl
        with self._lock:
            return [text for ts, text in self._lines if ts >= cutoff]

    def take_dirty(self) -> bool:
        with self._lock:
            was = self._dirty
            self._dirty = False
            return was


def _render_lines_to_png(lines: list[str], path: Path) -> bool:
    """Render overlay text to a PNG. Returns False if rendering is unavailable."""
    try:
        from PyQt6.QtGui import QColor, QFont, QImage, QPainter
        image = QImage(_IMAGE_W, _IMAGE_H, QImage.Format.Format_ARGB32)
        image.fill(QColor(12, 14, 18, 220))
        painter = QPainter(image)
        try:
            font = QFont()
            font.setPointSize(14)
            painter.setFont(font)
            painter.setPen(QColor('#e6e8eb'))
            y = 18
            painter.drawText(10, y, 'larpbox')
            painter.setPen(QColor('#c4c8ce'))
            small = QFont()
            small.setPointSize(11)
            painter.setFont(small)
            for line in lines[-_MAX_LINES:]:
                y += 34
                painter.drawText(12, y, line[:60])
        finally:
            painter.end()
        return bool(image.save(str(path), 'PNG'))
    except Exception:
        logger.debug('Overlay render failed', exc_info=True)
        return False


class VrOverlayService(QThread):
    """Optional SteamVR/OpenVR dashboard overlay for feed + notifications.

    Entirely feature-gated and best-effort: if `openvr` is not installed, or
    SteamVR is not running, the service logs once and becomes a no-op. It never
    raises into the rest of the app.
    """

    def __init__(self, bus: VrcEventBus | None = None, parent=None):
        super().__init__(parent)
        self._bus = bus or VrcEventBus.instance()
        self._stop = threading.Event()
        self._buffer = _MessageBuffer()
        self._png_path = Path(tempfile.gettempdir()) / 'larpbox_overlay.png'
        self._bus.event.connect(self._on_event)

    def _on_event(self, kind: str, payload: dict) -> None:
        text = format_overlay_message(kind, payload)
        if text:
            self._buffer.add(text)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            import openvr
        except Exception:
            logger.info('OpenVR not installed — VR overlay disabled (install pyopenvr to enable)')
            return
        system = None
        overlay = None
        handle = None
        try:
            openvr.init(openvr.VRApplication_Background)
            overlay = openvr.IVROverlay()
            handle = overlay.createOverlay(_OVERLAY_KEY, _OVERLAY_NAME)
            overlay.setOverlayWidthInMeters(handle, 0.45)
            self._position_overlay(openvr, overlay, handle)
            logger.info('VR overlay initialized')
        except Exception:
            logger.info('SteamVR/OpenVR unavailable — VR overlay disabled', exc_info=True)
            self._safe_shutdown(openvr)
            return
        last_render = 0.0
        while not self._stop.is_set():
            try:
                now = time.time()
                if self._buffer.take_dirty() or now - last_render > 2.0:
                    lines = self._buffer.current(now=now)
                    if lines and _render_lines_to_png(lines, self._png_path):
                        overlay.setOverlayFromFile(handle, str(self._png_path))
                        overlay.showOverlay(handle)
                    else:
                        overlay.hideOverlay(handle)
                    last_render = now
            except Exception:
                logger.debug('VR overlay tick failed', exc_info=True)
            self._stop.wait(1.0)
        self._safe_shutdown(openvr)

    def _position_overlay(self, openvr: Any, overlay: Any, handle: Any) -> None:
        try:
            transform = openvr.HmdMatrix34_t()
            values = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, -0.25), (0.0, 0.0, 1.0, -1.4))
            for r in range(3):
                for c in range(4):
                    transform[r][c] = values[r][c]
            overlay.setOverlayTransformTrackedDeviceRelative(handle, openvr.k_unTrackedDeviceIndex_Hmd, transform)
        except Exception:
            logger.debug('Could not set overlay transform', exc_info=True)

    def _safe_shutdown(self, openvr: Any) -> None:
        try:
            if openvr is not None:
                openvr.shutdown()
        except Exception:
            logger.debug('OpenVR shutdown failed', exc_info=True)
