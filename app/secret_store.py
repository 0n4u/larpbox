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
ACCOUNTS_PATH = PROJECT_ROOT / 'data' / 'accounts.dat'
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


def _write_encrypted(path: Path, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    blob = _dpapi_encrypt(raw)
    if blob is not None:
        envelope = {'enc': 'dpapi', 'data': base64.b64encode(blob).decode('ascii')}
    else:
        logger.warning('Storing credentials WITHOUT OS encryption (DPAPI unavailable on this platform)')
        envelope = {'enc': 'plain', 'data': base64.b64encode(raw).decode('ascii')}
    _atomic_write(path, json.dumps(envelope))


def _read_encrypted(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        envelope = json.loads(path.read_text(encoding='utf-8'))
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
        logger.warning('Failed to read encrypted store %s', path.name, exc_info=True)
        return {}


def save_secrets(secrets: dict[str, Any]) -> None:
    try:
        _write_encrypted(SECRETS_PATH, secrets)
    except Exception:
        logger.error('Failed to write credential store', exc_info=True)


def load_secrets() -> dict[str, Any]:
    return _read_encrypted(SECRETS_PATH)


def save_account_secrets(user_id: str, secrets: dict[str, Any]) -> None:
    """Persist per-account credentials in a separate encrypted vault, keyed by user_id."""
    if not user_id:
        return
    vault = _read_encrypted(ACCOUNTS_PATH)
    vault[user_id] = secrets
    try:
        _write_encrypted(ACCOUNTS_PATH, vault)
    except Exception:
        logger.error('Failed to write account vault', exc_info=True)


def load_account_secrets(user_id: str) -> dict[str, Any]:
    if not user_id:
        return {}
    entry = _read_encrypted(ACCOUNTS_PATH).get(user_id)
    return entry if isinstance(entry, dict) else {}


def list_account_secret_ids() -> list[str]:
    return [key for key in _read_encrypted(ACCOUNTS_PATH).keys() if isinstance(key, str)]


def remove_account_secrets(user_id: str) -> None:
    if not user_id:
        return
    vault = _read_encrypted(ACCOUNTS_PATH)
    if user_id in vault:
        del vault[user_id]
        try:
            _write_encrypted(ACCOUNTS_PATH, vault)
        except Exception:
            logger.warning('Failed to update account vault', exc_info=True)


def has_secrets() -> bool:
    return SECRETS_PATH.exists()


def clear_secrets() -> None:
    try:
        SECRETS_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        logger.warning('Failed to clear credential store', exc_info=True)


def clear_account_vault() -> None:
    try:
        ACCOUNTS_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        logger.warning('Failed to clear account vault', exc_info=True)


def clear_all_secrets() -> None:
    clear_secrets()
    clear_account_vault()
