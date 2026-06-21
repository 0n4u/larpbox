from __future__ import annotations
import time
from collections import OrderedDict
from collections.abc import Iterable
from PyQt6.QtCore import Qt, QRunnable, QThreadPool, QTimer, QUrl, pyqtSignal, QObject
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QLabel, QWidget
from .image_cache import cached_image_path, save_image_bytes_to_cache
from .logging_setup import debug_event, get_logger, log_exception, truncate_for_log
from .vrc_image_utils import is_allowed_image_host, normalize_vrc_image_url, parse_vrc_image_url
from .vrchat_auth import USER_AGENT, VRChatSession
logger = get_logger('images')
_THUMB_STYLE = 'QLabel#avatarThumb { background-color: #232323; border: 1px solid #3a3a3a; border-radius: 6px; color: #666; }'
                                                                              
_PREFETCH_RESOLUTION = 128
_LIST_THUMB_RESOLUTION = 64

class _DiskLoadSignals(QObject):
    finished = pyqtSignal(str, object, object)

_disk_signals = _DiskLoadSignals()
_disk_pool = QThreadPool.globalInstance()

class _DiskLoadRunnable(QRunnable):

    def __init__(self, path: str, cache_key: str, labels: list['RemoteImageLabel']):
        super().__init__()
        self.path = path
        self.cache_key = cache_key
        self.labels = labels
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            pixmap = QPixmap(self.path)
            _disk_signals.finished.emit(self.cache_key, pixmap, self.labels)
        except Exception as exc:
            log_exception(logger, 'disk image load', exc)
            _disk_signals.finished.emit(self.cache_key, QPixmap(), self.labels)

