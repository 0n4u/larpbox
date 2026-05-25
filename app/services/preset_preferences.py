from __future__ import annotations
from ..config import load_config, save_config

def get_favorites() -> list[str]:
    config = load_config()
    raw = config.get('preset_favorites', [])
    return [str(x) for x in raw if isinstance(x, str)] if isinstance(raw, list) else []

def get_recents() -> list[str]:
    config = load_config()
    raw = config.get('preset_recents', [])
    return [str(x) for x in raw if isinstance(x, str)] if isinstance(raw, list) else []

def toggle_favorite(name: str) -> list[str]:
    favorites = get_favorites()
    if name in favorites:
        favorites = [item for item in favorites if item != name]
    else:
        favorites = [name, *favorites]
    save_config({'preset_favorites': favorites[:50]})
    return favorites

def is_favorite(name: str) -> bool:
    return name in get_favorites()

def record_recent(name: str) -> None:
    recents = [item for item in get_recents() if item != name]
    recents.insert(0, name)
    save_config({'preset_recents': recents[:20]})
