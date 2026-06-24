from __future__ import annotations
import unicodedata
from dataclasses import dataclass
from typing import Callable
MAX_PAYLOAD_CHARS = 144
MAX_VISIBLE_LINES = 9
VRCHAT_WRAP_WIDTH_PX = 268.0
ZERO_WIDTH_CHARS = frozenset({'\x03', '\x1f', '\u200b', '\u2060', '\r'})
WALL_FILL_CHAR = 'ㅤ'
EGG_PAIR = '\x03\x1f'
EXTREME_LINE = '\n\u2060'

@dataclass(frozen=True)
class ChatboxLayout:
    lines: tuple[str, ...]
    payload: str
    payload_length: int
    mode: str
    egg_pairs: int
    wall_fill_count: int
    leading_blank_lines: int
    line_count: int
    truncated: bool

def char_width_units(ch: str) -> float:
    if ch in ZERO_WIDTH_CHARS:
        return 0.0
    if ch == WALL_FILL_CHAR:
        return 2.0
    east = unicodedata.east_asian_width(ch)
    if east in ('F', 'W'):
        return 2.0
    if east in ('A',):
        return 2.0
    return 1.0

def strip_egg_suffix(text: str) -> tuple[str, int]:
    pairs = 0
    while len(text) >= 2 and text.endswith(EGG_PAIR):
        text = text[:-len(EGG_PAIR)]
        pairs += 1
    return (text, pairs)

def strip_wall_fill(text: str) -> tuple[str, int]:
    fill = 0
    while text and text[-1] == WALL_FILL_CHAR:
        text = text[:-1]
        fill += 1
    return (text, fill)

def split_extreme_prefix(text: str) -> tuple[int, str]:
    blank_lines = 0
    while text.startswith(EXTREME_LINE):
        blank_lines += 1
        text = text[len(EXTREME_LINE):]
    if blank_lines and text.startswith('\n'):
        blank_lines += 1
        text = text[1:]
    return (blank_lines, text)

def detect_mode(payload: str, egg_pairs: int, wall_fill: int, leading_blank: int) -> str:
    if wall_fill > 0:
        return 'wall_of_china'
    if leading_blank > 0:
        return 'extreme_height'
    if egg_pairs > 0:
        return 'egg'
    return 'normal'

def remove_zero_width(text: str) -> str:
    if not text:
        return ''
    return ''.join((ch for ch in text if ch not in ZERO_WIDTH_CHARS))

def wrap_segment(text: str, max_width_units: float, measure: Callable[[str], float] | None=None) -> list[str]:
    if measure is None:
        measure = lambda s: sum((char_width_units(c) for c in s))
    text = remove_zero_width(text)
    if not text:
        return ['']
    if measure(text) <= max_width_units:
        return [text]
    lines: list[str] = []
    words = text.split(' ')
    current = ''

    def flush() -> None:
        nonlocal current
        if current or not lines:
            lines.append(current)
        current = ''
    for word in words:
        if not word:
            continue
        if not current:
            if measure(word) <= max_width_units:
                current = word
            else:
                buf = ''
                for ch in word:
                    candidate = buf + ch
                    if buf and measure(candidate) > max_width_units:
                        lines.append(buf)
                        buf = ch
                    else:
                        buf = candidate
                current = buf
            continue
        candidate = current + ' ' + word
        if measure(candidate) <= max_width_units:
            current = candidate
        else:
            flush()
            if measure(word) <= max_width_units:
                current = word
            else:
                buf = ''
                for ch in word:
                    candidate = buf + ch
                    if buf and measure(candidate) > max_width_units:
                        lines.append(buf)
                        buf = ch
                    else:
                        buf = candidate
                current = buf
    flush()
    return lines or ['']

def wrap_text(text: str, max_width_units: float, measure: Callable[[str], float] | None=None) -> list[str]:
    if not text:
        return ['']
    lines: list[str] = []
    for part in text.split('\n'):
        lines.extend(wrap_segment(part, max_width_units, measure))
    return lines or ['']

def layout_chatbox_payload(payload: str, max_width_units: float=24.0, measure: Callable[[str], float] | None=None, max_lines: int=MAX_VISIBLE_LINES) -> ChatboxLayout:
    payload = '' if payload is None else str(payload)[:MAX_PAYLOAD_CHARS]
    body, egg_pairs = strip_egg_suffix(payload)
    body, wall_fill = strip_wall_fill(body)
    leading_blank, body = split_extreme_prefix(body)
    mode = detect_mode(payload, egg_pairs, wall_fill, leading_blank)
    wrapped = wrap_text(body, max_width_units, measure)
    lines: list[str] = [''] * leading_blank + wrapped
    truncated = len(lines) > max_lines
    if truncated:
        if mode == 'extreme_height':
            lines = lines[-max_lines:]
        else:
            lines = lines[:max_lines]
    if not lines:
        lines = ['']
    return ChatboxLayout(lines=tuple(lines), payload=payload, payload_length=len(payload), mode=mode, egg_pairs=egg_pairs, wall_fill_count=wall_fill, leading_blank_lines=leading_blank, line_count=len(lines), truncated=truncated)
