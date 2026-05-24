from __future__ import annotations

import re
from collections import OrderedDict

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtWidgets import QLabel, QWidget

from .logging_setup import get_logger
from .vrchat_api import normalize_thumbnail_url
from .vrchat_auth import USER_AGENT, VRChatSession

logger = get_logger('images')

_THUMB_STYLE = (
    'QLabel#avatarThumb { background-color: #232323; border: 1px solid #3a3a3a; '
    'border-radius: 6px; color: #666; }'
)
_VRCHAT_IMAGE_RE = re.compile(
    r'^https://api\.vrchat\.cloud/api/1/image/(file_[a-f0-9-]+)/(\d+)/(\d+)$',
    re.IGNORECASE,
)


class RemoteImageLabel(QLabel):
    _CACHE_MAX = 256
    _MAX_CONCURRENT = 12
    _cache: OrderedDict[str, QPixmap] = OrderedDict()
    _pending: dict[str, list[RemoteImageLabel]] = {}
    _failed_urls: set[str] = set()
    _logged_failures: set[str] = set()
    _retry_chains: dict[str, list[str]] = {}
    _failed_chain_count = 0
    _session: VRChatSession | None = None
    _auth_token: str | None = None
    _two_factor_token: str | None = None
    _manager: QNetworkAccessManager | None = None
    _network_connected: bool = False
    _active_urls: set[str] = set()
    _queued_urls: list[str] = []

    @classmethod
    def set_session(cls, session: VRChatSession | None) -> None:
        cls._session = session
        if session is None:
            cls.set_auth(None, None)
            return
        cls.set_auth(session.auth_token, session.two_factor_token)

    @classmethod
    def set_auth(cls, auth_token: str | None, two_factor_token: str | None = None) -> None:
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

    @classmethod
    def _chain_key(cls, url: str) -> str:
        match = _VRCHAT_IMAGE_RE.match(url.strip())
        if match:
            file_id, _, size = match.groups()
            return f'{file_id}:{size}'
        return url.strip()

    @classmethod
    def _alternate_image_urls(cls, url: str) -> list[str]:
        match = _VRCHAT_IMAGE_RE.match(url.strip())
        if not match:
            return []
        file_id, version, size = match.groups()
        tried_version = int(version)
        tried_size = int(size)
        urls: list[str] = []
        seen: set[str] = set()
        for alt_size in (tried_size, 128, 256, 64):
            for alt_version in range(1, 9):
                if alt_version == tried_version and alt_size == tried_size:
                    continue
                candidate = f'https://api.vrchat.cloud/api/1/image/{file_id}/{alt_version}/{alt_size}'
                if candidate in seen:
                    continue
                seen.add(candidate)
                urls.append(candidate)
        return urls

    def __init__(self, size: int = 56, parent: QWidget | None = None):
        super().__init__(parent)
        self._url = ''
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName('avatarThumb')
        self.setText('…')
        self.setStyleSheet(_THUMB_STYLE)
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

    def load(self, url: str) -> None:
        self._url = normalize_thumbnail_url(url.strip())
        if not self._url:
            self.setText('?')
            return
        if self._url in self._failed_urls:
            self.setText('?')
            return
        cached = self._cache.get(self._url)
        if cached is not None:
            self._cache.move_to_end(self._url)
            self._apply_pixmap(cached)
            return
        waiters = self._pending.setdefault(self._url, [])
        if self not in waiters:
            waiters.append(self)
        if len(waiters) != 1:
            return
        self._schedule_request(self._url)

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
            request.setAttribute(
                QNetworkRequest.Attribute.RedirectPolicyAttribute,
                QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
            )
        if cls._auth_token and 'vrchat.cloud' in url:
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

    def _start_request(self, url: str) -> None:
        self._schedule_request(url)

    def _apply_pixmap(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
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
    def _store_cache(cls, url: str, pixmap: QPixmap) -> None:
        if not url:
            return
        cls._cache[url] = pixmap
        cls._cache.move_to_end(url)
        while len(cls._cache) > cls._CACHE_MAX:
            cls._cache.popitem(last=False)

    @classmethod
    def _should_retry_image(cls, request_url: str, reply: QNetworkReply) -> bool:
        if 'api.vrchat.cloud/api/1/image/' not in request_url:
            return False
        if reply.error() == QNetworkReply.NetworkError.ContentNotFoundError:
            return True
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        return status == 404

    @classmethod
    def _show_placeholder(cls, labels: list[RemoteImageLabel]) -> None:
        for label in labels:
            cls._touch_label(label, lambda lbl=label: lbl.setText('?'))

    @classmethod
    def _note_chain_failure(cls, chain_key: str) -> None:
        cls._failed_chain_count += 1
        if chain_key in cls._logged_failures:
            return
        cls._logged_failures.add(chain_key)
        if cls._failed_chain_count <= 3 or cls._failed_chain_count % 25 == 0:
            logger.debug(
                'Thumbnail unavailable (%s total failures, latest %s)',
                cls._failed_chain_count,
                chain_key,
            )

    @classmethod
    def _retry_or_fail(cls, request_url: str, labels: list[RemoteImageLabel]) -> None:
        cls._failed_urls.add(request_url)
        chain_key = cls._chain_key(request_url)
        remaining = cls._retry_chains.get(chain_key)
        if remaining is None:
            remaining = cls._alternate_image_urls(request_url)
            cls._retry_chains[chain_key] = remaining

        while remaining:
            next_url = remaining.pop(0)
            if next_url in cls._failed_urls:
                continue
            cls._retry_chains[chain_key] = remaining
            cls._pending[next_url] = labels
            cls._schedule_request(next_url)
            return

        cls._retry_chains.pop(chain_key, None)
        cls._note_chain_failure(chain_key)
        cls._show_placeholder(labels)

    @classmethod
    def _on_network_finished(cls, reply: QNetworkReply) -> None:
        request_url = cls._resolve_pending_key(reply)
        labels = cls._pending.pop(request_url, [])
        if not labels:
            cls._active_urls.discard(request_url)
            cls._pump_queue()
            reply.deleteLater()
            return
        if reply.error() == QNetworkReply.NetworkError.NoError:
            data = reply.readAll()
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                cls._store_cache(request_url, pixmap)
                final_url = reply.url().toString()
                if final_url and final_url != request_url:
                    cls._store_cache(final_url, pixmap)
                cls._retry_chains.pop(cls._chain_key(request_url), None)
                for label in labels:
                    cls._touch_label(label, lambda lbl=label: lbl._apply_pixmap(pixmap))
            else:
                for label in labels:
                    cls._touch_label(label, lambda lbl=label: lbl.setText('?'))
        elif cls._should_retry_image(request_url, reply):
            cls._retry_or_fail(request_url, labels)
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
