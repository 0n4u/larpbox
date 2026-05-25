from __future__ import annotations
from PyQt6.QtCore import QObject, pyqtSignal

class NotificationBus(QObject):
    notify = pyqtSignal(str, str, str)
    _instance: 'NotificationBus | None' = None

    @classmethod
    def instance(cls) -> 'NotificationBus':
        if cls._instance is None:
            cls._instance = NotificationBus()
        return cls._instance

    def post(self, title: str, body: str='', *, level: str='info') -> None:
        self.notify.emit(title, body, level)

    def friend_online(self, display_name: str) -> None:
        self.post('Friend Online', display_name, level='info')

    def friend_joined_instance(self, display_name: str) -> None:
        self.post('Friend Nearby', f'{display_name} is in your instance', level='info')

    def friend_joinable(self, display_name: str, world_name: str) -> None:
        self.post('Friend Joinable', f'{display_name} — {world_name}', level='info')
