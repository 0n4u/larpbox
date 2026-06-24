from __future__ import annotations
from dataclasses import dataclass
from typing import Any
_STATUS_STYLES: dict[str, tuple[str, str]] = {'active': ('Online', '#4cd964'), 'join me': ('Join Me', '#4ea3ff'), 'ask me': ('Ask Me', '#e8a040'), 'busy': ('Do Not Disturb', '#e07070'), 'offline': ('Offline', '#6a6a6a'), 'invisible': ('Invisible', '#c084fc'), 'private': ('Private', '#9aa0a6')}

@dataclass(frozen=True)
class UserStatusInfo:
    key: str
    label: str
    color: str
    in_world: bool = False
    location: str | None = None

    @property
    def is_online(self) -> bool:
        return self.key not in ('offline',)

def status_dot_style(color: str, *, size: int=8) -> str:
    radius = max(2, size // 2)
    return f'background-color: {color}; border: 1px solid rgba(0, 0, 0, 0.45); border-radius: {radius}px; min-width: {size}px; max-width: {size}px; min-height: {size}px; max-height: {size}px;'

def _state_value(user: Any) -> str:
    state = getattr(user, 'state', None)
    if state is None:
        return ''
    return str(getattr(state, 'value', state)).strip().lower()

def _status_text(user: Any) -> str:
    for source in (user, getattr(user, 'presence', None)):
        if source is None:
            continue
        value = str(getattr(source, 'status', '') or '').strip().lower()
        if value:
            return value
    return ''

def _location_from_presence(user: Any) -> str | None:
    presence = getattr(user, 'presence', None)
    if presence is None:
        return None
    world = str(getattr(presence, 'world', '') or '').strip()
    instance = str(getattr(presence, 'instance', '') or '').strip()
    if instance.startswith('wrld_') and ':' in instance:
        return instance
    if world.startswith('wrld_') and instance:
        return f'{world}:{instance}'
    return None

def resolve_user_location(user: Any, *, log_fallback: str | None=None) -> str | None:
    location_attr = getattr(user, 'location', None)
    if isinstance(location_attr, str):
        location = location_attr.strip()
        if location and (not location.casefold().startswith('offline')):
            return location
    presence_location = _location_from_presence(user)
    if presence_location:
        return presence_location
    if log_fallback:
        fallback = log_fallback.strip()
        if fallback and (not fallback.casefold().startswith('offline')):
            return fallback
    if _state_value(user) == 'offline':
        return 'offline'
    return None

def user_status_from_user(user: Any, *, log_fallback: str | None=None) -> UserStatusInfo:
    location = resolve_user_location(user, log_fallback=log_fallback)
    status_text = _status_text(user)
    in_world = bool(location and (not location.casefold().startswith('offline')))
    if location and location.casefold().startswith('private'):
        return UserStatusInfo(key='private', label='Private', color=_STATUS_STYLES['private'][1], in_world=True, location=location)
    if in_world and status_text == 'offline':
        return UserStatusInfo(key='invisible', label='Invisible', color=_STATUS_STYLES['invisible'][1], in_world=True, location=location)
    if status_text in _STATUS_STYLES:
        label, color = _STATUS_STYLES[status_text]
        return UserStatusInfo(key=status_text.replace(' ', '_'), label=label, color=color, in_world=in_world, location=location if in_world else None)
    if in_world:
        label, color = _STATUS_STYLES['active']
        return UserStatusInfo(key='active', label=label, color=color, in_world=True, location=location)
    label, color = _STATUS_STYLES['offline']
    return UserStatusInfo(key='offline', label=label, color=color, in_world=False, location=None)
