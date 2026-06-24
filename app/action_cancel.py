from __future__ import annotations
import threading
from contextlib import contextmanager
from typing import Iterator


class CancelledError(Exception):
    pass


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.is_cancelled:
            raise CancelledError()


_local = threading.local()


def current_cancel_token() -> CancelToken | None:
    token = getattr(_local, 'token', None)
    return token if isinstance(token, CancelToken) else None


def check_cancelled() -> None:
    token = current_cancel_token()
    if token is not None:
        token.check()


@contextmanager
def action_cancel_scope(token: CancelToken | None) -> Iterator[CancelToken | None]:
    previous = getattr(_local, 'token', None)
    _local.token = token
    try:
        yield token
    finally:
        _local.token = previous
