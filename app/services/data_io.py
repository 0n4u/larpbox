from __future__ import annotations
import csv
import io
import json
import time
from pathlib import Path
from typing import Any
from ..atomic_io import atomic_write_json, atomic_write_text
from ..logging_setup import get_logger
from ..store import favorites_repo
from ..store.database import get_db
from ..version import __version__

logger = get_logger('data_io')


def _rows_to_csv(header: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def export_favorites(path: str | Path) -> int:
    favorites = favorites_repo.list_favorites()
    atomic_write_json(Path(path), {'app': 'larpbox', 'kind': 'favorites', 'exported_ts': time.time(), 'favorites': favorites})
    return len(favorites)


def import_favorites(path: str | Path) -> int:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    favorites = data.get('favorites') if isinstance(data, dict) else data
    if not isinstance(favorites, list):
        return 0
    imported = 0
    for fav in favorites:
        if not isinstance(fav, dict):
            continue
        target_id = str(fav.get('target_id') or '')
        kind = str(fav.get('kind') or '')
        if not target_id or not kind:
            continue
        favorites_repo.add_favorite(
            kind=kind, target_id=target_id, name=str(fav.get('name') or ''),
            group_name=str(fav.get('group_name') or 'default'), image_url=str(fav.get('image_url') or ''),
        )
        imported += 1
    return imported


def export_gamelog_csv(path: str | Path) -> int:
    db = get_db()
    locations = db.query("SELECT ts, 'location' AS kind, world_id, location, world_name, instance_type, region, '' AS user_id, '' AS display_name FROM gamelog_location")
    players = db.query("SELECT ts, kind, '' AS world_id, location, '' AS world_name, '' AS instance_type, '' AS region, user_id, display_name FROM gamelog_join_leave")
    rows = sorted(locations + players, key=lambda r: float(r.get('ts') or 0), reverse=True)
    for row in rows:
        row['iso_time'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(row.get('ts') or 0)))
    header = ['ts', 'iso_time', 'kind', 'world_id', 'location', 'world_name', 'instance_type', 'region', 'user_id', 'display_name']
    atomic_write_text(Path(path), _rows_to_csv(header, rows))
    return len(rows)


def export_feed_csv(path: str | Path) -> int:
    rows = get_db().query('SELECT ts, type, user_id, display_name, prev, current FROM friend_log ORDER BY ts DESC')
    for row in rows:
        row['iso_time'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(row.get('ts') or 0)))
    header = ['ts', 'iso_time', 'type', 'user_id', 'display_name', 'prev', 'current']
    atomic_write_text(Path(path), _rows_to_csv(header, rows))
    return len(rows)


def export_history_json(path: str | Path) -> dict[str, int]:
    db = get_db()
    payload = {
        'app': 'larpbox',
        'version': __version__,
        'exported_ts': time.time(),
        'locations': db.query('SELECT * FROM gamelog_location ORDER BY ts DESC'),
        'join_leave': db.query('SELECT * FROM gamelog_join_leave ORDER BY ts DESC'),
        'friend_log': db.query('SELECT * FROM friend_log ORDER BY ts DESC'),
        'media': db.query('SELECT * FROM media_log ORDER BY ts DESC'),
    }
    atomic_write_json(Path(path), payload)
    return {key: len(value) for key, value in payload.items() if isinstance(value, list)}


def export_config_backup(path: str | Path) -> None:
    from ..config import load_config
    from ..preset_storage import load_presets
    payload = {
        'app': 'larpbox',
        'version': __version__,
        'exported_ts': time.time(),
        'config': load_config(),
        'presets': load_presets(),
        'favorites': favorites_repo.list_favorites(),
    }
    atomic_write_json(Path(path), payload)


def import_config_backup(path: str | Path) -> dict[str, int]:
    from ..config import DEFAULT_CONFIG, save_config
    from ..preset_storage import load_presets, save_presets
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Backup file is not valid.')
    counts = {'config': 0, 'presets': 0, 'favorites': 0}
    config = data.get('config')
    if isinstance(config, dict):
        clean = {key: value for key, value in config.items() if key in DEFAULT_CONFIG}
        save_config(clean)
        counts['config'] = len(clean)
    presets = data.get('presets')
    if isinstance(presets, dict):
        merged = load_presets()
        merged.update(presets)
        save_presets(merged)
        counts['presets'] = len(presets)
    favorites = data.get('favorites')
    if isinstance(favorites, list):
        for fav in favorites:
            if isinstance(fav, dict) and fav.get('target_id') and fav.get('kind'):
                favorites_repo.add_favorite(
                    kind=str(fav['kind']), target_id=str(fav['target_id']), name=str(fav.get('name') or ''),
                    group_name=str(fav.get('group_name') or 'default'), image_url=str(fav.get('image_url') or ''),
                )
                counts['favorites'] += 1
    return counts
