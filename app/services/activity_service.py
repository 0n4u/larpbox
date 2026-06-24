from __future__ import annotations
from typing import Any
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from ..config import get_bool, load_config
from ..logging_setup import get_logger
from .event_bus import VrcEventBus
from .log_watcher import LogWatcher
from .store_recorder import VrcStoreRecorder

logger = get_logger('store')


class _BackfillWorker(QThread):
    done = pyqtSignal(dict)

    def run(self) -> None:
        result: dict[str, Any]
        try:
            from .log_backfill import run_backfill
            result = run_backfill()
        except Exception:
            logger.warning('Log backfill failed', exc_info=True)
            result = {'error': True}
        self.done.emit(result)


class ActivityService(QObject):
    """Bootstraps the persistence pipeline: recorder + backfill + watcher + websocket.

    Everything here is flag-gated and degrades gracefully: a missing database,
    a missing websocket dependency, or a failed backfill never stops the rest
    of the app from running.
    """

    _instance: 'ActivityService | None' = None

    @classmethod
    def instance(cls) -> 'ActivityService':
        if cls._instance is None:
            cls._instance = ActivityService()
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self._bus = VrcEventBus.instance()
        self._recorder: VrcStoreRecorder | None = None
        self._recorder_thread: QThread | None = None
        self._watcher: LogWatcher | None = None
        self._pipeline: Any = None
        self._overlay: Any = None
        self._server_status: Any = None
        self._backfill_worker: _BackfillWorker | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
                                                                               
                                                                            
        self._maybe_start_overlay()
        self._maybe_start_server_status()
        if not get_bool(load_config().get('enable_activity_log', True)):
            logger.info('Activity logging disabled by config')
            return
        try:
            from ..store.database import get_db
            get_db()
        except Exception:
            logger.warning('Store unavailable — activity logging disabled', exc_info=True)
            return
        self._started = True
        self._recorder_thread = QThread()
        self._recorder_thread.setObjectName('larpbox-recorder')
        self._recorder = VrcStoreRecorder(self._bus)
        self._recorder.moveToThread(self._recorder_thread)
        self._recorder_thread.start()
        self._start_watcher()
        self._backfill_worker = _BackfillWorker()
        self._backfill_worker.done.connect(self._on_backfill_done)
        self._backfill_worker.start()
        self._maybe_start_pipeline()
        logger.info('Activity service started')

    def _on_backfill_done(self, result: dict[str, Any]) -> None:
        if result.get('error'):
            logger.debug('Backfill reported an error (live watcher already running)')

    def _start_watcher(self) -> None:
        if self._watcher is not None:
            return
        self._watcher = LogWatcher(self._bus)
        self._watcher.start()

    def _maybe_start_pipeline(self) -> None:
        if self._pipeline is not None:
            return
        if not get_bool(load_config().get('enable_pipeline', False)):
            return
        from .session_manager import SessionManager

        def token_provider() -> str | None:
            session = SessionManager.instance().session
            return session.auth_token if session is not None else None

        from .vrc_pipeline import VrcPipeline
        self._pipeline = VrcPipeline(token_provider, self._bus)
        self._pipeline.start()
        from .friend_avatar_tracker import FriendAvatarTracker
        FriendAvatarTracker.instance().start()
        logger.info('VRChat pipeline enabled')

    def _maybe_start_overlay(self) -> None:
        if self._overlay is not None:
            return
        if not get_bool(load_config().get('enable_vr_overlay', False)):
            return
        try:
            from .vr_overlay import VrOverlayService
            self._overlay = VrOverlayService(self._bus)
            self._overlay.start()
            logger.info('VR overlay service starting')
        except Exception:
            logger.debug('Could not start VR overlay service', exc_info=True)
            self._overlay = None

    def _maybe_start_server_status(self) -> None:
        if self._server_status is not None:
            return
        if not get_bool(load_config().get('enable_server_status', True)):
            return
        try:
            from .server_status import ServerStatusService
            self._server_status = ServerStatusService.instance()
            self._server_status.start()
        except Exception:
            logger.debug('Could not start server status service', exc_info=True)
            self._server_status = None

    def stop(self) -> None:
        if self._server_status is not None:
            self._server_status.stop()
            self._server_status.wait(3000)
            self._server_status = None
        if self._overlay is not None:
            self._overlay.stop()
            self._overlay.wait(3000)
            self._overlay = None
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher.wait(3000)
            self._watcher = None
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline.wait(3000)
            self._pipeline = None
        if self._backfill_worker is not None and self._backfill_worker.isRunning():
            self._backfill_worker.wait(5000)
        if self._recorder_thread is not None:
            self._recorder_thread.quit()
            self._recorder_thread.wait(3000)
            self._recorder_thread = None
        self._recorder = None
        try:
            from ..store.database import close_db
            close_db()
        except Exception:
            logger.debug('Error closing store', exc_info=True)
        self._started = False
