from __future__ import annotations
import threading
import time
from typing import Any, Callable, TypeVar
from .logging_setup import get_logger, is_debug_mode, debug_event
from .action_cancel import check_cancelled
logger = get_logger('api_rate_limit')
T = TypeVar('T')
_DEFAULT_MIN_INTERVAL = 0.5
_DEFAULT_MAX_PER_MINUTE = 50
_DEFAULT_COOLDOWN_SEC = 30.0
_MAX_RETRIES = 5

def _is_rate_limited_exc(exc: Exception) -> bool:
    status = getattr(exc, 'status', None)
    if status == 429:
        return True
    parts = [str(getattr(exc, 'reason', '') or '')]
    body = getattr(exc, 'body', None)
    if body:
        raw = body.decode('utf-8', errors='replace') if isinstance(body, bytes) else str(body)
        parts.append(raw)
    message = ' '.join(parts).casefold()
    return 'too many' in message or 'rate limit' in message

class ApiRateLimiter:

    def __init__(self, *, min_interval: float=_DEFAULT_MIN_INTERVAL, max_per_minute: int=_DEFAULT_MAX_PER_MINUTE) -> None:
        self._min_interval = min_interval
        self._max_per_minute = max_per_minute
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._request_times: list[float] = []
        self._cooldown_until = 0.0

    def wait(self) -> None:
        while True:
            check_cancelled()
            with self._lock:
                now = time.monotonic()
                if now < self._cooldown_until:
                    delay = self._cooldown_until - now
                else:
                    self._request_times = [stamp for stamp in self._request_times if now - stamp < 60.0]
                    if len(self._request_times) >= self._max_per_minute:
                        delay = 60.0 - (now - self._request_times[0])
                    else:
                        delay = max(0.0, self._min_interval - (now - self._last_request))
                    if delay <= 0:
                        self._last_request = now
                        self._request_times.append(now)
                        return
                if is_debug_mode() and delay > 0:
                    debug_event(logger, 'rate limiter waiting', delay_sec=round(delay, 2), minute_count=len(self._request_times))
            time.sleep(min(max(delay, 0.05), 15.0))
            check_cancelled()

    def note_rate_limited(self, *, retry_after: float=_DEFAULT_COOLDOWN_SEC) -> None:
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + max(retry_after, 1.0))
        logger.warning('VRChat API rate limit hit — cooling down for %.0fs', retry_after)

    def call(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            self.wait()
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if _is_rate_limited_exc(exc) and attempt < _MAX_RETRIES - 1:
                    self.note_rate_limited(retry_after=_DEFAULT_COOLDOWN_SEC * (attempt + 1))
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError('Rate-limited call failed without an exception.')
VRCHAT_API_LIMITER = ApiRateLimiter()
EXTERNAL_API_LIMITER = ApiRateLimiter(min_interval=0.25, max_per_minute=80)

def rate_limited_vrchat_call(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    return VRCHAT_API_LIMITER.call(fn, *args, **kwargs)

def rate_limited_external_call(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    return EXTERNAL_API_LIMITER.call(fn, *args, **kwargs)
