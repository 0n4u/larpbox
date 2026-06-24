from __future__ import annotations
import threading
import time
from contextlib import contextmanager
from enum import Enum
from typing import Any, Callable, Iterator, TypeVar
from .action_cancel import check_cancelled
from .logging_setup import debug_event, get_logger, is_debug_mode
logger = get_logger('api_rate_limit')
T = TypeVar('T')
_DEFAULT_COOLDOWN_SEC = 30.0
_MAX_RETRIES = 5
_INTERACTIVE_MIN_INTERVAL = 0.12
_BACKGROUND_MIN_INTERVAL = 0.45
_EXTERNAL_INTERACTIVE_MIN_INTERVAL = 0.15
_EXTERNAL_BACKGROUND_MIN_INTERVAL = 0.3
_VRCHAT_INTERACTIVE_PER_MIN = 18
_VRCHAT_BACKGROUND_PER_MIN = 32
_EXTERNAL_INTERACTIVE_PER_MIN = 24
_EXTERNAL_BACKGROUND_PER_MIN = 40
_PRESSURE_THRESHOLD = 0.85


class ApiPriority(str, Enum):
    INTERACTIVE = 'interactive'
    BACKGROUND = 'background'


class _PriorityState:
    def __init__(self) -> None:
        self._local = threading.local()

    def current(self) -> ApiPriority:
        value = getattr(self._local, 'value', None)
        return value if isinstance(value, ApiPriority) else ApiPriority.BACKGROUND

    def set(self, priority: ApiPriority) -> ApiPriority:
        previous = self.current()
        self._local.value = priority
        return previous

    def interactive_depth(self) -> int:
        return int(getattr(self._local, 'interactive_depth', 0) or 0)

    def enter_interactive(self) -> None:
        depth = self.interactive_depth()
        self._local.interactive_depth = depth + 1

    def leave_interactive(self) -> None:
        depth = self.interactive_depth()
        self._local.interactive_depth = max(0, depth - 1)


class _GlobalInteractiveGate:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._depth = 0

    def enter(self) -> None:
        with self._lock:
            self._depth += 1

    def leave(self) -> None:
        with self._lock:
            self._depth = max(0, self._depth - 1)

    def active(self) -> bool:
        with self._lock:
            return self._depth > 0


_GLOBAL_INTERACTIVE = _GlobalInteractiveGate()


_PRIORITY = _PriorityState()


@contextmanager
def api_priority(priority: ApiPriority | str) -> Iterator[None]:
    if isinstance(priority, str):
        priority = ApiPriority(priority)
    previous = _PRIORITY.set(priority)
    entered = False
    if priority == ApiPriority.INTERACTIVE:
        _PRIORITY.enter_interactive()
        _GLOBAL_INTERACTIVE.enter()
        entered = True
    try:
        yield
    finally:
        if entered:
            _GLOBAL_INTERACTIVE.leave()
            _PRIORITY.leave_interactive()
        _PRIORITY.set(previous)


def current_api_priority() -> ApiPriority:
    return _PRIORITY.current()


def interactive_api_active() -> bool:
    return _GLOBAL_INTERACTIVE.active()


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


class TokenBucket:

    def __init__(self, *, rate_per_minute: int, min_interval: float) -> None:
        self._rate = max(1, rate_per_minute)
        self._min_interval = max(0.0, min_interval)
        self._tokens = float(self._rate)
        self._last_refill = time.monotonic()
        self._last_consume = 0.0
        self._lock = threading.Lock()

    @property
    def rate_per_minute(self) -> int:
        return self._rate

    def pressure(self) -> float:
        with self._lock:
            return 1.0 - min(1.0, self._tokens / float(self._rate))

    def _refill_locked(self, now: float) -> None:
        elapsed = max(0.0, now - self._last_refill)
        if elapsed <= 0:
            return
        self._last_refill = now
        self._tokens = min(float(self._rate), self._tokens + elapsed * (self._rate / 60.0))

    def acquire(self, *, label: str, yield_to_interactive: bool=False) -> None:
        while True:
            check_cancelled()
            if yield_to_interactive and interactive_api_active():
                time.sleep(0.05)
                continue
            with self._lock:
                now = time.monotonic()
                self._refill_locked(now)
                interval_ok = now - self._last_consume >= self._min_interval
                if self._tokens >= 1.0 and interval_ok:
                    self._tokens -= 1.0
                    self._last_consume = now
                    return
                token_wait = 0.0
                if self._tokens < 1.0:
                    token_wait = (1.0 - self._tokens) * (60.0 / self._rate)
                interval_wait = max(0.0, self._min_interval - (now - self._last_consume))
                delay = max(token_wait, interval_wait)
                tokens = self._tokens
            if is_debug_mode() and delay > 0.05:
                debug_event(logger, 'rate limiter waiting', bucket=label, delay_sec=round(delay, 2), tokens=round(tokens, 2))
            time.sleep(min(max(delay, 0.05), 8.0))


