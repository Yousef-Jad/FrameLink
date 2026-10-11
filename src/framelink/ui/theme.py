"""Semantic appearance tokens for the FrameLink desktop surface."""

import os


PALETTES = {
    "dark": {
        "content": "#0B0D12",
        "elevated": "#151922",
        "glass": "rgba(25, 29, 38, 0.82)",
        "opaque": "#151922",
        "text": "#F2F4F8",
        "secondary": "#9299A8",
        "tertiary": "#6F7787",
        "separator": "rgba(255, 255, 255, 0.07)",
        "separator_strong": "rgba(255, 255, 255, 0.13)",
        "accent": "#AFC5FF",
        "accent_text": "#101626",
        "recording": "#FF453A",
        "focus": "#C8D7FF",
        "success": "#82D7A4",
        "warning": "#F0C36B",
        "shadow": "rgba(0, 0, 0, 0.38)",
    },
    "light": {
        "content": "#F4F5F8",
        "elevated": "#FFFFFF",
        "glass": "rgba(255, 255, 255, 0.84)",
        "opaque": "#FFFFFF",
        "text": "#172033",
        "secondary": "#5D687A",
        "tertiary": "#7C8797",
        "separator": "rgba(23, 32, 51, 0.10)",
        "separator_strong": "rgba(23, 32, 51, 0.16)",
        "accent": "#385CA0",
        "accent_text": "#FFFFFF",
        "recording": "#D92D27",
        "focus": "#385CA0",
        "success": "#247A4A",
        "warning": "#8B5E00",
        "shadow": "rgba(21, 28, 44, 0.16)",
    },
}

SPACING = {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 32}
RADII = {"control": 8, "preview": 12, "floating": 16}
MOTION = {"mode_ms": 160, "drawer_ms": 180, "recording_ms": 900}


def palette(light=False, high_contrast=False):
    """Return a copy of the semantic palette for the current appearance."""

    values = dict(PALETTES["light" if light else "dark"])
    if high_contrast:
        values.update(
            {
                "glass": values["opaque"],
                "separator": "#616B7B",
                "separator_strong": "#8491A8",
                "secondary": values["text"],
                "tertiary": values["secondary"],
                "shadow": "#000000",
            }
        )
    return values


def motion_enabled():
    """Respect the existing automation switch used by headless verification."""

    return os.environ.get("QT_NO_ANIMATIONS", "").lower() not in {"1", "true", "yes"}


