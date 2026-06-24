from __future__ import annotations
import os
import re
from dataclasses import dataclass
from pathlib import Path
from .logging_setup import get_logger
logger = get_logger('vrchat_log')
_JOIN_RE = re.compile('\\[Behaviour\\] OnPlayerJoined (.+) \\((usr_[a-f0-9-]+)\\)\\s*$', re.IGNORECASE)
_LEAVE_RE = re.compile('\\[Behaviour\\] OnPlayerLeft (.+) \\((usr_[a-f0-9-]+)\\)\\s*$', re.IGNORECASE)
_ROOM_START_RE = re.compile('\\[Behaviour\\] (?:Entering Room:|Joining wrld_)', re.IGNORECASE)
_JOINING_LOCATION_RE = re.compile('\\[Behaviour\\] Joining (wrld_[a-f0-9-]+:[^\\s]+)', re.IGNORECASE)
_SWITCH_RE = re.compile('\\[Behaviour\\] Switching (.+?) to avatar (.+?)\\s*$', re.IGNORECASE)
_LOAD_AVATAR_RE = re.compile('Loading Avatar Data:(avtr_[a-f0-9-]+)', re.IGNORECASE)
_AVTR_MENTION_RE = re.compile('(?:Avatar|avatar) [\'\\"]?(avtr_[a-f0-9-]+)[\'\\"]?', re.IGNORECASE)
_API_AVTR_RE = re.compile('avatars/(avtr_[a-f0-9-]+)', re.IGNORECASE)
_ANALYSIS_AVTR_RE = re.compile("avatar ['\\\"](avtr_[a-f0-9-]+)['\\\"]", re.IGNORECASE)
_UNPACK_RE = re.compile('\\[AssetBundleDownloadManager\\].*Unpacking Avatar \\((.+?) by (.+?)\\)', re.IGNORECASE)
_EMBEDDED_BY_RE = re.compile('[\\s（(]+(?:by)\\s+(.+?)[)）]?\\s*$', re.IGNORECASE)
_FOLLOW_WINDOW = 200
_AVTR_WINDOW = 250
_TAIL_READ_BYTES = 512 * 1024
_MAX_TAIL_READ_BYTES = 16 * 1024 * 1024
_log_lines_cache: tuple[str, float, list[str]] | None = None
_user_avatar_map_cache: tuple[tuple, dict[str, str]] | None = None

@dataclass(frozen=True)
class LogPlayer:
    user_id: str
    display_name: str

@dataclass(frozen=True)
class PlayerAvatarInfo:
    avatar_id: str | None = None
    avatar_name: str | None = None
    author_name: str | None = None

def vrchat_log_dir() -> Path:
    user_profile = os.environ.get('USERPROFILE') or str(Path.home())
    return Path(user_profile) / 'AppData' / 'LocalLow' / 'VRChat' / 'VRChat'

def find_vrchat_log_file() -> Path | None:
    files = find_vrchat_log_files()
    return files[0] if files else None

def find_vrchat_log_files() -> list[Path]:
    log_dir = vrchat_log_dir()
    if not log_dir.is_dir():
        return []
    candidates = list(log_dir.glob('output_log*.txt'))
    legacy = log_dir / 'output_log.txt'
    if legacy.is_file():
        candidates.append(legacy)
    unique = {path.resolve(): path for path in candidates if path.is_file()}
    return sorted(unique.values(), key=lambda path: path.stat().st_mtime, reverse=True)

def invalidate_log_cache() -> None:
    global _log_lines_cache, _user_avatar_map_cache
    _log_lines_cache = None
    _user_avatar_map_cache = None

def current_log_signature() -> tuple[str, float, int] | None:
    """Cheap metadata check — no file read."""
    path = find_vrchat_log_file()
    if path is None or not path.is_file():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), stat.st_mtime, stat.st_size)

