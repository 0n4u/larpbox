from __future__ import annotations
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from .config import load_auth_session
from .frameless_chrome import apply_frameless_chrome
from .theme import dark_theme
from .title_bar import TitleBar
from .ui_animations import pop_in_widget, stagger_pop_in, window_fade_in
from .vrchat_auth import LoginFailed, TwoFactorRequired, VRChatSession, login, persist_session
_INPUT_HEIGHT = 30
_BASE_HEIGHT = 292
_2FA_EXTRA_HEIGHT = 56
_WINDOW_WIDTH = 380

class LoginWorker(QThread):
    finished_ok = pyqtSignal(object)
    finished_error = pyqtSignal(str)
    needs_two_factor = pyqtSignal(str)

    def __init__(self, username: str, password: str, two_factor_code: str | None=None, two_factor_method: str | None=None):
        super().__init__()
        self.username = username
        self.password = password
        self.two_factor_code = two_factor_code
        self.two_factor_method = two_factor_method

    def run(self) -> None:
        try:
            session = login(self.username, self.password, two_factor_code=self.two_factor_code, two_factor_method=self.two_factor_method)
            self.finished_ok.emit(session)
        except TwoFactorRequired as exc:
            self.needs_two_factor.emit(exc.method)
        except LoginFailed as exc:
            self.finished_error.emit(str(exc))
        except Exception as exc:
            self.finished_error.emit(str(exc) or 'Login failed.')

