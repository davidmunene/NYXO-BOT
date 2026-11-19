"""NYXO design language: dark navy, cyan + orange accents, rounded cards."""
from __future__ import annotations

NAVY = "#0d1b2e"
NAVY_2 = "#13243a"
NAVY_3 = "#1b304c"
LINE = "#25405f"
TEXT = "#e8f7ff"
MUTED = "#8aa4c0"
CYAN = "#3ff0ff"
CYAN_DIM = "#1aa6b8"
ORANGE = "#ff8a1f"
RED = "#ff4d5e"
GREEN = "#4ade80"

QSS = f"""
* {{ font-size: 10.5pt; color: {TEXT}; }}
QWidget#root, QDialog, QMainWindow {{ background: {NAVY}; }}
QWidget {{ background: transparent; }}
QToolTip {{ background: {NAVY_3}; color: {TEXT}; border: 1px solid {LINE}; padding: 4px; border-radius: 6px; }}
QLabel#title {{ font-size: 15pt; font-weight: 700; color: {TEXT}; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 700; color: {CYAN}; padding-top: 4px; }}
QLabel#muted, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#status {{ color: {MUTED}; font-size: 9.5pt; }}
QFrame#card {{ background: {NAVY_2}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#confirm {{ background: #2a1d10; border: 1px solid {ORANGE}; border-radius: 14px; }}
QFrame#msgUser {{ background: #12395a; border-radius: 14px; border-bottom-right-radius: 4px; }}
QFrame#msgBot {{ background: {NAVY_2}; border: 1px solid {LINE}; border-radius: 14px; border-bottom-left-radius: 4px; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {NAVY_3}; border: 1px solid {LINE}; border-radius: 10px; padding: 6px 10px;
    selection-background-color: {CYAN_DIM};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {CYAN}; }}
QComboBox QAbstractItemView {{ background: {NAVY_3}; border: 1px solid {LINE}; selection-background-color: {CYAN_DIM}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QPushButton {{
    background: {NAVY_3}; border: 1px solid {LINE}; border-radius: 10px; padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {CYAN}; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {NAVY_3}; }}
QPushButton#primary {{ background: {CYAN}; color: {NAVY}; border: none; }}
QPushButton#primary:hover {{ background: #8af6ff; }}
QPushButton#accent {{ background: {ORANGE}; color: {NAVY}; border: none; }}
QPushButton#danger {{ border-color: {RED}; color: {RED}; }}
QPushButton#icon {{ padding: 6px; min-width: 30px; border-radius: 15px; }}
QPushButton#icon:checked {{ background: {RED}; border-color: {RED}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 34px; height: 18px; border-radius: 9px; background: {NAVY_3}; border: 1px solid {LINE}; }}
QCheckBox::indicator:checked {{ background: {CYAN_DIM}; border-color: {CYAN}; }}
QSlider::groove:horizontal {{ height: 6px; background: {NAVY_3}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {CYAN_DIM}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {ORANGE}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
QListWidget#nav {{ background: {NAVY_2}; border: none; border-right: 1px solid {LINE}; padding: 8px; outline: none; }}
QListWidget#nav::item {{ padding: 9px 12px; border-radius: 10px; margin: 2px 0; }}
QListWidget#nav::item:selected {{ background: {NAVY_3}; color: {CYAN}; border-left: 3px solid {ORANGE}; }}
QListWidget, QTableWidget {{ background: {NAVY_2}; border: 1px solid {LINE}; border-radius: 10px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: {NAVY_3}; color: {CYAN}; }}
QHeaderView::section {{ background: {NAVY_3}; border: none; padding: 4px; color: {MUTED}; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QMenu {{ background: {NAVY_2}; border: 1px solid {LINE}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {NAVY_3}; color: {CYAN}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}
"""