def _log_files_signature(paths: list[Path]) -> tuple:
    signature: list[tuple[str, float, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((str(path), stat.st_mtime, stat.st_size))
    return tuple(signature)

def _read_log_text(path: Path, *, tail_only: bool=False) -> str | None:
    try:
        if not tail_only:
            return path.read_text(encoding='utf-8', errors='replace')
        with path.open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            read_bytes = _TAIL_READ_BYTES
            while True:
                start = max(0, size - read_bytes)
                handle.seek(start)
                text = handle.read().decode('utf-8', errors='replace')
                if start == 0 or read_bytes >= _MAX_TAIL_READ_BYTES or _ROOM_START_RE.search(text):
                    return text
                read_bytes *= 2
    except OSError:
        logger.debug('Could not read VRChat log at %s', path, exc_info=True)
        return None

def _read_log_lines_cached(log_path: Path | None=None) -> list[str] | None:
    global _log_lines_cache
    path = log_path or find_vrchat_log_file()
    if path is None or not path.is_file():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cache_key = str(path.resolve())
    tail_only = log_path is None
    cache_suffix = ':tail' if tail_only else ''
    full_cache_key = f'{cache_key}{cache_suffix}'
    if _log_lines_cache is not None and _log_lines_cache[0] == full_cache_key and (_log_lines_cache[1] == mtime):
        return _log_lines_cache[2]
    text = _read_log_text(path, tail_only=tail_only)
    if text is None:
        return None
    lines = text.splitlines()
    _log_lines_cache = (full_cache_key, mtime, lines)
    return lines

def _last_room_start_index(lines: list[str]) -> int:
    start_idx = 0
    for index, line in enumerate(lines):
        if _ROOM_START_RE.search(line):
            start_idx = index
    return start_idx

def _parse_players_from_lines(lines: list[str]) -> tuple[dict[str, str], str | None]:
    start_idx = _last_room_start_index(lines)
    players: dict[str, str] = {}
    last_location: str | None = None
    for line in lines[start_idx:]:
        location_match = _JOINING_LOCATION_RE.search(line)
        if location_match:
            last_location = location_match.group(1).strip()
        join_match = _JOIN_RE.search(line)
        if join_match:
            players[join_match.group(2)] = join_match.group(1).strip()
            continue
        leave_match = _LEAVE_RE.search(line)
        if leave_match:
            players.pop(leave_match.group(2), None)
    return (players, last_location)

def parse_players_from_log(log_path: Path | None=None) -> tuple[list[LogPlayer], str | None]:
    lines = _read_log_lines_cached(log_path)
    if not lines:
        return ([], None)
    players, location = _parse_players_from_lines(lines)
    ordered = [LogPlayer(user_id=user_id, display_name=name) for user_id, name in players.items()]
    ordered.sort(key=lambda item: item.display_name.casefold())
    return (ordered, location)

def log_players_for_current_room() -> tuple[list[LogPlayer], str | None]:
    return parse_players_from_log()

def _avatar_id_from_line(line: str) -> str:
    load_match = _LOAD_AVATAR_RE.search(line)
    if load_match:
        return load_match.group(1)
    mention_match = _AVTR_MENTION_RE.search(line)
    if mention_match:
        return mention_match.group(1)
    api_match = _API_AVTR_RE.search(line)
    if api_match:
        return api_match.group(1)
    analysis_match = _ANALYSIS_AVTR_RE.search(line)
    if analysis_match:
        return analysis_match.group(1)
    return ''

def _avatar_id_after_unpack(lines: list[str], switch_index: int, avatar_name: str) -> str:
    """Find avtr tied to a switch — includes analysis lines that appear before unpack."""
    end = min(len(lines), switch_index + _FOLLOW_WINDOW)
    unpack_index: int | None = None
    for index in range(switch_index, end):
        unpack = _UNPACK_RE.search(lines[index])
        if unpack and _avatar_names_equivalent(unpack.group(1), avatar_name):
            unpack_index = index
            break
    if unpack_index is None:
        return ''
    scan_end = min(len(lines), unpack_index + 400)
    for index in range(switch_index, scan_end):
        if index > unpack_index:
            follow = lines[index]
            if _SWITCH_RE.search(follow) or _LEAVE_RE.search(follow):
                break
        avatar_id = _avatar_id_from_line(lines[index])
        if avatar_id:
            return avatar_id
    return ''

def _avatar_id_after_line(lines: list[str], start_index: int, *, window: int=_AVTR_WINDOW) -> str:
    end = min(len(lines), start_index + window)
    for follow_index in range(start_index, end):
        follow = lines[follow_index]
        if follow_index > start_index:
            if _JOIN_RE.search(follow) or _SWITCH_RE.search(follow) or _LEAVE_RE.search(follow):
                break
        avatar_id = _avatar_id_from_line(follow)
        if avatar_id:
            return avatar_id
    return ''

def _avatar_id_near_index(lines: list[str], start_index: int, *, window: int=_AVTR_WINDOW, stop_at_switch: bool=True) -> str:
    end = min(len(lines), start_index + window)
    for follow_index in range(start_index, end):
        follow = lines[follow_index]
        if follow_index > start_index and stop_at_switch:
            if _SWITCH_RE.search(follow) or _LEAVE_RE.search(follow):
                break
        avatar_id = _avatar_id_from_line(follow)
        if avatar_id:
            return avatar_id
    return ''

def _switch_index_before(lines: list[str], before_index: int, display_name: str, *, lookback: int=800) -> int | None:
    target = display_name.strip()
    if not target:
        return None
    start = max(0, before_index - lookback)
    for index in range(before_index - 1, start - 1, -1):
        switch_match = _SWITCH_RE.search(lines[index])
        if switch_match and switch_match.group(1).strip() == target:
            return index
    return None

def _clean_avatar_name(name: str) -> str:
    cleaned = name.strip()
    match = _EMBEDDED_BY_RE.search(cleaned)
    if match:
        return cleaned[:match.start()].strip()
    return cleaned

def _avatar_names_equivalent(a: str, b: str) -> bool:
    return _clean_avatar_name(a).casefold() == _clean_avatar_name(b).casefold()

def _author_for_avatar_name(lines: list[str], avatar_name: str, start_index: int) -> str:
    target = _clean_avatar_name(avatar_name).casefold()
    if not target:
        return ''
    for follow in lines[start_index:start_index + _FOLLOW_WINDOW]:
        unpack = _UNPACK_RE.search(follow)
        if not unpack:
            continue
        if _avatar_names_equivalent(unpack.group(1), avatar_name):
            return unpack.group(2).strip()
    return ''

def _switch_avatar_names(room_lines: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in room_lines:
        switch_match = _SWITCH_RE.search(line)
        if switch_match:
            mapping[switch_match.group(1).strip()] = switch_match.group(2).strip()
    return mapping

def _avatar_id_for_join(lines: list[str], join_index: int, display_name: str) -> str:
    avatar_id = _avatar_id_after_line(lines, join_index + 1)
    if avatar_id:
        return avatar_id
    switch_idx = _switch_index_before(lines, join_index, display_name)
    if switch_idx is None:
        return ''
    return _avatar_id_near_index(lines, switch_idx + 1, stop_at_switch=False)

def _build_user_avatar_map_from_lines(lines: list[str]) -> dict[str, str]:
    name_to_user: dict[str, str] = {}
    user_to_avatar: dict[str, str] = {}
    for index, line in enumerate(lines):
        join_match = _JOIN_RE.search(line)
        if join_match:
            display_name = join_match.group(1).strip()
            user_id = join_match.group(2)
            name_to_user[display_name] = user_id
            avatar_id = _avatar_id_for_join(lines, index, display_name)
            if avatar_id:
                user_to_avatar[user_id] = avatar_id
            continue
        switch_match = _SWITCH_RE.search(line)
        if switch_match:
            display_name = switch_match.group(1).strip()
            avatar_name = switch_match.group(2).strip()
            user_id = name_to_user.get(display_name)
            if user_id:
                user_to_avatar.pop(user_id, None)
                avatar_id = _avatar_id_after_unpack(lines, index, avatar_name) or _avatar_id_near_index(lines, index + 1, stop_at_switch=False)
                if avatar_id:
                    user_to_avatar[user_id] = avatar_id
            continue
        leave_match = _LEAVE_RE.search(line)
        if leave_match:
            user_id = leave_match.group(2)
            user_to_avatar.pop(user_id, None)
            for display_name, mapped_user_id in list(name_to_user.items()):
                if mapped_user_id == user_id:
                    name_to_user.pop(display_name, None)
    for display_name, user_id in name_to_user.items():
        if user_id in user_to_avatar:
            continue
        switch_idx = _switch_index_before(lines, len(lines), display_name)
        if switch_idx is None:
            continue
        switch_match = _SWITCH_RE.search(lines[switch_idx])
        avatar_name = switch_match.group(2).strip() if switch_match else ''
        avatar_id = _avatar_id_after_unpack(lines, switch_idx, avatar_name) if avatar_name else ''
        if not avatar_id:
            avatar_id = _avatar_id_near_index(lines, switch_idx + 1, stop_at_switch=False)
        if avatar_id:
            user_to_avatar[user_id] = avatar_id
    return user_to_avatar

def parse_avatar_ids_from_log(log_path: Path | None=None) -> dict[str, str]:
    try:
        lines = _read_log_lines_cached(log_path)
        if not lines:
            return {}
        start_idx = _last_room_start_index(lines)
        return _build_user_avatar_map_from_lines(lines[start_idx:])
    except Exception as e:
        logger.warning('Failed to parse avatar IDs from log: %s', e, exc_info=True)
        return {}

def _read_log_lines(*, current_room_only: bool) -> list[str] | None:
    lines = _read_log_lines_cached()
    if lines is None:
        return None
    if current_room_only:
        return lines[_last_room_start_index(lines):]
    return lines

def _read_room_log_lines() -> list[str] | None:
    return _read_log_lines(current_room_only=True)

def _build_player_avatar_info(room_lines: list[str], user_id: str, display_name: str | None=None) -> PlayerAvatarInfo:
    user_to_avatar = _build_user_avatar_map_from_lines(room_lines)
    name_to_user: dict[str, str] = {}
    switch_names: dict[str, str] = {}
    for line in room_lines:
        join_match = _JOIN_RE.search(line)
        if join_match:
            name_to_user[join_match.group(1).strip()] = join_match.group(2)
            continue
        switch_match = _SWITCH_RE.search(line)
        if switch_match:
            switch_names[switch_match.group(1).strip()] = switch_match.group(2).strip()
    avatar_id = user_to_avatar.get(user_id) or None
    if avatar_id:
        return PlayerAvatarInfo(avatar_id=avatar_id)
    avatar_name: str | None = None
    if display_name and display_name in switch_names:
        avatar_name = switch_names[display_name]
    else:
        for player_name, av_name in switch_names.items():
            if name_to_user.get(player_name) == user_id:
                avatar_name = av_name
                break
    if display_name and (not avatar_id):
        for index, line in enumerate(room_lines):
            switch_match = _SWITCH_RE.search(line)
            if not switch_match or switch_match.group(1).strip() != display_name:
                continue
            found_id = _avatar_id_after_line(room_lines, index + 1)
            if found_id:
                avatar_id = found_id
                break
    author_name: str | None = None
    if avatar_name:
        for index, line in enumerate(room_lines):
            switch = _SWITCH_RE.search(line)
            if not switch or not _avatar_names_equivalent(switch.group(2), avatar_name):
                continue
            player_name = switch.group(1).strip()
            if display_name and player_name != display_name and name_to_user.get(player_name) != user_id:
                continue
            if not display_name and name_to_user.get(player_name) != user_id:
                continue
            found_author = _author_for_avatar_name(room_lines, avatar_name, index + 1)
            if found_author:
                author_name = found_author
                break
    return PlayerAvatarInfo(avatar_id=avatar_id, avatar_name=avatar_name, author_name=author_name)

def _room_lines_from_file_lines(lines: list[str]) -> list[str]:
    return lines[_last_room_start_index(lines):]

def build_user_avatar_map_from_logs() -> dict[str, str]:
    global _user_avatar_map_cache
    files = find_vrchat_log_files()
    signature = _log_files_signature(files)
    if _user_avatar_map_cache is not None and _user_avatar_map_cache[0] == signature:
        return dict(_user_avatar_map_cache[1])
    merged: dict[str, str] = {}
    for path in reversed(files):
        lines = _read_log_lines_cached(path)
        if not lines:
            try:
                lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                continue
        merged.update(_build_user_avatar_map_from_lines(_room_lines_from_file_lines(lines)))
    _user_avatar_map_cache = (signature, merged)
    return dict(merged)

def lookup_player_avatar_info(user_id: str, display_name: str | None=None) -> PlayerAvatarInfo:
    try:
        room_lines = _read_room_log_lines()
        if not room_lines:
            return PlayerAvatarInfo()
        return _build_player_avatar_info(room_lines, user_id, display_name=display_name)
    except Exception as e:
        logger.warning('Failed to lookup player avatar info for %s: %s', display_name or user_id, e, exc_info=True)
        return PlayerAvatarInfo()

def lookup_player_avatar_info_historical(user_id: str, display_name: str | None=None) -> PlayerAvatarInfo:
    for path in find_vrchat_log_files():
        lines = _read_log_lines_cached(path)
        if lines is None:
            try:
                lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                continue
        info = _build_player_avatar_info(lines, user_id, display_name=display_name)
        if info.avatar_id or info.avatar_name:
            return info
    return PlayerAvatarInfo()

def current_room_user_ids() -> set[str]:
    players, _ = log_players_for_current_room()
    return {player.user_id for player in players}

def player_avatar_info(user_id: str, display_name: str | None=None) -> PlayerAvatarInfo:
    room_lines = _read_room_log_lines()
    if not room_lines:
        return PlayerAvatarInfo()
    return _build_player_avatar_info(room_lines, user_id, display_name=display_name)

def _avatar_id_from_log(user_id: str, display_name: str | None=None) -> str | None:
    info = player_avatar_info(user_id, display_name=display_name)
    return info.avatar_id

def players_in_current_room() -> list[LogPlayer]:
    players, _ = log_players_for_current_room()
    return players

def current_room_location() -> str | None:
    _, location = log_players_for_current_room()
    return location

def log_player_ids() -> set[str]:
    return {player.user_id for player in players_in_current_room()}

def log_player_names() -> dict[str, str]:
    return {player.user_id: player.display_name for player in players_in_current_room()}

def log_player_count() -> int:
    return len(players_in_current_room())
