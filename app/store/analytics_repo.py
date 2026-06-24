from __future__ import annotations
import time
from typing import Any
from .database import get_db


def open_visit(*, world_id: str, location: str, joined_ts: float | None = None) -> None:
    """Open a new world visit, closing any still-open visit first."""
    now = joined_ts if joined_ts is not None else time.time()
    close_open_visits(left_ts=now)
    get_db().execute(
        'INSERT INTO world_visits (world_id, location, joined_ts, left_ts, seconds) VALUES (?, ?, ?, NULL, NULL)',
        (world_id, location, now),
    )


def close_open_visits(*, left_ts: float | None = None) -> None:
    now = left_ts if left_ts is not None else time.time()
    get_db().execute(
        'UPDATE world_visits SET left_ts = ?, seconds = MAX(0, ? - joined_ts) WHERE left_ts IS NULL',
        (now, now),
    )


def has_open_visit(location: str) -> bool:
    row = get_db().query_one(
        'SELECT 1 FROM world_visits WHERE location = ? AND left_ts IS NULL LIMIT 1',
        (location,),
    )
    return row is not None


_NAME_SUBQUERY = (
    '(SELECT gl.world_name FROM gamelog_location gl'
    ' WHERE gl.world_id = wv.world_id AND gl.world_name IS NOT NULL AND gl.world_name != ""'
    ' ORDER BY gl.ts DESC LIMIT 1) AS world_name'
)


def playtime_by_world(*, since_ts: float | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if since_ts is None:
        return get_db().query(
            f'SELECT wv.world_id, SUM(wv.seconds) AS seconds, COUNT(*) AS visits, {_NAME_SUBQUERY}'
            ' FROM world_visits wv'
            ' WHERE wv.seconds IS NOT NULL AND wv.world_id IS NOT NULL AND wv.world_id != ""'
            ' GROUP BY wv.world_id ORDER BY seconds DESC LIMIT ?',
            (limit,),
        )
    return get_db().query(
        f'SELECT wv.world_id, SUM(wv.seconds) AS seconds, COUNT(*) AS visits, {_NAME_SUBQUERY}'
        ' FROM world_visits wv'
        ' WHERE wv.seconds IS NOT NULL AND wv.joined_ts >= ? AND wv.world_id IS NOT NULL AND wv.world_id != ""'
        ' GROUP BY wv.world_id ORDER BY seconds DESC LIMIT ?',
        (since_ts, limit),
    )


def total_playtime_seconds(*, since_ts: float | None = None) -> float:
    if since_ts is None:
        row = get_db().query_one('SELECT SUM(seconds) AS s FROM world_visits WHERE seconds IS NOT NULL')
    else:
        row = get_db().query_one(
            'SELECT SUM(seconds) AS s FROM world_visits WHERE seconds IS NOT NULL AND joined_ts >= ?',
            (since_ts,),
        )
    return float(row['s']) if row and row['s'] is not None else 0.0


def activity_heatmap(*, since_ts: float | None = None) -> list[dict[str, Any]]:
    """Return (weekday 0-6, hour 0-23, count) buckets from world-join events."""
    where = ''
    params: list[Any] = []
    if since_ts is not None:
        where = 'WHERE ts >= ?'
        params.append(since_ts)
    return get_db().query(
        "SELECT CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER) AS weekday,"
        " CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS hour,"
        " COUNT(*) AS count FROM gamelog_location "
        f'{where} GROUP BY weekday, hour',
        params,
    )


def session_count(*, since_ts: float | None = None) -> int:
    if since_ts is None:
        row = get_db().query_one('SELECT COUNT(*) AS c FROM gamelog_location')
    else:
        row = get_db().query_one('SELECT COUNT(*) AS c FROM gamelog_location WHERE ts >= ?', (since_ts,))
    return int(row['c']) if row else 0
