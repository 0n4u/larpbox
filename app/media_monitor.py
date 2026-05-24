from __future__ import annotations
import ctypes
import re
import threading
from typing import Any
import psutil
from PyQt6.QtCore import QThread, pyqtSignal
from app.media_smtc import fetch_smtc_media, format_track_display, smtc_available
from app.logging_setup import get_logger, truncate_for_log
logger = get_logger('media')
_MEDIA_PLAYERS = frozenset({'spotify.exe', 'vlc.exe', 'itunes.exe', 'musicbee.exe', 'foobar2000.exe', 'winamp.exe', 'wmplayer.exe', 'chrome.exe', 'msedge.exe', 'firefox.exe', 'discord.exe', 'aimp.exe', 'qmmp.exe'})
_TITLE_PREFIX_RE = re.compile('^YouTube\\s*[—–-]\\s*|^SoundCloud\\s*[—–-]\\s*|^Bandcamp\\s*[—–-]\\s*|^Spotify\\s*[—–-]\\s*|^Amazon Music\\s*[—–-]\\s*', re.IGNORECASE)
_ARTIST_SONG_RE = re.compile('^(.+?)\\s*[—–-]\\s*(.+)$')
_PLAYER_STRIP = ('Spotify', 'VLC media player', 'iTunes', 'MusicBee', 'foobar2000', 'Winamp')

class MediaIntegration(QThread):
    song_updated = pyqtSignal(dict)
    status_updated = pyqtSignal(str)
    POLL_PLAYING_MS = 750
    POLL_IDLE_MS = 2000

    def __init__(self):
        super().__init__()
        self.running = False
        self.current_track: dict[str, Any] | None = None
        self.enabled = False
        self._wake = threading.Event()
        self._last_fingerprint: str | None = None
        self._backend = 'smtc' if smtc_available() else 'windows'
        logger.debug('MediaIntegration backend=%s', self._backend)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            label = 'Media detection enabled (system media)' if self._backend == 'smtc' else 'Media detection enabled (window titles)'
            logger.info('%s', label)
            self.status_updated.emit(label)
            self.request_poll()
        else:
            logger.info('Media detection disabled')
            self.status_updated.emit('Media detection disabled')
            self._clear_current_track()

    def request_poll(self) -> None:
        self._wake.set()

    def get_media_window_titles(self) -> list[tuple[str, str]]:
        try:
            try:
                import win32gui
                import win32process
                windows: list[tuple[str, str]] = []

                def callback(hwnd, _extra):
                    if not win32gui.IsWindowVisible(hwnd):
                        return True
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        process = psutil.Process(pid)
                        process_name = process.name()
                        if process_name.lower() not in _MEDIA_PLAYERS:
                            return True
                        window_text = win32gui.GetWindowText(hwnd)
                        if window_text:
                            windows.append((process_name, window_text))
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    return True
                win32gui.EnumWindows(callback, None)
                return windows
            except ImportError:
                return self.get_media_titles_ctypes()
        except Exception:
            return []

    def get_media_titles_ctypes(self) -> list[tuple[str, str]]:
        try:
            media_pids = {proc.info['pid']: proc.info['name'] for proc in psutil.process_iter(['pid', 'name']) if proc.info['name'] and proc.info['name'].lower() in _MEDIA_PLAYERS}
            if not media_pids:
                return []
            user32 = ctypes.windll.user32
            found_titles: list[tuple[str, str]] = []
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def enum_windows_callback(hwnd, _lparam):
                try:
                    process_id = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                    if process_id.value not in media_pids:
                        return True
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length <= 0:
                        return True
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value.strip()
                    if title and (not title.startswith(('Default IME', 'MSCTFIME', 'IME'))):
                        found_titles.append((media_pids[process_id.value], title))
                except Exception:
                    pass
                return True
            user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
            return found_titles
        except Exception:
            return []

    def parse_media_title(self, title: str, process_name: str) -> dict[str, Any] | None:
        if not title or not title.strip():
            return None
        clean_title = title
        for player in _PLAYER_STRIP:
            if clean_title.startswith(player):
                if clean_title.strip() in (player, f'{player} -'):
                    return self._track_dict('Paused', '', False, 'Media (Paused)', process_name)
                clean_title = clean_title.replace(player + ' - ', '', 1).replace(player + ' — ', '', 1)
        clean_title = _TITLE_PREFIX_RE.sub('', clean_title).strip()
        match = _ARTIST_SONG_RE.match(clean_title)
        if match:
            artist = match.group(1).strip()
            song = match.group(2).strip()
            display = format_track_display(song, artist, True)
            return self._track_dict(song, artist, True, display, process_name)
        player_names = {p.replace('.exe', '') for p in _MEDIA_PLAYERS}
        if len(clean_title) > 3 and clean_title.lower() not in player_names:
            return self._track_dict(clean_title, '', True, clean_title, process_name)
        return None

    def _track_dict(self, name: str, artists: str, is_playing: bool, display_text: str, source: str) -> dict[str, Any]:
        fingerprint = f'{name}|{artists}|{int(is_playing)}|{source}'
        return {'name': name, 'artists': artists, 'is_playing': is_playing, 'display_text': display_text, 'source': source, 'fingerprint': fingerprint}

    def _select_track_from_windows(self, media_windows: list[tuple[str, str]]) -> dict[str, Any] | None:
        playing_info: dict[str, Any] | None = None
        paused_info: dict[str, Any] | None = None
        for process_name, title in media_windows:
            parsed = self.parse_media_title(title, process_name)
            if not parsed:
                continue
            if parsed.get('is_playing'):
                if not playing_info:
                    playing_info = parsed
            elif not paused_info:
                paused_info = parsed
        return playing_info or paused_info

    def get_media_info_windows(self) -> dict[str, Any] | None:
        try:
            media_windows = self.get_media_window_titles()
            if not media_windows:
                return None
            return self._select_track_from_windows(media_windows)
        except Exception:
            return None

    def poll_media(self) -> dict[str, Any] | None:
        if self._backend == 'smtc':
            track = fetch_smtc_media()
            if track:
                return track
        return self.get_media_info_windows()

    def _emit_if_changed(self, track_info: dict[str, Any] | None) -> None:
        if not track_info:
            self._clear_current_track()
            return
        fingerprint = track_info.get('fingerprint') or track_info.get('display_text')
        if fingerprint == self._last_fingerprint:
            return
        playing = track_info.get('is_playing', False)
        display = track_info.get('display_text') or fingerprint
        logger.info('Media track changed playing=%s → %s', playing, truncate_for_log(str(display)))
        self._last_fingerprint = fingerprint
        self.current_track = track_info
        self.song_updated.emit(track_info)

    def _clear_current_track(self) -> None:
        if self._last_fingerprint is None and self.current_track is None:
            return
        logger.debug('Media track cleared')
        self._last_fingerprint = None
        self.current_track = None
        self.song_updated.emit({'display_text': '', 'is_playing': False})

    def run(self) -> None:
        self.running = True
        logger.info('Media poll thread started')
        while self.running:
            if self.enabled:
                self._emit_if_changed(self.poll_media())
            playing = bool(self.current_track and self.current_track.get('is_playing', False))
            interval = self.POLL_PLAYING_MS if playing else self.POLL_IDLE_MS
            self._wake.wait(interval / 1000.0)
            self._wake.clear()

    def stop(self) -> None:
        logger.info('Media poll thread stopping')
        self.running = False
        self._wake.set()
