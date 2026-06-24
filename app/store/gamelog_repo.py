from __future__ import annotations
import time
from typing import Any
from .database import get_db

JOIN = 'join'
LEAVE = 'leave'


def record_location(
    *,
    world_id: str,
    instance_id: str,
    location: str,
    world_name: str = '',
    region: str = '',
    instance_type: str = '',
    group_id: str = '',
    ts: float | None = None,
) -> None:
    get_db().execute(
        'INSERT INTO gamelog_location (ts, world_id, instance_id, location, world_name, region, instance_type, group_id)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (ts if ts is not None else time.time(), world_id, instance_id, location, world_name, region, instance_type, group_id),
    )


def location_exists(location: str, *, since_ts: float) -> bool:
    if not location:
        return False
    row = get_db().query_one(
        'SELECT 1 FROM gamelog_location WHERE location = ? AND ts >= ? LIMIT 1',
        (location, since_ts),
    )
    return row is not None


def last_location() -> dict[str, Any] | None:
    return get_db().query_one('SELECT * FROM gamelog_location ORDER BY ts DESC LIMIT 1')


def update_last_location_name(location: str, world_name: str) -> None:
    if not location or not world_name:
        return
    get_db().execute(
        'UPDATE gamelog_location SET world_name = ?'
        ' WHERE id = (SELECT id FROM gamelog_location WHERE location = ? ORDER BY ts DESC LIMIT 1)'
        " AND (world_name IS NULL OR world_name = '')",
        (world_name, location),
    )


def recent_locations(*, limit: int = 100, before_ts: float | None = None) -> list[dict[str, Any]]:
    if before_ts is None:
        return get_db().query('SELECT * FROM gamelog_location ORDER BY ts DESC LIMIT ?', (limit,))
    return get_db().query(
        'SELECT * FROM gamelog_location WHERE ts < ? ORDER BY ts DESC LIMIT ?',
        (before_ts, limit),
    )


def record_join_leave(
    *,
    kind: str,
    user_id: str,
    display_name: str,
    location: str,
    ts: float | None = None,
) -> None:
    get_db().execute(
        'INSERT INTO gamelog_join_leave (ts, kind, user_id, display_name, location) VALUES (?, ?, ?, ?, ?)',
        (ts if ts is not None else time.time(), kind, user_id, display_name, location),
    )


def recent_join_leave(*, limit: int = 200, before_ts: float | None = None) -> list[dict[str, Any]]:
    if before_ts is None:
        return get_db().query('SELECT * FROM gamelog_join_leave ORDER BY ts DESC LIMIT ?', (limit,))
    return get_db().query(
        'SELECT * FROM gamelog_join_leave WHERE ts < ? ORDER BY ts DESC LIMIT ?',
        (before_ts, limit),
    )


def record_media(*, url: str, title: str = '', location: str = '', source: str = '', ts: float | None = None) -> None:
    get_db().execute(
        'INSERT INTO media_log (ts, location, url, title, source) VALUES (?, ?, ?, ?, ?)',
        (ts if ts is not None else time.time(), location, url, title, source),
    )


def media_exists(url: str, *, since_ts: float) -> bool:
    if not url:
        return False
    row = get_db().query_one(
        'SELECT 1 FROM media_log WHERE url = ? AND ts >= ? LIMIT 1',
        (url, since_ts),
    )
    return row is not None


def recent_media(*, limit: int = 100, before_ts: float | None = None) -> list[dict[str, Any]]:
    if before_ts is None:
        return get_db().query('SELECT * FROM media_log ORDER BY ts DESC LIMIT ?', (limit,))
    return get_db().query(
        'SELECT * FROM media_log WHERE ts < ? ORDER BY ts DESC LIMIT ?',
        (before_ts, limit),
    )
