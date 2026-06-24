from __future__ import annotations
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path
from .atomic_io import atomic_write_bytes
from .config import PROJECT_ROOT
from .logging_setup import get_logger
from .vrc_image_utils import is_allowed_image_host, parse_vrc_image_url
from .vrchat_auth import USER_AGENT
logger = get_logger('image_cache')
_CACHE_ROOT = PROJECT_ROOT / 'ImageCache'
_MAX_CACHE_DIRS = 1100
_KEEP_CACHE_DIRS = 1000
_lock = threading.Lock()

def cache_root() -> Path:
    return _CACHE_ROOT

def cache_file_path(file_id: str, version: str) -> Path:
    return _CACHE_ROOT / file_id / f'{version}.png'

def cached_image_path(url: str) -> Path | None:
    parsed = parse_vrc_image_url(url)
    if not parsed:
        return None
    file_id, version, _size = parsed
    path = cache_file_path(file_id, version)
    if path.is_file() and path.stat().st_size > 0:
        path.parent.touch()
        return path
    return None

def _cookie_header(auth_token: str | None, two_factor_token: str | None) -> str | None:
    if not auth_token:
        return None
    cookie = f'auth={auth_token}'
    if two_factor_token:
        cookie += f'; twoFactorAuth={two_factor_token}'
    return cookie

def save_image_bytes_to_cache(url: str, data: bytes) -> Path | None:
    parsed = parse_vrc_image_url(url)
    if not parsed or not data:
        return None
    file_id, version, _size = parsed
    destination = cache_file_path(file_id, version)
    directory = destination.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_bytes(destination, data)
        directory.touch()
        _maybe_clean_cache()
        return destination
    except Exception:
        logger.debug('Could not write image cache: %s', destination, exc_info=True)
        return None

def fetch_image_to_cache(url: str, *, auth_token: str | None=None, two_factor_token: str | None=None) -> Path | None:
    cleaned = (url or '').strip()
    if not cleaned or not is_allowed_image_host(cleaned):
        return None
    parsed = parse_vrc_image_url(cleaned)
    if not parsed:
        return None
    file_id, version, _size = parsed
    destination = cache_file_path(file_id, version)
    existing = cached_image_path(cleaned)
    if existing is not None:
        return existing
    directory = destination.parent
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(cleaned, headers={'User-Agent': USER_AGENT})
    cookie = _cookie_header(auth_token, two_factor_token)
    if cookie and any((host in cleaned for host in ('vrchat.cloud', 'vrchat.com', 'cloudfront.net'))):
        request.add_header('Cookie', cookie)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        logger.debug('Image fetch failed (%s): %s', exc.code, cleaned)
        shutil.rmtree(directory, ignore_errors=True)
        return None
    except Exception:
        logger.debug('Image fetch failed: %s', cleaned, exc_info=True)
        shutil.rmtree(directory, ignore_errors=True)
        return None
    if not data:
        shutil.rmtree(directory, ignore_errors=True)
        return None
    temp_path = destination.with_suffix('.png.part')
    try:
        temp_path.write_bytes(data)
        temp_path.replace(destination)
    except Exception:
        logger.debug('Could not write image cache: %s', destination, exc_info=True)
        shutil.rmtree(directory, ignore_errors=True)
        return None
    _maybe_clean_cache()
    return destination

def _maybe_clean_cache() -> None:
    with _lock:
        if not _CACHE_ROOT.is_dir():
            return
        folders = [entry for entry in _CACHE_ROOT.iterdir() if entry.is_dir()]
        if len(folders) <= _MAX_CACHE_DIRS:
            return
        folders.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for folder in folders[_KEEP_CACHE_DIRS:]:
            shutil.rmtree(folder, ignore_errors=True)
        logger.debug('ImageCache cleaned (%d folders kept)', _KEEP_CACHE_DIRS)

def clear_image_cache() -> None:
    with _lock:
        if _CACHE_ROOT.is_dir():
            shutil.rmtree(_CACHE_ROOT, ignore_errors=True)
