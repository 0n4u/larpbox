from __future__ import annotations
import sqlite3
from ..logging_setup import get_logger

logger = get_logger('store')

SCHEMA_VERSION = 2

_SCHEMA_V1 = (
    """
    CREATE TABLE IF NOT EXISTS kv_meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gamelog_location (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ts            REAL NOT NULL,
        world_id      TEXT,
        instance_id   TEXT,
        location      TEXT,
        world_name    TEXT,
        region        TEXT,
        instance_type TEXT,
        group_id      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gamelog_join_leave (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts           REAL NOT NULL,
        kind         TEXT NOT NULL,
        user_id      TEXT,
        display_name TEXT,
        location     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS friend_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts           REAL NOT NULL,
        type         TEXT NOT NULL,
        user_id      TEXT,
        display_name TEXT,
        prev         TEXT,
        current      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS feed (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts           REAL NOT NULL,
        type         TEXT NOT NULL,
        user_id      TEXT,
        display_name TEXT,
        payload_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        vrc_id          TEXT UNIQUE,
        ts              REAL NOT NULL,
        type            TEXT,
        sender_user_id  TEXT,
        sender_username TEXT,
        message         TEXT,
        details_json    TEXT,
        seen            INTEGER NOT NULL DEFAULT 0,
        responded       INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_visits (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        world_id  TEXT,
        location  TEXT,
        joined_ts REAL,
        left_ts   REAL,
        seconds   REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_log (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        ts       REAL NOT NULL,
        location TEXT,
        url      TEXT,
        title    TEXT,
        source   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS accounts (
        user_id      TEXT PRIMARY KEY,
        username     TEXT,
        display_name TEXT,
        added_ts     REAL,
        last_used_ts REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_notes (
        user_id TEXT PRIMARY KEY,
        note    TEXT,
        color   TEXT,
        tags    TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gamelog_location_ts ON gamelog_location(ts)",
    "CREATE INDEX IF NOT EXISTS idx_join_leave_ts ON gamelog_join_leave(ts)",
    "CREATE INDEX IF NOT EXISTS idx_join_leave_user ON gamelog_join_leave(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_friend_log_ts ON friend_log(ts)",
    "CREATE INDEX IF NOT EXISTS idx_friend_log_user ON friend_log(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_feed_ts ON feed(ts)",
    "CREATE INDEX IF NOT EXISTS idx_feed_user ON feed(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_seen ON notifications(seen)",
    "CREATE INDEX IF NOT EXISTS idx_world_visits_world ON world_visits(world_id)",
    "CREATE INDEX IF NOT EXISTS idx_media_log_ts ON media_log(ts)",
)


_SCHEMA_V2 = (
    """
    CREATE TABLE IF NOT EXISTS local_favorites (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        kind       TEXT NOT NULL,
        target_id  TEXT NOT NULL,
        name       TEXT,
        group_name TEXT NOT NULL DEFAULT 'default',
        image_url  TEXT,
        added_ts   REAL,
        UNIQUE(kind, target_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_local_favorites_kind ON local_favorites(kind)",
)


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute('PRAGMA user_version').fetchone()
    return int(row[0]) if row else 0


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA_V1:
        conn.execute(statement)


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA_V2:
        conn.execute(statement)


def apply_migrations(conn: sqlite3.Connection) -> None:
    version = _current_version(conn)
    if version >= SCHEMA_VERSION:
        return
    logger.info('Applying store migrations: v%d -> v%d', version, SCHEMA_VERSION)
    if version < 1:
        _migrate_to_v1(conn)
    if version < 2:
        _migrate_to_v2(conn)
    conn.execute(
        'INSERT OR REPLACE INTO kv_meta(key, value) VALUES (?, ?)',
        ('schema_version', str(SCHEMA_VERSION)),
    )
    conn.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
    conn.commit()
