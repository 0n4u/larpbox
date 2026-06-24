"""Central theme tokens and stylesheet blocks for larpbox."""

                                                                      
COLOR_TEXT = '#dcdcdc'
COLOR_TEXT_BRIGHT = '#ffffff'
COLOR_TEXT_MUTED = '#888888'
COLOR_TEXT_SUBTLE = '#9aa0a6'
COLOR_BORDER = '#3a3a3a'
COLOR_BORDER_ROW = '#2a2a2a'
COLOR_BG_INPUT = '#232323'
COLOR_BG_BUTTON = '#262a33'
COLOR_BG_CONTAINER = '#1e1e1e'
COLOR_BG_CARD = '#1e1e1e'
COLOR_BG_HOVER = '#2f3542'
COLOR_ACCENT = '#4ea3ff'
COLOR_SUCCESS = '#7db87d'
COLOR_ERROR = '#e07070'
COLOR_WARNING = '#e8a040'
COLOR_DANGER_BRIGHT = '#e57373'

                                                                
TITLE_CHROME_BTN_SIZE = 18
PANEL_TOOLBAR_BTN_SIZE = 28
TITLE_TOOLBAR_BTN_SIZE = 22

BASE_WIDGET = f"""
    QWidget {{
        background-color: transparent;
        color: {COLOR_TEXT};
        font-family: 'Segoe UI';
        font-size: 10pt;
    }}
"""
ROUND_CONTAINER = f"""
    #roundContainer {{
        background-color: {COLOR_BG_CONTAINER};
        border-radius: 12px;
    }}
    #previewContainer {{
        background-color: {COLOR_BG_CONTAINER};
        border-radius: 12px;
    }}
"""
GROUP_BOX = f"""
    QGroupBox {{
        border: 1px solid {COLOR_BORDER};
        border-radius: 8px;
        margin-top: 8px;
        padding-top: 10px;
        font-weight: normal;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 5px 0 5px;
    }}
"""
BUTTONS = f"""
    QPushButton {{
        background-color: {COLOR_BG_BUTTON};
        border: 1px solid #3f4a5e;
        border-radius: 4px;
        padding: 2px 6px;
    }}
    QPushButton:hover {{
        background-color: {COLOR_BG_HOVER};
        border-color: #666666;
    }}
    QPushButton:pressed {{
        background-color: #222733;
        border-color: #808080;
    }}
    QPushButton:disabled {{
        background-color: #1b1b1b;
        color: {COLOR_TEXT_MUTED};
        border-color: #333333;
    }}
"""
INPUTS = f"""
    QLineEdit, QSpinBox {{
        background-color: {COLOR_BG_INPUT};
        border: 1px solid {COLOR_BORDER};
        border-radius: 4px;
        padding: 3px;
    }}
    QLineEdit:focus, QSpinBox:focus {{
        border: 1px solid {COLOR_ACCENT};
    }}
"""
LIST_WIDGET = f"""
    QListWidget {{
        background-color: {COLOR_BG_INPUT};
        border: 1px solid {COLOR_BORDER};
        border-radius: 6px;
    }}
"""
SCROLLBAR = f"""
    QScrollBar:vertical {{
        background: rgba(255, 255, 255, 0.04);
        width: 10px;
        margin: 6px 3px 6px 0;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical {{
        background: #454545;
        min-height: 28px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: #5a8abf;
    }}
    QScrollBar::handle:vertical:pressed {{
        background: {COLOR_ACCENT};
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        background: transparent;
        height: 0px;
        border: none;
    }}
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 0px;
        margin: 0;
    }}
"""
MENU = f"""
    QMenu {{
        background-color: {COLOR_BG_CONTAINER};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        border-radius: 6px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 28px 6px 12px;
        border-radius: 4px;
        background-color: transparent;
    }}
    QMenu::item:selected {{
        background-color: {COLOR_BG_HOVER};
        color: {COLOR_TEXT_BRIGHT};
    }}
    QMenu::item:disabled {{
        color: {COLOR_TEXT_MUTED};
    }}
    QMenu::separator {{
        height: 1px;
        background: {COLOR_BORDER};
        margin: 4px 8px;
    }}
    QMenu::icon {{
        padding-left: 8px;
    }}
"""
COMBO_BOX = f"""
    QComboBox {{
        background-color: {COLOR_BG_INPUT};
        border: 1px solid {COLOR_BORDER};
        border-radius: 4px;
        padding: 3px 8px;
        min-height: 22px;
    }}
    QComboBox:hover {{
        border-color: {COLOR_ACCENT};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 18px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {COLOR_BG_CONTAINER};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        selection-background-color: {COLOR_BG_HOVER};
        selection-color: {COLOR_TEXT_BRIGHT};
        outline: none;
    }}
"""
TOOLBAR_BUTTON = f"""
    QPushButton {{
        background-color: {COLOR_BG_BUTTON};
        border: 1px solid #3f4a5e;
        border-radius: 4px;
        padding: 0px;
        font-size: 11px;
        color: {COLOR_TEXT};
    }}
    QPushButton:hover {{
        background-color: {COLOR_BG_HOVER};
        border-color: {COLOR_ACCENT};
    }}
    QPushButton:pressed {{
        background-color: #222733;
    }}
    QPushButton:disabled {{
        background-color: #1b1b1b;
        color: {COLOR_TEXT_MUTED};
        border-color: #333333;
    }}
    QPushButton:checked {{
        background-color: {COLOR_BG_HOVER};
        border-color: {COLOR_ACCENT};
        color: {COLOR_ACCENT};
    }}
"""

