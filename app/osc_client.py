from __future__ import annotations
from dataclasses import dataclass
from pythonosc import udp_client

class ChatboxMessageFormatter:
    MAX_LENGTH = 144

    def format(self, text: str) -> str:
        if len(text) > self.MAX_LENGTH:
            return text[:self.MAX_LENGTH]
        return text

class MessageFormatter:

    def format(self, text: str) -> str:
        return text

class RateLimiter:

    def check(self) -> bool:
        return True

    def record(self) -> None:
        return None

class _PythonOscTransport:

    def __init__(self, host: str='127.0.0.1', port: int=9000) -> None:
        self._client = udp_client.SimpleUDPClient(host, port)

    def send_chatbox(self, text: str, immediate: bool, notify: bool) -> None:
        self._client.send_message('/chatbox/input', [text, immediate, notify])

    def send_typing(self, typing: bool) -> None:
        self._client.send_message('/chatbox/typing', [typing])

class DisplayModeFormatter(MessageFormatter):

    def __init__(self, base: ChatboxMessageFormatter, mode_strategy) -> None:
        self._base = base
        self._strategy = mode_strategy

    def format(self, text: str) -> str:
        return self._strategy.apply(self._base.format(text))

@dataclass
class OscConnectionConfig:
    host: str = '127.0.0.1'
    port: int = 9000
    secondary_host: str | None = None
    secondary_port: int | None = None
    use_secondary: bool = False

class EggModeStrategy:

    def apply(self, text: str) -> str:
        egg_chars = '\x03\x1f'
        available = ChatboxMessageFormatter.MAX_LENGTH - len(text)
        pairs_to_add = max(0, min(50, available // 2))
        return text + egg_chars * pairs_to_add

class ExtremeHeightStrategy:
    _LINE = '\n\u2060'

    def apply(self, text: str) -> str:
        available_chars = ChatboxMessageFormatter.MAX_LENGTH - len(text)
        line_count = available_chars // 2
        remainder = available_chars % 2
        prefix = self._LINE * line_count
        if remainder == 1:
            prefix += '\n'
        return prefix + text

class WallOfChinaStrategy:
    FILL_CHAR = 'ㅤ'

    def __init__(self, extreme: ExtremeHeightStrategy) -> None:
        self._extreme = extreme

    def apply(self, text: str) -> str:
        base = _strip_egg_control_chars(text)
        max_len = ChatboxMessageFormatter.MAX_LENGTH
        if len(base) >= max_len:
            return base[:max_len]
        fill_len = max_len - len(base)
        return base + self.FILL_CHAR * fill_len

def _strip_egg_control_chars(text: str) -> str:
    return text.replace('\x03', '').replace('\x1f', '')

class PassthroughStrategy:

    def apply(self, text: str) -> str:
        return text

class OscChatClient:

    def __init__(self, host: str='127.0.0.1', port: int=9000, formatter: MessageFormatter | None=None, rate_limiter: RateLimiter | None=None) -> None:
        base_formatter = formatter or ChatboxMessageFormatter()
        display_formatter = DisplayModeFormatter(base_formatter, PassthroughStrategy())
        self._egg_strategy = EggModeStrategy()
        self._extreme_strategy = ExtremeHeightStrategy()
        self._wall_of_china_strategy = WallOfChinaStrategy(self._extreme_strategy)
        self._base_formatter = base_formatter
        self._display_formatter = display_formatter
        self._rate_limiter = rate_limiter or RateLimiter()
        self._transport = _PythonOscTransport(host, port)
        self._is_connected = False

    def connect(self) -> None:
        self._is_connected = True

    def disconnect(self) -> None:
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def update_connection(self, host: str, port: int) -> None:
        self._transport = _PythonOscTransport(host, port)

    def send_chatbox_raw(self, text: str, immediate: bool=True, notify: bool=False) -> None:
        if not self._rate_limiter.check():
            return
        formatted = self._base_formatter.format(text)
        try:
            self._transport.send_chatbox(formatted, immediate=immediate, notify=notify)
            self._rate_limiter.record()
            from .osc_connection import OscConnectionTracker
            OscConnectionTracker.instance().record_success()
        except Exception as exc:
            from .osc_connection import OscConnectionTracker
            OscConnectionTracker.instance().record_failure(str(exc))
            raise

    def format_with_mode(self, text: str, egg_mode: bool, extreme_height: bool, wall_of_china: bool=False) -> str:
        formatted = self._base_formatter.format(text)
        if wall_of_china:
            return self._wall_of_china_strategy.apply(formatted)
        if extreme_height:
            return self._extreme_strategy.apply(_strip_egg_control_chars(formatted))
        if egg_mode:
            return self._egg_strategy.apply(formatted)
        return formatted

    def send_chatbox_with_mode(self, text: str, egg_mode: bool, extreme_height: bool, wall_of_china: bool=False, immediate: bool=True, notify: bool=False) -> None:
        formatted = self.format_with_mode(text, egg_mode=egg_mode, extreme_height=extreme_height, wall_of_china=wall_of_china)
        if not self._rate_limiter.check():
            return
        try:
            self._transport.send_chatbox(formatted, immediate=immediate, notify=notify)
            self._rate_limiter.record()
            from .osc_connection import OscConnectionTracker
            OscConnectionTracker.instance().record_success()
        except Exception as exc:
            from .osc_connection import OscConnectionTracker
            OscConnectionTracker.instance().record_failure(str(exc))
            raise

    def send_typing(self, typing: bool) -> None:
        try:
            self._transport.send_typing(typing)
            from .osc_connection import OscConnectionTracker
            OscConnectionTracker.instance().record_success()
        except Exception as exc:
            from .osc_connection import OscConnectionTracker
            OscConnectionTracker.instance().record_failure(str(exc))
