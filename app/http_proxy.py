from __future__ import annotations
import os
import re
import threading
import urllib.error
import urllib.request
from typing import IO
from .logging_setup import get_logger
logger = get_logger('http_proxy')
_HOST_PORT_RE = re.compile(r'^[\w.\-]+:\d+$')
_opener_lock = threading.Lock()
_cached_opener: urllib.request.OpenerDirector | None = None
_cached_proxy_key: str | None = None


def normalize_proxy_url(value: str) -> str | None:
    raw = (value or '').strip()
    if not raw:
        return None
    if '://' not in raw:
        if _HOST_PORT_RE.match(raw):
            raw = f'http://{raw}'
        else:
            return None
    scheme = raw.split('://', 1)[0].casefold()
    if scheme not in ('http', 'https', 'socks5', 'socks5h', 'socks4'):
        return None
    return raw


def configured_proxy_url() -> str | None:
    from .config import load_config_cached
    cfg = load_config_cached()
    saved = normalize_proxy_url(str(cfg.get('http_proxy') or ''))
    if saved:
        return saved
    for key in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy'):
        env = normalize_proxy_url(os.environ.get(key, ''))
        if env:
            return env
    return None


def proxy_status_label() -> str:
    proxy = configured_proxy_url()
    if not proxy:
        return 'Direct (no proxy)'
    return f'Proxy: {proxy}'


def invalidate_proxy_opener() -> None:
    global _cached_opener, _cached_proxy_key
    with _opener_lock:
        _cached_opener = None
        _cached_proxy_key = None


def _build_opener(proxy_url: str | None) -> urllib.request.OpenerDirector:
    if proxy_url:
        handlers = [urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})]
        return urllib.request.build_opener(*handlers)
    return urllib.request.build_opener()


def proxy_opener() -> urllib.request.OpenerDirector:
    global _cached_opener, _cached_proxy_key
    proxy_url = configured_proxy_url()
    key = proxy_url or ''
    with _opener_lock:
        if _cached_opener is not None and _cached_proxy_key == key:
            return _cached_opener
        _cached_opener = _build_opener(proxy_url)
        _cached_proxy_key = key
        if proxy_url:
            logger.info('Avatar HTTP proxy enabled: %s', proxy_url)
        return _cached_opener


def urlopen(request: urllib.request.Request, *, timeout: float=30.0) -> IO[bytes]:
    proxy_url = configured_proxy_url()
    if proxy_url and proxy_url.casefold().startswith('socks'):
        try:
            import socks                                
        except ImportError as exc:
            raise RuntimeError('SOCKS proxy requires PySocks. Use an HTTP proxy port from your VPN (e.g. 127.0.0.1:7890) or install PySocks.') from exc
        _ = socks
    return proxy_opener().open(request, timeout=timeout)
