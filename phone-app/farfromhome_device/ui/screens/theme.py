"""Theme tokens and the Qt stylesheet.

Redesigned to match the inspo screens: pure black background, gold/amber accent
(#F2C700), dark-slate surfaces, and a bottom-tab-bar navigation shell.
Colour names are kept stable so nothing else in the codebase needs changing.
"""

from __future__ import annotations

from typing import Dict

# ---------------------------------------------------------------------------
# Palette — maps the original token names onto the new inspo values so every
# caller (widgets, screens) still uses PALETTE["lagoon"] / PALETTE["signal"]
# without needing a find-replace.
# ---------------------------------------------------------------------------
PALETTE: Dict[str, str] = {
    # backgrounds
    "night":     "#0A0A0A",   # pure black — main background
    "deep_sea":  "#1A1A2E",   # dark navy — card / surface
    "ink":       "#0D0D0D",   # near-black text base

    # text / muted
    "fog":       "#E8EDEE",   # light text
    "paper":     "#FFFFFF",   # pure white text
    "mist":      "#6B7280",   # muted / secondary text

    # accents — gold is the primary brand colour in inspo
    "signal":    "#F2C700",   # gold — primary accent (was amber / orange)
    "lagoon":    "#F2C700",   # keep same token name, now gold
    "lagoon_dark": "#C9A800", # darker gold for pressed states

    # status
    "success":   "#22C55E",   # green
    "danger":    "#EF4444",   # red
    "warning":   "#F59E0B",   # amber

    # surface variants
    "surface":   "#141428",   # slightly lighter than deep_sea for nested cards
    "border":    "#2A2A3E",   # subtle border colour
}

FONT_STACK = '"Segoe UI", "SF Pro Display", Arial, sans-serif'
MONO_STACK = '"Consolas", "SF Mono", monospace'


def stylesheet(theme: str = "dark") -> str:
    """Application stylesheet.  Only dark theme is used now; the arg is kept
    for backwards-compat with callers that pass ``theme=settings.ui_theme``."""
    bg      = PALETTE["night"]
    surface = PALETTE["deep_sea"]
    text    = PALETTE["paper"]
    muted   = PALETTE["mist"]
    accent  = PALETTE["signal"]       # gold
    border  = PALETTE["border"]

    return f"""
    /* ── base ─────────────────────────────────────────────────── */
    QWidget {{
        background-color: {bg};
        color: {text};
        font-family: {FONT_STACK};
        font-size: 15px;
    }}

    /* ── cards ─────────────────────────────────────────────────── */
    QFrame#Card, QWidget#Card {{
        background-color: {surface};
        border-radius: 18px;
        border: 1px solid {border};
    }}
    QFrame#CardFlat, QWidget#CardFlat {{
        background-color: {surface};
        border-radius: 18px;
        border: 1px solid {border};
    }}

    /* ── typography ─────────────────────────────────────────────── */
    QLabel#Title  {{ font-size: 32px; font-weight: 800; color: {accent}; }}
    QLabel#Subtitle {{ font-size: 15px; color: {muted}; }}
    QLabel#Huge {{ font-size: 60px; font-weight: 800; }}
    QLabel#Big  {{ font-size: 36px; font-weight: 700; }}
    QLabel#Mono {{
        font-family: {MONO_STACK};
        font-size: 19px;
        font-weight: 700;
        color: {accent};
    }}
    QLabel#Muted   {{ color: {muted}; font-size: 14px; }}
    QLabel#Danger  {{ color: {PALETTE['danger']};  font-weight: 700; }}
    QLabel#Success {{ color: {PALETTE['success']}; font-weight: 700; }}
    QLabel#Warning {{ color: {PALETTE['warning']}; font-weight: 700; }}
    QLabel#Gold    {{ color: {accent}; font-weight: 700; }}

    /* ── primary button (gold pill) ──────────────────────────────── */
    QPushButton {{
        background-color: {accent};
        color: #0A0A0A;
        border: none;
        border-radius: 14px;
        padding: 14px 22px;
        font-size: 16px;
        font-weight: 700;
        min-height: 38px;
    }}
    QPushButton:hover   {{ background-color: {PALETTE['lagoon_dark']}; }}
    QPushButton:pressed {{ background-color: #A88600; }}
    QPushButton:disabled {{ background-color: {surface}; color: {muted}; border: 1px solid {border}; }}

    /* secondary (outline) */
    QPushButton#Secondary {{
        background-color: {surface};
        color: {text};
        border: 1px solid {border};
    }}
    QPushButton#Secondary:hover {{ border-color: {accent}; color: {accent}; }}

    /* danger */
    QPushButton#Danger {{
        background-color: {PALETTE['danger']};
        color: {text};
    }}

    /* signal — same as primary now */
    QPushButton#Signal {{
        background-color: {accent};
        color: #0A0A0A;
    }}

    /* big variant */
    QPushButton#Big {{
        font-size: 18px;
        min-height: 52px;
        border-radius: 16px;
    }}

    /* ── list / text widgets ─────────────────────────────────────── */
    QListWidget, QTableWidget, QTextEdit, QPlainTextEdit {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 12px;
        padding: 6px;
        color: {text};
    }}
    QListWidget::item {{ padding: 10px 8px; border-radius: 8px; }}
    QListWidget::item:selected {{
        background-color: {accent};
        color: #0A0A0A;
    }}

    /* ── progress bar ────────────────────────────────────────────── */
    QProgressBar {{
        background-color: {surface};
        border: none;
        border-radius: 8px;
        height: 10px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background-color: {accent};
        border-radius: 8px;
    }}

    /* ── slider ──────────────────────────────────────────────────── */
    QSlider::groove:horizontal {{
        height: 6px;
        background: {surface};
        border-radius: 3px;
        border: 1px solid {border};
    }}
    QSlider::handle:horizontal {{
        background: {accent};
        width: 22px;
        height: 22px;
        border-radius: 11px;
        margin: -8px 0;
    }}

    /* ── form inputs ─────────────────────────────────────────────── */
    QComboBox, QSpinBox, QLineEdit {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 9px 12px;
        color: {text};
        font-size: 15px;
    }}
    QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{
        border-color: {accent};
    }}
    QComboBox::drop-down {{ border: none; }}
    QComboBox QAbstractItemView {{
        background-color: {surface};
        border: 1px solid {border};
        selection-background-color: {accent};
        selection-color: #0A0A0A;
        border-radius: 8px;
    }}

    /* ── misc ────────────────────────────────────────────────────── */
    QCheckBox {{ spacing: 10px; color: {text}; }}
    QCheckBox::indicator {{
        width: 20px; height: 20px;
        border-radius: 6px;
        border: 2px solid {border};
        background: {surface};
    }}
    QCheckBox::indicator:checked {{
        background: {accent};
        border-color: {accent};
    }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{
        background: {bg};
        width: 6px;
        border-radius: 3px;
    }}
    QScrollBar::handle:vertical {{
        background: {border};
        border-radius: 3px;
        min-height: 30px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QToolTip {{
        background-color: {surface};
        color: {text};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 4px 8px;
    }}
    QStatusBar {{ background-color: {bg}; color: {muted}; font-size: 12px; }}
    """


def apply_theme(app: object, theme: str = "dark") -> None:
    """Apply the stylesheet to a ``QApplication``. Safe to call twice."""
    try:
        app.setStyleSheet(stylesheet(theme))  # type: ignore[attr-defined]
    except Exception:
        pass


def colour(name: str, default: str = "#F2C700") -> str:
    return PALETTE.get(name, default)
