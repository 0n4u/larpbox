from __future__ import annotations
import time
from typing import Any
from .database import get_db

FRIEND = 'friend'
WORLD = 'world'
AVATAR = 'avatar'


def add_favorite(*, kind: str, target_id: str, name: str = '', group_name: str = 'default', image_url: str = '', added_ts: float | None = None) -> None:
    if not target_id:
        return
    get_db().execute(
        'INSERT INTO local_favorites (kind, target_id, name, group_name, image_url, added_ts)'
        ' VALUES (?, ?, ?, ?, ?, ?)'
        ' ON CONFLICT(kind, target_id) DO UPDATE SET'
        '   name=excluded.name, group_name=excluded.group_name, image_url=excluded.image_url',
        (kind, target_id, name, group_name or 'default', image_url, added_ts if added_ts is not None else time.time()),
    )


def remove_favorite(*, kind: str, target_id: str) -> None:
    get_db().execute('DELETE FROM local_favorites WHERE kind = ? AND target_id = ?', (kind, target_id))


def is_favorite(*, kind: str, target_id: str) -> bool:
    row = get_db().query_one('SELECT 1 FROM local_favorites WHERE kind = ? AND target_id = ? LIMIT 1', (kind, target_id))
    return row is not None


def toggle_favorite(*, kind: str, target_id: str, name: str = '', group_name: str = 'default', image_url: str = '') -> bool:
    if is_favorite(kind=kind, target_id=target_id):
        remove_favorite(kind=kind, target_id=target_id)
        return False
    add_favorite(kind=kind, target_id=target_id, name=name, group_name=group_name, image_url=image_url)
    return True


def list_favorites(*, kind: str | None = None, group_name: str | None = None) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if kind:
        clauses.append('kind = ?')
        params.append(kind)
    if group_name:
        clauses.append('group_name = ?')
        params.append(group_name)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
    return get_db().query(f'SELECT * FROM local_favorites {where} ORDER BY added_ts DESC', params)


def favorite_groups(*, kind: str) -> list[str]:
    rows = get_db().query('SELECT DISTINCT group_name FROM local_favorites WHERE kind = ? ORDER BY group_name', (kind,))
    return [str(row['group_name']) for row in rows if row.get('group_name')]


def count(*, kind: str | None = None) -> int:
    if kind:
        row = get_db().query_one('SELECT COUNT(*) AS c FROM local_favorites WHERE kind = ?', (kind,))
    else:
        row = get_db().query_one('SELECT COUNT(*) AS c FROM local_favorites')
    return int(row['c']) if row else 0
