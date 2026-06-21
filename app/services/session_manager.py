from __future__ import annotations
from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget
from ..config import clear_auth_session, save_auth_session
from ..logging_setup import get_logger
from ..vrchat_auth import VRChatSession, verify_session
from .auth_errors import AuthSessionError, is_auth_failure, is_rate_limit_error
logger = get_logger('session')
_HEALTH_CHECK_INTERVAL_MS = 120000

class VerifySessionWorker(QThread):
    finished = pyqtSignal(object)

    def __init__(self, session: VRChatSession):
        super().__init__()
        self._session = session

    def run(self) -> None:
        try:
            verified = verify_session(self._session)
            self.finished.emit(verified)
        except AuthSessionError as exc:
            self.finished.emit(exc)
        except Exception as exc:
            self.finished.emit(exc)

def prompt_for_login(parent: QWidget | None=None) -> VRChatSession | None:
    from ..login_window import LoginWindow
    app = QApplication.instance()
    if app is None:
        return None
    login_window = LoginWindow()
    login_window.prepare_for_display(parent, relogin=False)
    from PyQt6.QtCore import QEventLoop
    loop = QEventLoop()
    holder: dict[str, VRChatSession | None] = {'session': None}

    def on_success(session: VRChatSession) -> None:
        holder['session'] = session
        loop.quit()

    def on_cancel() -> None:
        loop.quit()
    login_window.login_succeeded.connect(on_success)
    login_window.login_cancelled.connect(on_cancel)
    login_window.show()
    login_window.raise_()
    login_window.activateWindow()
    loop.exec()
    login_window.login_succeeded.disconnect(on_success)
    login_window.login_cancelled.disconnect(on_cancel)
    session = holder['session']
    if session is not None:
        SessionManager.instance().mark_login_success()
    return session

