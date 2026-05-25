import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal
from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from pythonosc import udp_client
from .config import load_config, message_interval_ms, parse_idle_char, VRCHAT_CHATBOX_MIN_INTERVAL_MS
from .logging_setup import get_logger, is_debug_mode, truncate_for_log
from .osc_client import OscChatClient
from app.preset_storage import load_presets, resolve_preset_name
logger = get_logger('osc')

class OSCHandler(QObject):
    chatbox_sent = pyqtSignal(str, str)
    _IDLE_TINY_CHAR = '\u2060'

    @dataclass
    class _SendJob:
        kind: Literal['chatbox', 'chatbox_raw', 'typing', 'osc']
        text: str | None = None
        fx: bool = False
        address: str | None = None
        arguments: list[Any] | None = None
        frame_index: int | None = None
        frame_text: str | None = None
        tag: str | None = None
        preview_label: str | None = None
        done_event: threading.Event | None = None

    def __init__(self, ip='127.0.0.1', port=9000):
        super().__init__()
        self.settings = QSettings('VRChatOSC', 'Settings')
        self.client = udp_client.SimpleUDPClient(ip, port)
        self.secondary_client = None
        self.is_running = False
        self.current_preset = None
        self.current_frame_index = 0
        self.animation_thread = None
        self.typing_thread = None
        self.media_text = ''
        self.media_enabled = False
        self._last_media_display_sent = ''
        self.insane_egg_mode = False
        self.wall_of_china = False
        self.egg_mode = True
        self._chat_client = OscChatClient()
        self.minimum_delay_ms = VRCHAT_CHATBOX_MIN_INTERVAL_MS
        self._stop_animation_event = threading.Event()
        self._send_cv = threading.Condition()
        self._send_queue: deque[OSCHandler._SendJob] = deque()
        self._send_stop_event = threading.Event()
        self._next_send_time = time.monotonic()
        self._osc_lock = threading.Lock()
        self._sender_thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._sender_thread.start()
        self.idle_tiny_char = self._IDLE_TINY_CHAR
        self.typing_indicator = False
        self.typing_duration = 0
        self.send_blank_when_idle = True
        self.use_secondary_osc = False
        self.message_interval = VRCHAT_CHATBOX_MIN_INTERVAL_MS
        self.blank_message_interval = VRCHAT_CHATBOX_MIN_INTERVAL_MS
        self.load_settings()
        logger.info('OSC handler ready → %s:%s interval=%sms', ip, port, self.message_interval)

    def _exclusive_payload(self) -> str | None:
        if self.wall_of_china:
            return 'ㅤ'
        if self.insane_egg_mode:
            return '\u2060'
        return None

    def is_exclusive_mode_active(self) -> bool:
        return self._exclusive_payload() is not None

    def shutdown(self) -> None:
        logger.info('OSC handler shutting down')
        self._send_stop_event.set()
        with self._send_cv:
            self._send_cv.notify_all()
        try:
            if self._sender_thread.is_alive():
                self._sender_thread.join(timeout=1.0)
        except Exception:
            pass

    def _sender_loop(self) -> None:
        while not self._send_stop_event.is_set():
            with self._send_cv:
                while not self._send_queue and (not self._send_stop_event.is_set()):
                    self._send_cv.wait(timeout=0.5)
                if self._send_stop_event.is_set():
                    return
                now = time.monotonic()
                wait_s = self._next_send_time - now
                if wait_s > 0:
                    self._send_cv.wait(timeout=wait_s)
                    continue
                job = self._send_queue.popleft()
                self._send_cv.notify_all()
            exclusive = self._exclusive_payload()
            kind = job.kind
            text = job.text
            fx = job.fx
            address = job.address
            arguments = job.arguments
            if exclusive is not None and kind in ('chatbox', 'chatbox_raw'):
                kind = 'chatbox'
                text = exclusive
                fx = False
            formatted_payload: str | None = None
            try:
                if kind == 'chatbox':
                    if text is not None:
                        formatted_payload = self._format_outgoing_message(text)
                        self._send_input_message(text, fx)
                elif kind == 'chatbox_raw':
                    if text is not None:
                        formatted_payload = self._format_raw_outgoing_message(text)
                        self._send_raw_message(text)
                elif kind == 'typing':
                    self._send_typing_message(bool(arguments[0]) if arguments else False)
                elif kind == 'osc':
                    if address is not None:
                        self._send_generic_osc(address, arguments or [])
            except Exception:
                logger.warning('Send job failed tag=%s kind=%s', job.tag, kind, exc_info=is_debug_mode())
            finally:
                if job.done_event is not None:
                    try:
                        job.done_event.set()
                    except Exception:
                        pass
            if formatted_payload is not None:
                label = job.preview_label or self._preview_label_for_job(job, text)
                if exclusive is not None and job.kind in ('chatbox', 'chatbox_raw'):
                    label = 'Exclusive'
                logger.debug('Sent [%s] tag=%s kind=%s len=%d → %s', label, job.tag or '-', kind, len(formatted_payload), truncate_for_log(formatted_payload))
                try:
                    self.chatbox_sent.emit(formatted_payload, label)
                except Exception:
                    logger.warning('chatbox_sent emit failed', exc_info=is_debug_mode())
            self._next_send_time = time.monotonic() + self.minimum_delay_ms / 1000.0

    def _queue_job(self, job: 'OSCHandler._SendJob') -> None:
        if self._send_stop_event.is_set():
            return
        with self._send_cv:
            while len(self._send_queue) >= 50 and (not self._send_stop_event.is_set()):
                self._send_cv.wait(timeout=0.5)
            self._send_queue.append(job)
            self._send_cv.notify_all()
        if is_debug_mode() and job.tag != 'animation':
            logger.debug('Queued tag=%s kind=%s queue=%d preview=%s', job.tag or '-', job.kind, len(self._send_queue), truncate_for_log(job.text))

    def _format_raw_outgoing_message(self, message: str) -> str:
        return self._chat_client._base_formatter.format(message)

    def _preview_label_for_job(self, job: _SendJob, text: str | None) -> str:
        if job.preview_label:
            return job.preview_label
        if job.tag == 'animation' and job.frame_index is not None:
            return f'Frame {job.frame_index + 1}'
        if job.tag == 'media':
            return 'Media'
        if job.tag == 'idle':
            if self.insane_egg_mode:
                return 'Extreme Height'
            if self.is_exclusive_mode_active():
                return 'Exclusive'
            return 'Idle'
        if self.is_exclusive_mode_active():
            return 'Exclusive'
        _ = text
        return ''

    def _queue_send(self, text: str, fx: bool, *, raw: bool, frame_index: int | None=None, frame_text: str | None=None, done_event: threading.Event | None=None, tag: str | None=None, preview_label: str | None=None) -> None:
        kind: Literal['chatbox', 'chatbox_raw'] = 'chatbox_raw' if raw else 'chatbox'
        self._queue_job(OSCHandler._SendJob(kind=kind, text=text, fx=fx, frame_index=frame_index, frame_text=frame_text, tag=tag, preview_label=preview_label, done_event=done_event))

    def send_osc_message(self, address: str, arguments: list[Any] | None=None) -> None:
        self._queue_job(OSCHandler._SendJob(kind='osc', address=address, arguments=list(arguments or [])))

    def _send_secondary(self, address: str, arguments: list[Any]) -> None:
        with self._osc_lock:
            client = self.secondary_client
            enabled = self.use_secondary_osc
        if enabled and client is not None:
            try:
                client.send_message(address, list(arguments))
            except Exception:
                logger.debug('Secondary OSC send failed', exc_info=is_debug_mode())

    def _send_generic_osc(self, address: str, arguments: list[Any]) -> None:
        with self._osc_lock:
            primary = self.client
        try:
            primary.send_message(address, list(arguments))
        except Exception:
            logger.debug('Primary OSC send failed', exc_info=is_debug_mode())
        self._send_secondary(address, arguments)

    def _send_typing_message(self, typing: bool) -> None:
        try:
            self._chat_client.send_typing(typing)
        except Exception:
            with self._osc_lock:
                primary = self.client
            primary.send_message('/chatbox/typing', typing)
            self._send_secondary('/chatbox/typing', [typing])

    def _format_outgoing_message(self, message: str) -> str:
        return self._chat_client.format_with_mode(message, egg_mode=self.egg_mode, extreme_height=self.insane_egg_mode, wall_of_china=self.wall_of_china)

    def combine_with_media(self, frame: str) -> str:
        frame = '' if frame is None else str(frame)
        if self.media_enabled and self.media_text:
            return f'{self.media_text}\n{frame}'
        return frame

    def update_media_text(self, display_text: str) -> bool:
        display_text = '' if display_text is None else str(display_text).strip()
        if display_text == self.media_text:
            return False
        logger.info('Media text updated: %s', truncate_for_log(display_text))
        self.media_text = display_text
        self._last_media_display_sent = ''
        return True

    def push_media_to_chatbox(self, force: bool=False) -> bool:
        if not self.media_enabled or not self.media_text:
            return False
        if self.is_exclusive_mode_active():
            return False
        if not force and self.media_text == self._last_media_display_sent:
            return False
        logger.debug('Pushing media to chatbox: %s', truncate_for_log(self.media_text))
        self._last_media_display_sent = self.media_text
        self.send_chatbox_message_async(self.media_text, tag='media', preview_label='Media')
        return True

    def clear_media_text(self) -> None:
        if self.media_text:
            logger.debug('Media text cleared')
        self.media_text = ''
        self._last_media_display_sent = ''

    def format_chatbox_preview(self, message: str, fx: bool=False) -> str:
        _ = fx
        try:
            exclusive = self._exclusive_payload()
            if exclusive is not None:
                message = exclusive
            message = '' if message is None else str(message)
            return self._format_outgoing_message(message)
        except Exception:
            return '' if message is None else str(message)

    def _send_input_message(self, message: str, fx: bool) -> None:
        formatted = self._format_outgoing_message(message)
        try:
            self._chat_client.send_chatbox_raw(formatted, immediate=True, notify=fx)
        except Exception:
            with self._osc_lock:
                primary = self.client
            primary.send_message('/chatbox/input', [formatted, True, fx])
        self._send_secondary('/chatbox/input', [formatted, True, fx])

    def _send_idle_raw_message(self, message: str) -> None:
        if self.is_running:
            return
        self._queue_send(message, fx=False, raw=True, tag='idle', preview_label='Idle')

    def _send_raw_message(self, message: str) -> None:
        try:
            self._chat_client.send_chatbox_raw(message, immediate=True, notify=False)
        except Exception:
            with self._osc_lock:
                primary = self.client
            primary.send_message('/chatbox/input', [message, True, False])
        self._send_secondary('/chatbox/input', [message, True, False])

    def send_chatbox_message(self, message: str, typing: bool=False, fx: bool=False, *, tag: str | None=None, preview_label: str | None=None):
        try:
            exclusive = self._exclusive_payload()
            if exclusive is not None:
                message = exclusive
                typing = False
                fx = False
                preview_label = preview_label or 'Exclusive'
            if typing and self.typing_indicator:
                self.start_typing_indicator()
            self._queue_send(message, fx=fx, raw=False, tag=tag, preview_label=preview_label)
        except Exception as e:
            _ = e

    def send_chatbox_message_async(self, message: str, typing: bool=False, fx: bool=False, *, tag: str | None=None, preview_label: str | None=None):
        self.send_chatbox_message(message, typing=typing, fx=fx, tag=tag, preview_label=preview_label)

    def send_blank_message_async(self):
        self.send_blank_message()

    def start_animation(self, preset_name: str, animation_frames: list) -> bool:
        if self._exclusive_payload() is not None:
            logger.debug('Animation refused — exclusive mode active')
            return False
        try:
            frames = list(animation_frames) if animation_frames is not None else []
        except Exception:
            frames = []
        if not frames:
            logger.warning('Animation refused — no frames for preset %s', preset_name)
            self.is_running = False
            self.current_preset = None
            self.current_frame_index = 0
            return False
        if self.is_running or (self.animation_thread and self.animation_thread.is_alive()):
            self.stop_animation()
        self._stop_animation_event.clear()
        self.is_running = True
        self.current_preset = preset_name
        self.current_frame_index = 0
        logger.info('Animation started preset=%s frames=%d', preset_name, len(frames))

        def animate():
            try:
                frame_index = 0
                while not self._stop_animation_event.is_set():
                    if self._exclusive_payload() is not None:
                        break
                    if frame_index >= len(frames):
                        frame_index = 0
                    if not frames:
                        break
                    frame = frames[frame_index]
                    if frame is None:
                        frame = ''
                    frame = str(frame)
                    self.current_frame_index = frame_index
                    combined_message = self.combine_with_media(frame)
                    done = threading.Event()
                    self._queue_send(combined_message, fx=False, raw=False, frame_index=frame_index, frame_text=frame, done_event=done, tag='animation', preview_label=f'Frame {frame_index + 1}')
                    while not done.is_set() and (not self._stop_animation_event.is_set()):
                        if self._exclusive_payload() is not None:
                            break
                        done.wait(timeout=0.25)
                    frame_index += 1
            except Exception:
                logger.error('Animation thread error', exc_info=True)
            finally:
                logger.info('Animation thread ended preset=%s', preset_name)
                self.is_running = False
        self.animation_thread = threading.Thread(target=animate, daemon=True)
        self.animation_thread.start()
        return True

    def stop_animation(self):
        if not self.is_running and (not (self.animation_thread and self.animation_thread.is_alive())):
            return
        preset = self.current_preset
        logger.info('Stopping animation preset=%s', preset or '(none)')
        self.is_running = False
        self._stop_animation_event.set()
        with self._send_cv:
            kept: deque[OSCHandler._SendJob] = deque()
            dropped = 0
            while self._send_queue:
                old = self._send_queue.popleft()
                if old.tag == 'animation':
                    dropped += 1
                    if old.done_event is not None:
                        try:
                            old.done_event.set()
                        except Exception:
                            pass
                    continue
                kept.append(old)
            self._send_queue = kept
            self._send_cv.notify_all()
        if dropped:
            logger.debug('Dropped %d pending animation frame(s) from queue', dropped)
        if self.animation_thread and self.animation_thread.is_alive():
            self.animation_thread.join(timeout=2.0)
            if self.animation_thread.is_alive():
                pass
        self.animation_thread = None
        self.current_preset = None
        self.current_frame_index = 0
        if self.typing_indicator:
            self.stop_typing_indicator()

    def send_blank_message(self):
        if self.is_running:
            logger.debug('Blank skipped — animation running')
            return
        if self.send_blank_when_idle:
            logger.debug('Sending idle keep-alive')
            if self.wall_of_china:
                self.send_chatbox_message('ㅤ', typing=False, fx=False, tag='idle', preview_label='Exclusive')
            elif self.insane_egg_mode:
                self.send_chatbox_message('\u2060', typing=False, fx=False, tag='idle', preview_label='Extreme Height')
            else:
                try:
                    self._send_idle_raw_message(self.idle_tiny_char)
                except Exception as e:
                    _ = e

    def set_interval(self, ms: int):
        ms = int(ms)
        self.message_interval = max(VRCHAT_CHATBOX_MIN_INTERVAL_MS, ms)
        self.minimum_delay_ms = self.message_interval
        self.blank_message_interval = self.message_interval

    def start_typing_indicator(self):
        if not self.typing_indicator:
            return

        def show_typing():
            try:
                self._queue_job(OSCHandler._SendJob(kind='typing', arguments=[True], tag='typing'))
                time.sleep(max(0, self.typing_duration) / 1000.0)
                self._queue_job(OSCHandler._SendJob(kind='typing', arguments=[False], tag='typing'))
            except Exception as e:
                _ = e
        if self.typing_thread and self.typing_thread.is_alive():
            return
        self.typing_thread = threading.Thread(target=show_typing, daemon=True)
        self.typing_thread.start()

    def stop_typing_indicator(self):
        try:
            self._queue_job(OSCHandler._SendJob(kind='typing', arguments=[False], tag='typing'))
        except Exception as e:
            _ = e

    def load_settings(self):
        from .config import CONFIG_PATH
        with self._osc_lock:
            if CONFIG_PATH.exists():
                config = load_config()
                self.insane_egg_mode = config.get('insane_egg_mode', False)
                self.wall_of_china = config.get('wall_of_china', False)
                self.egg_mode = config.get('egg_mode', True)
                self.use_secondary_osc = bool(config.get('use_secondary_osc', False))
                self.secondary_osc_ip = config.get('secondary_osc_ip', '127.0.0.1')
                self.secondary_osc_port = int(config.get('secondary_osc_port', 9001))
                self.small_delay = config.get('small_delay', True)
                delay_ms = message_interval_ms(config)
                self.message_interval = delay_ms
                self.minimum_delay_ms = delay_ms
                self.blank_message_interval = delay_ms
                self.typing_indicator = False
                self.typing_duration = 0
                self.cooldown_duration = 0
                self.send_blank_when_idle = True
                self.animation_speed = 1.0
                self.idle_tiny_char = parse_idle_char(config.get('idle_tiny_char', self._IDLE_TINY_CHAR), self._IDLE_TINY_CHAR)
                ip = config.get('osc_ip', '127.0.0.1')
                port = int(config.get('osc_port', 9000))
                if port != 9000:
                    logger.warning('OSC port is %s — VRChat receives on 9000 by default (Action Menu → OSC → Enabled)', port)
                self.client = udp_client.SimpleUDPClient(ip, port)
                try:
                    self._chat_client.update_connection(ip, port)
                    self._chat_client.connect()
                except Exception:
                    pass
            else:
                delay_ms = max(VRCHAT_CHATBOX_MIN_INTERVAL_MS, int(self.settings.value('message_interval', VRCHAT_CHATBOX_MIN_INTERVAL_MS)))
                self.message_interval = delay_ms
                self.minimum_delay_ms = delay_ms
                self.blank_message_interval = delay_ms
                self.idle_tiny_char = parse_idle_char(self.settings.value('idle_tiny_char', self._IDLE_TINY_CHAR), self._IDLE_TINY_CHAR)
                ip = self.settings.value('osc_ip', '127.0.0.1')
                port = int(self.settings.value('osc_port', 9000))
                self.client = udp_client.SimpleUDPClient(ip, port)
                try:
                    self._chat_client.update_connection(ip, port)
                except Exception:
                    pass
            if self.use_secondary_osc:
                sec_ip = getattr(self, 'secondary_osc_ip', '127.0.0.1')
                sec_port = int(getattr(self, 'secondary_osc_port', 9001))
                self.secondary_client = udp_client.SimpleUDPClient(sec_ip, sec_port)
            else:
                self.secondary_client = None
        logger.debug('Settings loaded egg=%s extreme=%s wall=%s secondary=%s interval=%sms', self.egg_mode, self.insane_egg_mode, self.wall_of_china, self.use_secondary_osc, self.message_interval)

class PresetAnimations:

    def __init__(self):
        self.animations = {}
        self._sorted_names: list[str] = []
        self.load_animations()

    def load_animations(self):
        self.animations = load_presets()
        self._sorted_names = sorted(self.animations.keys())

    def get_animation_frames(self, preset_name: str) -> list:
        preset_name = resolve_preset_name(preset_name)
        return self.animations.get(preset_name, ['No animation available'])

    def get_available_presets(self) -> list:
        return self._sorted_names