class DualApiRateLimiter:

    def __init__(self, *, interactive_rate: int, background_rate: int, interactive_interval: float, background_interval: float, label: str) -> None:
        self._label = label
        self._interactive = TokenBucket(rate_per_minute=interactive_rate, min_interval=interactive_interval)
        self._background = TokenBucket(rate_per_minute=background_rate, min_interval=background_interval)
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    def _bucket(self, priority: ApiPriority) -> TokenBucket:
        return self._interactive if priority == ApiPriority.INTERACTIVE else self._background

    def pressure(self) -> float:
        return max(self._interactive.pressure(), self._background.pressure())

    def wait(self, *, priority: ApiPriority | None=None) -> None:
        chosen = priority or _PRIORITY.current()
        while True:
            check_cancelled()
            with self._lock:
                cooldown = max(0.0, self._cooldown_until - time.monotonic())
            if cooldown > 0:
                if is_debug_mode():
                    debug_event(logger, 'rate limiter cooldown', bucket=self._label, delay_sec=round(cooldown, 2))
                time.sleep(min(cooldown, 8.0))
                continue
            yield_to_interactive = chosen == ApiPriority.BACKGROUND
            self._bucket(chosen).acquire(label=self._label, yield_to_interactive=yield_to_interactive)
            return

    def note_rate_limited(self, *, retry_after: float=_DEFAULT_COOLDOWN_SEC) -> None:
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + max(retry_after, 1.0))
        logger.warning('%s rate limit hit — cooling down for %.0fs', self._label, retry_after)

    def call(self, fn: Callable[..., T], /, *args: Any, priority: ApiPriority | None=None, **kwargs: Any) -> T:
        chosen = priority or _PRIORITY.current()
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            self.wait(priority=chosen)
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


VRCHAT_API_LIMITER = DualApiRateLimiter(
    interactive_rate=_VRCHAT_INTERACTIVE_PER_MIN,
    background_rate=_VRCHAT_BACKGROUND_PER_MIN,
    interactive_interval=_INTERACTIVE_MIN_INTERVAL,
    background_interval=_BACKGROUND_MIN_INTERVAL,
    label='vrchat',
)
EXTERNAL_API_LIMITER = DualApiRateLimiter(
    interactive_rate=_EXTERNAL_INTERACTIVE_PER_MIN,
    background_rate=_EXTERNAL_BACKGROUND_PER_MIN,
    interactive_interval=_EXTERNAL_INTERACTIVE_MIN_INTERVAL,
    background_interval=_EXTERNAL_BACKGROUND_MIN_INTERVAL,
    label='external',
)


def api_budget_pressure() -> float:
    return max(VRCHAT_API_LIMITER.pressure(), EXTERNAL_API_LIMITER.pressure())


def api_budget_saturated() -> bool:
    return api_budget_pressure() >= _PRESSURE_THRESHOLD or interactive_api_active()


def rate_limited_vrchat_call(fn: Callable[..., T], /, *args: Any, priority: ApiPriority | str | None=None, **kwargs: Any) -> T:
    chosen = ApiPriority(priority) if isinstance(priority, str) else priority
    return VRCHAT_API_LIMITER.call(fn, *args, priority=chosen, **kwargs)


def rate_limited_external_call(fn: Callable[..., T], /, *args: Any, priority: ApiPriority | str | None=None, **kwargs: Any) -> T:
    chosen = ApiPriority(priority) if isinstance(priority, str) else priority
    return EXTERNAL_API_LIMITER.call(fn, *args, priority=chosen, **kwargs)
