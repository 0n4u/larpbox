BASE_WIDGET = "\n    QWidget {\n        background-color: transparent;\n        color: #dcdcdc;\n        font-family: 'Segoe UI';\n        font-size: 10pt;\n    }\n"
ROUND_CONTAINER = '\n    #roundContainer {\n        background-color: #1e1e1e;\n        border-radius: 12px;\n    }\n    #previewContainer {\n        background-color: #1e1e1e;\n        border-radius: 12px;\n    }\n'
GROUP_BOX = '\n    QGroupBox {\n        border: 1px solid #3a3a3a;\n        border-radius: 8px;\n        margin-top: 8px;\n        padding-top: 10px;\n        font-weight: normal;\n    }\n    QGroupBox::title {\n        subcontrol-origin: margin;\n        left: 10px;\n        padding: 0 5px 0 5px;\n    }\n'
BUTTONS = '\n    QPushButton {\n        background-color: #262a33;\n        border: 1px solid #3f4a5e;\n        border-radius: 4px;\n        padding: 2px 6px;\n    }\n    QPushButton:hover {\n        background-color: #2f3542;\n        border-color: #666666;\n    }\n    QPushButton:pressed {\n        background-color: #222733;\n        border-color: #808080;\n    }\n    QPushButton:disabled {\n        background-color: #1b1b1b;\n        color: #666666;\n        border-color: #333333;\n    }\n'
INPUTS = '\n    QLineEdit, QSpinBox {\n        background-color: #232323;\n        border: 1px solid #3a3a3a;\n        border-radius: 4px;\n        padding: 3px;\n    }\n    QLineEdit:focus, QSpinBox:focus {\n        border: 1px solid #4ea3ff;\n    }\n'
LIST_WIDGET = '\n    QListWidget {\n        background-color: #232323;\n        border: 1px solid #3a3a3a;\n        border-radius: 6px;\n    }\n'
SCROLLBAR = '\n    QScrollBar:vertical {\n        background: rgba(255, 255, 255, 0.04);\n        width: 10px;\n        margin: 6px 3px 6px 0;\n        border-radius: 5px;\n    }\n    QScrollBar::handle:vertical {\n        background: #454545;\n        min-height: 28px;\n        border-radius: 5px;\n    }\n    QScrollBar::handle:vertical:hover {\n        background: #5a8abf;\n    }\n    QScrollBar::handle:vertical:pressed {\n        background: #4ea3ff;\n    }\n    QScrollBar::add-line:vertical,\n    QScrollBar::sub-line:vertical {\n        background: transparent;\n        height: 0px;\n        border: none;\n    }\n    QScrollBar::add-page:vertical,\n    QScrollBar::sub-page:vertical {\n        background: transparent;\n    }\n    QScrollBar:horizontal {\n        background: transparent;\n        height: 0px;\n        margin: 0;\n    }\n'
MENU = '\n    QMenu {\n        background-color: #1e1e1e;\n        color: #dcdcdc;\n        border: 1px solid #3a3a3a;\n        border-radius: 6px;\n        padding: 4px;\n    }\n    QMenu::item {\n        padding: 6px 28px 6px 12px;\n        border-radius: 4px;\n        background-color: transparent;\n    }\n    QMenu::item:selected {\n        background-color: #2f3542;\n        color: #ffffff;\n    }\n    QMenu::item:disabled {\n        color: #666666;\n    }\n    QMenu::separator {\n        height: 1px;\n        background: #3a3a3a;\n        margin: 4px 8px;\n    }\n    QMenu::icon {\n        padding-left: 8px;\n    }\n'
COMBO_BOX = '\n    QComboBox {\n        background-color: #232323;\n        border: 1px solid #3a3a3a;\n        border-radius: 4px;\n        padding: 3px 8px;\n        min-height: 22px;\n    }\n    QComboBox:hover {\n        border-color: #4ea3ff;\n    }\n    QComboBox::drop-down {\n        border: none;\n        width: 18px;\n    }\n    QComboBox QAbstractItemView {\n        background-color: #1e1e1e;\n        color: #dcdcdc;\n        border: 1px solid #3a3a3a;\n        selection-background-color: #2f3542;\n        selection-color: #ffffff;\n        outline: none;\n    }\n'

def dark_theme(*extra_blocks: str) -> str:
    parts = [BASE_WIDGET, ROUND_CONTAINER, GROUP_BOX, BUTTONS, INPUTS, LIST_WIDGET, SCROLLBAR, MENU, COMBO_BOX]
    parts.extend(extra_blocks)
    return '\n'.join(parts)


def themed_menu(parent=None):
    from PyQt6.QtWidgets import QMenu

    menu = QMenu(parent)
    menu.setStyleSheet(MENU)
    return menu
