from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget
from .avatar_search_providers import probe_all_providers, provider_list
from .config import DEFAULT_CONFIG, VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, get_bool, load_config, save_config, write_config
from .http_proxy import proxy_status_label
from .logging_setup import get_logger
from .title_bar import TitleBar
from .theme import dark_theme
from .frameless_chrome import apply_frameless_chrome
from .ui_animations import pop_in_widget, toggle_expand_widget, window_fade_in
logger = get_logger('settings')

class AvatarApiTestWorker(QThread):
    finished_probe = pyqtSignal(list)

    def __init__(self, session=None):
        super().__init__()
        self._session = session

    def run(self) -> None:
        try:
            results = probe_all_providers(session=self._session)
        except Exception as exc:
            logger.warning('Avatar API probe failed: %s', exc, exc_info=True)
            results = []
        self.finished_probe.emit(results)

class SettingsWindow(QWidget):
    settings_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle('')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(0.0)
        self.setFixedSize(360, 900)
        self.default_settings = DEFAULT_CONFIG.copy()
        self.loading_settings = False
        self.init_ui()
        self.loading_settings = True
        self.load_settings()
        self.loading_settings = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(400)
        self._autosave_timer.timeout.connect(self._debounced_autosave)
        self._api_test_worker: AvatarApiTestWorker | None = None
        self._vrchat_session = None
        self.apply_theme()

    def set_vrchat_session(self, session) -> None:
        self._vrchat_session = session

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)
        self.container = QWidget()
        self.container.setObjectName('roundContainer')
        inner_layout = QVBoxLayout(self.container)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)
        self.title_bar = TitleBar(self, title='Settings')
        inner_layout.addWidget(self.title_bar)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(6, 6, 6, 6)
        content_layout.setSpacing(4)
        group = QGroupBox('Settings Configuration')
        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(8, 8, 8, 6)
        group_layout.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('\n            QScrollArea {\n                border: none;\n                background-color: transparent;\n            }\n        ')
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(4)
        scroll_layout.setContentsMargins(4, 4, 4, 4)
        osc_label = QLabel('OSC Connection')
        osc_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(osc_label)
        osc_layout = QHBoxLayout()
        osc_layout.setSpacing(8)
        ip_label = QLabel('IP:')
        self.osc_ip_input = QLineEdit()
        self.osc_ip_input.setPlaceholderText('127.0.0.1')
        self.osc_ip_input.setMaximumWidth(100)
        port_label = QLabel('Port:')
        self.osc_port_input = QSpinBox()
        self.osc_port_input.setRange(1, 65535)
        self.osc_port_input.setValue(9000)
        self.osc_port_input.setToolTip('VRChat listens for OSC on port 9000 by default')
        self.osc_port_input.setMaximumWidth(70)
        test_btn = QPushButton('Test')
        test_btn.setFixedSize(50, 22)
        test_btn.clicked.connect(self.test_connection)
        osc_layout.addWidget(ip_label)
        osc_layout.addWidget(self.osc_ip_input)
        osc_layout.addWidget(port_label)
        osc_layout.addWidget(self.osc_port_input)
        osc_layout.addWidget(test_btn)
        osc_layout.addStretch()
        scroll_layout.addLayout(osc_layout)
        self.connection_status = QLabel('Not tested')
        self.connection_status.setStyleSheet('font-size: 9pt; margin-left: 5px;')
        scroll_layout.addWidget(self.connection_status)
        self.use_secondary_check = QCheckBox('Enable Secondary OSC')
        self.use_secondary_check.setToolTip('Send OSC messages to a second endpoint simultaneously')
        self.use_secondary_check.toggled.connect(self.toggle_secondary_osc)
        scroll_layout.addWidget(self.use_secondary_check)
        self.secondary_frame = QFrame()
        self.secondary_frame.setVisible(False)
        sec_layout = QHBoxLayout(self.secondary_frame)
        sec_layout.setContentsMargins(20, 0, 0, 0)
        sec_ip_label = QLabel('IP:')
        self.secondary_ip_input = QLineEdit()
        self.secondary_ip_input.setPlaceholderText('127.0.0.1')
        self.secondary_ip_input.setMaximumWidth(100)
        sec_port_label = QLabel('Port:')
        self.secondary_port_input = QSpinBox()
        self.secondary_port_input.setRange(1, 65535)
        self.secondary_port_input.setValue(9001)
        self.secondary_port_input.setMaximumWidth(70)
        sec_layout.addWidget(sec_ip_label)
        sec_layout.addWidget(self.secondary_ip_input)
        sec_layout.addWidget(sec_port_label)
        sec_layout.addWidget(self.secondary_port_input)
        sec_layout.addStretch()
        scroll_layout.addWidget(self.secondary_frame)
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.HLine)
        separator1.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator1)
        delay_label = QLabel('Message Delay')
        delay_label.setObjectName('sectionHeader')
        delay_label.setToolTip(f"Fixed at {VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS}ms — VRChat's fastest stable chatbox rate. Lower values cause spam timeouts.")
        scroll_layout.addWidget(delay_label)
        delay_layout = QHBoxLayout()
        delay_layout.setSpacing(8)
        delay_input_label = QLabel('Delay:')
        delay_input_label.setStyleSheet('font-size: 9pt;')
        self.delay_value_label = QLabel(f'{VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS}ms')
        self.delay_value_label.setStyleSheet('font-weight: bold;')
        delay_layout.addWidget(delay_input_label)
        delay_layout.addWidget(self.delay_value_label)
        delay_layout.addStretch()
        scroll_layout.addLayout(delay_layout)
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.HLine)
        separator2.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator2)
        display_label = QLabel('Display Modes')
        display_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(display_label)
        box_label = QLabel('Small Box Effect')
        box_label.setStyleSheet('color: #88aa88; font-size: 9pt; margin-left: 5px;')
        scroll_layout.addWidget(box_label)
        self.egg_mode_check = QCheckBox('Enable Small Box Effect')
        self.egg_mode_check.setChecked(True)
        self.egg_mode_check.setToolTip('Creates a compact chatbox by appending invisible characters. Recommended for stacking animations and media text.')
        scroll_layout.addWidget(self.egg_mode_check)
        self.insane_egg_mode_check = QCheckBox('Extreme Height Mode')
        self.insane_egg_mode_check.setStyleSheet('color: #cc6666; font-weight: bold; font-size: 10pt;')
        self.insane_egg_mode_check.setToolTip('Fills all 144 characters with newlines and fullwidth spaces for maximum height. Animations are disabled when enabled. Height messages sent every 2 seconds.')
        scroll_layout.addWidget(self.insane_egg_mode_check)
        self.wall_of_china_check = QCheckBox('Wall of China')
        self.wall_of_china_check.setStyleSheet('color: #550000; font-weight: bold; font-size: 10pt;')
        self.wall_of_china_check.setToolTip('Expands the chatbox to fill the entire available chat area. This mode is applied by OSC output formatting.')
        self.wall_of_china_check.toggled.connect(self.on_wall_of_china_toggled)
        scroll_layout.addWidget(self.wall_of_china_check)
        self.insane_egg_mode_check.toggled.connect(self.on_insane_egg_mode_toggled)
        separator3 = QFrame()
        separator3.setFrameShape(QFrame.Shape.HLine)
        separator3.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator3)
        media_label = QLabel('Media Integration')
        media_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(media_label)
        self.enable_media_check = QCheckBox('Display media status')
        self.enable_media_check.setToolTip('Shows currently playing media using Windows system media controls when available (works while minimized). Falls back to window titles. Stacks on top of preset animations.')
        scroll_layout.addWidget(self.enable_media_check)
        media_fmt_label = QLabel('Media format string')
        media_fmt_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(media_fmt_label)
        self.media_format_input = QLineEdit()
        self.media_format_input.setPlaceholderText('{title} by {artist}')
        self.media_format_input.setToolTip('Variables: {title}, {artist}, {name}, {playing}')
        scroll_layout.addWidget(self.media_format_input)
        apps_label = QLabel('Allowed apps (comma-separated, empty = all)')
        apps_label.setObjectName('mutedHint')
        scroll_layout.addWidget(apps_label)
        self.media_apps_input = QLineEdit()
        self.media_apps_input.setPlaceholderText('spotify.exe, chrome.exe')
        scroll_layout.addWidget(self.media_apps_input)
        separator4 = QFrame()
        separator4.setFrameShape(QFrame.Shape.HLine)
        separator4.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator4)
        panels_label = QLabel('Panels')
        panels_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(panels_label)
        panels_desc = QLabel('Hide panels you do not use. The window size stays fixed.')
        panels_desc.setObjectName('mutedHint')
        panels_desc.setWordWrap(True)
        scroll_layout.addWidget(panels_desc)
        self.show_friends_list_check = QCheckBox('Friends list')
        self.show_avatar_search_check = QCheckBox('Avatar tools')
        self.show_player_list_check = QCheckBox('Player list')
        self.show_instance_info_check = QCheckBox('Instance info bar')
        self.show_account_info_check = QCheckBox('Account info')
        self.show_chatbox_preview_check = QCheckBox('Chatbox preview')
        self.show_preset_config_check = QCheckBox('Preset configuration')
        for checkbox in (self.show_friends_list_check, self.show_avatar_search_check, self.show_player_list_check, self.show_instance_info_check, self.show_account_info_check, self.show_chatbox_preview_check, self.show_preset_config_check):
            scroll_layout.addWidget(checkbox)
        separator5 = QFrame()
        separator5.setFrameShape(QFrame.Shape.HLine)
        separator5.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator5)
        avatar_api_label = QLabel('Avatar Tools')
        avatar_api_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(avatar_api_label)
        avatar_api_desc = QLabel('Choose which database to search. Filters in the avatar panel change per API.')
        avatar_api_desc.setObjectName('mutedHint')
        avatar_api_desc.setWordWrap(True)
        scroll_layout.addWidget(avatar_api_desc)
        api_row = QHBoxLayout()
        api_row.setSpacing(8)
        api_row.addWidget(QLabel('Provider:'))
        self.avatar_provider_combo = QComboBox()
        for provider in provider_list():
            self.avatar_provider_combo.addItem(provider.label, provider.id)
        self.avatar_provider_combo.setToolTip('Community avatar search APIs. Retries alternate HTTPS ports when the default fails.')
        api_row.addWidget(self.avatar_provider_combo, 1)
        scroll_layout.addLayout(api_row)
        proxy_row = QHBoxLayout()
        proxy_row.setSpacing(8)
        proxy_row.addWidget(QLabel('HTTP proxy:'))
        self.http_proxy_input = QLineEdit()
        self.http_proxy_input.setPlaceholderText('127.0.0.1:7890 (optional — for blocked APIs)')
        self.http_proxy_input.setToolTip('Route avatar search through a local HTTP proxy (same as VRCX --proxy-server). Use your VPN HTTP port if community APIs are blocked. SOCKS needs PySocks.')
        proxy_row.addWidget(self.http_proxy_input, 1)
        scroll_layout.addLayout(proxy_row)
        api_test_row = QHBoxLayout()
        self.avatar_api_test_btn = QPushButton('Test APIs')
        self.avatar_api_test_btn.setFixedSize(70, 22)
        self.avatar_api_test_btn.clicked.connect(self.test_avatar_apis)
        api_test_row.addWidget(self.avatar_api_test_btn)
        api_test_row.addStretch()
        scroll_layout.addLayout(api_test_row)
        self.avatar_api_status = QLabel('')
        self.avatar_api_status.setObjectName('mutedHint')
        self.avatar_api_status.setWordWrap(True)
        scroll_layout.addWidget(self.avatar_api_status)
        separator6 = QFrame()
        separator6.setFrameShape(QFrame.Shape.HLine)
        separator6.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator6)
        presence_label = QLabel('Rich Presence')
        presence_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(presence_label)
        self.enable_discord_presence_check = QCheckBox('Discord — show VRChat world (requires pypresence)')
        scroll_layout.addWidget(self.enable_discord_presence_check)
        separator6f = QFrame()
        separator6f.setFrameShape(QFrame.Shape.HLine)
        separator6f.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator6f)
        features_label = QLabel('Companion Features')
        features_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(features_label)
        features_desc = QLabel('Activity logging, analytics, and the realtime pipeline persist VRChat data locally to data/larpbox.db.')
        features_desc.setObjectName('mutedHint')
        features_desc.setWordWrap(True)
        scroll_layout.addWidget(features_desc)
        self.enable_activity_log_check = QCheckBox('Activity logging & history (feed, gamelog, world visits)')
        scroll_layout.addWidget(self.enable_activity_log_check)
        self.enable_analytics_check = QCheckBox('Analytics & playtime tracking')
        scroll_layout.addWidget(self.enable_analytics_check)
        self.enable_pipeline_check = QCheckBox('Realtime VRChat pipeline (websocket) — live friend/notification events')
        scroll_layout.addWidget(self.enable_pipeline_check)
        self.enable_server_status_check = QCheckBox('VRChat server-status monitor')
        scroll_layout.addWidget(self.enable_server_status_check)
        self.enable_multi_account_check = QCheckBox('Multi-account switcher (shows the account toolbar button)')
        scroll_layout.addWidget(self.enable_multi_account_check)
        self.enable_vr_overlay_check = QCheckBox('SteamVR in-headset overlay (requires the optional openvr package)')
        scroll_layout.addWidget(self.enable_vr_overlay_check)
        separator6c = QFrame()
        separator6c.setFrameShape(QFrame.Shape.HLine)
        separator6c.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator6c)
        app_label = QLabel('Application')
        app_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(app_label)
        self.close_to_tray_check = QCheckBox('Minimize to tray when closing the window')
        self.close_to_tray_check.setToolTip('Keep larpbox running in the system tray when you close the main window')
        scroll_layout.addWidget(self.close_to_tray_check)
        self.reduce_motion_check = QCheckBox('Reduce motion (faster UI)')
        self.reduce_motion_check.setToolTip('Disables list pop-in, panel stagger, and pulsing loaders for a snappier interface.')
        scroll_layout.addWidget(self.reduce_motion_check)
        self.launch_on_startup_check = QCheckBox('Launch larpbox when Windows starts')
        self.launch_on_startup_check.setToolTip('Adds larpbox to your Windows startup (current user only)')
        scroll_layout.addWidget(self.launch_on_startup_check)
        self.auto_restart_check = QCheckBox('Auto-restart after an unexpected crash')
        self.auto_restart_check.setToolTip('Relaunches larpbox if it exits unexpectedly (max 3 times per 10 minutes)')
        scroll_layout.addWidget(self.auto_restart_check)
        self.enable_hotkeys_check = QCheckBox('Global hotkeys (Ctrl+Alt+1–5)')
        self.enable_hotkeys_check.setToolTip('Play bound presets while VRChat is running. Set preset names below.')
        scroll_layout.addWidget(self.enable_hotkeys_check)
        self._hotkey_inputs: list[QLineEdit] = []
        for slot in range(1, 6):
            row = QHBoxLayout()
            row.addWidget(QLabel(f'Hotkey {slot}:'))
            field = QLineEdit()
            field.setPlaceholderText('Preset name (exact match)')
            field.setToolTip(f'Ctrl+Alt+{slot} plays this preset when VRChat is running')
            self._hotkey_inputs.append(field)
            row.addWidget(field, 1)
            scroll_layout.addLayout(row)
        separator6b = QFrame()
        separator6b.setFrameShape(QFrame.Shape.HLine)
        separator6b.setStyleSheet('background-color: #3a3a3a; max-height: 1px; margin: 6px 0;')
        scroll_layout.addWidget(separator6b)
        debug_label = QLabel('Debug & Logging')
        debug_label.setObjectName('sectionHeader')
        scroll_layout.addWidget(debug_label)
        debug_desc = QLabel('Show the console and write detailed logs for OSC, media, and presets.')
        debug_desc.setObjectName('mutedHint')
        debug_desc.setWordWrap(True)
        scroll_layout.addWidget(debug_desc)
        self.debug_card = QFrame()
        self.debug_card.setObjectName('debugCard')
        debug_card_layout = QVBoxLayout(self.debug_card)
        debug_card_layout.setContentsMargins(10, 8, 10, 8)
        debug_card_layout.setSpacing(6)
        self.debug_mode_check = QCheckBox('Enable debug mode')
        self.debug_mode_check.setObjectName('debugModeCheck')
        self.debug_mode_check.setToolTip('Opens a console window with colorized live logs.\nAll events are also saved to logs/app.log')
        debug_card_layout.addWidget(self.debug_mode_check)
        self.debug_status_label = QLabel('Console hidden | INFO-level file logging')
        self.debug_status_label.setObjectName('debugStatus')
        self.debug_status_label.setWordWrap(True)
        debug_card_layout.addWidget(self.debug_status_label)
        self.debug_log_path_label = QLabel('')
        self.debug_log_path_label.setObjectName('debugLogPath')
        self.debug_log_path_label.setWordWrap(True)
        debug_card_layout.addWidget(self.debug_log_path_label)
        scroll_layout.addWidget(self.debug_card)
        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        group_layout.addWidget(scroll)
        group.setLayout(group_layout)
        content_layout.addWidget(group)
        self.osc_ip_input.textChanged.connect(self.autosave)
        self.osc_port_input.valueChanged.connect(self.autosave)
        self.egg_mode_check.toggled.connect(self.autosave)
        self.insane_egg_mode_check.toggled.connect(self.autosave)
        self.wall_of_china_check.toggled.connect(self.autosave)
        self.use_secondary_check.toggled.connect(self.autosave)
        self.secondary_ip_input.textChanged.connect(self.autosave)
        self.secondary_port_input.valueChanged.connect(self.autosave)
        self.enable_media_check.toggled.connect(self.autosave)
        self.media_format_input.textChanged.connect(self.autosave)
        self.media_apps_input.textChanged.connect(self.autosave)
        self.show_friends_list_check.toggled.connect(self.autosave)
        self.show_avatar_search_check.toggled.connect(self.autosave)
        self.show_player_list_check.toggled.connect(self.autosave)
        self.show_instance_info_check.toggled.connect(self.autosave)
        self.show_account_info_check.toggled.connect(self.autosave)
        self.show_chatbox_preview_check.toggled.connect(self.autosave)
        self.show_preset_config_check.toggled.connect(self.autosave)
        self.enable_discord_presence_check.toggled.connect(self.autosave)
        self.enable_activity_log_check.toggled.connect(self.autosave)
        self.enable_analytics_check.toggled.connect(self.autosave)
        self.enable_pipeline_check.toggled.connect(self.autosave)
        self.enable_server_status_check.toggled.connect(self.autosave)
        self.enable_multi_account_check.toggled.connect(self.autosave)
        self.enable_vr_overlay_check.toggled.connect(self.autosave)
        self.close_to_tray_check.toggled.connect(self.autosave)
        self.reduce_motion_check.toggled.connect(self.autosave)
        self.launch_on_startup_check.toggled.connect(self._on_launch_on_startup_toggled)
        self.auto_restart_check.toggled.connect(self.autosave)
        self.enable_hotkeys_check.toggled.connect(self.autosave)
        for field in self._hotkey_inputs:
            field.textChanged.connect(self.autosave)
        self.avatar_provider_combo.currentIndexChanged.connect(self.autosave)
        self.http_proxy_input.textChanged.connect(self.autosave)
        self.debug_mode_check.toggled.connect(self.on_debug_mode_changed)
        status_bar = QHBoxLayout()
        status_bar.setContentsMargins(0, 4, 0, 0)
        self.status_label = QLabel('Status: Ready')
        self.btn_save = QPushButton('Save')
        self.btn_save.setToolTip('Save settings')
        self.btn_reset = QPushButton('Reset')
        self.btn_close = QPushButton('Close')
        for btn in [self.btn_save, self.btn_reset, self.btn_close]:
            btn.setFixedSize(50, 20)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_save.clicked.connect(self.save_settings_clicked)
        self.btn_reset.clicked.connect(self.reset_settings)
        self.btn_close.clicked.connect(self.close)
        status_bar.addStretch()
        status_bar.addWidget(self.btn_save)
        status_bar.addWidget(self.btn_reset)
        status_bar.addWidget(self.btn_close)
        content_layout.addLayout(status_bar)
        inner_layout.addWidget(content_widget)
        layout.addWidget(self.container)

    def toggle_secondary_osc(self, enabled):
        if self.loading_settings:
            return
        toggle_expand_widget(self.secondary_frame, enabled, duration=280)

    def test_avatar_apis(self) -> None:
        if self._api_test_worker is not None and self._api_test_worker.isRunning():
            return
        self.avatar_api_test_btn.setEnabled(False)
        self.avatar_api_status.setText(f'{proxy_status_label()}\nTesting avatar APIs (TLS + HTTP + search)...')
        self.avatar_api_status.setStyleSheet('')
        self._api_test_worker = AvatarApiTestWorker(session=self._vrchat_session)
        self._api_test_worker.finished_probe.connect(self._on_avatar_api_test_done)
        self._api_test_worker.start()

    def _on_avatar_api_test_done(self, results: list) -> None:
        self.avatar_api_test_btn.setEnabled(True)
        if not results:
            self.avatar_api_status.setText('Probe failed — check your network connection.')
            self.avatar_api_status.setStyleSheet('color: #cc6666; font-size: 8pt;')
            return
        lines: list[str] = [proxy_status_label()]
        for result in results:
            if result.api_ok:
                icon = 'OK'
            elif result.tls_ok:
                icon = 'TLS only'
            else:
                icon = 'FAIL'
            lines.append(f'{icon}: {result.label} — {result.detail}')
        self.avatar_api_status.setText('\n'.join(lines))
        if all((result.api_ok for result in results)):
            self.avatar_api_status.setStyleSheet('color: #88aa88; font-size: 8pt;')
        else:
            self.avatar_api_status.setStyleSheet('color: #cc6666; font-size: 8pt;')

    def test_connection(self):
        from pythonosc import udp_client
        from .osc_connection import OscConnectionTracker
        ip = self.osc_ip_input.text().strip() or '127.0.0.1'
        port = self.osc_port_input.value()
        try:
            client = udp_client.SimpleUDPClient(ip, port)
            client.send_message('/chatbox/input', ['larpbox connection test', True, False])
            OscConnectionTracker.instance().record_success()
            self.connection_status.setText(f'Test sent to {ip}:{port}')
            self.connection_status.setStyleSheet('font-size: 9pt; margin-left: 5px; color: #88aa88;')
        except Exception as e:
            OscConnectionTracker.instance().record_failure(str(e))
            self.connection_status.setText(f'Failed: {e}')
            self.connection_status.setStyleSheet('font-size: 9pt; margin-left: 5px; color: #cc6666;')

    def apply_theme(self):
        self.setStyleSheet(dark_theme("\n            QScrollArea {\n                border: none;\n                background-color: transparent;\n            }\n            QLabel#sectionHeader {\n                font-weight: bold;\n                margin-top: 5px;\n            }\n            QLabel#mutedHint {\n                color: #7a8a7a;\n                font-size: 9pt;\n                margin: 0 2px 4px 2px;\n            }\n            #debugCard {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n                margin-top: 2px;\n            }\n            QLabel#debugStatus {\n                color: #9aa0a6;\n                font-size: 9pt;\n                margin-left: 22px;\n            }\n            QLabel#debugLogPath {\n                color: #6a7a8a;\n                font-size: 8pt;\n                font-family: 'Consolas', 'Cascadia Mono', monospace;\n                margin-left: 22px;\n            }\n            QCheckBox#debugModeCheck {\n                font-weight: bold;\n            }\n            QCheckBox {\n                spacing: 5px;\n            }\n            QCheckBox::indicator {\n                width: 16px;\n                height: 16px;\n                border-radius: 3px;\n                border: 1px solid #3a3a3a;\n                background-color: #232323;\n            }\n            QCheckBox::indicator:checked {\n                background-color: #808080;\n                border: 1px solid #a0a0a0;\n            }\n            QLabel {\n                color: #cccccc;\n            }\n            "))

    def load_settings(self):
        config = load_config()
        self.osc_ip_input.setText(str(config.get('osc_ip', '127.0.0.1')))
        self.osc_port_input.setValue(int(config.get('osc_port', 9000)))
        self.insane_egg_mode_check.setChecked(get_bool(config.get('insane_egg_mode')))
        self.wall_of_china_check.setChecked(get_bool(config.get('wall_of_china')))
        self.egg_mode_check.setChecked(get_bool(config.get('egg_mode', True)))
        use_secondary = get_bool(config.get('use_secondary_osc'))
        self.use_secondary_check.blockSignals(True)
        self.use_secondary_check.setChecked(use_secondary)
        self.use_secondary_check.blockSignals(False)
        self.secondary_frame.setVisible(use_secondary)
        self.secondary_ip_input.setText(str(config.get('secondary_osc_ip', '127.0.0.1')))
        self.secondary_port_input.setValue(int(config.get('secondary_osc_port', 9001)))
        self.enable_media_check.setChecked(get_bool(config.get('enable_media')))
        self.media_format_input.setText(str(config.get('media_format', '{title} by {artist}')))
        apps = config.get('media_allowed_apps') or []
        if isinstance(apps, list):
            self.media_apps_input.setText(', '.join((str(a) for a in apps)))
        else:
            self.media_apps_input.setText('')
        self.show_friends_list_check.setChecked(get_bool(config.get('show_friends_list', True)))
        self.show_avatar_search_check.setChecked(get_bool(config.get('show_avatar_search', True)))
        self.show_player_list_check.setChecked(get_bool(config.get('show_player_list', True)))
        self.show_instance_info_check.setChecked(get_bool(config.get('show_instance_info', True)))
        self.show_account_info_check.setChecked(get_bool(config.get('show_account_info', True)))
        self.show_chatbox_preview_check.setChecked(get_bool(config.get('show_chatbox_preview', True)))
        self.show_preset_config_check.setChecked(get_bool(config.get('show_preset_config', True)))
        self.enable_discord_presence_check.setChecked(get_bool(config.get('enable_discord_presence', False)))
        self.enable_activity_log_check.setChecked(get_bool(config.get('enable_activity_log', True)))
        self.enable_analytics_check.setChecked(get_bool(config.get('enable_analytics', True)))
        self.enable_pipeline_check.setChecked(get_bool(config.get('enable_pipeline', False)))
        self.enable_server_status_check.setChecked(get_bool(config.get('enable_server_status', True)))
        self.enable_multi_account_check.setChecked(get_bool(config.get('enable_multi_account', False)))
        self.enable_vr_overlay_check.setChecked(get_bool(config.get('enable_vr_overlay', False)))
        self.close_to_tray_check.setChecked(get_bool(config.get('close_to_tray', True)))
        self.reduce_motion_check.setChecked(get_bool(config.get('reduce_motion', True)))
        self.launch_on_startup_check.setChecked(get_bool(config.get('launch_on_startup', False)))
        self.auto_restart_check.setChecked(get_bool(config.get('auto_restart_on_crash', False)))
        self.enable_hotkeys_check.setChecked(get_bool(config.get('enable_hotkeys', True)))
        for index, field in enumerate(self._hotkey_inputs, start=1):
            field.setText(str(config.get(f'hotkey_preset_{index}', '') or ''))
        provider_id = str(config.get('avatar_search_provider', 'avtrdb'))
        provider_index = self.avatar_provider_combo.findData(provider_id)
        if provider_index >= 0:
            self.avatar_provider_combo.setCurrentIndex(provider_index)
        self.http_proxy_input.setText(str(config.get('http_proxy', '') or ''))
        self.debug_mode_check.setChecked(get_bool(config.get('debug_mode')))
        self._refresh_debug_status_labels()

    def _refresh_debug_status_labels(self) -> None:
        from app.logging_setup import get_log_file_path
        enabled = self.debug_mode_check.isChecked()
        log_path = get_log_file_path()
        if enabled:
            self.debug_status_label.setText('Console visible | DEBUG-level live + file logging')
            self.debug_status_label.setStyleSheet('color: #88aa88;')
        else:
            self.debug_status_label.setText('Console hidden | INFO-level file logging only')
            self.debug_status_label.setStyleSheet('')
        self.debug_log_path_label.setText(f'Log file: {log_path}')

    def save_settings_clicked(self):
        self.save_settings()
        self.status_label.setText('Status: Settings Saved')
        self.settings_changed.emit()

    def autosave(self):
        if not self.loading_settings:
            self._autosave_timer.start()

    def _debounced_autosave(self):
        self.save_settings()
        self.status_label.setText('Status: Saved')
        self.settings_changed.emit()

    def _collect_settings(self) -> dict:
        provider_id = self.avatar_provider_combo.currentData()
        apps_raw = self.media_apps_input.text().strip()
        apps = [part.strip() for part in apps_raw.split(',') if part.strip()] if apps_raw else []
        hotkeys = {f'hotkey_preset_{index}': field.text().strip() for index, field in enumerate(self._hotkey_inputs, start=1)}
        return {'osc_ip': self.osc_ip_input.text() or '127.0.0.1', 'osc_port': self.osc_port_input.value(), 'insane_egg_mode': self.insane_egg_mode_check.isChecked(), 'wall_of_china': self.wall_of_china_check.isChecked(), 'egg_mode': self.egg_mode_check.isChecked(), 'use_secondary_osc': self.use_secondary_check.isChecked(), 'secondary_osc_ip': self.secondary_ip_input.text() or '127.0.0.1', 'secondary_osc_port': self.secondary_port_input.value(), 'small_delay_time': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'message_interval': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'blank_message_interval': VRCHAT_CHATBOX_DEFAULT_INTERVAL_MS, 'enable_media': self.enable_media_check.isChecked(), 'media_format': self.media_format_input.text().strip() or '{title} by {artist}', 'media_allowed_apps': apps, 'debug_mode': self.debug_mode_check.isChecked(), 'show_friends_list': self.show_friends_list_check.isChecked(), 'show_avatar_search': self.show_avatar_search_check.isChecked(), 'show_player_list': self.show_player_list_check.isChecked(), 'show_instance_info': self.show_instance_info_check.isChecked(), 'show_account_info': self.show_account_info_check.isChecked(), 'show_chatbox_preview': self.show_chatbox_preview_check.isChecked(), 'show_preset_config': self.show_preset_config_check.isChecked(), 'enable_discord_presence': self.enable_discord_presence_check.isChecked(), 'enable_activity_log': self.enable_activity_log_check.isChecked(), 'enable_analytics': self.enable_analytics_check.isChecked(), 'enable_pipeline': self.enable_pipeline_check.isChecked(), 'enable_server_status': self.enable_server_status_check.isChecked(), 'enable_multi_account': self.enable_multi_account_check.isChecked(), 'enable_vr_overlay': self.enable_vr_overlay_check.isChecked(), 'close_to_tray': self.close_to_tray_check.isChecked(), 'reduce_motion': self.reduce_motion_check.isChecked(), 'launch_on_startup': self.launch_on_startup_check.isChecked(), 'auto_restart_on_crash': self.auto_restart_check.isChecked(), 'enable_hotkeys': self.enable_hotkeys_check.isChecked(), **hotkeys, 'avatar_search_provider': provider_id or 'combined', 'http_proxy': self.http_proxy_input.text().strip()}

    def _on_launch_on_startup_toggled(self, enabled: bool):
        if self.loading_settings:
            return
        self.save_settings()
        try:
            from app.services.autostart import set_enabled
            set_enabled(bool(enabled))
        except Exception:
            pass
        self.settings_changed.emit()

    def on_debug_mode_changed(self, enabled: bool):
        if not self.loading_settings:
            self.save_settings()
            try:
                from app.logging_setup import get_logger, setup_logging, show_console, hide_console
                log = get_logger('config')
                if enabled:
                    show_console()
                else:
                    hide_console()
                setup_logging(debug_mode=enabled)
                if enabled:
                    log.info('Debug mode enabled from settings')
                else:
                    log.info('Debug mode disabled from settings')
                self._refresh_debug_status_labels()
                self.status_label.setText('Status: Debug mode updated')
            except Exception as e:
                from app.logging_setup import get_logger
                get_logger('config').warning('Debug mode toggle failed: %s', e, exc_info=True)
                self.status_label.setText('Status: Saved (restart may be required for console)')
            self.settings_changed.emit()

    def save_settings(self):
        from .http_proxy import invalidate_proxy_opener
        save_config(self._collect_settings())
        invalidate_proxy_opener()

    def reset_settings(self):
        write_config(self.default_settings.copy())
        self.load_settings()
        self.status_label.setText('Status: Reset to Default')
        self.settings_changed.emit()

    def on_wall_of_china_toggled(self, enabled: bool) -> None:
        if self.loading_settings:
            return
        if enabled and self.insane_egg_mode_check.isChecked():
            self.loading_settings = True
            self.insane_egg_mode_check.setChecked(False)
            self.loading_settings = False

    def on_insane_egg_mode_toggled(self, enabled: bool) -> None:
        if self.loading_settings:
            return
        if enabled and self.wall_of_china_check.isChecked():
            self.loading_settings = True
            self.wall_of_china_check.setChecked(False)
            self.loading_settings = False

    def showEvent(self, event):
        super().showEvent(event)
        apply_frameless_chrome(self)
        if not getattr(self, '_intro_animated', False):
            self._intro_animated = True
            window_fade_in(self, duration=340)
            pop_in_widget(self.container, duration=300)
