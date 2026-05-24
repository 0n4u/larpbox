from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget
from .chatbox_preview import ChatboxPreview
from .config import get_bool, load_config
from .logging_setup import get_logger
from .title_bar import TitleBar
from .media_monitor import MediaIntegration
from .chatbox_controller import OSCHandler, PresetAnimations
from .preset_editor import PresetManagerWindow
from .preset_storage import delete_preset, resolve_preset_name
from .avatar_search_panel import AvatarSearchPanel
from .account_info_panel import AccountInfoPanel
from .friends_list_panel import FriendsListPanel
from .player_list_bar import PlayerListBar
from .ui_layout import PREVIEW_CONTENT_HEIGHT, PREVIEW_PANEL_HEIGHT, PREVIEW_PANEL_WIDTH
from .settings_dialog import SettingsWindow
from .status_text import format_status_label
from .theme import dark_theme
from .frameless_chrome import apply_frameless_chrome
from .image_loader import RemoteImageLabel
from .ui_animations import pop_in_widget, stagger_pop_in
from .vrchat_auth import VRChatSession
logger = get_logger('ui')

WINDOW_HEIGHT = 532
BASE_WINDOW_WIDTH = 848


class PresetConfigUI(QWidget):

    def __init__(self, session: VRChatSession | None=None):
        super().__init__()
        self.session = session
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle('')
        self.setFixedSize(BASE_WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(BASE_WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMaximumSize(BASE_WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setWindowOpacity(0.0)
        self._fade_in_started = False
        self.osc_handler = OSCHandler()
        self.current_playing_preset = None
        self.preset_animations = PresetAnimations()
        self.settings_window = None
        self.preset_manager_window = None
        self.send_blank_enabled = True
        self.chatbox_preview: ChatboxPreview | None = None
        self.media = MediaIntegration()
        self.media_enabled = False
        self.init_ui()
        self.start_timers()
        self.init_media()
        self._apply_media_setting_from_config()
        self._apply_exclusive_ui_state()
        self._apply_panel_visibility()
        if self.session is not None:
            RemoteImageLabel.set_session(self.session)
        if not self.media_enabled:
            self.osc_handler.media_enabled = False
            self.media.set_enabled(False)

    def _window_title(self) -> str:
        return 'larpbox'

    def _apply_media_setting_from_config(self) -> None:
        config = load_config()
        media_enabled = bool(config.get('enable_media', False))
        self.media_enabled = media_enabled
        self.osc_handler.media_enabled = media_enabled
        self.media.set_enabled(media_enabled)
        if media_enabled:
            self.media.request_poll()
            if not self.media.isRunning():
                self.media.start()
        elif self.media.isRunning():
            self.media.stop()

    def init_ui(self):
        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(8)

        left_column = QWidget()
        self.left_column = left_column
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.friends_list = FriendsListPanel(session=self.session, parent=self)
        left_layout.addWidget(self.friends_list, 0)

        self.avatar_search = AvatarSearchPanel(session=self.session, parent=self)
        left_layout.addWidget(self.avatar_search, 1)
        outer_layout.addWidget(left_column)

        center_column = QWidget()
        center_layout = QHBoxLayout(center_column)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)

        preset_stack = QWidget()
        preset_stack_layout = QVBoxLayout(preset_stack)
        preset_stack_layout.setContentsMargins(0, 0, 0, 0)
        preset_stack_layout.setSpacing(6)

        self.player_list = PlayerListBar(session=self.session, parent=self)
        preset_stack_layout.addWidget(self.player_list)

        self.container = QWidget()
        self.container.setObjectName('roundContainer')
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title_bar = TitleBar(self, title=self._window_title())
        layout.addWidget(self.title_bar)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(8, 8, 8, 8)
        group = QGroupBox('Preset Configuration')
        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(10, 10, 10, 8)
        group_layout.setSpacing(6)
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(4)
        self.btn_add = QPushButton('+')
        self.btn_remove = QPushButton('-')
        for b in (self.btn_add, self.btn_remove):
            b.setFixedSize(22, 22)
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            b.setStyleSheet('\n                QPushButton {\n                    padding: 0px;\n                    font-size: 11px;\n                }\n                ')
        self.btn_stop = QPushButton('🔻')
        self.btn_stop.setFixedSize(22, 22)
        self.btn_stop.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_stop.setStyleSheet('\n            QPushButton {\n                background-color: #2a2a2a;\n                border: 1px solid #444;\n                border-radius: 4px;\n                color: red;\n                font-size: 10pt;\n                padding: 0px;\n            }\n            QPushButton:hover {\n                background-color: #3a3a3a;\n            }\n        ')
        self.btn_stop.clicked.connect(self.stop_osc_and_clear)
        self.btn_stop.setToolTip('Stop animation and resume idle (blank or media)')
        self.btn_add.clicked.connect(self.open_preset_manager)
        self.btn_add.setToolTip('Open preset manager')
        self.btn_remove.clicked.connect(self.delete_selected_preset)
        self.btn_remove.setToolTip('Delete selected preset')
        settings = QPushButton('⚙️')
        settings.setFixedSize(22, 22)
        settings.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        settings.setStyleSheet('\n            QPushButton {\n                font-size: 10pt;\n                padding: 0px;\n            }\n        ')
        settings.clicked.connect(self.open_settings)
        top_bar.addWidget(self.btn_add)
        top_bar.addWidget(self.btn_remove)
        top_bar.addStretch()
        top_bar.addWidget(self.btn_stop)
        top_bar.addWidget(settings)
        self.preset_list = QListWidget()
        self.load_presets()
        self.preset_list.itemDoubleClicked.connect(self.play_preset)
        status_bar = QHBoxLayout()
        self.status_label = QLabel('Status: Idle')
        status_bar.addWidget(self.status_label)
        status_bar.addStretch()
        group_layout.addLayout(top_bar)
        group_layout.addWidget(self.preset_list)
        group_layout.addLayout(status_bar)
        group.setLayout(group_layout)
        content_layout.addWidget(group)
        layout.addLayout(content_layout)

        self.preview_container = QWidget()
        self.preview_container.setObjectName('previewContainer')
        self.preview_container.setFixedSize(PREVIEW_PANEL_WIDTH, PREVIEW_PANEL_HEIGHT)
        preview_layout = QVBoxLayout(self.preview_container)
        preview_layout.setContentsMargins(10, 10, 10, 10)
        preview_layout.setSpacing(6)
        preview_title = QLabel('Chatbox Preview')
        preview_title.setObjectName('previewHeader')
        preview_layout.addWidget(preview_title)
        self.preview_text = QTextEdit()
        self.preview_text.setObjectName('previewText')
        self.preview_text.setReadOnly(True)
        self.preview_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.preview_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.preview_text.setFixedHeight(PREVIEW_CONTENT_HEIGHT)
        preview_layout.addWidget(self.preview_text)
        self.preview_meta = QLabel('0/144')
        self.preview_meta.setObjectName('previewMeta')
        preview_layout.addWidget(self.preview_meta)
        self.chatbox_preview = ChatboxPreview(self.preview_text, self.preview_meta, self.osc_handler.format_chatbox_preview)

        preset_stack_layout.addWidget(self.container, 1)

        preview_column = QWidget()
        self.preview_column = preview_column
        preview_column.setFixedWidth(PREVIEW_PANEL_WIDTH)
        preview_column_layout = QVBoxLayout(preview_column)
        preview_column_layout.setContentsMargins(0, 0, 0, 0)
        preview_column_layout.setSpacing(6)
        self.account_info = AccountInfoPanel(session=self.session, parent=self)
        preview_column_layout.addWidget(self.account_info, 0, Qt.AlignmentFlag.AlignTop)
        preview_column_layout.addWidget(self.preview_container, 0, Qt.AlignmentFlag.AlignTop)
        preview_column_layout.addStretch(1)

        center_layout.addWidget(preset_stack, 1)
        center_layout.addWidget(preview_column, 0)
        outer_layout.addWidget(center_column, 1)
        self.osc_handler.chatbox_sent.connect(self.on_chatbox_sent)
        self.chatbox_preview.set_payload('', label='Idle')
        self.setStyleSheet(dark_theme("\n            QLabel#previewHeader {\n                font-weight: bold;\n                margin-bottom: 2px;\n            }\n            QLabel#previewMeta {\n                color: #999999;\n                font-size: 9pt;\n            }\n            QPushButton:hover {\n                border-color: #4ea3ff;\n            }\n            QPushButton:pressed {\n                border-color: #3b82f6;\n            }\n            QTextEdit {\n                background-color: #232323;\n                border: 1px solid #3a3a3a;\n                border-radius: 6px;\n                padding: 6px;\n                font-family: 'Segoe UI';\n                font-size: 10pt;\n            }\n            QTextEdit#previewText {\n                font-family: 'Segoe UI';\n                font-size: 10pt;\n            }\n            QLabel {\n                color: #cccccc;\n            }\n            "))

    def _panel_visibility(self) -> dict[str, bool]:
        config = load_config()
        return {
            'friends_list': get_bool(config.get('show_friends_list', True)),
            'avatar_search': get_bool(config.get('show_avatar_search', True)),
            'player_list': get_bool(config.get('show_player_list', True)),
            'account_info': get_bool(config.get('show_account_info', True)),
            'chatbox_preview': get_bool(config.get('show_chatbox_preview', True)),
            'preset_config': get_bool(config.get('show_preset_config', True)),
        }

    def _apply_panel_visibility(self) -> None:
        visibility = self._panel_visibility()
        show_left = visibility['friends_list'] or visibility['avatar_search']
        show_right = visibility['chatbox_preview'] or visibility['account_info']

        self.left_column.setVisible(show_left)
        self.friends_list.setVisible(visibility['friends_list'])
        self.avatar_search.setVisible(visibility['avatar_search'])

        self.player_list.setVisible(visibility['player_list'])
        self.container.setVisible(visibility['preset_config'])
        self.preview_column.setVisible(show_right)
        self.preview_container.setVisible(visibility['chatbox_preview'])
        self.account_info.setVisible(visibility['account_info'])

        if self.chatbox_preview is not None:
            QTimer.singleShot(0, self.chatbox_preview.refresh_geometry)

    def showEvent(self, event):
        super().showEvent(event)
        apply_frameless_chrome(self)
        if self.chatbox_preview is not None:
            QTimer.singleShot(0, self.chatbox_preview.refresh_geometry)
        if not getattr(self, '_fade_in_started', False):
            self._fade_in_started = True
            animation = QPropertyAnimation(self, b'windowOpacity')
            animation.setDuration(320)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutQuart)
            animation.start()
            self._fade_animation = animation
            panel_base_delay_ms = 320
            panel_stagger = (
                (self.avatar_search.container, 40),
                (self.friends_list.frame, 70),
                (self.player_list.frame, 90),
                (self.account_info.frame, 110),
                (self.container, 130),
                (self.preview_container, 160),
            )
            for widget, delay_ms in panel_stagger:
                pop_in_widget(widget, duration=300, delay_ms=panel_base_delay_ms + delay_ms)

    def start_timers(self):
        self.placeholder_timer = QTimer()
        self.placeholder_timer.timeout.connect(self.send_blank_if_idle)
        start_interval = self._get_placeholder_interval()
        self.placeholder_timer.start(start_interval)
        QTimer.singleShot(0, lambda: self._send_idle_keepalive(force_media=True))

    def _get_placeholder_interval(self):
        return getattr(self.osc_handler, 'blank_message_interval', 1500)

    def _configure_and_start_placeholder_timer(self):
        interval = self._get_placeholder_interval()
        self.placeholder_timer.setInterval(interval)
        self.placeholder_timer.start()

    def stop_osc_and_clear(self):
        logger.info('Stop pressed — returning to idle keep-alive')
        if self.current_playing_preset:
            self.stop_preset()
        else:
            self.osc_handler.stop_animation()
            self._configure_and_start_placeholder_timer()
        self._send_idle_keepalive(force_media=True)
        self._set_idle_status_label()

    def _send_idle_keepalive(self, *, force_media: bool=False) -> None:
        if self.osc_handler.is_exclusive_mode_active():
            self.osc_handler.send_blank_message_async()
            return
        if not self.osc_handler.insane_egg_mode:
            if self.osc_handler.media_enabled and self.osc_handler.media_text:
                self.osc_handler.push_media_to_chatbox(force=True)
                return
        self.osc_handler.send_blank_message_async()

    def _refresh_status_label(self, *, playing_preset: str | None=None) -> None:
        preset = playing_preset if playing_preset is not None else self.current_playing_preset
        self.status_label.setText(format_status_label(playing_preset=preset, wall_of_china=self.osc_handler.wall_of_china, insane_egg_mode=self.osc_handler.insane_egg_mode, exclusive_mode=self.osc_handler.is_exclusive_mode_active(), media_enabled=self.osc_handler.media_enabled, media_text=self.osc_handler.media_text))

    def _set_idle_status_label(self) -> None:
        self._refresh_status_label(playing_preset=None)

    def send_blank_if_idle(self):
        if self.osc_handler.is_exclusive_mode_active():
            self.osc_handler.send_blank_message_async()
            return
        if self.send_blank_enabled and (not self.current_playing_preset) and (not self.osc_handler.is_running):
            self._send_idle_keepalive(force_media=False)

    def play_preset(self, item):
        if self.osc_handler.is_exclusive_mode_active():
            self.status_label.setText('Status: Presets disabled in Exclusive Mode')
            return
        preset_name = item.text()
        if self.current_playing_preset:
            self.status_label.setText(f'Status: Stopping {self.current_playing_preset}...')
            self.stop_preset()
        animation_name = resolve_preset_name(preset_name)
        frames = self.preset_animations.get_animation_frames(animation_name)
        self.placeholder_timer.stop()
        started = self.osc_handler.start_animation(animation_name, frames)
        if started:
            logger.info('Playing preset=%s (%d frames)', preset_name, len(frames))
            self.current_playing_preset = preset_name
            self.status_label.setText(f'Status: Playing {preset_name}')
        else:
            self.current_playing_preset = None
            self.status_label.setText('Status: Failed to start preset')
            self._configure_and_start_placeholder_timer()

    def on_chatbox_sent(self, formatted_payload: str, label: str) -> None:
        self.chatbox_preview.set_payload(formatted_payload, label=label)

    def stop_preset(self):
        logger.info('Stopping preset=%s', self.current_playing_preset or '(none)')
        self.osc_handler.stop_animation()
        self.current_playing_preset = None
        self._refresh_status_label()
        self._configure_and_start_placeholder_timer()

    def open_settings(self):
        if not self.settings_window:
            self.settings_window = SettingsWindow()
            self.settings_window.settings_changed.connect(self.on_settings_changed)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def on_settings_changed(self):
        was_playing = self.current_playing_preset
        old_insane_mode = self.osc_handler.insane_egg_mode
        self.osc_handler.load_settings()
        self._apply_exclusive_ui_state()
        send_blank = False
        if self.osc_handler.is_exclusive_mode_active():
            if was_playing:
                self.stop_preset()
                was_playing = None
            send_blank = True
        elif self.osc_handler.insane_egg_mode and was_playing:
            self.stop_preset()
            was_playing = None
        self._apply_media_setting_from_config()
        if hasattr(self, 'avatar_search'):
            self.avatar_search.apply_settings()
        self._apply_panel_visibility()
        if old_insane_mode and (not self.osc_handler.insane_egg_mode):
            send_blank = True
        self.placeholder_timer.stop()
        if send_blank:
            self.osc_handler.send_blank_message_async()
        if was_playing:
            for i in range(self.preset_list.count()):
                item = self.preset_list.item(i)
                if item.text() == was_playing:
                    self.play_preset(item)
                    break
        else:
            self._refresh_status_label()
        self._configure_and_start_placeholder_timer()

    def open_preset_manager(self):
        if not self.preset_manager_window:
            self.preset_manager_window = PresetManagerWindow(parent=self)
        self.preset_manager_window.show()
        self.preset_manager_window.raise_()
        self.preset_manager_window.activateWindow()

    def delete_selected_preset(self):
        if self.osc_handler.is_exclusive_mode_active():
            self.status_label.setText('Status: Presets disabled in Exclusive Mode')
            return
        items = self.preset_list.selectedItems()
        if not items:
            QMessageBox.information(self, 'No Selection', 'Select a preset to delete.')
            return
        name = items[0].text()
        reply = QMessageBox.question(self, 'Delete Preset', f"Delete preset '{name}' from presets.json?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not delete_preset(name):
            QMessageBox.warning(self, 'Not Found', f"Preset '{name}' was not found.")
            return
        if self.current_playing_preset == name:
            self.stop_preset()
        self.on_presets_changed_from_manager()
        self.status_label.setText(f'Status: Deleted {name}')

    def on_presets_changed_from_manager(self):
        self.preset_animations.load_animations()
        self._refresh_preset_list()

    def _refresh_preset_list(self) -> None:
        try:
            preset_names = self.preset_animations.get_available_presets()
            self.preset_list.clear()
            if preset_names:
                self.preset_list.addItems(preset_names)
            else:
                self.preset_list.addItems(['Gamesense', 'Aimware', 'Fatality'])
        except Exception:
            self.preset_list.addItems(['Gamesense', 'Aimware', 'Fatality'])

    def load_presets(self):
        self._refresh_preset_list()

    def init_media(self):
        self.media.song_updated.connect(self.on_media_update)

    def on_media_update(self, song_info):
        if self.osc_handler.is_exclusive_mode_active():
            return
        if not song_info:
            self.osc_handler.clear_media_text()
            if not self.current_playing_preset:
                self._refresh_status_label()
            return
        display_text = (song_info.get('display_text') or '').strip()
        if not display_text:
            self.osc_handler.clear_media_text()
            if not self.current_playing_preset:
                self._refresh_status_label()
            return
        changed = self.osc_handler.update_media_text(display_text)
        if not self.osc_handler.media_enabled:
            return
        if self.current_playing_preset:
            if changed:
                self.status_label.setText('Status: Playing (Media + Preset)')
            return
        self.status_label.setText('Status: Playing (Media)')
        if changed:
            self.osc_handler.push_media_to_chatbox(force=True)

    def _apply_exclusive_ui_state(self) -> None:
        active = self.osc_handler.is_exclusive_mode_active()
        for w in (getattr(self, 'btn_add', None), getattr(self, 'btn_remove', None), getattr(self, 'preset_list', None)):
            if w is not None:
                w.setEnabled(not active)

    def closeEvent(self, event):
        self.cleanup()
        event.accept()

    def cleanup(self):
        logger.info('Application shutting down')
        self.osc_handler.stop_animation()
        self.placeholder_timer.stop()
        if hasattr(self, 'avatar_search'):
            self.avatar_search.cleanup()
        if hasattr(self, 'friends_list'):
            self.friends_list.cleanup()
        if hasattr(self, 'player_list'):
            self.player_list.cleanup()
        if hasattr(self, 'account_info'):
            self.account_info.cleanup()
        if hasattr(self, 'media'):
            self.media.stop()
            self.media.wait()
        try:
            self.osc_handler.shutdown()
        except Exception:
            pass
        if self.settings_window:
            self.settings_window.close()
