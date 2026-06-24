from __future__ import annotations
from typing import Any, Callable
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)
from ..logging_setup import get_logger
from ..vrchat_auth import VRChatSession, verify_session
from .panel_window import PanelWindow

logger = get_logger('account_switcher')


class _VerifyWorker(QThread):
    """Verifies a candidate session off the UI thread before switching to it."""

    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, seed: VRChatSession):
        super().__init__()
        self._seed = seed

    def run(self) -> None:
        try:
            self.loaded.emit(verify_session(self._seed))
        except Exception as exc:
            self.failed.emit(str(exc) or 'Could not verify the saved account.')


class AccountSwitcherWindow(PanelWindow):
    """Lists saved VRChat accounts and switches the active session between them.

    Account metadata lives in the SQLite ``accounts`` table; the credentials
    used to switch are read from the per-account encrypted vault keyed by
    ``user_id`` (never the plain DB).
    """

    def __init__(self, session: VRChatSession | None = None):
        super().__init__('Accounts', width=460, height=520)
        self._session = session
        self._worker: _VerifyWorker | None = None
        self._busy = False

        header = QLabel('Saved accounts')
        header.setObjectName('panelSectionHeader')
        self.content_layout.addWidget(header)

        hint = QLabel('Switch between accounts you have signed into with "remember me" enabled.')
        hint.setObjectName('panelHint')
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.content_layout.addWidget(self.list, 1)

        self.status = QLabel('')
        self.status.setObjectName('panelHint')
        self.status.setWordWrap(True)
        self.content_layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.btn_switch = QPushButton('Switch')
        self.btn_switch.clicked.connect(self._switch_selected)
        self.btn_add = QPushButton('Add account…')
        self.btn_add.clicked.connect(self._add_account)
        self.btn_forget = QPushButton('Forget')
        self.btn_forget.clicked.connect(self._forget_selected)
        buttons.addWidget(self.btn_switch)
        buttons.addWidget(self.btn_add)
        buttons.addStretch()
        buttons.addWidget(self.btn_forget)
        self.content_layout.addLayout(buttons)

        self.reload()

    def set_session(self, session: VRChatSession | None) -> None:
        self._session = session
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        current_id = self._session.user_id if self._session is not None else ''
        for account in self._load_accounts():
            user_id = str(account.get('user_id') or '')
            display = str(account.get('display_name') or account.get('username') or user_id or 'Unknown')
            username = str(account.get('username') or '')
            has_creds = bool(account.get('has_credentials'))
            is_current = bool(user_id) and user_id == current_id
            label = display
            if username and username != display:
                label = f'{display}  ({username})'
            tags: list[str] = []
            if is_current:
                tags.append('active')
            if not has_creds:
                tags.append('no saved login')
            if tags:
                label = f'{label}   —   {", ".join(tags)}'
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, account)
            if is_current:
                item.setForeground(QColor('#58c073'))
            elif not has_creds:
                item.setForeground(QColor('#8a9099'))
            self.list.addItem(item)
        if self.list.count() == 0:
            placeholder = QListWidgetItem('No accounts saved yet. Use "Add account…" to sign in.')
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setForeground(QColor('#8a9099'))
            self.list.addItem(placeholder)
        self._update_buttons()

    def _load_accounts(self) -> list[dict[str, Any]]:
        try:
            from ..store import meta_repo
            accounts = meta_repo.list_accounts()
        except Exception:
            logger.debug('Could not list accounts', exc_info=True)
            accounts = []
        try:
            from ..secret_store import list_account_secret_ids
            cred_ids = set(list_account_secret_ids())
        except Exception:
            cred_ids = set()
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for account in accounts:
            user_id = str(account.get('user_id') or '')
            row = dict(account)
            row['has_credentials'] = user_id in cred_ids
            merged.append(row)
            seen.add(user_id)
                                                                                
        for user_id in cred_ids - seen:
            merged.append({'user_id': user_id, 'username': '', 'display_name': '', 'has_credentials': True})
        return merged

    def _selected_account(self) -> dict[str, Any] | None:
        item = self.list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _update_buttons(self) -> None:
        account = self._selected_account()
        current_id = self._session.user_id if self._session is not None else ''
        is_current = account is not None and str(account.get('user_id') or '') == current_id and bool(current_id)
        has_creds = account is not None and bool(account.get('has_credentials'))
        self.btn_switch.setEnabled(not self._busy and account is not None and has_creds and not is_current)
        self.btn_forget.setEnabled(not self._busy and account is not None and not is_current)
        self.btn_add.setEnabled(not self._busy)

    def _set_busy(self, busy: bool, message: str = '') -> None:
        self._busy = busy
        if message:
            self.status.setText(message)
        self._update_buttons()

    def _switch_selected(self) -> None:
        account = self._selected_account()
        if account is None or self._busy:
            return
        user_id = str(account.get('user_id') or '')
        if not user_id:
            self.status.setText('This account is missing an id and cannot be switched to.')
            return
        try:
            from ..secret_store import load_account_secrets
            secrets = load_account_secrets(user_id)
        except Exception:
            secrets = {}
        auth_token = str(secrets.get('auth_token') or '').strip()
        if not auth_token:
            self.status.setText('No saved login for this account. Use "Add account…" to sign in again.')
            return
        seed = VRChatSession(
            user_id=str(secrets.get('auth_user_id') or user_id),
            display_name=str(secrets.get('auth_display_name') or account.get('display_name') or ''),
            username=str(secrets.get('auth_username') or account.get('username') or ''),
            auth_token=auth_token,
            two_factor_token=str(secrets.get('two_factor_token') or '').strip() or None,
        )
        self._set_busy(True, 'Verifying account…')
        worker = _VerifyWorker(seed)
        worker.loaded.connect(self._on_verified)
        worker.failed.connect(self._on_verify_failed)
        worker.finished.connect(lambda: self._clear_worker(worker))
        self._worker = worker
        worker.start()

    def _on_verified(self, session: object) -> None:
        if not isinstance(session, VRChatSession):
            self._on_verify_failed('Account verification returned no session.')
            return
        try:
            from ..store import meta_repo
            meta_repo.touch_account(session.user_id)
        except Exception:
            logger.debug('Could not update account usage', exc_info=True)
        try:
            from ..services.session_manager import SessionManager
            SessionManager.instance().apply_switched_session(session)
        except Exception:
            logger.warning('Failed to apply switched session', exc_info=True)
            self._set_busy(False, 'Could not activate the selected account.')
            return
        self._session = session
        self._set_busy(False, f'Switched to {session.display_name or session.username}.')
        self.reload()

    def _on_verify_failed(self, message: str) -> None:
        self._set_busy(False, f'Switch failed: {message[:200]}')

    def _clear_worker(self, worker: _VerifyWorker) -> None:
        if self._worker is worker:
            self._worker = None

    def _add_account(self) -> None:
        if self._busy:
            return
        try:
            from ..services.session_manager import SessionManager, prompt_for_login
            session = prompt_for_login(self)
        except Exception:
            logger.warning('Add-account login flow failed', exc_info=True)
            self.status.setText('Could not open the login window.')
            return
        if session is None:
            self.status.setText('Sign-in cancelled.')
            return
        try:
            from ..services.session_manager import SessionManager
            SessionManager.instance().apply_switched_session(session)
        except Exception:
            logger.debug('Could not broadcast added session', exc_info=True)
        self._session = session
        self.status.setText(f'Added {session.display_name or session.username}.')
        self.reload()

    def _forget_selected(self) -> None:
        account = self._selected_account()
        if account is None or self._busy:
            return
        user_id = str(account.get('user_id') or '')
        if not user_id:
            return
        try:
            from ..secret_store import remove_account_secrets
            remove_account_secrets(user_id)
        except Exception:
            logger.debug('Could not remove account secrets', exc_info=True)
        try:
            from ..store import meta_repo
            meta_repo.remove_account(user_id)
        except Exception:
            logger.debug('Could not remove account metadata', exc_info=True)
        self.status.setText('Account removed from this device.')
        self.reload()

    def closeEvent(self, event) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(2000)
        super().closeEvent(event)
