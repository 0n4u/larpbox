from __future__ import annotations
import re
import threading
import time
from pathlib import Path
from typing import Any
from PyQt6.QtCore import QThread
from ..logging_setup import get_logger
from ..vrchat_log_players import find_vrchat_log_files
from . import event_bus as ev
from .event_bus import VrcEventBus

logger = get_logger('log_watcher')

_TS_RE = re.compile(r'^(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2}):(\d{2})')
_JOIN_RE = re.compile(r'\[Behaviour\] OnPlayerJoined (.+) \((usr_[a-f0-9-]+)\)\s*$', re.IGNORECASE)
_LEAVE_RE = re.compile(r'\[Behaviour\] OnPlayerLeft (.+) \((usr_[a-f0-9-]+)\)\s*$', re.IGNORECASE)
_LOCATION_RE = re.compile(r'\[Behaviour\] Joining (wrld_[a-f0-9-]+:[^\s]+)', re.IGNORECASE)
_ROOM_NAME_RE = re.compile(r'\[Behaviour\] (?:Joining or Creating Room|Entering Room): (.+?)\s*$', re.IGNORECASE)
_SWITCH_RE = re.compile(r'\[Behaviour\] Switching (.+?) to avatar (.+?)\s*$', re.IGNORECASE)
_VIDEO_RE = re.compile(r"\[Video Playback\][^\n]*?(https?://[^\s'\"]+)", re.IGNORECASE)
_URL_VIDEO_RE = re.compile(r"User (.+?) added URL[^\n]*?(https?://[^\s'\"]+)", re.IGNORECASE)

_ROOM_NAME_LOOKAHEAD = 12
_ROOM_NAME_LOOKBACK = 4


def parse_ts(line: str, default: float) -> float:
    match = _TS_RE.match(line)
    if not match:
        return default
    try:
        year, month, day, hour, minute, second = (int(part) for part in match.groups())
        return time.mktime((year, month, day, hour, minute, second, 0, 0, -1))
    except (ValueError, OverflowError):
        return default


def parse_location(location: str) -> dict[str, str]:
    result = {'world_id': '', 'instance_id': '', 'region': '', 'instance_type': 'public', 'group_id': ''}
    if not location:
        return result
    if ':' not in location:
        result['world_id'] = location
        return result
    world_id, _, rest = location.partition(':')
    result['world_id'] = world_id
    parts = rest.split('~')
    result['instance_id'] = parts[0]
    flags: dict[str, str] = {}
    for tag in parts[1:]:
        name, _, value = tag.partition('(')
        flags[name] = value.rstrip(')')
    result['region'] = flags.get('region', '')
    result['group_id'] = flags.get('group', '')
    if 'group' in flags:
        access = flags.get('groupAccessType', '')
        result['instance_type'] = f'group+{access}' if access else 'group'
    elif 'private' in flags:
        result['instance_type'] = 'invite+' if 'canRequestInvite' in flags else 'invite'
    elif 'hidden' in flags:
        result['instance_type'] = 'friends+'
    elif 'friends' in flags:
        result['instance_type'] = 'friends'
    else:
        result['instance_type'] = 'public'
    return result


def _find_room_name(lines: list[str], idx: int) -> str:
    end = min(len(lines), idx + _ROOM_NAME_LOOKAHEAD)
    for follow in range(idx + 1, end):
        match = _ROOM_NAME_RE.search(lines[follow])
        if match:
            return match.group(1).strip()
        if _LOCATION_RE.search(lines[follow]):
            break
    start = max(0, idx - _ROOM_NAME_LOOKBACK)
    for back in range(idx - 1, start - 1, -1):
        match = _ROOM_NAME_RE.search(lines[back])
        if match:
            return match.group(1).strip()
    return ''