def apply_title_toolbar_button(button) -> None:
    from PyQt6.QtWidgets import QSizePolicy
    button.setFixedSize(TITLE_TOOLBAR_BTN_SIZE, TITLE_TOOLBAR_BTN_SIZE)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    button.setStyleSheet(TOOLBAR_BUTTON)


def apply_panel_toolbar_button(button) -> None:
    """Size panel toolbar controls to match the title-bar button row."""
    from PyQt6.QtWidgets import QSizePolicy
    button.setFixedSize(PANEL_TOOLBAR_BTN_SIZE, PANEL_TOOLBAR_BTN_SIZE)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    button.setStyleSheet(TOOLBAR_BUTTON)

THUMB_STYLE = f'QLabel#avatarThumb {{ background-color: {COLOR_BG_INPUT}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; color: {COLOR_TEXT_MUTED}; }}'
FRIEND_THUMB_STYLE = f'QLabel#avatarThumb {{ background-color: {COLOR_BG_INPUT}; border: none; border-radius: 0px; color: {COLOR_TEXT_MUTED}; }}'
THEME_SEPARATOR = f'background-color: {COLOR_BORDER}; max-height: 1px; margin: 6px 0;'
PANEL_STYLE = f"""
QTabWidget::pane {{
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    top: -1px;
}}
QTabBar::tab {{
    background: {COLOR_BG_INPUT};
    color: #b8b8b8;
    border: 1px solid {COLOR_BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 5px 14px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {COLOR_BG_HOVER};
    color: {COLOR_TEXT_BRIGHT};
}}
QTabBar::tab:hover {{
    color: {COLOR_ACCENT};
}}
QListWidget::item {{
    padding: 4px 6px;
    border-bottom: 1px solid {COLOR_BORDER_ROW};
}}
QListWidget::item:selected {{
    background: {COLOR_BG_HOVER};
    color: {COLOR_TEXT_BRIGHT};
}}
QLabel#panelSectionHeader {{
    font-weight: 600;
    color: {COLOR_TEXT};
}}
QLabel#panelHint {{
    color: {COLOR_TEXT_MUTED};
    font-size: 9pt;
}}
"""
DASHBOARD_CARD_STYLE = f"""
    QFrame#dashboardCard {{
        background: {COLOR_BG_CARD};
        border: 1px solid {COLOR_BORDER};
        border-radius: 8px;
    }}
    QLabel#cardTitle {{
        font-weight: 600;
        color: {COLOR_TEXT};
    }}
    QLabel#cardBig {{
        font-size: 18px;
        font-weight: 700;
        color: {COLOR_ACCENT};
    }}
    QLabel#cardLine {{
        color: {COLOR_TEXT};
    }}
    QLabel#cardMuted {{
        color: {COLOR_TEXT_SUBTLE};
    }}
"""
NOTIFICATION_ROW_STYLE = f"""
    QFrame#notifRow {{
        border-bottom: 1px solid {COLOR_BORDER_ROW};
    }}
    QLabel#notifTitle {{
        font-weight: 600;
        color: {COLOR_TEXT};
    }}
    QLabel#notifTitle[unread="true"] {{
        color: {COLOR_TEXT_BRIGHT};
    }}
    QLabel#notifSubtitle {{
        color: {COLOR_TEXT_SUBTLE};
        font-size: 9pt;
    }}
    QLabel#notifWhen {{
        color: {COLOR_TEXT_MUTED};
        font-size: 8pt;
    }}
"""
STATUS_COLORS = {
    'none': COLOR_SUCCESS,
    'minor': COLOR_WARNING,
    'major': '#e0772f',
    'critical': COLOR_ERROR,
    'maintenance': COLOR_ACCENT,
    'unknown': COLOR_TEXT_SUBTLE,
}

def dark_theme(*extra_blocks: str) -> str:
    parts = [BASE_WIDGET, ROUND_CONTAINER, GROUP_BOX, BUTTONS, INPUTS, LIST_WIDGET, SCROLLBAR, MENU, COMBO_BOX]
    parts.extend(extra_blocks)
    return '\n'.join(parts)

def themed_menu(parent=None):
    from PyQt6.QtWidgets import QMenu
    menu = QMenu(parent)
    menu.setStyleSheet(MENU)
    return menu

def motion_enabled() -> bool:
    from .config import get_bool, load_config_cached
    return not get_bool(load_config_cached().get('reduce_motion', True))
