import sys
from PyQt6.QtWidgets import QApplication
from app.logging_setup import get_debug_mode_from_config, get_logger, hide_console, setup_logging, show_console
from app.main_window import PresetConfigUI
from app.services.errors import ErrorBus
from app.services.session_manager import SessionManager, prompt_for_login
from app.vrchat_auth import VRChatSession, restore_session
from app.widgets.notification_overlay import NotificationOverlay
from app.widgets.system_tray import AppSystemTray
logger = get_logger('startup')

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
    app.setQuitOnLastWindowClosed(True)
    session = restore_session()
    if session is None:
        logger.info('No saved VRChat session — showing login window')
        session = _prompt_for_login(app)
        if session is None:
            logger.info('Login cancelled — exiting')
            return 0
    else:
        logger.info('Restored VRChat session for %s', session.display_name)
    try:
        from app.config import load_config, write_config
        write_config(load_config())
    except Exception:
        logger.debug('Config reorder skipped', exc_info=True)
    try:
        from app.avatar_cache import refresh_from_logs
        added = refresh_from_logs()
        if added:
            logger.info('Avatar cache warmed from logs (%d new entries)', added)
    except Exception:
        logger.debug('Avatar cache warm-up failed', exc_info=True)
    overlay = NotificationOverlay()
    overlay.reposition()
    window = PresetConfigUI(session=session)
    SessionManager.instance().set_session(session)
    SessionManager.instance().set_main_window(window)
    tray = AppSystemTray(window, app)
    tray.setup()
    shutdown_state = {'done': False}

    def shutdown() -> None:
        if shutdown_state['done']:
            return
        shutdown_state['done'] = True
        logger.info('Application exiting — cleaning up')
        tray.cleanup()
        overlay.cleanup()
        window.cleanup()
    app.aboutToQuit.connect(shutdown)

    def on_error(text: str, level: str) -> None:
        if level == 'error':
            overlay._on_notify('larpbox', text, level)
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
