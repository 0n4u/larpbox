from __future__ import annotations
import asyncio
import sys
import threading
from typing import Any
_PLAYING = 4
_PAUSED = 5
from .services.media_format import format_media_display

_loop_storage = threading.local()


def _get_event_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_loop_storage, 'loop', None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop_storage.loop = loop
    return loop

def smtc_available() -> bool:
    if sys.platform != 'win32':
        return False
    try:
        import winrt.windows.media.control
        return True
    except ImportError:
        return False

def format_track_display(title: str, artist: str, is_playing: bool) -> str:
    return format_media_display(title, artist, is_playing)

async def _fetch_async() -> dict[str, Any] | None:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SessionManager
    manager = await SessionManager.request_async()
    session = manager.get_current_session()
    if session is None:
        for candidate in manager.get_sessions():
            try:
                status = candidate.get_playback_info().playback_status
            except Exception:
                continue
            if status == _PLAYING:
                session = candidate
                break
    if session is None:
        return None
    try:
        props = await session.try_get_media_properties_async()
        playback = session.get_playback_info()
        status = playback.playback_status
    except Exception:
        return None
    title = (props.title or '').strip()
    artist = (props.artist or '').strip()
    is_playing = status == _PLAYING
    is_paused = status == _PAUSED
    if not title and (not artist):
        if is_paused:
            return {'name': 'Paused', 'artists': '', 'is_playing': False, 'display_text': 'Media (Paused)', 'source': 'smtc', 'fingerprint': 'paused|smtc'}
        if not is_playing:
            return None
    source = 'smtc'
    try:
        app_id = session.source_app_user_model_id or ''
        if app_id:
            source = app_id.split('!')[-1] if '!' in app_id else app_id
    except Exception:
        pass
    display = format_track_display(title, artist, is_playing)
    if not display:
        return None
    fingerprint = f'{title}|{artist}|{int(is_playing)}|{source}'
    return {'name': title or artist, 'artists': artist, 'is_playing': is_playing, 'display_text': display, 'source': source, 'fingerprint': fingerprint}

def fetch_smtc_media() -> dict[str, Any] | None:
    if not smtc_available():
        return None
    try:
        return _get_event_loop().run_until_complete(_fetch_async())
    except Exception:
        return None
