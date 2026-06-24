from __future__ import annotations
import json
from json import JSONDecodeError
from pathlib import Path
from .atomic_io import atomic_write_text
from .config import PROJECT_ROOT
PRESETS_PATH = PROJECT_ROOT / 'presets.json'
ALIASES_PATH = PROJECT_ROOT / 'app' / 'preset_aliases.json'
_ALIASES_CACHE: dict[str, str] | None = None
_PRESETS_CACHE: dict[str, list] | None = None

def _escape_control_chars_in_strings(raw: str) -> str:
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in raw:
        if in_string:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == '\\':
                out.append(ch)
                escaped = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = False
                continue
            code = ord(ch)
            if code < 32:
                out.append(f'\\u{code:04x}')
            else:
                out.append(ch)
            continue
        out.append(ch)
        if ch == '"':
            in_string = True
            escaped = False
    return ''.join(out)

def _read_json_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ('utf-8-sig', 'utf-8'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode('cp1252')

def _parse_presets_json(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except JSONDecodeError:
        parsed = json.loads(_escape_control_chars_in_strings(raw))
    return parsed if isinstance(parsed, dict) else {}

def _live_preset_keys() -> set[str]:
    global _PRESETS_CACHE
    if _PRESETS_CACHE is not None:
        return set(_PRESETS_CACHE.keys())
    if not PRESETS_PATH.exists():
        return set()
    try:
        load_presets(PRESETS_PATH)
        return set(_PRESETS_CACHE.keys()) if _PRESETS_CACHE else set()
    except Exception:
        return set()

def load_preset_aliases() -> dict[str, str]:
    global _ALIASES_CACHE
    if _ALIASES_CACHE is not None:
        return _ALIASES_CACHE
    if not ALIASES_PATH.exists():
        _ALIASES_CACHE = {}
        return _ALIASES_CACHE
    try:
        raw = json.loads(_read_json_text(ALIASES_PATH))
        if not isinstance(raw, dict):
            _ALIASES_CACHE = {}
            return _ALIASES_CACHE
    except Exception:
        _ALIASES_CACHE = {}
        return _ALIASES_CACHE
    live = _live_preset_keys()
    aliases: dict[str, str] = {}
    for old, new in raw.items():
        if not isinstance(old, str) or not isinstance(new, str):
            continue
        if old == new:
            continue
        if live and new not in live:
            continue
        aliases[old] = new
    _ALIASES_CACHE = aliases
    return _ALIASES_CACHE

def load_presets(path: Path | None=None) -> dict[str, list]:
    global _PRESETS_CACHE
    presets_path = path or PRESETS_PATH
    if presets_path == PRESETS_PATH and _PRESETS_CACHE is not None:
        return _PRESETS_CACHE.copy()
    if not presets_path.exists():
        result = {'Gamesense': ['No animation available']}
    else:
        try:
            raw = _read_json_text(presets_path)
            animations = _parse_presets_json(raw)
            result = animations
        except Exception:
            result = {'Gamesense': ['No animation available']}
    if presets_path == PRESETS_PATH:
        _PRESETS_CACHE = result
    return result

def save_presets(presets: dict, path: Path | None=None) -> None:
    presets_path = path or PRESETS_PATH
    atomic_write_text(presets_path, json.dumps(presets, indent=4, ensure_ascii=False))
    if presets_path == PRESETS_PATH:
        global _PRESETS_CACHE
        _PRESETS_CACHE = presets
        global _ALIASES_CACHE
        _ALIASES_CACHE = None

def delete_preset(name: str, path: Path | None=None) -> bool:
    presets = load_presets(path)
    resolved = resolve_preset_name(name)
    if resolved not in presets:
        return False
    del presets[resolved]
    save_presets(presets, path)
    return True

def resolve_preset_name(name: str) -> str:
    aliases = load_preset_aliases()
    seen: set[str] = set()
    current = name
    while current in aliases and current not in seen:
        seen.add(current)
        current = aliases[current]
    return current