class LoginWindow(QWidget):
    login_succeeded = pyqtSignal(object)
    login_cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.session: VRChatSession | None = None
        self._two_factor_method: str | None = None
        self._two_factor_visible = False
        self._worker: LoginWorker | None = None
        self._intro_animated = False
        self.setWindowTitle('')
        self.setWindowOpacity(0.0)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(_WINDOW_WIDTH, _BASE_HEIGHT)
        self.init_ui()
        self.apply_theme()
        self._load_saved_username()

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)
        self.container = QWidget()
        self.container.setObjectName('roundContainer')
        inner_layout = QVBoxLayout(self.container)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)
        self.title_bar = TitleBar(self, title='larpbox — Login', show_help=True)
        inner_layout.addWidget(self.title_bar)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(14, 10, 14, 12)
        content_layout.setSpacing(0)
        header = QLabel('Sign in to VRChat')
        header.setObjectName('headerTitle')
        content_layout.addWidget(header)
        intro = QLabel('Use your VRChat account to continue to larpbox.')
        intro.setObjectName('mutedHint')
        intro.setWordWrap(True)
        content_layout.addWidget(intro)
        content_layout.addSpacing(10)
        self.username_input = self._make_input('Username or email')
        username_block = self._field_block('USERNAME', self.username_input)
        content_layout.addWidget(username_block)
        content_layout.addSpacing(8)
        self.password_input = self._make_input('Password', password=True)
        password_block = self._field_block('PASSWORD', self.password_input)
        content_layout.addWidget(password_block)
        content_layout.addSpacing(8)
        self.two_factor_label = QLabel('2FA CODE')
        self.two_factor_label.setObjectName('fieldLabel')
        self.two_factor_input = self._make_input('6-digit code')
        self.two_factor_input.setMaxLength(8)
        self.two_factor_block = self._field_block('2FA CODE', self.two_factor_input)
        self.two_factor_block.setVisible(False)
        content_layout.addWidget(self.two_factor_block)
        self.status_label = QLabel('')
        self.status_label.setObjectName('statusLabel')
        self.status_label.setWordWrap(True)
        self.status_label.setFixedHeight(18)
        content_layout.addSpacing(6)
        content_layout.addWidget(self.status_label)
        footer = QFrame()
        footer.setObjectName('loginFooter')
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 8, 0, 0)
        footer_layout.setSpacing(8)
        self.remember_check = QCheckBox('Remember me')
        self.remember_check.setToolTip('Save your session locally to skip login next time.')
        footer_layout.addWidget(self.remember_check)
        footer_layout.addStretch()
        self.btn_login = QPushButton('Sign in')
        self.btn_login.setObjectName('primaryButton')
        self.btn_login.setFixedSize(84, 28)
        self.btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_login.clicked.connect(self._submit_login)
        footer_layout.addWidget(self.btn_login)
        content_layout.addWidget(footer)
        inner_layout.addWidget(content_widget)
        layout.addWidget(self.container)
        self._intro_targets = [header, intro, username_block, password_block, footer]
        self.username_input.returnPressed.connect(self._submit_login)
        self.password_input.returnPressed.connect(self._submit_login)
        self.two_factor_input.returnPressed.connect(self._submit_login)

    def _make_input(self, placeholder: str, *, password: bool=False) -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setFixedHeight(_INPUT_HEIGHT)
        field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if password:
            field.setEchoMode(QLineEdit.EchoMode.Password)
        return field

    def _field_block(self, label_text: str, field: QLineEdit) -> QWidget:
        block = QWidget()
        block_layout = QVBoxLayout(block)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(4)
        label = QLabel(label_text)
        label.setObjectName('fieldLabel')
        block_layout.addWidget(label)
        block_layout.addWidget(field)
        if label_text.startswith('2FA'):
            self.two_factor_label = label
        return block

    def apply_theme(self) -> None:
        self.setStyleSheet(dark_theme('\n            QLabel#headerTitle {\n                color: #f0f0f0;\n                font-size: 13pt;\n                font-weight: 600;\n            }\n            QLabel#fieldLabel {\n                color: #8a8a8a;\n                font-size: 8pt;\n                font-weight: 600;\n                letter-spacing: 0.5px;\n            }\n            QLabel#mutedHint {\n                color: #7a8a7a;\n                font-size: 9pt;\n                margin-top: 2px;\n            }\n            QLabel#statusLabel {\n                font-size: 9pt;\n            }\n            QLineEdit {\n                padding: 4px 8px;\n            }\n            #loginFooter {\n                border-top: 1px solid #333333;\n            }\n            QPushButton#primaryButton {\n                background-color: #3d6fa8;\n                border: 1px solid #4ea3ff;\n                border-radius: 5px;\n                color: #ffffff;\n                font-weight: 600;\n                padding: 0 12px;\n            }\n            QPushButton#primaryButton:hover {\n                background-color: #4a7fbd;\n                border-color: #6bb5ff;\n            }\n            QPushButton#primaryButton:pressed {\n                background-color: #345f92;\n            }\n            QPushButton#primaryButton:disabled {\n                background-color: #2a3544;\n                border-color: #3a4555;\n                color: #888888;\n            }\n            QCheckBox {\n                spacing: 6px;\n                color: #b0b0b0;\n                font-size: 9pt;\n            }\n            QCheckBox::indicator {\n                width: 15px;\n                height: 15px;\n                border-radius: 3px;\n                border: 1px solid #3a3a3a;\n                background-color: #232323;\n            }\n            QCheckBox::indicator:checked {\n                background-color: #4ea3ff;\n                border: 1px solid #6bb5ff;\n            }\n        '))

    def _load_saved_username(self) -> None:
        auth = load_auth_session()
        username = str(auth.get('auth_username') or '').strip()
        if username:
            self.username_input.setText(username)
        self.remember_check.setChecked(bool(auth.get('remember_login')))

    def prepare_for_display(self, parent: QWidget | None=None, *, relogin: bool=False, reason: str='') -> None:
        self.session = None
        self._set_busy(False)
        if parent is not None:
            center = parent.frameGeometry().center()
            geo = self.frameGeometry()
            geo.moveCenter(center)
            self.move(geo.topLeft())
        if relogin:
            self.setWindowOpacity(1.0)
            message = reason.strip() or 'Your VRChat session expired. Sign in again.'
            self._set_status(message, error=True)
            self.password_input.clear()
            self.two_factor_input.clear()
            if self._two_factor_visible:
                self.two_factor_block.setVisible(False)
                self._two_factor_visible = False
                self._set_window_height(two_factor=False)
            self.username_input.setFocus()
        else:
            self.setWindowOpacity(0.0)
            self._load_saved_username()

    def _set_window_height(self, two_factor: bool) -> None:
        height = _BASE_HEIGHT + (_2FA_EXTRA_HEIGHT if two_factor else 0)
        self.setFixedSize(_WINDOW_WIDTH, height)

    def _show_two_factor(self, method: str) -> None:
        if method == 'email':
            self.two_factor_label.setText('EMAIL 2FA CODE')
            self.two_factor_input.setPlaceholderText('Code from your email')
            self._set_status('Check your email and enter the code below.')
        else:
            self.two_factor_label.setText('AUTHENTICATOR CODE')
            self.two_factor_input.setPlaceholderText('6-digit code')
            self._set_status('Enter the code from your authenticator app.')
        if not self._two_factor_visible:
            self._two_factor_visible = True
            self.two_factor_block.setVisible(True)
            self._set_window_height(two_factor=True)
        self.two_factor_input.clear()
        self.two_factor_input.setFocus()

    def _set_busy(self, busy: bool) -> None:
        self.btn_login.setEnabled(not busy)
        self.username_input.setEnabled(not busy)
        self.password_input.setEnabled(not busy)
        self.two_factor_input.setEnabled(not busy)
        self.remember_check.setEnabled(not busy)
        self.btn_login.setText('Signing in...' if busy else 'Sign in')

    def _set_status(self, message: str, *, error: bool=False) -> None:
        color = '#e07070' if error else '#7db87d'
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f'color: {color}; font-size: 9pt;')

    def _submit_login(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        username = self.username_input.text().strip()
        password = self.password_input.text()
        if not username or not password:
            self._set_status('Enter your username and password.', error=True)
            return
        tfa_code = self.two_factor_input.text().strip() or None
        self._set_busy(True)
        self._set_status('Connecting to VRChat...')
        self._disconnect_worker()
        worker = LoginWorker(username, password, two_factor_code=tfa_code, two_factor_method=self._two_factor_method)
        self._worker = worker
        worker.finished_ok.connect(self._on_login_ok)
        worker.finished_error.connect(self._on_login_error)
        worker.needs_two_factor.connect(self._on_two_factor_required)
        worker.start()

    def _disconnect_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        for signal, slot in ((worker.finished_ok, self._on_login_ok), (worker.finished_error, self._on_login_error), (worker.needs_two_factor, self._on_two_factor_required)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def _on_two_factor_required(self, method: str) -> None:
        self._set_busy(False)
        self._two_factor_method = method
        self._show_two_factor(method)

    def _on_login_ok(self, session: VRChatSession) -> None:
        self._set_busy(False)
        persist_session(session, self.remember_check.isChecked())
        self.session = session
        self._set_status(f'Welcome, {session.display_name}!')
        self.login_succeeded.emit(session)
        self.close()

    def _on_login_error(self, message: str) -> None:
        self._set_busy(False)
        self._set_status(message, error=True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_frameless_chrome(self)
        if not self._intro_animated:
            self._intro_animated = True
            window_fade_in(self, duration=340)
            pop_in_widget(self.container, duration=300)
            stagger_pop_in(self._intro_targets, duration=260, step_ms=45)
        self.username_input.setFocus()

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.wait(3000)
        if self.session is None:
            self.login_cancelled.emit()
        super().closeEvent(event)
