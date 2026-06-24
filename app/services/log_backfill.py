from __future__ import annotations
import os
import time
from pathlib import Path
from typing import Any
from ..logging_setup import get_logger
from ..store import meta_repo
from ..store.database import get_db
from ..vrchat_log_players import find_vrchat_log_files
from . import event_bus as ev
from .log_watcher import parse_log_lines, prime_offsets_to_eof

logger = get_logger('store')

_BACKFILL_FLAG = 'backfill_done'
_MAX_FILE_BYTES = 64 * 1024 * 1024


def is_backfill_done() -> bool:
    return meta_repo.get_meta_flag(_BACKFILL_FLAG)


def _read_capped(path: Path) -> str | None:
    try:
        with path.open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - _MAX_FILE_BYTES)
            handle.seek(start)
            return handle.read().decode('utf-8', errors='replace')
    except OSError:
        logger.debug('Backfill could not read %s', path, exc_info=True)
        return None


def run_backfill(*, force: bool = False) -> dict[str, Any]:
    db = get_db()
    if not db.is_available():
        return {'skipped': 'db_unavailable'}
    if not force and is_backfill_done():
        return {'skipped': 'already_done'}

    loc_rows: list[tuple] = []
    jl_rows: list[tuple] = []
    media_rows: list[tuple] = []
    visit_rows: list[tuple] = []
    open_visit: tuple[str, str, float] | None = None
    current_location = ''
    seen_media: set[str] = set()

    for path in reversed(find_vrchat_log_files()):                                         
        text = _read_capped(path)
        if not text:
            continue
        for event in parse_log_lines(text.splitlines()):
            kind = event['kind']
            ts = float(event['ts'])
            if kind == ev.WORLD_JOIN:
                location = str(event.get('location') or '')
                world_id = str(event.get('world_id') or '')
                loc_rows.append((
                    ts, world_id, str(event.get('instance_id') or ''), location,
                    str(event.get('world_name') or ''), str(event.get('region') or ''),
                    str(event.get('instance_type') or ''), str(event.get('group_id') or ''),
                ))
                if open_visit is not None:
                    ov_world, ov_loc, ov_ts = open_visit
                    visit_rows.append((ov_world, ov_loc, ov_ts, ts, max(0.0, ts - ov_ts)))
                open_visit = (world_id, location, ts)
                current_location = location
            elif kind == ev.PLAYER_JOIN:
                jl_rows.append((ts, 'join', str(event.get('user_id') or ''), str(event.get('display_name') or ''), current_location))
            elif kind == ev.PLAYER_LEAVE:
                jl_rows.append((ts, 'leave', str(event.get('user_id') or ''), str(event.get('display_name') or ''), current_location))
            elif kind == ev.VIDEO_URL:
                url = str(event.get('url') or '')
                if url and url not in seen_media:
                    seen_media.add(url)
                    media_rows.append((ts, current_location, url, str(event.get('title') or ''), str(event.get('source') or '')))

    if open_visit is not None:
        ov_world, ov_loc, ov_ts = open_visit
        visit_rows.append((ov_world, ov_loc, ov_ts, None, None))

    if loc_rows:
        db.executemany('INSERT INTO gamelog_location (ts, world_id, instance_id, location, world_name, region, instance_type, group_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', loc_rows, wait=True)
    if jl_rows:
        db.executemany('INSERT INTO gamelog_join_leave (ts, kind, user_id, display_name, location) VALUES (?, ?, ?, ?, ?)', jl_rows, wait=True)
    if media_rows:
        db.executemany('INSERT INTO media_log (ts, location, url, title, source) VALUES (?, ?, ?, ?, ?)', media_rows, wait=True)
    if visit_rows:
        db.executemany('INSERT INTO world_visits (world_id, location, joined_ts, left_ts, seconds) VALUES (?, ?, ?, ?, ?)', visit_rows, wait=True)

    meta_repo.set_meta_flag(_BACKFILL_FLAG, True)
    meta_repo.set_meta('backfill_ts', str(time.time()))
    prime_offsets_to_eof()
    counts = {'locations': len(loc_rows), 'join_leave': len(jl_rows), 'media': len(media_rows), 'visits': len(visit_rows)}
    logger.info('Log backfill complete: %s', counts)
    return counts
