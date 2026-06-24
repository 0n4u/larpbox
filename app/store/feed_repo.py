from __future__ import annotations
import json
import time
from typing import Any, Sequence
from .database import get_db

                                                                                
ONLINE = 'online'
OFFLINE = 'offline'
STATUS = 'status'
LOCATION = 'location'
AVATAR = 'avatar'
BIO = 'bio'
DISPLAY_NAME = 'displayName'
TRUST = 'trustLevel'
ADDED = 'friendAdded'
REMOVED = 'friendRemoved'

FRIEND_LOG_TYPES = (ONLINE, OFFLINE, STATUS, LOCATION, AVATAR, BIO, DISPLAY_NAME, TRUST, ADDED, REMOVED)


def add_friend_log(
    *,
    type: str,
    user_id: str,
    display_name: str,
    prev: str = '',
    current: str = '',
    ts: float | None = None,
) -> None:
    get_db().execute(
        'INSERT INTO friend_log (ts, type, user_id, display_name, prev, current) VALUES (?, ?, ?, ?, ?, ?)',
        (ts if ts is not None else time.time(), type, user_id, display_name, prev, current),
    )


def recent_friend_log(
    *,
    limit: int = 200,
    before_ts: float | None = None,
    types: Sequence[str] | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if before_ts is not None:
        clauses.append('ts < ?')
        params.append(before_ts)
    if types:
        placeholders = ','.join('?' for _ in types)
        clauses.append(f'type IN ({placeholders})')
        params.extend(types)
    if user_id:
        clauses.append('user_id = ?')
        params.append(user_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
    params.append(limit)
    return get_db().query(f'SELECT * FROM friend_log {where} ORDER BY ts DESC LIMIT ?', params)


def add_feed(
    *,
    type: str,
    user_id: str = '',
    display_name: str = '',
    payload: dict[str, Any] | None = None,
    ts: float | None = None,
) -> None:
    get_db().execute(
        'INSERT INTO feed (ts, type, user_id, display_name, payload_json) VALUES (?, ?, ?, ?, ?)',
        (
            ts if ts is not None else time.time(),
            type,
            user_id,
            display_name,
            json.dumps(payload, ensure_ascii=False) if payload else None,
        ),
    )


def recent_unified(*, limit: int = 200, before_ts: float | None = None) -> list[dict[str, Any]]:
    """Merged timeline of friend events, own location changes, and media."""
    bound = before_ts if before_ts is not None else 9_999_999_999.0
    return get_db().query(
        'SELECT ts, kind, primary_text, secondary_text FROM ('
        "  SELECT ts, 'friend:' || type AS kind, display_name AS primary_text, current AS secondary_text"
        '    FROM friend_log WHERE ts < ?'
        '  UNION ALL'
        "  SELECT ts, 'location' AS kind, COALESCE(NULLIF(world_name, ''), location) AS primary_text,"
        '    instance_type AS secondary_text FROM gamelog_location WHERE ts < ?'
        '  UNION ALL'
        "  SELECT ts, 'media' AS kind, COALESCE(NULLIF(title, ''), url) AS primary_text,"
        '    source AS secondary_text FROM media_log WHERE ts < ?'
        ') ORDER BY ts DESC LIMIT ?',
        (bound, bound, bound, limit),
    )


def recent_feed(
    *,
    limit: int = 200,
    before_ts: float | None = None,
    types: Sequence[str] | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if before_ts is not None:
        clauses.append('ts < ?')
        params.append(before_ts)
    if types:
        placeholders = ','.join('?' for _ in types)
        clauses.append(f'type IN ({placeholders})')
        params.extend(types)
    if user_id:
        clauses.append('user_id = ?')
        params.append(user_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
    params.append(limit)
    rows = get_db().query(f'SELECT * FROM feed {where} ORDER BY ts DESC LIMIT ?', params)
    for row in rows:
        raw = row.get('payload_json')
        if raw:
            try:
                row['payload'] = json.loads(raw)
            except (ValueError, TypeError):
                row['payload'] = {}
        else:
            row['payload'] = {}
    return rows
