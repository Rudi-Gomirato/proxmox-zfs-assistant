"""Desktop palette and widget styling, independent of workflow decisions."""
from PySide6.QtGui import QColor
from .core import BAD, GOOD
from .workflow import ACTIVE_STATES

STYLE = """
QWidget { background:#101923; color:#dce5ef; font-family:'Segoe UI'; font-size:10pt; }
QMainWindow { background:#101923; }
QFrame#sidebar { background:#0b121b; border-right:1px solid #243142; }
QFrame#sidebar QLabel { background:transparent; }
QLabel#brand { font-size:17pt; font-weight:700; color:#f0f6fc; }
QLabel#title { font-size:22pt; font-weight:650; color:#f0f6fc; }
QLabel#subtitle, QLabel#muted { color:#9aaec4; }
QLabel#badge { color:#8be5ce; background:#163b39; padding:8px 12px; border-radius:6px; }
QListWidget#navigation { background:transparent; border:0; outline:0; }
QListWidget#navigation::item { padding:13px 10px; margin:3px 0; border-left:3px solid transparent; color:#aab9cb; }
QListWidget#navigation::item:selected { color:#a4f2dc; background:#183331; border-left:3px solid #57d2b0; border-radius:4px; }
QListWidget#navigation::item:hover { background:#192636; }
QFrame#card, QFrame#notice { background:#182532; border:1px solid #2b3e50; border-radius:9px; }
QFrame#card QLabel, QFrame#notice QLabel { background:transparent; }
QLabel#metric { font-size:22pt; font-weight:700; }
QLabel#stepTitle { font-size:14pt; font-weight:600; color:#eaf3fc; }
QLabel#steps { color:#8fe3cd; font-weight:600; }
QTabWidget::pane { border:0; }
QGroupBox { border:1px solid #2b3e50; border-radius:8px; margin-top:12px; padding:14px; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 6px; color:#acbfd2; }
QPushButton, QToolButton { background:#233447; border:1px solid #3c536b; border-radius:6px; padding:8px 12px; }
QPushButton:hover, QToolButton:hover { background:#2d475b; border-color:#65809a; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus { border:2px solid #69d7be; }
QPushButton:disabled, QToolButton:disabled { color:#77899b; background:#172330; border-color:#283949; }
QPushButton#primary { background:#177e6c; color:#fff; border-color:#4bb89f; font-weight:600; }
QPushButton#danger { background:#713e49; border-color:#a66875; color:#fff; }
QLineEdit, QSpinBox, QComboBox { background:#0e1b28; border:1px solid #3a5068; border-radius:5px; padding:7px; selection-background-color:#276756; }
QTreeWidget, QTableWidget, QPlainTextEdit, QTextBrowser { background:#121e2b; border:1px solid #2c4053; border-radius:6px; alternate-background-color:#162635; }
QTreeWidget::item, QTableWidget::item { padding:7px; }
QTreeWidget::item:selected, QTableWidget::item:selected { background:#254e60; color:#f4f8fc; }
QHeaderView::section { background:#1c3042; color:#b6cadd; padding:9px; border:0; font-weight:600; }
QTextBrowser { padding:12px; }
QCheckBox { spacing:8px; }
QStatusBar { background:#0b121b; color:#9eb4c9; }
QSplitter::handle { background:#2b4053; width:5px; }
QScrollArea { border:0; }
QProgressBar { border:1px solid #365367; border-radius:5px; background:#14202d; text-align:center; min-height:20px; }
QProgressBar::chunk { background:#217e71; border-radius:4px; }
"""


def color_for(state):
    return QColor("#7de0c4" if state in GOOD | {"COMPLETED", "OK"} else
                  "#f5c878" if state in ACTIVE_STATES | {"DEGRADED", "BOOT_TEST_REQUIRED", "BOOT_TEST_PENDING", "ESP_INITIALIZATION_REQUIRED"} else
                  "#ffacb5" if state in BAD | {"FAILED", "UNKNOWN", "BLOCKED", "ERROR", "BOOT_REVIEW_REQUIRED", "ZFS_REVIEW_REQUIRED"} else "#b9cce6")
