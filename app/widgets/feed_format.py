from __future__ import annotations
import time

_INSTANCE_TYPE_LABELS = {
    'public': 'Public',
    'friends': 'Friends',
    'friends+': 'Friends+',
    'invite': 'Invite',
    'invite+': 'Invite+',
    'group': 'Group',
}


def format_relative_time(ts: float, *, now: float | None = None) -> str:
    current = now if now is not None else time.time()
    delta = current - float(ts or 0)
    if delta < 0:
        delta = 0
    if delta < 45:
        return 'just now'
    if delta < 3600:
        return f'{int(delta // 60)}m ago'
    if delta < 86400:
        return f'{int(delta // 3600)}h ago'
    if delta < 7 * 86400:
        return f'{int(delta // 86400)}d ago'
    return time.strftime('%b %d', time.localtime(ts))


def format_abs_time(ts: float) -> str:
    try:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(ts)))
    except (ValueError, OSError):
        return ''


def format_instance_type(instance_type: str) -> str:
    if not instance_type:
        return ''
    base = instance_type.split('+')[0].split('~')[0]
    if instance_type.startswith('group'):
        return 'Group'
    return _INSTANCE_TYPE_LABELS.get(instance_type, _INSTANCE_TYPE_LABELS.get(base, instance_type.title()))


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f'{seconds}s'
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes}m'
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f'{hours}h {minutes}m'
    days, hours = divmod(hours, 24)
    return f'{days}d {hours}h'
