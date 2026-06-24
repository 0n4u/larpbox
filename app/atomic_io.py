from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _atomic_write(path: Path, data: bytes | str, *, encoding: str='utf-8') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix or '.tmp'
    fd, temp_name = tempfile.mkstemp(prefix=f'{path.stem}.', suffix=suffix, dir=path.parent)
    temp_path = Path(temp_name)
    try:
        if isinstance(data, str):
            with os.fdopen(fd, 'w', encoding=encoding) as handle:
                handle.write(data)
        else:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str, *, encoding: str='utf-8') -> None:
    _atomic_write(path, text, encoding=encoding)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    _atomic_write(path, data)


def atomic_write_json(path: Path, obj: Any, *, indent: int=2, ensure_ascii: bool=False) -> None:
    _atomic_write(path, json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii))
