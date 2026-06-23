from __future__ import annotations
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from .logging_setup import get_logger

logger = get_logger('secrets')
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRETS_PATH = PROJECT_ROOT / 'data' / 'credentials.dat'
_ENTROPY = b'larpbox-vrchat-credential-store-v1'
_DESCRIPTION = 'larpbox VRChat credentials'


def _dpapi_encrypt(data: bytes) -> bytes | None:
    if sys.platform != 'win32':
        return None
    try:
        import win32crypt
        return win32crypt.CryptProtectData(data, _DESCRIPTION, _ENTROPY, None, None, 0)
    except Exception:
        logger.warning('DPAPI encryption unavailable — credentials cannot be encrypted', exc_info=True)
        return None


def _dpapi_decrypt(blob: bytes) -> bytes | None:
    if sys.platform != 'win32':
        return None
    try:
        import win32crypt
        _description, data = win32crypt.CryptUnprotectData(blob, _ENTROPY, None, None, 0)
        return data
    except Exception:
        logger.warning('DPAPI decryption failed — stored credentials unreadable', exc_info=True)
        return None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='credentials.', suffix='.dat', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def save_secrets(secrets: dict[str, Any]) -> None:
    payload = json.dumps(secrets, ensure_ascii=False).encode('utf-8')
    blob = _dpapi_encrypt(payload)
    if blob is not None:
        envelope = {'enc': 'dpapi', 'data': base64.b64encode(blob).decode('ascii')}
    else:
        logger.warning('Storing credentials WITHOUT OS encryption (DPAPI unavailable on this platform)')
        envelope = {'enc': 'plain', 'data': base64.b64encode(payload).decode('ascii')}
    try:
        _atomic_write(SECRETS_PATH, json.dumps(envelope))
    except Exception:
        logger.error('Failed to write credential store', exc_info=True)


def load_secrets() -> dict[str, Any]:
    if not SECRETS_PATH.exists():
        return {}
    try:
        envelope = json.loads(SECRETS_PATH.read_text(encoding='utf-8'))
        if not isinstance(envelope, dict):
            return {}
        raw = base64.b64decode(envelope.get('data') or '')
        if envelope.get('enc') == 'dpapi':
            data = _dpapi_decrypt(raw)
            if data is None:
                return {}
        else:
            data = raw
        result = json.loads(data.decode('utf-8'))
        return result if isinstance(result, dict) else {}
    except Exception:
        logger.warning('Failed to read credential store', exc_info=True)
        return {}


def has_secrets() -> bool:
    return SECRETS_PATH.exists()


def clear_secrets() -> None:
    try:
        SECRETS_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        logger.warning('Failed to clear credential store', exc_info=True)
