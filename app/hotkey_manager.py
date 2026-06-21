from __future__ import annotations
import sys
from PyQt6.QtCore import QAbstractNativeEventFilter, QAbstractEventDispatcher, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
from .config import get_bool, load_config
from .logging_setup import get_logger

logger = get_logger('hotkeys')
_WM_HOTKEY = 0x0312
_MOD_CONTROL = 0x0002
_MOD_ALT = 0x0001
_MOD_NOREPEAT = 0x4000
_HOTKEY_BASE_ID = 0x4C41        
_SLOT_COUNT = 5


class _HotkeyNativeFilter(QAbstractNativeEventFilter):

    def __init__(self, manager: HotkeyManager) -> None:
        super().__init__()
        self._manager = manager

    def nativeEventFilter(self, eventType, message):
        if sys.platform != 'win32':
            return False, 0
        if eventType != b'windows_generic_MSG':
            return False, 0
        try:
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
        except Exception:
            return False, 0
        if msg.message != _WM_HOTKEY:
            return False, 0
        hotkey_id = int(msg.wParam)
        slot = hotkey_id - _HOTKEY_BASE_ID
        if 0 <= slot < _SLOT_COUNT:
            self._manager._on_hotkey(slot)
        return False, 0


class HotkeyManager(QObject):
    preset_triggered = pyqtSignal(int)
    _instance: HotkeyManager | None = None

    def __init__(self) -> None:
        super().__init__()
        self._filter: _HotkeyNativeFilter | None = None
        self._registered: set[int] = set()

    @classmethod
    def instance(cls) -> HotkeyManager:
        if cls._instance is None:
            cls._instance = HotkeyManager()
        return cls._instance

    def setup(self) -> None:
        if sys.platform != 'win32':
            return
        app = QApplication.instance()
        if app is None:
            return
        if self._filter is None:
            self._filter = _HotkeyNativeFilter(self)
            QAbstractEventDispatcher.instance().installNativeEventFilter(self._filter)
        self.reload_bindings()

    def cleanup(self) -> None:
        self.unregister_all()
        if self._filter is not None:
            dispatcher = QAbstractEventDispatcher.instance()
            if dispatcher is not None:
                dispatcher.removeNativeEventFilter(self._filter)
            self._filter = None

    def reload_bindings(self) -> None:
        self.unregister_all()
        if sys.platform != 'win32':
            return
        if not get_bool(load_config().get('enable_hotkeys', True)):
            return
        config = load_config()
        for slot in range(_SLOT_COUNT):
            preset = str(config.get(f'hotkey_preset_{slot + 1}', '') or '').strip()
            if preset:
                self._register_slot(slot)

    def preset_for_slot(self, slot: int) -> str:
        if slot < 0 or slot >= _SLOT_COUNT:
            return ''
        return str(load_config().get(f'hotkey_preset_{slot + 1}', '') or '').strip()

    def _register_slot(self, slot: int) -> None:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hotkey_id = _HOTKEY_BASE_ID + slot
            modifiers = _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT
            vk = 0x30 + ((slot + 1) % 10)                 
            if slot < 4:
                vk = 0x31 + slot
            if user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                self._registered.add(slot)
            else:
                logger.debug('Failed to register hotkey slot %d', slot + 1)
        except Exception:
            logger.debug('Hotkey registration failed for slot %d', slot + 1, exc_info=True)

    def unregister_all(self) -> None:
        if sys.platform != 'win32':
            self._registered.clear()
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            for slot in list(self._registered):
                user32.UnregisterHotKey(None, _HOTKEY_BASE_ID + slot)
        except Exception:
            logger.debug('Hotkey unregister failed', exc_info=True)
        self._registered.clear()

    def _on_hotkey(self, slot: int) -> None:
        if not get_bool(load_config().get('enable_hotkeys', True)):
            return
        preset = self.preset_for_slot(slot)
        if not preset:
            return
        from .services.vrchat_window import find_vrchat_window
        if find_vrchat_window() is None:
            logger.debug('Hotkey ignored — VRChat not running')
            return
        self.preset_triggered.emit(slot)
