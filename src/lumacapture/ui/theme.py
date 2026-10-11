DARK = {
    "bg": "#11151c",
    "panel": "#191f29",
    "surface": "#222a36",
    "text": "#edf1f7",
    "muted": "#a0acbf",
    "line": "#303a4a",
    "accent": "#bdcdfa",
    "accent_text": "#17223b",
}
LIGHT = {
    "bg": "#f2f4f8",
    "panel": "#ffffff",
    "surface": "#e9edf4",
    "text": "#1b2434",
    "muted": "#5e6a7c",
    "line": "#d9e0ea",
    "accent": "#385ca0",
    "accent_text": "#ffffff",
}


def stylesheet(light=False):
    c = LIGHT if light else DARK
    return f"""
    QWidget {{ color: {c["text"]}; font-family: 'Inter', 'Segoe UI', sans-serif; font-size: 13px; }}
    QMainWindow, QWidget#page {{ background: {c["bg"]}; }}
    QFrame#sidebar, QFrame#card {{ background: {c["panel"]}; border: 1px solid {c["line"]}; border-radius: 16px; }}
    QFrame#sidebar {{ border-radius: 0; border-width: 0 1px 0 0; }}
    QLabel#brand {{ font-size: 23px; font-weight: 650; letter-spacing: -1px; }}
    QLabel#title {{ font-size: 29px; font-weight: 650; }}
    QLabel#subtitle, QLabel#muted {{ color: {c["muted"]}; }}
    QLabel#eyebrow {{ color: {c["muted"]}; font-size: 11px; font-weight: 650; letter-spacing: 1px; }}
    QLabel#timer {{ font-size: 32px; font-weight: 500; font-family: 'Cascadia Mono', 'DejaVu Sans Mono', monospace; }}
    QPushButton {{ background: {c["surface"]}; border: 1px solid {c["line"]}; border-radius: 9px; padding: 10px 15px; font-weight: 550; }}
    QPushButton:hover {{ border-color: {c["accent"]}; }}
    QPushButton:checked, QPushButton#primary {{ background: {c["accent"]}; color: {c["accent_text"]}; border-color: {c["accent"]}; }}
    QPushButton#danger {{ color: #eb8b96; }}
    QPushButton:disabled {{ color: {c["muted"]}; background: {c["panel"]}; }}
    QPushButton#nav {{ text-align: left; padding: 13px 18px; border: none; background: transparent; }}
    QPushButton#nav:checked {{ background: {c["surface"]}; color: {c["text"]}; }}
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{ background: {c["surface"]}; border: 1px solid {c["line"]}; border-radius: 7px; padding: 7px 10px; min-height: 20px; }}
    QComboBox QAbstractItemView {{ background: {c["panel"]}; selection-background-color: {c["accent"]}; selection-color: {c["accent_text"]}; }}
    QCheckBox {{ spacing: 9px; padding: 4px; }}
    QTableWidget {{ background: {c["panel"]}; alternate-background-color: {c["surface"]}; border: 1px solid {c["line"]}; border-radius: 10px; gridline-color: {c["line"]}; selection-background-color: {c["surface"]}; }}
    QHeaderView::section {{ background: {c["surface"]}; color: {c["muted"]}; border: none; padding: 12px 9px; }}
    QTableWidget::item {{ padding: 8px; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; }}
    QScrollBar::handle:vertical {{ background: {c["line"]}; border-radius: 4px; min-height: 25px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QGroupBox {{ border: 1px solid {c["line"]}; border-radius: 10px; margin-top: 18px; padding-top: 12px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 4px; }}
    QDialog {{ background: {c["bg"]}; }}
    QToolTip {{ background: {c["panel"]}; color: {c["text"]}; border: 1px solid {c["line"]}; padding: 5px; }}
    """
