from __future__ import annotations
from PyQt6.QtCore import QObject
from ..config import get_bool, load_config
from ..logging_setup import get_logger
from ..vrchat.models import InstanceInfo
logger = get_logger('rich_presence')

class RichPresenceManager(QObject):
    _instance: 'RichPresenceManager | None' = None

    @classmethod
    def instance(cls) -> 'RichPresenceManager':
        if cls._instance is None:
            cls._instance = RichPresenceManager()
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self._discord = None
        self._last_key = ''

    def _discord_enabled(self) -> bool:
        return get_bool(load_config().get('enable_discord_presence', False))

    def _ensure_discord(self) -> bool:
        if self._discord is not None:
            return True
        if not self._discord_enabled():
            return False
        try:
            from pypresence import Presence
        except ImportError:
            logger.debug('pypresence not installed — Discord presence disabled')
            return False
        client_id = str(load_config().get('discord_client_id') or '438933080159576067').strip()
        if not client_id:
            return False
        try:
            self._discord = Presence(client_id)
            self._discord.connect()
            return True
        except Exception:
            logger.debug('Discord presence connect failed', exc_info=True)
            self._discord = None
            return False

    def update_instance(self, info: InstanceInfo | None) -> None:
        if info is None or not info.world_name:
            self.clear()
            return
        key = f'{info.world_id}:{info.instance_id}:{info.player_count}'
        if key == self._last_key:
            return
        self._last_key = key
        world = info.world_name
        details = f'{info.player_count} players'
        if info.max_players:
            details = f'{info.player_count}/{info.max_players} players'
        state = info.instance_type.title() if info.instance_type else 'In VRChat'
        if self._discord_enabled() and self._ensure_discord():
            try:
                self._discord.update(details=world, state=state, large_image='vrchat', large_text=world, small_text=details)
            except Exception:
                logger.debug('Discord presence update failed', exc_info=True)
                self._discord = None

    def clear(self) -> None:
        self._last_key = ''
        if self._discord is not None:
            try:
                self._discord.clear()
            except Exception:
                pass
            try:
                self._discord.close()
            except Exception:
                pass
            self._discord = None

    def cleanup(self) -> None:
        self.clear()
