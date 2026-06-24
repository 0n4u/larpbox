from __future__ import annotations
import json
import threading
import time
import urllib.request
from typing import Any
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from ..logging_setup import get_logger

logger = get_logger('server_status')

_STATUS_URL = 'https://status.vrchat.com/api/v2/summary.json'
_USER_AGENT = 'larpbox/1.0'
_POLL_INTERVAL_SEC = 300.0

_INDICATOR_LABELS = {
    'none': 'All systems operational',
    'minor': 'Minor outage',
    'major': 'Major outage',
    'critical': 'Critical outage',
    'maintenance': 'Under maintenance',
}


def parse_status_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {'indicator': 'unknown', 'description': 'Unknown', 'components': []}
    status = payload.get('status')
    if not isinstance(status, dict):
        return {'indicator': 'unknown', 'description': 'Unknown', 'components': []}
    indicator = str(status.get('indicator') or 'unknown').strip().lower()
    description = str(status.get('description') or _INDICATOR_LABELS.get(indicator, 'Unknown')).strip()
    components: list[dict[str, str]] = []
    raw_components = payload.get('components')
    if isinstance(raw_components, list):
        for comp in raw_components:
            if not isinstance(comp, dict):
                continue
                                                                         
            if comp.get('group') is True:
                continue
            name = str(comp.get('name') or '').strip()
            if not name:
                continue
            components.append({'name': name, 'status': str(comp.get('status') or 'unknown').strip().lower()})
    return {'indicator': indicator, 'description': description, 'components': components}


def fetch_status(*, timeout: float = 8.0) -> dict[str, Any]:
    request = urllib.request.Request(_STATUS_URL, headers={'User-Agent': _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode('utf-8'))
    return parse_status_summary(payload if isinstance(payload, dict) else {})


class ServerStatusService(QThread):
    """Polls the VRChat status page periodically and caches the latest summary."""

    updated = pyqtSignal(dict)
    _instance: 'ServerStatusService | None' = None

    @classmethod
    def instance(cls) -> 'ServerStatusService':
        if cls._instance is None:
            cls._instance = ServerStatusService()
        return cls._instance

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last: dict[str, Any] = {'indicator': 'unknown', 'description': 'Checking…', 'components': []}

    def last(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
                                                                   
        self._stop.clear()
        while not self._stop.is_set():
            try:
                summary = fetch_status()
                with self._lock:
                    self._last = summary
                self.updated.emit(summary)
            except Exception:
                logger.debug('Server status fetch failed', exc_info=True)
            self._stop.wait(_POLL_INTERVAL_SEC)
