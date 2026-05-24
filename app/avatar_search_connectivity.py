from __future__ import annotations

import ssl
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

import urllib.error
import urllib.request

from .logging_setup import get_logger
from .vrchat_api import AvatarResult, VRCX_USER_AGENT

logger = get_logger('avatar_search_connectivity')

_ALT_HTTPS_PORTS = (2053, 8443)


@dataclass(frozen=True)
class ProviderProbeResult:
    provider_id: str
    label: str
    tls_ok: bool
    api_ok: bool
    detail: str


def tls_handshake_ok(host: str, port: int = 443) -> bool:
    import socket

    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                return True
    except OSError:
        return False


def probe_provider(provider_id: str, label: str, base_url: str) -> ProviderProbeResult:
    host = urllib.parse.urlparse(base_url).hostname or base_url
    tls_443 = tls_handshake_ok(host, 443)
    tls_alt = any(tls_handshake_ok(host, port) for port in _ALT_HTTPS_PORTS)
    tls_ok = tls_443 or tls_alt

    if not tls_ok:
        detail = 'TLS handshake failed'
    else:
        detail = 'TLS OK'

    test_url = f'{base_url}?search=test&n=1' if '?' not in base_url else f'{base_url}&search=test&n=1'
    api_ok, api_detail = _probe_api_url(test_url)
    if api_ok:
        detail = api_detail
    elif not api_ok and tls_alt and not tls_443:
        for port in _ALT_HTTPS_PORTS:
            parsed = urllib.parse.urlparse(base_url)
            alt_url = f'{parsed.scheme}://{parsed.hostname}:{port}{parsed.path}'
            alt_test = f'{alt_url}?search=test&n=1'
            alt_ok, alt_msg = _probe_api_url(alt_test)
            if alt_ok:
                api_ok = True
                detail = f'OK via port {port}'
                break
        if not api_ok:
            detail = api_detail
    elif not api_ok:
        detail = api_detail

    return ProviderProbeResult(provider_id, label, tls_ok, api_ok, detail)


def probe_provider_search(
    provider_id: str,
    label: str,
    search_fn: Callable[..., list[AvatarResult]],
) -> ProviderProbeResult:
    try:
        results = search_fn(None, 'cat', 'all', limit=2)
        count = len(results)
        if count > 0:
            return ProviderProbeResult(provider_id, label, True, True, f'OK ({count} results)')
        return ProviderProbeResult(provider_id, label, True, False, 'No results for test query')
    except Exception as exc:
        detail = str(exc).strip()[:120] or type(exc).__name__
        return ProviderProbeResult(provider_id, label, False, False, detail)


def _probe_api_url(url: str) -> tuple[bool, str]:
    req = urllib.request.Request(
        url,
        headers={'User-Agent': VRCX_USER_AGENT, 'Referer': 'https://vrcx.app', 'Accept': 'application/json,*/*'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read(400)
            if body.lstrip().startswith(b'['):
                return True, 'API reachable'
            return False, f'Unexpected response ({resp.headers.get("Content-Type", "?")})'
    except urllib.error.HTTPError as exc:
        return False, str(exc).strip()[:120] or f'HTTP {exc.code}'
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return False, str(reason).strip()[:120]
        return False, str(exc).strip()[:120] or str(reason)[:120]
    except Exception as exc:
        return False, str(exc).strip()[:120]