class RemoteImageLabel(QLabel):
    _MAX_CONCURRENT = 32
    _CACHE_MAX = 1024
    _NETWORK_RETRY_MS = 1500
    _MAX_NETWORK_RETRIES = 4
    _FAILED_RETRY_COOLDOWN_SEC = 90.0
    _retry_counts: dict[str, int] = {}
    _failed_at: dict[str, float] = {}
    _cache: OrderedDict[str, QPixmap] = OrderedDict()
    _pending: dict[str, list[RemoteImageLabel]] = {}
    _failed_urls: set[str] = set()
    _session: VRChatSession | None = None
    _auth_token: str | None = None
    _two_factor_token: str | None = None
    _manager: QNetworkAccessManager | None = None
    _network_connected: bool = False
    _active_urls: set[str] = set()
    _queued_high: list[str] = []
    _queued_low: list[str] = []
    _disk_hook_installed = False

    @classmethod
    def clear_failed_urls(cls) -> None:
        cls._failed_urls.clear()
        cls._failed_at.clear()
        cls._retry_counts.clear()

    @classmethod
    def _cache_key(cls, url: str) -> str:
        parsed = parse_vrc_image_url(url)
        if parsed:
            file_id, version, _size = parsed
            return f'{file_id}:{version}'
        return url.strip()

    @classmethod
    def set_session(cls, session: VRChatSession | None) -> None:
        cls._session = session
        if session is None:
            cls.set_auth(None, None)
            return
        cls.clear_failed_urls()
        cls.set_auth(session.auth_token, session.two_factor_token)

    @classmethod
    def set_auth(cls, auth_token: str | None, two_factor_token: str | None=None) -> None:
        cls._auth_token = auth_token.strip() if auth_token else None
        cls._two_factor_token = two_factor_token.strip() if two_factor_token else None

    @classmethod
    def prefetch(cls, urls: Iterable[str], *, resolution: int=_PREFETCH_RESOLUTION) -> None:
        try:
            cls._ensure_disk_hook()
            queued = 0
            for raw in urls:
                url = cls._normalize_url(raw, resolution=resolution)
                if not url:
                    continue
                cache_key = cls._cache_key(url)
                if cache_key in cls._cache or cached_image_path(url) is not None:
                    continue
                if url in cls._failed_urls or url in cls._active_urls:
                    continue
                if url in cls._queued_high or url in cls._queued_low or url in cls._pending:
                    continue
                cls._pending.setdefault(url, [])
                cls._queued_low.append(url)
                queued += 1
            if queued:
                debug_event(logger, 'prefetch queued', count=queued, active=len(cls._active_urls))
            cls._pump_queue()
        except Exception as exc:
            log_exception(logger, 'image prefetch', exc)

    @classmethod
    def _normalize_url(cls, url: str, *, resolution: int) -> str:
        cleaned = normalize_vrc_image_url((url or '').strip(), resolution=resolution)
        if not cleaned or not is_allowed_image_host(cleaned):
            return ''
        return cleaned

    @classmethod
    def _ensure_disk_hook(cls) -> None:
        if cls._disk_hook_installed:
            return
        _disk_signals.finished.connect(cls._on_disk_loaded)
        cls._disk_hook_installed = True

    @classmethod
    def _on_disk_loaded(cls, cache_key: str, pixmap_obj: object, labels_obj: object) -> None:
        try:
            labels = [label for label in (labels_obj if isinstance(labels_obj, list) else []) if isinstance(label, RemoteImageLabel)]
            url = labels[0]._url if labels else ''
            if url:
                cls._pending.pop(url, None)
            pixmap = pixmap_obj if isinstance(pixmap_obj, QPixmap) else QPixmap()
            if pixmap.isNull():
                debug_event(logger, 'disk cache miss', key=cache_key)
                for label in labels:
                    if cls._cache_key(label._url) == cache_key:
                        label._schedule_network(high_priority=True)
                return
            cls._store_memory(cache_key, pixmap)
            debug_event(logger, 'disk cache hit', key=cache_key, labels=len(labels))
            for label in labels:
                cls._touch_label(label, lambda lbl=label, pix=pixmap: lbl._apply_pixmap(pix))
        except Exception as exc:
            log_exception(logger, 'disk image apply', exc)

    @classmethod
    def _network(cls) -> QNetworkAccessManager:
        if cls._manager is None:
            cls._manager = QNetworkAccessManager()
        if not cls._network_connected:
            cls._manager.finished.connect(cls._on_network_finished)
            cls._network_connected = True
        return cls._manager

    def __init__(self, size: int=56, parent: QWidget | None=None):
        super().__init__(parent)
        self._url = ''
        self._resolution = max(size, 32)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName('avatarThumb')
        self.setText('…')
        self.setStyleSheet(_THUMB_STYLE)
        self.setScaledContents(False)
        self.destroyed.connect(self._unregister_pending)

    def _unregister_pending(self) -> None:
        for waiters in self._pending.values():
            try:
                waiters.remove(self)
            except ValueError:
                pass

    @staticmethod
    def _touch_label(label: RemoteImageLabel, action) -> None:
        try:
            action()
        except RuntimeError:
            pass

    def _target_resolution(self) -> int:
        display = max(self.width(), self.height(), self._resolution, 32)
        return min(display, _LIST_THUMB_RESOLUTION if display <= 80 else _PREFETCH_RESOLUTION)

    def load(self, url: str) -> None:
        try:
            self._ensure_disk_hook()
            resolution = self._target_resolution()
            self._url = self._normalize_url(url, resolution=resolution)
            if not self._url:
                self.setPixmap(QPixmap())
                self.setText('?')
                return
            if self._url in self._failed_urls:
                failed_at = self._failed_at.get(self._url, 0.0)
                if time.monotonic() - failed_at < self._FAILED_RETRY_COOLDOWN_SEC:
                    self.setPixmap(QPixmap())
                    self.setText('?')
                    return
                self._failed_urls.discard(self._url)
                self._retry_counts.pop(self._url, None)
                self._failed_at.pop(self._url, None)
            cache_key = self._cache_key(self._url)
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                self._apply_pixmap(cached)
                debug_event(logger, 'memory cache hit', key=cache_key)
                return
            disk_path = cached_image_path(self._url)
            if disk_path is not None:
                waiters = self._pending.setdefault(self._url, [])
                if self not in waiters:
                    waiters.append(self)
                if len(waiters) == 1:
                    _disk_pool.start(_DiskLoadRunnable(str(disk_path), cache_key, waiters))
                return
            waiters = self._pending.setdefault(self._url, [])
            if self not in waiters:
                waiters.append(self)
            if len(waiters) == 1:
                debug_event(logger, 'network fetch queued', url=truncate_for_log(self._url))
                self._schedule_network(high_priority=True)
        except Exception as exc:
            log_exception(logger, 'image load', exc)
            self.setPixmap(QPixmap())
            self.setText('?')

    def _schedule_network(self, *, high_priority: bool=True) -> None:
        if not self._url:
            return
        RemoteImageLabel._schedule_request(self._url, high_priority=high_priority)

    @classmethod
    def _store_memory(cls, key: str, pixmap: QPixmap) -> None:
        if not key or pixmap.isNull():
            return
        cls._cache[key] = pixmap
        cls._cache.move_to_end(key)
        while len(cls._cache) > cls._CACHE_MAX:
            cls._cache.popitem(last=False)

    @classmethod
    def _schedule_request(cls, url: str, *, high_priority: bool=True) -> None:
        if not url or url in cls._active_urls:
            return
        queue = cls._queued_high if high_priority else cls._queued_low
        other = cls._queued_low if high_priority else cls._queued_high
        if url not in queue and url not in other:
            queue.append(url)
        elif high_priority and url in cls._queued_low:
            cls._queued_low.remove(url)
            cls._queued_high.append(url)
        cls._pump_queue()

    @classmethod
    def _begin_request(cls, url: str) -> None:
        if not url or url in cls._active_urls:
            return
        cls._active_urls.add(url)
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, USER_AGENT)
        if hasattr(request, 'setRedirectPolicy'):
            request.setRedirectPolicy(QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        elif hasattr(QNetworkRequest.Attribute, 'RedirectPolicyAttribute'):
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        if cls._auth_token and any((host in url for host in ('vrchat.cloud', 'vrchat.com', 'cloudfront.net'))):
            cookie = f'auth={cls._auth_token}'
            if cls._two_factor_token:
                cookie += f'; twoFactorAuth={cls._two_factor_token}'
            request.setRawHeader(b'Cookie', cookie.encode())
        cls._network().get(request)

    @classmethod
    def _pump_queue(cls) -> None:
        try:
            while cls._queued_high or cls._queued_low:
                if len(cls._active_urls) >= cls._MAX_CONCURRENT:
                    return
                url = cls._queued_high.pop(0) if cls._queued_high else cls._queued_low.pop(0)
                if url in cls._active_urls:
                    continue
                if url not in cls._pending:
                    cls._pending[url] = []
                cls._begin_request(url)
        except Exception as exc:
            log_exception(logger, 'image queue pump', exc)

    def _apply_pixmap(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)
        self.setText('')

    @classmethod
    def _resolve_pending_key(cls, reply: QNetworkReply) -> str:
        request_url = reply.request().url().toString()
        if request_url in cls._pending:
            return request_url
        for url in list(cls._pending):
            if reply.url().toString() == url or reply.url().toString().startswith(url):
                return url
        return request_url

    @classmethod
    def _show_placeholder(cls, labels: list[RemoteImageLabel]) -> None:
        for label in labels:
            cls._touch_label(label, lambda lbl=label: (lbl.setPixmap(QPixmap()), lbl.setText('?')))

    @staticmethod
    def _network_error_code(reply: QNetworkReply) -> int:
        err = reply.error()
        return int(getattr(err, 'value', err))

    @classmethod
    def _is_transient_network_error(cls, reply: QNetworkReply) -> bool:
        transient = {QNetworkReply.NetworkError.ConnectionRefusedError, QNetworkReply.NetworkError.RemoteHostClosedError, QNetworkReply.NetworkError.HostNotFoundError, QNetworkReply.NetworkError.TimeoutError, QNetworkReply.NetworkError.OperationCanceledError, QNetworkReply.NetworkError.TemporaryNetworkFailureError, QNetworkReply.NetworkError.NetworkSessionFailedError, QNetworkReply.NetworkError.UnknownNetworkError, QNetworkReply.NetworkError.ServiceUnavailableError}
        return reply.error() in transient

    @classmethod
    def _on_network_finished(cls, reply: QNetworkReply) -> None:
        request_url = ''
        try:
            request_url = cls._resolve_pending_key(reply)
            labels = cls._pending.pop(request_url, [])
            cache_key = cls._cache_key(request_url)
            if reply.error() == QNetworkReply.NetworkError.NoError:
                cls._retry_counts.pop(request_url, None)
                data = bytes(reply.readAll())
                pixmap = QPixmap()
                if data and pixmap.loadFromData(data):
                    save_image_bytes_to_cache(request_url, data)
                    cls._store_memory(cache_key, pixmap)
                    debug_event(logger, 'network ok', key=cache_key, bytes=len(data), labels=len(labels))
                    for label in labels:
                        cls._touch_label(label, lambda lbl=label, pix=pixmap: lbl._apply_pixmap(pix))
                elif labels:
                    cls._show_placeholder(labels)
            elif labels and cls._is_transient_network_error(reply):
                retries = cls._retry_counts.get(request_url, 0) + 1
                cls._retry_counts[request_url] = retries
                debug_event(logger, 'network retry', url=truncate_for_log(request_url), attempt=retries, error=cls._network_error_code(reply))
                if retries >= cls._MAX_NETWORK_RETRIES:
                    cls._failed_urls.add(request_url)
                    cls._failed_at[request_url] = time.monotonic()
                    cls._retry_counts.pop(request_url, None)
                    cls._show_placeholder(labels)
                else:
                    cls._pending[request_url] = labels
                    QTimer.singleShot(cls._NETWORK_RETRY_MS, lambda url=request_url: cls._schedule_request(url, high_priority=True))
            elif labels:
                cls._retry_counts.pop(request_url, None)
                cls._failed_urls.add(request_url)
                cls._failed_at[request_url] = time.monotonic()
                logger.warning('Image fetch failed (%s): %s', cls._network_error_code(reply), truncate_for_log(request_url))
                cls._show_placeholder(labels)
        except Exception as exc:
            log_exception(logger, 'image network finished', exc)
        finally:
            if request_url:
                cls._active_urls.discard(request_url)
                for queue in (cls._queued_high, cls._queued_low):
                    try:
                        queue.remove(request_url)
                    except ValueError:
                        pass
            try:
                cls._pump_queue()
            except Exception as exc:
                log_exception(logger, 'image queue pump (after network)', exc)
            reply.deleteLater()
