import sys

from PyQt6.QtCore import QEventLoop
from PyQt6.QtWidgets import QApplication

from app.logging_setup import get_debug_mode_from_config, get_logger, hide_console, setup_logging, show_console
from app.login_window import LoginWindow
from app.main_window import PresetConfigUI
from app.vrchat_auth import VRChatSession, restore_session

logger = get_logger('startup')


def _prompt_for_login(app: QApplication) -> VRChatSession | None:
    login_window = LoginWindow()
    loop = QEventLoop()
    session_holder: dict[str, VRChatSession | None] = {'session': None}

    def on_success(session: VRChatSession) -> None:
        session_holder['session'] = session
        loop.quit()

    login_window.login_succeeded.connect(on_success)
    login_window.login_cancelled.connect(loop.quit)
    login_window.show()
    loop.exec()
    return session_holder['session']


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
        from app.avatar_cache import refresh_from_logs

        added = refresh_from_logs()
        if added:
            logger.info('Avatar cache warmed from logs (%d new entries)', added)
    except Exception:
        logger.debug('Avatar cache warm-up failed', exc_info=True)

    window = PresetConfigUI(session=session)
    window.show()
    logger.info('Application window opened')
    try:
        return app.exec()
    except KeyboardInterrupt:
        logger.info('Interrupted — shutting down')
        try:
            window.cleanup()
        except Exception:
            pass
        return 0


if __name__ == '__main__':
    sys.exit(main())
