from __future__ import annotations
import re
from urllib.parse import urlparse
VRCHAT_API = 'https://api.vrchat.cloud/api/1'
ALLOWED_IMAGE_HOSTS = frozenset({'api.vrchat.cloud', 'files.vrchat.cloud', 'd348imysud55la.cloudfront.net', 'assets.vrchat.com'})
_FILE_ID_RE = re.compile('file_[0-9A-Za-z-]+', re.IGNORECASE)
_FILE_PATH_RE = re.compile('/file/(file_[0-9A-Fa-f-]+)/(\\d+)(?:/file)?/?$', re.IGNORECASE)
_IMAGE_PATH_RE = re.compile('/image/(file_[0-9A-Fa-f-]+)/(\\d+)/(\\d+)/?$', re.IGNORECASE)

def extract_file_id(value: str) -> str:
    match = _FILE_ID_RE.search(str(value or ''))
    return match.group(0) if match else ''

def extract_file_version(value: str) -> str:
    match = re.search('/file_[0-9A-Za-z-]+/(\\d+)', str(value or ''), re.IGNORECASE)
    return match.group(1) if match else ''

def convert_file_url_to_image_url(url: str, *, resolution: int=128) -> str:
    cleaned = (url or '').strip()
    if not cleaned:
        return ''
    match = _FILE_PATH_RE.search(cleaned.replace('\\', '/'))
    if not match:
        return cleaned
    file_id, version = match.groups()
    return f'{VRCHAT_API}/image/{file_id}/{version}/{resolution}'

def parse_vrc_image_url(url: str) -> tuple[str, str, int] | None:
    cleaned = (url or '').strip()
    if not cleaned:
        return None
    match = _IMAGE_PATH_RE.search(cleaned.replace('\\', '/'))
    if not match:
        return None
    file_id, version, size = match.groups()
    return (file_id, version, int(size))

def is_allowed_image_host(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ''
    except Exception:
        return False
    return host in ALLOWED_IMAGE_HOSTS

def normalize_vrc_image_url(url: str, *, resolution: int=128) -> str:
    cleaned = (url or '').strip()
    if not cleaned:
        return ''
    if cleaned.startswith('/'):
        cleaned = f'{VRCHAT_API}{cleaned}'
    if '/api/1/file/' in cleaned and '/api/1/image/' not in cleaned:
        converted = convert_file_url_to_image_url(cleaned, resolution=resolution)
        return converted if is_allowed_image_host(converted) else ''
    parsed = parse_vrc_image_url(cleaned)
    if parsed:
        file_id, version, _size = parsed
        normalized = f'{VRCHAT_API}/image/{file_id}/{version}/{resolution}'
        return normalized if is_allowed_image_host(normalized) else ''
    if is_allowed_image_host(cleaned) and cleaned.startswith('http'):
        return cleaned
    return ''