def stylesheet(light=False, high_contrast=False):
    """Build the shared stylesheet from semantic roles rather than widget colors."""

    c = palette(light, high_contrast)
    border = c["separator_strong"] if high_contrast else c["separator"]
    return f"""
    QWidget {{
        color: {c["text"]};
        font-family: 'Inter', 'Segoe UI', 'Noto Sans', sans-serif;
        font-size: 13px;
        outline: none;
    }}
    QMainWindow, QWidget#page, QWidget#content {{ background: {c["content"]}; }}
    QFrame#rail {{
        background: {c["glass"]};
        border: 1px solid {border};
        border-radius: 16px;
    }}
    QFrame#material-glass {{
        background: {c["glass"]};
        border: 1px solid {border};
        border-radius: 16px;
    }}
    QFrame#material-floating {{
        background: {c["glass"]};
        border: 1px solid {border};
        border-radius: 16px;
    }}
    QFrame#material-elevated, QFrame#material-opaque {{
        background: {c["elevated"]};
        border: 1px solid {border};
        border-radius: 12px;
    }}
    QFrame#recording-tile {{ background: {c["elevated"]}; border: 1px solid {c["separator"]}; border-radius: 12px; }}
    QFrame#recording-tile:hover {{ border-color: {c["focus"]}; }}
    QLabel#recording-thumbnail {{ background: {c["content"]}; border-radius: 8px; min-height: 130px; }}
    QLabel#recording-title {{ color: {c["text"]}; font-weight: 650; }}
    QFrame#library-jobs {{ background: {c["glass"]}; border: 1px solid {c["separator"]}; border-radius: 10px; }}
    QFrame#mode-segment {{ background: {c["elevated"]}; border: 1px solid {c["separator"]}; border-radius: 10px; }}
    QLabel#brand-mark {{
        color: {c["text"]};
        font-size: 15px;
        font-weight: 700;
        letter-spacing: 1px;
    }}
    QLabel#title {{ font-size: 22px; font-weight: 650; letter-spacing: -0.3px; }}
    QLabel#subtitle, QLabel#muted {{ color: {c["secondary"]}; }}
    QLabel#eyebrow {{
        color: {c["secondary"]};
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 1.1px;
    }}
    QLabel#timer {{
        color: {c["text"]};
        font-size: 20px;
        font-weight: 550;
        font-family: 'Cascadia Mono', 'DejaVu Sans Mono', monospace;
    }}
    QLabel#recording-state {{ color: {c["recording"]}; font-weight: 650; }}
    QLabel#status-pill {{
        color: {c["secondary"]};
        background: transparent;
        padding: 4px 8px;
    }}
    QToolButton#rail-button {{
        color: {c["secondary"]};
        background: transparent;
        border: 1px solid transparent;
        border-radius: 10px;
        min-width: 40px;
        max-width: 40px;
        min-height: 40px;
        max-height: 40px;
        padding: 8px;
    }}
    QToolButton#rail-button:hover {{ color: {c["text"]}; background: {c["separator"]}; }}
    QToolButton#rail-button:checked {{
        color: {c["text"]};
        background: {c["separator"]};
        border-left: 2px solid {c["accent"]};
    }}
    QToolButton#icon-button {{
        color: {c["secondary"]};
        background: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        min-width: 36px;
        min-height: 36px;
        padding: 7px;
    }}
    QToolButton#icon-button:hover, QToolButton#icon-button:checked {{
        color: {c["text"]};
        background: {c["separator"]};
    }}
    QToolButton#icon-button:focus, QToolButton#rail-button:focus,
    QPushButton:focus, QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{
        border: 1px solid {c["focus"]};
    }}
    QToolButton#record-button {{
        color: #FFFFFF;
        background: {c["accent"]};
        border: none;
        border-radius: 10px;
        padding: 9px 14px;
        font-weight: 700;
    }}
    QToolButton#record-button:checked {{ background: {c["recording"]}; }}
    QToolButton#record-button:hover {{ background: {c["recording"]}; }}
    QPushButton {{
        color: {c["text"]};
        background: {c["elevated"]};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 8px 12px;
        min-height: 20px;
    }}
    QPushButton:hover {{ border-color: {c["focus"]}; }}
    QPushButton:pressed {{ background: {c["separator_strong"]}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {c["tertiary"]}; background: {c["content"]}; }}
    QPushButton#primary {{ color: {c["accent_text"]}; background: {c["accent"]}; border-color: {c["accent"]}; }}
    QPushButton#danger {{ color: {c["recording"]}; }}
    QToolButton#segment {{
        color: {c["secondary"]};
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        padding: 7px 10px;
        min-height: 24px;
    }}
    QToolButton#segment:hover {{ color: {c["text"]}; }}
    QToolButton#segment:checked {{ color: {c["text"]}; border-bottom-color: {c["accent"]}; }}
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
        color: {c["text"]};
        background: {c["elevated"]};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 7px 10px;
        min-height: 20px;
    }}
    QComboBox QAbstractItemView {{
        color: {c["text"]};
        background: {c["elevated"]};
        selection-background-color: {c["accent"]};
        selection-color: {c["accent_text"]};
    }}
    QCheckBox {{ spacing: 8px; padding: 4px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 24px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QToolTip {{ color: {c["text"]}; background: {c["elevated"]}; border: 1px solid {border}; padding: 5px; }}
    QProgressBar {{ color: {c["text"]}; background: {c["content"]}; border: none; border-radius: 3px; text-align: center; height: 6px; }}
    QProgressBar::chunk {{ background: {c["accent"]}; border-radius: 3px; }}
    """
