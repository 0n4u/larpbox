from __future__ import annotations
import queue
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from ..logging_setup import get_logger
from .migrations import apply_migrations

logger = get_logger('store')

DATA_DIR = Path(__file__).resolve().parents[2] / 'data'
DB_PATH = DATA_DIR / 'larpbox.db'

_SHUTDOWN = object()
_QUERY_TIMEOUT_SEC = 15.0


class _Job:
    __slots__ = ('fn', 'event', 'result', 'error')

    def __init__(self, fn: Callable[[sqlite3.Connection], Any], *, want_result: bool):
        self.fn = fn
        self.event: threading.Event | None = threading.Event() if want_result else None
        self.result: Any = None
        self.error: BaseException | None = None


class Database:
    """SQLite store with a single owner thread.

    All connection access happens on one background thread, which is the safest
    model for SQLite. Callers submit work; reads block (with a timeout) for the
    result, writes can be fire-and-forget. WAL keeps writes cheap and durable.
    """

    def __init__(self, path: Path = DB_PATH):
        self._path = path
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name='larpbox-db', daemon=True)
        self._started = False
        self._failed = False
        self._start_lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def is_available(self) -> bool:
        return not self._failed

    def _ensure_started(self) -> bool:
        if self._failed:
            return False
        if self._started:
            return True
        with self._start_lock:
            if self._failed:
                return False
            if not self._started:
                self._thread.start()
                self._started = True
        return True

    def _run(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._path))
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA busy_timeout=4000')
            apply_migrations(conn)
        except Exception:
            logger.error('Could not open database at %s — persistence disabled', self._path, exc_info=True)
            self._failed = True
            self._drain_after_failure()
            return
        logger.info('SQLite store ready at %s', self._path)
        while True:
            job = self._queue.get()
            if job is _SHUTDOWN:
                break
            try:
                result = job.fn(conn)
                if job.event is not None:
                    job.result = result
            except Exception as exc:
                if job.event is not None:
                    job.error = exc
                else:
                    logger.debug('Background DB write failed', exc_info=True)
            finally:
                if job.event is not None:
                    job.event.set()
        try:
            conn.commit()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    def _drain_after_failure(self) -> None:
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                return
            if isinstance(job, _Job) and job.event is not None:
                job.error = RuntimeError('Database unavailable')
                job.event.set()

    def submit(self, fn: Callable[[sqlite3.Connection], Any], *, wait: bool = False, default: Any = None) -> Any:
        if not self._ensure_started():
            return default
        job = _Job(fn, want_result=wait)
        self._queue.put(job)
        if not wait or job.event is None:
            return default
        if not job.event.wait(_QUERY_TIMEOUT_SEC):
            logger.warning('Database operation timed out')
            return default
        if job.error is not None:
            raise job.error
        return job.result

    def execute(self, sql: str, params: Sequence[Any] = (), *, wait: bool = False) -> int | None:
        def _op(conn: sqlite3.Connection) -> int | None:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid

        return self.submit(_op, wait=wait, default=None)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]], *, wait: bool = False) -> None:
        rows = list(seq_of_params)
        if not rows:
            return

        def _op(conn: sqlite3.Connection) -> None:
            conn.executemany(sql, rows)
            conn.commit()

        self.submit(_op, wait=wait, default=None)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        def _op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            cur = conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

        return self.submit(_op, wait=True, default=[]) or []

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        if not self._started or self._failed:
            return
        self._queue.put(_SHUTDOWN)
        self._thread.join(timeout=5.0)


_DB: Database | None = None
_DB_LOCK = threading.Lock()


def get_db() -> Database:
    global _DB
    if _DB is None:
        with _DB_LOCK:
            if _DB is None:
                _DB = Database()
    return _DB


def close_db() -> None:
    global _DB
    db = _DB
    if db is not None:
        db.close()
        _DB = None
