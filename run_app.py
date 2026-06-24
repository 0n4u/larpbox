import sys
from PyQt6.QtWidgets import QApplication
from app.logging_setup import get_debug_mode_from_config, get_logger, hide_console, setup_logging, show_console, shutdown_logging
from app.main_window import PresetConfigUI
from app.safe_runtime import install_crash_guards, run_shutdown_step, safe_call
from app.services.errors import ErrorBus
from app.services.session_manager import SessionManager, prompt_for_login
from app.vrchat_auth import VRChatSession, restore_session
from app.services.rich_presence import RichPresenceManager
from app.services.activity_service import ActivityService
from app.hotkey_manager import HotkeyManager
from app.widgets.notification_overlay import NotificationOverlay
from app.widgets.system_tray import AppSystemTray
logger = get_logger('startup')

def _warm_config() -> None:
    import json
    from app.config import CONFIG_PATH, DEFAULT_CONFIG, load_config, write_config
    if not CONFIG_PATH.exists():
        return
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            stored = json.load(f)
        if not isinstance(stored, dict):
            return
        if set(stored.keys()) - set(DEFAULT_CONFIG.keys()):
            write_config(load_config())
    except Exception:
        logger.warning('Failed to prune stale config keys', exc_info=True)

def _flush_wardrobe_cache() -> None:
    from app.wardrobe_cache import flush_cache
    flush_cache()

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
    from app.services import crash_recovery
    prior_crash = crash_recovery.detect_unclean_shutdown()
    crash_recovery.mark_running()
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

    def _apply_autostart() -> None:
        from app.services.autostart import apply_from_config
        apply_from_config()
    safe_call(_apply_autostart, context='autostart sync')

    overlay = NotificationOverlay()
    overlay.reposition()
    window = safe_call(lambda: PresetConfigUI(session=session), context='create main window')
    if window is None:
        logger.error('Failed to create main window — exiting')
        return 1
    overlay.friend_filter_requested.connect(window.show_friends_with_filter)
    SessionManager.instance().set_main_window(window)
    safe_call(ActivityService.instance().start, context='activity service start')
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
        run_shutdown_step('activity service', ActivityService.instance().stop)
        run_shutdown_step('main window', window.cleanup)
        run_shutdown_step('wardrobe cache', _flush_wardrobe_cache)
        run_shutdown_step('logging', shutdown_logging)
    app.aboutToQuit.connect(shutdown)

    def on_error(text: str, level: str) -> None:
        safe_call(lambda: overlay._on_notify('larpbox', text, level) if level == 'error' else None, context='error overlay notify')
    ErrorBus.instance().message_posted.connect(on_error)
    window.show()
    logger.info('Application window opened')
    if prior_crash:
        logger.warning('Previous session did not exit cleanly — recovered')

        def _notify_recovery() -> None:
            from app.services.notifications import NotificationBus
            NotificationBus.instance().post(
                'Recovered', 'larpbox restarted after an unexpected shutdown.',
                level='info', action='show_main',
            )
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: safe_call(_notify_recovery, context='crash recovery notice'))
    try:
        exit_code = app.exec()
        crash_recovery.mark_clean_exit()
        return exit_code
    except KeyboardInterrupt:
        logger.info('Interrupted — shutting down')
        shutdown()
        crash_recovery.mark_clean_exit()
        return 0
    except Exception:
                                                                                
                                                                              
        logger.critical('Fatal error escaped the event loop', exc_info=True)
        shutdown()
        from app.config import get_bool, load_config
        if get_bool(load_config().get('auto_restart_on_crash', False)) and crash_recovery.within_restart_budget():
            attempt = crash_recovery.register_restart()
            logger.warning('Attempting auto-restart (#%s)', attempt)
            crash_recovery.relaunch_detached()
        return 1
if __name__ == '__main__':
    sys.exit(main())