def parse_log_lines(lines: list[str], *, default_ts: float | None = None) -> list[dict[str, Any]]:
    now = default_ts if default_ts is not None else time.time()
    events: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        ts = parse_ts(line, now)
        location_match = _LOCATION_RE.search(line)
        if location_match:
            location = location_match.group(1).strip()
            info = parse_location(location)
            events.append({
                'kind': ev.WORLD_JOIN, 'ts': ts, 'location': location,
                'world_name': _find_room_name(lines, idx), **info,
            })
            continue
        join_match = _JOIN_RE.search(line)
        if join_match:
            events.append({
                'kind': ev.PLAYER_JOIN, 'ts': ts,
                'display_name': join_match.group(1).strip(), 'user_id': join_match.group(2),
            })
            continue
        leave_match = _LEAVE_RE.search(line)
        if leave_match:
            events.append({
                'kind': ev.PLAYER_LEAVE, 'ts': ts,
                'display_name': leave_match.group(1).strip(), 'user_id': leave_match.group(2),
            })
            continue
        switch_match = _SWITCH_RE.search(line)
        if switch_match:
            events.append({
                'kind': ev.AVATAR_CHANGE, 'ts': ts,
                'display_name': switch_match.group(1).strip(), 'avatar_name': switch_match.group(2).strip(),
            })
            continue
        video_match = _VIDEO_RE.search(line)
        if video_match:
            events.append({'kind': ev.VIDEO_URL, 'ts': ts, 'url': video_match.group(1).strip(), 'source': 'video_playback'})
            continue
        url_video_match = _URL_VIDEO_RE.search(line)
        if url_video_match:
            events.append({
                'kind': ev.VIDEO_URL, 'ts': ts, 'url': url_video_match.group(2).strip(),
                'title': url_video_match.group(1).strip(), 'source': 'user_added',
            })
    return events


class LogWatcher(QThread):
    """Continuously tails VRChat log files and emits events onto the VrcEventBus.

    Offsets are persisted per file in the store (kv_meta) so restarts do not
    replay history; rotation/truncation is detected and resets the offset.
    """

    def __init__(self, bus: VrcEventBus | None = None, *, poll_interval: float = 2.5, parent=None):
        super().__init__(parent)
        self._bus = bus or VrcEventBus.instance()
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._start_ts = time.time()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        logger.info('Log watcher started')
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                logger.debug('Log watcher tick failed', exc_info=True)
            self._stop.wait(self._poll_interval)
        logger.info('Log watcher stopped')

    def _tick(self) -> None:
        for path in find_vrchat_log_files():
            self._read_new(path)

    def _offset_key(self, path: Path) -> str:
        return f'logwatch:{path.resolve()}'

    def _read_new(self, path: Path) -> None:
        from ..store import meta_repo
        key = self._offset_key(path)
        try:
            size = path.stat().st_size
            mtime = path.stat().st_mtime
        except OSError:
            return
        stored = meta_repo.get_meta(key)
        if stored == '':
            offset = 0 if mtime >= self._start_ts - 5 else size
        else:
            try:
                offset = int(stored)
            except ValueError:
                offset = 0
            if size < offset:
                offset = 0
        if offset >= size:
            if stored == '':
                meta_repo.set_meta(key, str(size))
            return
        try:
            with path.open('rb') as handle:
                handle.seek(offset)
                chunk = handle.read()
        except OSError:
            return
        last_newline = chunk.rfind(b'\n')
        if last_newline == -1:
            return
        consumed = chunk[:last_newline + 1]
        text = consumed.decode('utf-8', errors='replace')
        for event in parse_log_lines(text.splitlines()):
            kind = event.pop('kind')
            self._bus.emit_event(kind, event)
        meta_repo.set_meta(key, str(offset + len(consumed)))


def prime_offsets_to_eof() -> None:
    """Mark every current log file as fully consumed (used after backfill)."""
    from ..store import meta_repo
    for path in find_vrchat_log_files():
        try:
            size = path.stat().st_size
        except OSError:
            continue
        meta_repo.set_meta(f'logwatch:{path.resolve()}', str(size))
