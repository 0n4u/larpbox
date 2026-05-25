def format_status_label(*, playing_preset: str | None=None, wall_of_china: bool=False, insane_egg_mode: bool=False, exclusive_mode: bool=False, media_enabled: bool=False, media_text: str='', presets_disabled: bool=False, failed_start: bool=False) -> str:
    if presets_disabled:
        return 'Status: Presets disabled in Exclusive Mode'
    if failed_start:
        return 'Status: Failed to start preset'
    if playing_preset:
        return f'Status: Playing {playing_preset}'
    if exclusive_mode:
        if wall_of_china:
            return 'Status: WALL OF CHINA (Exclusive)'
        return 'Status: EXTREME HEIGHT (Exclusive)'
    if insane_egg_mode:
        return 'Status: EXTREME HEIGHT MODE (MAXIMUM)'
    if media_enabled and media_text:
        return 'Status: Playing (Media)'
    return 'Status: Idle'