class SessionManager(QObject):
    session_updated = pyqtSignal(object)
    _instance: SessionManager | None = None

    def __init__(self) -> None:
        super().__init__()
        self._session: VRChatSession | None = None
        self._main_window: QWidget | None = None
        self._login_window = None
        self._relogin_active = False
        self._verify_worker: VerifySessionWorker | None = None
        self._verify_generation = 0
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._run_health_check)

    @classmethod
    def instance(cls) -> SessionManager:
        if cls._instance is None:
            cls._instance = SessionManager()
        return cls._instance

    def set_main_window(self, window: QWidget | None) -> None:
        self._main_window = window

    def set_session(self, session: VRChatSession | None) -> None:
        self._verify_generation += 1
        self._session = session
        if session is not None:
            self.mark_login_success()
            if not self._health_timer.isActive():
                self._health_timer.start(_HEALTH_CHECK_INTERVAL_MS)
        else:
            self._health_timer.stop()
            from ..vrchat.core import clear_session_caches
            clear_session_caches()

    @property
    def session(self) -> VRChatSession | None:
        return self._session

    def is_relogin_active(self) -> bool:
        return self._relogin_active

    def mark_login_success(self) -> None:
        if self._session is None:
            return
        from ..vrchat.core import clear_session_caches
        from ..image_loader import RemoteImageLabel
        clear_session_caches()
        try:
            from ..avatar_cache import refresh_from_logs
            refresh_from_logs()
        except Exception:
            logger.debug('Avatar cache log refresh failed', exc_info=True)
        RemoteImageLabel.set_session(self._session)
        window = self._main_window
        if window is None:
            return
        from PyQt6.QtCore import QTimer
        from ..config import get_bool, load_config

        def _refresh_panels() -> None:
            if hasattr(window, 'friends_list'):
                window.friends_list.refresh(full=True)
            if hasattr(window, 'player_list'):
                window.player_list.refresh()
            avatar_tools = getattr(window, 'avatar_search', None)
            wardrobe = getattr(avatar_tools, 'wardrobe', None) if avatar_tools is not None else None
            if wardrobe is not None:
                wardrobe.refresh()
            if hasattr(window, 'account_info'):
                window.account_info.refresh()
            if get_bool(load_config().get('enable_discord_presence', False)):
                player_list = getattr(window, 'player_list', None)
                instance = getattr(player_list, '_current_instance', None) if player_list is not None else None
                if instance is not None:
                    from .rich_presence import RichPresenceManager
                    RichPresenceManager.instance().update_instance(instance)
        QTimer.singleShot(0, _refresh_panels)

    def try_handle_auth_failure(self, value: BaseException | str | None) -> bool:
        if self._relogin_active:
            return True
        if is_rate_limit_error(value):
            return False
        if not is_auth_failure(value):
            return False
        self.request_relogin(str(value).strip() or 'VRChat session expired.')
        return True

    def request_relogin(self, reason: str) -> None:
        if self._relogin_active or is_rate_limit_error(reason):
            return
        self._begin_relogin(reason)

    def _begin_relogin(self, reason: str) -> None:
        self._relogin_active = True
        self._verify_generation += 1
        self._health_timer.stop()
        logger.warning('VRChat auth invalid — showing login: %s', reason)
        clear_auth_session()
        self._session = None
        self.session_updated.emit(None)
        QTimer.singleShot(0, lambda: self._show_login_window(reason))

    def _run_health_check(self) -> None:
        if self._relogin_active or self._session is None:
            return
        if self._verify_worker and self._verify_worker.isRunning():
            return
        generation = self._verify_generation
        self._verify_worker = VerifySessionWorker(self._session)
        self._verify_worker.finished.connect(lambda result, gen=generation: self._on_health_check_finished(result, gen))
        self._verify_worker.start()

    def _on_health_check_finished(self, result: object, generation: int) -> None:
        if generation != self._verify_generation or self._relogin_active or self._session is None:
            return
        if isinstance(result, VRChatSession):
            tokens_changed = result.auth_token != self._session.auth_token or result.two_factor_token != self._session.two_factor_token
            if tokens_changed:
                logger.debug('Refreshed VRChat session tokens from health check')
            self._session = result
            if tokens_changed:
                if get_bool(load_config().get('remember_login')):
                    save_auth_session(
                        auth_token=result.auth_token,
                        two_factor_token=result.two_factor_token,
                        username=result.username,
                        display_name=result.display_name,
                        user_id=result.user_id,
                        remember_login=True,
                    )
                self.session_updated.emit(result)
            return
        if isinstance(result, AuthSessionError) and (not is_rate_limit_error(result)):
            self.request_relogin(str(result))
        elif isinstance(result, Exception):
            from .errors import ErrorBus
            message = str(result).strip() or type(result).__name__
            ErrorBus.instance().warning(f'VRChat session check failed: {message[:160]}')

    def _show_login_window(self, reason: str) -> None:
        from ..login_window import LoginWindow
        parent = self._main_window
        if parent is not None:
            if not parent.isVisible():
                parent.show()
            parent.raise_()
        if self._login_window is None:
            self._login_window = LoginWindow()
            self._login_window.login_succeeded.connect(self._on_login_success)
            self._login_window.login_cancelled.connect(self._on_login_dismissed)
        self._login_window.prepare_for_display(parent, relogin=True, reason=reason)
        self._login_window.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self._login_window.show()
        self._login_window.raise_()
        self._login_window.activateWindow()

    def _on_login_success(self, session: VRChatSession) -> None:
        self._relogin_active = False
        self._session = session
        if not self._health_timer.isActive():
            self._health_timer.start(_HEALTH_CHECK_INTERVAL_MS)
        if self._login_window is not None:
            self._login_window.hide()
        self.session_updated.emit(session)
        logger.info('Re-login succeeded for %s', session.display_name)

    def _on_login_dismissed(self) -> None:
        self._relogin_active = False
        if self._login_window is not None:
            self._login_window.hide()
        logger.info('Re-login dismissed — VRChat features remain signed out')
