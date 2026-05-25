from __future__ import annotations
from collections import OrderedDict
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QLabel, QWidget
from .image_cache import cached_image_path, save_image_bytes_to_cache
from .logging_setup import get_logger
from .vrc_image_utils import is_allowed_image_host, normalize_vrc_image_url, parse_vrc_image_url
from .vrchat_auth import USER_AGENT, VRChatSession
logger = get_logger('images')
_THUMB_STYLE = 'QLabel#avatarThumb { background-color: #232323; border: 1px solid #3a3a3a; border-radius: 6px; color: #666; }'

class RemoteImageLabel(QLabel):
    _CACHE_MAX = 512
    _MAX_CONCURRENT = 12
    _NETWORK_RETRY_MS = 2000
    _cache: OrderedDict[str, QPixmap] = OrderedDict()
    _pending: dict[str, list[RemoteImageLabel]] = {}
    _failed_urls: set[str] = set()
    _session: VRChatSession | None = None
    _auth_token: str | None = None
    _two_factor_token: str | None = None
    _manager: QNetworkAccessManager | None = None
    _network_connected: bool = False
    _active_urls: set[str] = set()
    _queued_urls: list[str] = []

    @classmethod
    def clear_failed_urls(cls) -> None:
        cls._failed_urls.clear()

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
        return max(self.width(), self.height(), self._resolution, 32)

    def load(self, url: str) -> None:
        resolution = self._target_resolution()
        self._url = normalize_vrc_image_url(url.strip(), resolution=resolution)
        if not self._url:
            self.setPixmap(QPixmap())
            self.setText('?')
            return
        if not is_allowed_image_host(self._url):
            self.setPixmap(QPixmap())
            self.setText('?')
            return
        if self._url in self._failed_urls:
            self.setPixmap(QPixmap())
            self.setText('?')
            return
        cache_key = self._cache_key(self._url)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            self._apply_pixmap(cached)
            return
        disk_path = cached_image_path(self._url)
        if disk_path is not None:
            pixmap = QPixmap(str(disk_path))
            if not pixmap.isNull():
                self._store_memory(cache_key, pixmap)
                self._apply_pixmap(pixmap)
                return
        waiters = self._pending.setdefault(self._url, [])
        if self not in waiters:
            waiters.append(self)
        if len(waiters) != 1:
            return
        self._schedule_request(self._url)

    @classmethod
    def _store_memory(cls, key: str, pixmap: QPixmap) -> None:
        if not key:
            return
        cls._cache[key] = pixmap
        cls._cache.move_to_end(key)
        while len(cls._cache) > cls._CACHE_MAX:
            cls._cache.popitem(last=False)

    @classmethod
    def _schedule_request(cls, url: str) -> None:
        if not url or url in cls._active_urls or url in cls._queued_urls:
            return
        if url not in cls._pending:
            return
        if len(cls._active_urls) < cls._MAX_CONCURRENT:
            cls._begin_request(url)
        else:
            cls._queued_urls.append(url)

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
        while cls._queued_urls and len(cls._active_urls) < cls._MAX_CONCURRENT:
            url = cls._queued_urls.pop(0)
            if url in cls._pending:
                cls._begin_request(url)

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

    @classmethod
    def _is_transient_network_error(cls, reply: QNetworkReply) -> bool:
        transient = {QNetworkReply.NetworkError.ConnectionRefusedError, QNetworkReply.NetworkError.RemoteHostClosedError, QNetworkReply.NetworkError.HostNotFoundError, QNetworkReply.NetworkError.TimeoutError, QNetworkReply.NetworkError.OperationCanceledError, QNetworkReply.NetworkError.TemporaryNetworkFailureError, QNetworkReply.NetworkError.NetworkSessionFailedError, QNetworkReply.NetworkError.UnknownNetworkError, QNetworkReply.NetworkError.ServiceUnavailableError}
        return reply.error() in transient

    @classmethod
    def _on_network_finished(cls, reply: QNetworkReply) -> None:
        request_url = cls._resolve_pending_key(reply)
        labels = cls._pending.pop(request_url, [])
        if not labels:
            cls._active_urls.discard(request_url)
            cls._pump_queue()
            reply.deleteLater()
            return
        cache_key = cls._cache_key(request_url)
        if reply.error() == QNetworkReply.NetworkError.NoError:
            data = bytes(reply.readAll())
            pixmap = QPixmap()
            if data and pixmap.loadFromData(data):
                save_image_bytes_to_cache(request_url, data)
                cls._store_memory(cache_key, pixmap)
                for label in labels:
                    cls._touch_label(label, lambda lbl=label, pix=pixmap: lbl._apply_pixmap(pix))
            else:
                cls._show_placeholder(labels)
        elif cls._is_transient_network_error(reply):
            cls._pending[request_url] = labels
            QTimer.singleShot(cls._NETWORK_RETRY_MS, lambda url=request_url: cls._schedule_request(url))
        else:
            cls._failed_urls.add(request_url)
            cls._show_placeholder(labels)
        cls._active_urls.discard(request_url)
        try:
            cls._queued_urls.remove(request_url)
        except ValueError:
            pass
        cls._pump_queue()
        reply.deleteLater()
