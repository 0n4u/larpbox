from __future__ import annotations
import time
from typing import Any
from .database import get_db


def get_meta(key: str, default: str = '') -> str:
    row = get_db().query_one('SELECT value FROM kv_meta WHERE key = ?', (key,))
    return str(row['value']) if row and row['value'] is not None else default


def set_meta(key: str, value: str) -> None:
    get_db().execute(
        'INSERT INTO kv_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (key, value),
    )


def get_meta_flag(key: str) -> bool:
    return get_meta(key) == '1'


def set_meta_flag(key: str, value: bool) -> None:
    set_meta(key, '1' if value else '0')


def list_accounts() -> list[dict[str, Any]]:
    return get_db().query('SELECT * FROM accounts ORDER BY last_used_ts DESC')


def upsert_account(*, user_id: str, username: str, display_name: str) -> None:
    if not user_id:
        return
    now = time.time()
    get_db().execute(
        'INSERT INTO accounts (user_id, username, display_name, added_ts, last_used_ts)'
        ' VALUES (?, ?, ?, ?, ?)'
        ' ON CONFLICT(user_id) DO UPDATE SET'
        '   username=excluded.username,'
        '   display_name=excluded.display_name,'
        '   last_used_ts=excluded.last_used_ts',
        (user_id, username, display_name, now, now),
    )


def touch_account(user_id: str) -> None:
    get_db().execute('UPDATE accounts SET last_used_ts = ? WHERE user_id = ?', (time.time(), user_id))


def remove_account(user_id: str) -> None:
    get_db().execute('DELETE FROM accounts WHERE user_id = ?', (user_id,))


def get_user_note(user_id: str) -> dict[str, Any] | None:
    return get_db().query_one('SELECT * FROM user_notes WHERE user_id = ?', (user_id,))


def set_user_note(*, user_id: str, note: str = '', color: str = '', tags: str = '') -> None:
    if not user_id:
        return
    get_db().execute(
        'INSERT INTO user_notes (user_id, note, color, tags) VALUES (?, ?, ?, ?)'
        ' ON CONFLICT(user_id) DO UPDATE SET note=excluded.note, color=excluded.color, tags=excluded.tags',
        (user_id, note, color, tags),
    )
