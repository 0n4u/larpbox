from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .logging_setup import get_logger

logger = get_logger('vrchat_amplitude')

_AVTR_RE = re.compile(r'avtr_[a-f0-9-]{36}', re.IGNORECASE)
_USR_RE = re.compile(r'usr_[a-f0-9-]{36}', re.IGNORECASE)
_PAIR_RE = re.compile(
    r'"(?:userId|user_id)"\s*:\s*"(usr_[^"]+)"[\s\S]{0,800}?"(?:avatarId|avatar_id)"\s*:\s*"(avtr_[^"]+)"',
    re.IGNORECASE,
)


def _amplitude_paths() -> list[Path]:
    paths: list[Path] = []
    temp_root = Path(os.environ.get('TEMP', '')) / 'VRChat' / 'VRChat' / 'amplitude.cache'
    if temp_root.is_file():
        paths.append(temp_root)
    low_root = Path(os.environ.get('LOCALAPPDATA', '')).parent / 'LocalLow' / 'VRChat' / 'VRChat'
    unity_root = low_root / 'Unity'
    if unity_root.is_dir():
        for values_file in unity_root.glob('*/Analytics/values'):
            if values_file.is_file():
                paths.append(values_file)
    return paths


def build_user_avatar_map_from_amplitude() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in _amplitude_paths():
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if not text.strip():
            continue
        for user_id, avatar_id in _PAIR_RE.findall(text):
            mapping[user_id] = avatar_id
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                user_id = str(item.get('userId') or item.get('user_id') or '')
                avatar_id = str(item.get('avatarId') or item.get('avatar_id') or '')
                if user_id.startswith('usr_') and avatar_id.startswith('avtr_'):
                    mapping[user_id] = avatar_id
        elif isinstance(payload, dict):
            for user_id, avatar_id in _PAIR_RE.findall(json.dumps(payload)):
                mapping[user_id] = avatar_id
    return mapping
