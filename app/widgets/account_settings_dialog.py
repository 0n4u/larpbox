from __future__ import annotations
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from ..config import clear_auth_session, get_bool, load_config, save_config
from ..frameless_chrome import apply_frameless_chrome
from ..theme import dark_theme
from ..title_bar import TitleBar
from ..ui_animations import pop_in_widget
from ..vrchat_auth import VRChatSession

def _mask_identifier(value: str, *, visible: int=2) -> str:
    text = (value or '').strip()
    if not text:
        return ''
    if '@' in text:
        local, domain = text.split('@', 1)
        if len(local) > visible:
            local = local[:visible] + '•' * (len(local) - visible)
        if '.' in domain:
            name, tld = domain.rsplit('.', 1)
            hidden = max(1, len(name) - 1)
            domain = (name[:1] if name else '') + '•' * hidden + '.' + tld
        else:
            domain = '•' * len(domain)
        return f'{local}@{domain}'
    if len(text) <= visible:
        return text
    return text[:visible] + '•' * min(10, len(text) - visible)

class AccountSettingsWindow(QWidget):
    signed_out = pyqtSignal()
    settings_changed = pyqtSignal()

    def __init__(self, session: VRChatSession | None=None):
        super().__init__()
        self.session = session
        self.setWindowTitle('')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(360, 340)
        self.loading_settings = False
        self.init_ui()
        self.loading_settings = True
        self.load_settings()
        self.loading_settings = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(400)
        self._autosave_timer.timeout.connect(self._debounced_autosave)
        self.apply_theme()

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)
        self.container = QWidget()
        self.container.setObjectName('roundContainer')
        inner_layout = QVBoxLayout(self.container)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)
        self.title_bar = TitleBar(self, title='Account Settings')
        inner_layout.addWidget(self.title_bar)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(6, 6, 6, 6)
        content_layout.setSpacing(4)
        group = QGroupBox('Account Configuration')
        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(8, 8, 8, 6)
        group_layout.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea { border: none; background-color: transparent; }')
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(4)
        scroll_layout.setContentsMargins(4, 4, 4, 4)
        profile_label = QLabel('Signed In')
        profile_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(profile_label)
        self.profile_card = QFrame()
        self.profile_card.setObjectName('profileCard')
        profile_layout = QVBoxLayout(self.profile_card)
        profile_layout.setContentsMargins(10, 8, 10, 8)
        profile_layout.setSpacing(4)
        self.display_label = QLabel('')
        self.display_label.setObjectName('profileName')
        self.display_label.setWordWrap(True)
        profile_layout.addWidget(self.display_label)
        self.user_label = QLabel('')
        self.user_label.setObjectName('profileMeta')
        self.user_label.setWordWrap(True)
        profile_layout.addWidget(self.user_label)
        scroll_layout.addWidget(self.profile_card)
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.HLine)
        separator1.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator1)
        notify_label = QLabel('Notifications')
        notify_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(notify_label)
        notify_desc = QLabel('Friend online and joinable alerts appear in the top-right overlay.')
        notify_desc.setObjectName('mutedHint')
        notify_desc.setWordWrap(True)
        scroll_layout.addWidget(notify_desc)
        self.notify_check = QCheckBox('Enable overlay notifications')
        self.notify_check.setToolTip('Friend online / joinable alerts in the top-right of your screen')
        scroll_layout.addWidget(self.notify_check)
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.HLine)
        separator2.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator2)
        session_label = QLabel('Session')
        session_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(session_label)
        session_desc = QLabel('Sign out clears saved VRChat credentials on this PC.')
        session_desc.setObjectName('mutedHint')
        session_desc.setWordWrap(True)
        scroll_layout.addWidget(session_desc)
        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        group_layout.addWidget(scroll)
        group.setLayout(group_layout)
        content_layout.addWidget(group)
        self.notify_check.toggled.connect(self.autosave)
        status_bar = QHBoxLayout()
        status_bar.setContentsMargins(0, 4, 0, 0)
        self.status_label = QLabel('Status: Ready')
        self.btn_sign_out = QPushButton('Sign Out')
        self.btn_sign_out.setObjectName('signOutBtn')
        self.btn_sign_out.setToolTip('Clear saved login and close the app')
        self.btn_sign_out.setFixedSize(62, 20)
        self.btn_sign_out.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_sign_out.clicked.connect(self._sign_out)
        self.btn_close = QPushButton('Close')
        self.btn_close.setFixedSize(50, 20)
        self.btn_close.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_close.clicked.connect(self.close)
        status_bar.addWidget(self.status_label)
        status_bar.addStretch()
        status_bar.addWidget(self.btn_sign_out)
        status_bar.addWidget(self.btn_close)
        content_layout.addLayout(status_bar)
        inner_layout.addWidget(content_widget)
        layout.addWidget(self.container)

    def apply_theme(self) -> None:
        self.setStyleSheet(dark_theme('\n            QScrollArea {\n                border: none;\n                background-color: transparent;\n            }\n            QLabel#sectionHeader {\n                font-weight: bold;\n                margin-top: 5px;\n            }\n            QLabel#mutedHint {\n                color: #7a8a7a;\n                font-size: 9pt;\n                margin: 0 2px 4px 2px;\n            }\n            QFrame#profileCard {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n                margin-top: 2px;\n            }\n            QLabel#profileName {\n                color: #f0f0f0;\n                font-weight: 600;\n                font-size: 10pt;\n            }\n            QLabel#profileMeta {\n                color: #9aa0a6;\n                font-size: 9pt;\n            }\n            QPushButton#signOutBtn {\n                color: #e07070;\n                border: 1px solid #804040;\n            }\n            QPushButton#signOutBtn:hover {\n                background-color: rgba(224, 112, 112, 0.12);\n            }\n            QCheckBox {\n                spacing: 5px;\n            }\n            QCheckBox::indicator {\n                width: 16px;\n                height: 16px;\n                border-radius: 3px;\n                border: 1px solid #3a3a3a;\n                background-color: #232323;\n            }\n            QCheckBox::indicator:checked {\n                background-color: #808080;\n                border: 1px solid #a0a0a0;\n            }\n            QLabel {\n                color: #cccccc;\n            }\n        '))

    def load_settings(self) -> None:
        config = load_config()
        if self.session:
            self.display_label.setText(self.session.display_name or 'Signed in')
            raw = str(config.get('auth_username') or self.session.user_id or '')
            self.user_label.setText(_mask_identifier(raw))
            self.user_label.setToolTip('')
        else:
            self.display_label.setText('Not signed in')
            self.user_label.setText('')
        self.notify_check.setChecked(get_bool(config.get('enable_notifications', True)))

    def autosave(self) -> None:
        if not self.loading_settings:
            self._autosave_timer.start()

    def _debounced_autosave(self) -> None:
        save_config({'enable_notifications': self.notify_check.isChecked()})
        self.status_label.setText('Status: Saved')
        self.settings_changed.emit()

    def _sign_out(self) -> None:
        clear_auth_session()
        self.session = None
        self.status_label.setText('Status: Signed out')
        self.signed_out.emit()
        self.close()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_frameless_chrome(self)
        if not getattr(self, '_intro_animated', False):
            self._intro_animated = True
            pop_in_widget(self.container, duration=280)

    def set_session(self, session: VRChatSession | None) -> None:
        self.session = session
        self.load_settings()
