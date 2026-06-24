from __future__ import annotations
import json
import time
from typing import Any
from .database import get_db


def upsert_notification(
    *,
    vrc_id: str,
    type: str,
    sender_user_id: str = '',
    sender_username: str = '',
    message: str = '',
    details: dict[str, Any] | None = None,
    ts: float | None = None,
    seen: bool = False,
) -> None:
    if not vrc_id:
        return
    get_db().execute(
        'INSERT INTO notifications (vrc_id, ts, type, sender_user_id, sender_username, message, details_json, seen)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
        ' ON CONFLICT(vrc_id) DO UPDATE SET'
        '   type=excluded.type,'
        '   sender_user_id=excluded.sender_user_id,'
        '   sender_username=excluded.sender_username,'
        '   message=excluded.message,'
        '   details_json=excluded.details_json',
        (
            vrc_id,
            ts if ts is not None else time.time(),
            type,
            sender_user_id,
            sender_username,
            message,
            json.dumps(details, ensure_ascii=False) if details else None,
            1 if seen else 0,
        ),
    )


def list_notifications(*, limit: int = 100, unseen_only: bool = False) -> list[dict[str, Any]]:
    where = 'WHERE seen = 0' if unseen_only else ''
    rows = get_db().query(f'SELECT * FROM notifications {where} ORDER BY ts DESC LIMIT ?', (limit,))
    for row in rows:
        raw = row.get('details_json')
        if raw:
            try:
                row['details'] = json.loads(raw)
            except (ValueError, TypeError):
                row['details'] = {}
        else:
            row['details'] = {}
    return rows


def unseen_count() -> int:
    row = get_db().query_one('SELECT COUNT(*) AS c FROM notifications WHERE seen = 0')
    return int(row['c']) if row else 0


def mark_seen(vrc_id: str) -> None:
    get_db().execute('UPDATE notifications SET seen = 1 WHERE vrc_id = ?', (vrc_id,))


def mark_all_seen() -> None:
    get_db().execute('UPDATE notifications SET seen = 1 WHERE seen = 0')


def mark_responded(vrc_id: str) -> None:
    get_db().execute('UPDATE notifications SET responded = 1, seen = 1 WHERE vrc_id = ?', (vrc_id,))


def delete_notification(vrc_id: str) -> None:
    get_db().execute('DELETE FROM notifications WHERE vrc_id = ?', (vrc_id,))


def known_vrc_ids(*, limit: int = 500) -> set[str]:
    rows = get_db().query('SELECT vrc_id FROM notifications ORDER BY ts DESC LIMIT ?', (limit,))
    return {str(row['vrc_id']) for row in rows if row.get('vrc_id')}
