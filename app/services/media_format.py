from __future__ import annotations
from ..config import load_config_cached

def format_media_display(title: str, artist: str, is_playing: bool, *, template: str | None=None) -> str:
    title = (title or '').strip()
    artist = (artist or '').strip()
    if not title and (not artist):
        return 'Media (Paused)' if not is_playing else ''
    cfg = load_config_cached()
    fmt = template or str(cfg.get('media_format', '{title} by {artist}'))

    class _SafeDict(dict):

        def __missing__(self, key: str) -> str:
            return '{' + key + '}'
    mapping = _SafeDict(title=title, artist=artist, name=title or artist, playing='▶' if is_playing else '⏸')
    try:
        base = fmt.format_map(mapping).strip()
    except Exception:
        base = f'{title} by {artist}'.strip(' by ') if title or artist else ''
    if not base:
        return ''
    if not is_playing:
        return f'{base} (Paused)'
    return base
