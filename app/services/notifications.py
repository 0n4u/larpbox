from __future__ import annotations
from PyQt6.QtCore import QObject, pyqtSignal

class NotificationBus(QObject):
    notify = pyqtSignal(str, str, str, str)
    _instance: 'NotificationBus | None' = None

    @classmethod
    def instance(cls) -> 'NotificationBus':
        if cls._instance is None:
            cls._instance = NotificationBus()
        return cls._instance

    def post(self, title: str, body: str='', *, level: str='info', action: str='') -> None:
        self.notify.emit(title, body, level, action)

    def friend_online(self, display_name: str) -> None:
        self.post('Friend Online', display_name, level='info', action='show_friends')

    def friend_joined_instance(self, display_name: str) -> None:
        self.post('Friend Nearby', f'{display_name} is in your instance', level='info', action='show_friends')

    def friend_joinable(self, display_name: str, world_name: str) -> None:
        self.post('Friend Joinable', f'{display_name} — {world_name}', level='info', action='show_friends_joinable')

    def friend_request(self, sender: str, message: str='') -> None:
        body = message or 'Wants to be your friend'
        self.post('Friend Request', f'{sender}: {body}', level='info', action='show_friends')

    def instance_invite(self, sender: str, world_name: str, message: str='') -> None:
        body = message or world_name
        self.post('Instance Invite', f'{sender} — {body}', level='info', action='show_main')

    def invite_request(self, sender: str, message: str='') -> None:
        body = message or 'Requested an invite'
        self.post('Invite Request', f'{sender}: {body}', level='info', action='show_friends')

    def invite_response(self, sender: str, message: str='') -> None:
        body = message or 'Responded to your invite'
        self.post('Invite Response', f'{sender}: {body}', level='info', action='show_friends')
