from __future__ import annotations
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget
from ..theme import MENU
from ..version import __version__

def build_app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128):
        icon.addPixmap(_render_icon(size), QIcon.Mode.Normal, QIcon.State.Off)
    return icon

def _render_icon(size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = max(1, round(size * 0.06))
    inner = size - margin * 2
    radius = max(3, round(size * 0.24))
    painter.setBrush(QColor('#171b24'))
    painter.setPen(QColor('#2f3d54'))
    painter.drawRoundedRect(margin, margin, inner, inner, radius, radius)
    accent_h = max(2, round(size * 0.14))
    accent_y = margin + inner - accent_h - max(1, round(size * 0.08))
    accent_w = inner - max(2, round(size * 0.16))
    accent_x = margin + (inner - accent_w) // 2
    painter.setBrush(QColor('#4ea3ff'))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(accent_x, accent_y, accent_w, accent_h, 2, 2)
    glow = QColor('#4ea3ff')
    glow.setAlpha(36)
    painter.setBrush(glow)
    painter.drawEllipse(round(size * 0.18), round(size * 0.14), round(size * 0.64), round(size * 0.52))
    font = QFont('Segoe UI', max(7, round(size * 0.46)), QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor('#eef4ff'))
    painter.drawText(pix.rect().adjusted(0, -max(1, round(size * 0.04)), 0, 0), Qt.AlignmentFlag.AlignCenter, 'L')
    painter.end()
    return pix

class AppSystemTray:

    def __init__(self, window: QWidget, app: QApplication):
        self.window = window
        self.app = app
        self.tray: QSystemTrayIcon | None = None
        self._minimize_hint_shown = False

    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def is_active(self) -> bool:
        return self.tray is not None

    def show_minimized_hint(self) -> None:
        if self.tray is None or self._minimize_hint_shown:
            return
        self._minimize_hint_shown = True
        self.tray.showMessage('larpbox', 'Still running in the tray. Double-click the icon to reopen.', QSystemTrayIcon.MessageIcon.Information, 3000)

    def setup(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = build_app_icon()
        self.app.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self.app)
        self.tray.setToolTip(f'larpbox v{__version__} — VRChat companion\nDouble-click to show or hide')
        menu = QMenu()
        menu.setStyleSheet(MENU + '\n            QMenu {\n                min-width: 196px;\n                padding: 6px;\n            }\n            QMenu::item {\n                padding: 8px 18px 8px 14px;\n                margin: 2px 0;\n            }\n            QMenu::separator {\n                margin: 6px 10px;\n            }\n        ')
        menu.aboutToShow.connect(self._sync_menu_state)
        self._action_show = QAction('  Open larpbox', menu)
        self._action_show.triggered.connect(self._show_window)
        menu.addAction(self._action_show)
        self._action_hide = QAction('  Hide window', menu)
        self._action_hide.triggered.connect(self._hide_window)
        menu.addAction(self._action_hide)
        menu.addSeparator()
        self._action_quit = QAction('  Quit larpbox', menu)
        self._action_quit.triggered.connect(self._quit)
        menu.addAction(self._action_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    def _sync_menu_state(self) -> None:
        visible = self.window.isVisible() and (not self.window.isMinimized())
        self._action_show.setEnabled(not visible)
        self._action_hide.setEnabled(visible)

    def _show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        self._sync_menu_state()

    def _hide_window(self) -> None:
        self.window.hide()
        self._sync_menu_state()

    def _toggle_window(self) -> None:
        if self.window.isVisible() and (not self.window.isMinimized()):
            self._hide_window()
        else:
            self._show_window()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._toggle_window()

    def _quit(self) -> None:
        self.app.quit()

    def cleanup(self) -> None:
        if self.tray is None:
            return
        self.tray.hide()
        self.tray.setContextMenu(None)
        self.tray.deleteLater()
        self.tray = None
