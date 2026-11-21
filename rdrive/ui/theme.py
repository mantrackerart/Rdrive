"""Giao diện tối phong cách 2 khung, hài hòa khi nhúng trong SyAu."""
from __future__ import annotations

BG = "#14161b"
TOOLBAR_BG = "#1a1d24"
PANEL = "#1b1e25"
PANEL_2 = "#232732"
BORDER = "#2d323e"
BORDER_HOVER = "#3d4454"
TEXT = "#e6e8ec"
MUTED = "#8b93a1"
ACCENT = "#ff8a3d"
ACCENT_HOVER = "#ff9d5c"
BLUE = "#4f8cff"
GOOD = "#3ecf8e"
BAD = "#ff5d6c"
WARN = "#f5b841"

QSS = f"""
QWidget#RdriveRoot, QMainWindow#RdriveWindow {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 9.5pt;
}}

QFrame#RibbonBar {{
    background: {TOOLBAR_BG};
    border-bottom: 1px solid {BORDER};
}}

QToolButton#RibbonBtn {{
    background: transparent;
    color: {TEXT};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 10px;
    font-weight: 600;
    font-size: 9pt;
}}
QToolButton#RibbonBtn:hover {{
    background: {PANEL_2};
    border-color: {BORDER_HOVER};
}}
QToolButton#RibbonBtn:checked {{
    background: #272d3a;
    border-color: {BLUE};
    color: #ffffff;
}}

QFrame#PaneFrame {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}

QFrame#PaneTabBar {{
    background: #171920;
    border-bottom: 1px solid {BORDER};
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}

QPushButton#PaneTab {{
    background: transparent;
    color: {MUTED};
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    padding: 6px 10px;
    font-weight: 600;
    font-size: 9pt;
}}
QPushButton#PaneTab:hover {{
    background: #212530;
    color: {TEXT};
}}
QPushButton#PaneTab[active="true"] {{
    background: {PANEL};
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}

QFrame#PaneTabItem {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
}}
QFrame#PaneTabItem:hover {{
    background: #212530;
}}
QFrame#PaneTabItem[active="true"] {{
    background: {PANEL};
    border-bottom: 2px solid {ACCENT};
}}
QLabel#PaneTabLabel {{
    background: transparent;
    color: {MUTED};
    font-weight: 600;
    font-size: 9pt;
    border: none;
}}
QFrame#PaneTabItem:hover QLabel#PaneTabLabel,
QFrame#PaneTabItem[active="true"] QLabel#PaneTabLabel {{
    color: {TEXT};
}}
QToolButton#PaneTabClose {{
    background: transparent;
    color: {MUTED};
    border: none;
    border-radius: 3px;
    font-size: 9pt;
    font-weight: 700;
    padding: 0px;
}}
QToolButton#PaneTabClose:hover {{
    background: #3a2328;
    color: #ff6b78;
}}

QPushButton#PaneAddTab {{
    background: transparent;
    color: {MUTED};
    border: none;
    padding: 4px 9px;
    font-size: 11pt;
    font-weight: 700;
}}
QPushButton#PaneAddTab:hover {{
    background: {PANEL_2};
    color: {TEXT};
    border-radius: 4px;
}}

QFrame#NavBar {{
    background: {TOOLBAR_BG};
    border-bottom: 1px solid {BORDER};
}}

QFrame#MiniToolbar {{
    background: #181b22;
    border-bottom: 1px solid {BORDER};
}}

QToolButton#IconBtn {{
    background: transparent;
    color: {TEXT};
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 3px 6px;
}}
QToolButton#IconBtn:hover {{
    background: {PANEL_2};
    border-color: {BORDER_HOVER};
}}
QToolButton#IconBtn:checked {{
    background: #2a3140;
    border-color: {ACCENT};
}}

QToolButton#CenterAction {{
    background: {PANEL_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px;
    font-weight: 700;
    font-size: 11pt;
}}
QToolButton#CenterAction:hover {{
    background: #2d3444;
    border-color: {ACCENT};
    color: {ACCENT};
}}

QTreeWidget#DirTree, QTreeWidget#FileList {{
    background: #15181e;
    alternate-background-color: #191c23;
    border: none;
    outline: none;
    color: {TEXT};
    selection-background-color: #2b3850;
}}
QTreeWidget#DirTree::item, QTreeWidget#FileList::item {{
    padding: 3px 4px;
    border: none;
}}
QTreeWidget#DirTree::item:hover, QTreeWidget#FileList::item:hover {{
    background: #202530;
}}
QTreeWidget#DirTree::item:selected, QTreeWidget#FileList::item:selected {{
    background: #2b3a55;
    color: #ffffff;
}}
QHeaderView::section {{
    background: #1a1d24;
    color: {MUTED};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 5px 8px;
    font-weight: 600;
    font-size: 8.5pt;
}}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background: {PANEL_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 4px 8px;
    selection-background-color: {ACCENT};
    selection-color: #1b1206;
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}

QPushButton {{
    background: {PANEL_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: #2b303c;
    border-color: {BORDER_HOVER};
}}
QPushButton:disabled {{
    color: #5b616d;
    background: {PANEL};
}}
QPushButton#PrimaryBtn {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #1b1206;
}}
QPushButton#PrimaryBtn:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#DangerBtn {{
    color: {BAD};
}}

QFrame#PaneStatus {{
    background: #171920;
    border-top: 1px solid {BORDER};
    border-bottom-left-radius: 6px;
    border-bottom-right-radius: 6px;
}}

QLabel#Muted {{
    color: {MUTED};
    font-size: 8.5pt;
}}
QLabel#BadgeGood {{
    color: {GOOD};
    background: #193126;
    border: 1px solid #245c45;
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 8.5pt;
    font-weight: 600;
}}
QLabel#BadgeWarn {{
    color: {WARN};
    background: #332917;
    border: 1px solid #6b5222;
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 8.5pt;
    font-weight: 600;
}}
QLabel#BadgeBad {{
    color: {BAD};
    background: #341a1e;
    border: 1px solid #6b2b33;
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 8.5pt;
    font-weight: 600;
}}

QProgressBar {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    border-radius: 4px;
    height: 12px;
    text-align: center;
    font-size: 8pt;
    color: {TEXT};
}}
QProgressBar::chunk {{
    background: {BLUE};
    border-radius: 3px;
}}
QProgressBar[state="done"]::chunk {{
    background: {GOOD};
}}
QProgressBar[state="error"]::chunk {{
    background: {BAD};
}}

QSplitter::handle {{
    background: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}
QSplitter::handle:vertical {{
    height: 4px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle {{
    background: #383e4b;
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:hover {{
    background: #4b5365;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

QDialog {{
    background: {PANEL};
    color: {TEXT};
}}
QMenu {{
    background: {PANEL_2};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 22px 5px 12px;
    border-radius: 4px;
    color: {TEXT};
}}
QMenu::item:selected {{
    background: #31394a;
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 8px;
}}
"""
