import sys
from PyQt6.QtWidgets import QApplication
from app.logging_setup import get_debug_mode_from_config, get_logger, hide_console, setup_logging, show_console
from app.main_window import PresetConfigUI
from app.safe_runtime import install_crash_guards, run_shutdown_step, safe_call
from app.services.errors import ErrorBus
from app.services.session_manager import SessionManager, prompt_for_login
from app.vrchat_auth import VRChatSession, restore_session
from app.services.rich_presence import RichPresenceManager
from app.hotkey_manager import HotkeyManager
from app.osc_connection import OscConnectionTracker
from app.widgets.notification_overlay import NotificationOverlay
from app.widgets.system_tray import AppSystemTray
logger = get_logger('startup')

def _warm_config() -> None:
    import json
    from app.config import AUTH_KEYS, CONFIG_PATH, DEFAULT_CONFIG, load_config, write_config
    if not CONFIG_PATH.exists():
        return
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            stored = json.load(f)
        if not isinstance(stored, dict):
            return
        allowed = set(DEFAULT_CONFIG.keys()) | set(AUTH_KEYS) | {'_credentials_warning'}
        if set(stored.keys()) - allowed:
            write_config(load_config())
    except Exception:
        logger.warning('Failed to prune stale config keys', exc_info=True)

def _prompt_for_login(app: QApplication) -> VRChatSession | None:
    _ = app
    return prompt_for_login()

def main() -> int:
    debug_mode = get_debug_mode_from_config()
    if debug_mode:
        show_console()
    setup_logging(debug_mode=debug_mode)
    if debug_mode:
        logger.debug('Console window shown (debug mode)')
    else:
        hide_console()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    install_crash_guards(app)
    session = restore_session()
    if session is None:
        logger.info('No saved VRChat session — showing login window')
        session = _prompt_for_login(app)
        if session is None:
            logger.info('Login cancelled — exiting')
            return 0
    else:
        logger.info('Restored VRChat session for %s', session.display_name)
    safe_call(_warm_config, context='config reorder')

    overlay = NotificationOverlay()
    overlay.reposition()
    window = safe_call(lambda: PresetConfigUI(session=session), context='create main window')
    if window is None:
        logger.error('Failed to create main window — exiting')
        return 1
    SessionManager.instance().set_session(session)
    SessionManager.instance().set_main_window(window)
    tray = AppSystemTray(window, app)
    tray.setup()
    window.set_system_tray(tray)
    HotkeyManager.instance().setup()
    HotkeyManager.instance().preset_triggered.connect(window.play_hotkey_preset)
    shutdown_state = {'done': False}

    def shutdown() -> None:
        if shutdown_state['done']:
            return
        shutdown_state['done'] = True
        logger.info('Application exiting — cleaning up')
        run_shutdown_step('hotkeys', HotkeyManager.instance().cleanup)
        run_shutdown_step('tray', tray.cleanup)
        run_shutdown_step('overlay', overlay.cleanup)
        run_shutdown_step('rich presence', RichPresenceManager.instance().cleanup)
        run_shutdown_step('main window', window.cleanup)
    app.aboutToQuit.connect(shutdown)

    def on_error(text: str, level: str) -> None:
        safe_call(lambda: overlay._on_notify('larpbox', text, level) if level == 'error' else None, context='error overlay notify')
    ErrorBus.instance().message_posted.connect(on_error)
    window.show()
    logger.info('Application window opened')
    try:
        return app.exec()
    except KeyboardInterrupt:
        logger.info('Interrupted — shutting down')
        shutdown()
        return 0
if __name__ == '__main__':
    sys.exit(main())
