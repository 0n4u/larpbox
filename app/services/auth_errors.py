from __future__ import annotations
import json
from vrchatapi.exceptions import ApiException, UnauthorizedException
_AUTH_STATUS_CODES = frozenset({401, 403})
_AUTH_TEXT_MARKERS = ('invalid auth', 'auth cookie', 'session expired', 'not authenticated', 'authentication failed', 'authentication required', 'invalid credentials', 'missing credentials', 'login required', 'user not found', 'account not found', 'current user not found', 'could not find user')
_NETWORK_MARKERS = ('10051', '10054', '10060', 'unreachable network', 'unreachable', 'timed out', 'timeout', 'connection refused', 'connection reset', 'connection aborted', 'connection error', 'connection broken', 'failed to establish', 'newconnectionerror', 'max retries exceeded', 'getaddrinfo', 'temporarily unavailable', 'network is unreachable', 'name or service not known', 'socket operation', 'forcibly closed', 'remote end closed', 'ssl', 'certificate verify failed', 'wrong version number', 'vrchat api temporarily unavailable', 'temporarily unavailable')

class AuthSessionError(Exception):
    pass

def api_error_text(exc: BaseException | None) -> str:
    if exc is None:
        return ''
    parts: list[str] = []
    reason = str(getattr(exc, 'reason', '') or '').strip()
    if reason:
        parts.append(reason)
    body = getattr(exc, 'body', None)
    if body:
        raw = body.decode('utf-8', errors='replace') if isinstance(body, bytes) else str(body)
        try:
            payload = json.loads(raw)
            message = str((payload.get('error') or {}).get('message') or '').strip()
            if message:
                parts.append(message.strip('"'))
        except (json.JSONDecodeError, TypeError, AttributeError):
            parts.append(raw.strip())
    return ' '.join(parts).strip()

def is_rate_limit_error(value: BaseException | str | None) -> bool:
    if value is None:
        return False
    message = str(value).strip().casefold()
    return 'rate limit' in message or 'too many' in message

def is_network_error(value: BaseException | str | None) -> bool:
    if value is None:
        return False
    message = str(value).strip().casefold()
    if not message:
        return False
    return any((marker in message for marker in _NETWORK_MARKERS))

def is_auth_failure(value: BaseException | str | None) -> bool:
    if value is None:
        return False
    if is_network_error(value):
        return False
    if isinstance(value, AuthSessionError):
        return True
    if isinstance(value, UnauthorizedException):
        status = getattr(value, 'status', None)
        if status == 200:
            return False
        return status in _AUTH_STATUS_CODES
    if isinstance(value, ApiException):
        if auth_session_error_from_api(value) is not None:
            return True
        reason = str(getattr(value, 'reason', '') or '')
        return is_auth_failure(reason)
    message = str(value).strip()
    if not message:
        return False
    lower = message.casefold()
    if '401' in lower or '403' in lower:
        return True
    if any((marker in lower for marker in _AUTH_TEXT_MARKERS)):
        return True
    if '404' in lower and any((word in lower for word in ('user', 'account', 'auth'))):
        return True
    return False

def auth_session_error_from_api(exc: ApiException | UnauthorizedException) -> AuthSessionError | None:
    if is_network_error(exc):
        return None
    status = getattr(exc, 'status', None)
    reason = str(getattr(exc, 'reason', '') or '').strip()
    detail = api_error_text(exc)
    combined = f'{reason} {detail}'.strip()
    if isinstance(exc, UnauthorizedException) and status == 200:
        return None
    if detail and is_auth_failure(detail):
        return AuthSessionError(detail)
    if status in _AUTH_STATUS_CODES:
        lower = reason.casefold()
        if status == 401 and lower in ('', 'unauthorized'):
            if combined and is_auth_failure(combined):
                return AuthSessionError(detail or combined)
            return None
        return AuthSessionError(detail or reason or 'VRChat session expired')
    if status == 404 and any((word in combined.casefold() for word in ('user', 'account', 'auth'))):
        return AuthSessionError(detail or reason or 'VRChat account not found')
    if combined and is_auth_failure(combined):
        return AuthSessionError(detail or combined)
    return None
