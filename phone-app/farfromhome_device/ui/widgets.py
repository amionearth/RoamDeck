"""Custom painted widgets: compass, persona, QR card, tiles, tab bar.

Redesigned to match the inspo screenshots:
- CompassWidget  — gold needle, glowing centre dot, degree-readout pill
- BottomTabBar   — three-tab pill bar (Compass / Chat / Hub)
- PersonaWidget  — AI avatar bubble (chat-style)
- QrCard         — white QR card with join info
- TileGrid       — 5×5 bingo grid
- TickerList     — live feed
- CountdownLabel — mm:ss countdown
- LogView        — bounded scrolling log
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from .screens.theme import PALETTE

logger = logging.getLogger("ffh.ui.widgets")


# ──────────────────────────────────────────────────────────────────────────────
# CompassWidget  (inspo Screen 1)
# ──────────────────────────────────────────────────────────────────────────────
class CompassWidget(QtWidgets.QWidget):
    """Gold needle compass matching the inspo design.

    Draws:
    • Outer gold ring with thin tick marks
    • Inner dark-navy filled dial
    • N/S/E/W labels (N in gold, rest in mist)
    • Slim triangular gold needle pointing to target
    • Glowing gold centre dot
    • No distance field — brief §5.2 forbids it.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self._arrow      = 0.0    # degrees: (bearing − heading) mod 360
        self._heading    = 0.0
        self._label      = "next stop"
        self._has_fix    = True
        self._state      = "running"
        self._confidence = 0.8
        self._pulse      = 0.0   # for the searching animation
        self._glow       = 0.0   # for centre-dot pulse

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(50)   # 20 fps is plenty for smooth animation

    # ── state ──────────────────────────────────────────────────────────────
    def set_arrow(self, arrow_angle: float, heading: float = 0.0) -> None:
        self._arrow   = float(arrow_angle) % 360.0
        self._heading = float(heading)    % 360.0
        self.update()

    def set_label(self, label: str) -> None:
        if label != self._label:
            self._label = label
            self.update()

    def set_fix(self, has_fix: bool, state: str = "",
                confidence: Optional[float] = None) -> None:
        self._has_fix = has_fix
        if state:
            self._state = state
        if confidence is not None:
            self._confidence = max(0.0, min(1.0, confidence))
        self.update()

    def apply_compass_update(self, payload: Dict[str, Any]) -> None:
        self.set_arrow(payload.get("arrow_angle", 0.0),
                       payload.get("heading_deg",  0.0))
        self.set_label(str(payload.get("target_label") or "next stop"))
        self.set_fix(bool(payload.get("has_fix", True)),
                     str(payload.get("state", "")),
                     payload.get("heading_confidence"))

    # ── animation ──────────────────────────────────────────────────────────
    def _animate(self) -> None:
        self._pulse = (self._pulse + 0.07) % (2 * math.pi)
        self._glow  = (self._glow  + 0.04) % (2 * math.pi)
        if not self._has_fix:
            self.update()
        else:
            # Only redraw for the centre-dot glow, cheaper than full repaint
            self.update()

    # ── painting ───────────────────────────────────────────────────────────
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)

        w, h   = self.width(), self.height()
        side   = min(w, h)
        cx, cy = w / 2.0, h / 2.0
        centre = QtCore.QPointF(cx, cy)

        outer_r  = side * 0.455   # outer ring radius
        inner_r  = side * 0.395   # dial fill radius
        tick_big = side * 0.04
        tick_sm  = side * 0.022

        gold   = QtGui.QColor(PALETTE["signal"])      # #F2C700
        dark   = QtGui.QColor(PALETTE["deep_sea"])    # navy
        misted = QtGui.QColor(PALETTE["mist"])
        bg     = QtGui.QColor(PALETTE["night"])

        # ── outer glow ring ──────────────────────────────────────────────
        glow_alpha = int(40 + 30 * math.sin(self._glow))
        glow_col   = QtGui.QColor(gold)
        glow_col.setAlpha(glow_alpha)
        glow_pen   = QtGui.QPen(glow_col, side * 0.022)
        painter.setPen(glow_pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawEllipse(centre, outer_r + side * 0.018,
                                    outer_r + side * 0.018)

        # ── outer gold ring ──────────────────────────────────────────────
        painter.setPen(QtGui.QPen(gold, max(2.0, side * 0.012)))
        painter.drawEllipse(centre, outer_r, outer_r)

        # ── inner dark dial fill ─────────────────────────────────────────
        painter.setBrush(QtGui.QBrush(dark))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(centre, inner_r, inner_r)

        # ── tick marks ───────────────────────────────────────────────────
        painter.setPen(QtGui.QPen(misted, max(1.0, side * 0.006)))
        for deg in range(0, 360, 10):
            length = tick_big if deg % 45 == 0 else tick_sm
            rad    = math.radians(deg - 90)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            p1 = QtCore.QPointF(cx + cos_r * inner_r,
                                cy + sin_r * inner_r)
            p2 = QtCore.QPointF(cx + cos_r * (inner_r - length),
                                cy + sin_r * (inner_r - length))
            painter.drawLine(p1, p2)

        # ── cardinal labels N/E/S/W ──────────────────────────────────────
        font = QtGui.QFont(PALETTE.get("__font__", "Segoe UI"))
        font.setPointSizeF(max(10.0, side * 0.052))
        font.setBold(True)
        painter.setFont(font)
        label_r = inner_r * 0.70
        for label, deg in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            rad = math.radians(deg - 90)
            px  = cx + math.cos(rad) * label_r
            py  = cy + math.sin(rad) * label_r
            rect = QtCore.QRectF(px - 18, py - 14, 36, 28)
            col = gold if label == "N" else misted
            painter.setPen(QtGui.QPen(col))
            painter.drawText(rect, QtCore.Qt.AlignCenter, label)

        # ── needle or searching ring ─────────────────────────────────────
        if self._has_fix:
            self._draw_needle(painter, cx, cy, side, inner_r, gold)
        else:
            self._draw_searching(painter, cx, cy, side, inner_r)

        # ── centre glowing dot ───────────────────────────────────────────
        dot_r  = side * 0.048
        alpha  = int(160 + 80 * math.sin(self._glow))
        glow2  = QtGui.QColor(gold)
        glow2.setAlpha(alpha)
        grad   = QtGui.QRadialGradient(centre, dot_r * 2.5)
        grad.setColorAt(0.0, glow2)
        grad.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
        painter.setBrush(QtGui.QBrush(grad))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(centre, dot_r * 2.5, dot_r * 2.5)

        painter.setBrush(QtGui.QBrush(gold))
        painter.drawEllipse(centre, dot_r, dot_r)

        painter.end()

    def _draw_needle(self, painter: QtGui.QPainter, cx: float, cy: float,
                     side: float, inner_r: float,
                     gold: QtGui.QColor) -> None:
        """Slim triangular gold needle, inspo style."""
        angle = math.radians(self._arrow - 90)
        tip_r = inner_r * 0.72
        base_w = math.radians(9)   # half-angle of the needle base

        tip   = QtCore.QPointF(cx + math.cos(angle)          * tip_r,
                               cy + math.sin(angle)          * tip_r)
        left  = QtCore.QPointF(cx + math.cos(angle + base_w) * inner_r * 0.25,
                               cy + math.sin(angle + base_w) * inner_r * 0.25)
        right = QtCore.QPointF(cx + math.cos(angle - base_w) * inner_r * 0.25,
                               cy + math.sin(angle - base_w) * inner_r * 0.25)
        tail  = QtCore.QPointF(cx - math.cos(angle) * inner_r * 0.18,
                               cy - math.sin(angle) * inner_r * 0.18)

        # Needle shadow / glow
        shadow_col = QtGui.QColor(gold)
        shadow_col.setAlpha(50)
        painter.setBrush(QtGui.QBrush(shadow_col))
        painter.setPen(QtCore.Qt.NoPen)
        expand = 6
        big_tip   = QtCore.QPointF(tip.x()   + expand * math.cos(angle),
                                   tip.y()   + expand * math.sin(angle))
        big_left  = QtCore.QPointF(left.x()  - expand * math.sin(angle),
                                   left.y()  + expand * math.cos(angle))
        big_right = QtCore.QPointF(right.x() + expand * math.sin(angle),
                                   right.y() - expand * math.cos(angle))
        painter.drawPolygon(QtGui.QPolygonF([big_tip, big_left, tail, big_right]))

        # Needle body
        painter.setBrush(QtGui.QBrush(gold))
        painter.setPen(QtGui.QPen(gold.lighter(115), max(1.0, side * 0.005)))
        painter.drawPolygon(QtGui.QPolygonF([tip, left, tail, right]))

    def _draw_searching(self, painter: QtGui.QPainter, cx: float, cy: float,
                        side: float, inner_r: float) -> None:
        """Breathing ring shown while waiting for a GPS fix."""
        alpha  = int(100 + 80 * math.sin(self._pulse))
        colour = QtGui.QColor(PALETTE["warning"])
        colour.setAlpha(alpha)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(QtGui.QPen(colour, 5))
        painter.drawEllipse(QtCore.QPointF(cx, cy),
                            inner_r * 0.52, inner_r * 0.52)
        font = QtGui.QFont()
        font.setPointSizeF(max(11.0, side * 0.05))
        painter.setFont(font)
        painter.setPen(QtGui.QPen(QtGui.QColor(PALETTE["warning"])))
        painter.drawText(
            QtCore.QRectF(cx - inner_r, cy - 18, inner_r * 2, 36),
            QtCore.Qt.AlignCenter, "searching for GPS",
        )


# ──────────────────────────────────────────────────────────────────────────────
# DegreePill  — "000° N" readout below the compass
# ──────────────────────────────────────────────────────────────────────────────
class DegreePill(QtWidgets.QWidget):
    """Rounded pill showing the current bearing (e.g. '042° NE')."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._bearing  = 0.0
        self._cardinal = "N"
        self.setMinimumSize(180, 52)
        self.setMaximumHeight(56)

    def set_bearing(self, bearing_deg: float) -> None:
        self._bearing  = bearing_deg % 360.0
        self._cardinal = _deg_to_cardinal(self._bearing)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        r   = QtCore.QRectF(0, 0, self.width(), self.height())
        bg  = QtGui.QColor(PALETTE["deep_sea"])
        painter.setBrush(bg)
        painter.setPen(QtGui.QPen(QtGui.QColor(PALETTE["border"]), 1))
        painter.drawRoundedRect(r.adjusted(1, 1, -1, -1),
                                r.height() / 2, r.height() / 2)

        gold = QtGui.QColor(PALETTE["signal"])
        font = QtGui.QFont()
        font.setPointSizeF(max(14.0, self.height() * 0.38))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(gold)
        text = f"{int(self._bearing):03d}°  {self._cardinal}"
        painter.drawText(r, QtCore.Qt.AlignCenter, text)
        painter.end()


def _deg_to_cardinal(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx  = int((deg + 22.5) / 45.0) % 8
    return dirs[idx]


# ──────────────────────────────────────────────────────────────────────────────
# BottomTabBar  (inspo: pill tab bar at the bottom)
# ──────────────────────────────────────────────────────────────────────────────
class BottomTabBar(QtWidgets.QWidget):
    """Three-tab gold pill bar: Compass | Chat | Hub.

    The active tab gets a gold filled circle; inactive tabs are dark-grey.
    Emits ``tab_changed(str)`` when a tab is pressed.
    """

    tab_changed = QtCore.Signal(str)

    TABS = [
        ("compass", "Compass"),
        ("chat",    "Chat"),
        ("hub",     "Hub"),
    ]

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._active = "compass"
        self.setFixedHeight(74)
        self._build()

    def _build(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(0)

        # Pill container
        pill = QtWidgets.QFrame()
        pill.setObjectName("TabPill")
        pill.setStyleSheet(
            f"QFrame#TabPill {{"
            f"  background-color: {PALETTE['deep_sea']};"
            f"  border-radius: 30px;"
            f"  border: 1px solid {PALETTE['border']};"
            f"}}"
        )
        pill_layout = QtWidgets.QHBoxLayout(pill)
        pill_layout.setContentsMargins(6, 6, 6, 6)
        pill_layout.setSpacing(0)

        self._buttons: Dict[str, "_TabButton"] = {}
        for key, label in self.TABS:
            btn = _TabButton(key, label, active=(key == self._active))
            btn.clicked_tab.connect(self._on_tab)
            pill_layout.addWidget(btn, 1)
            self._buttons[key] = btn

        layout.addWidget(pill)

    def _on_tab(self, key: str) -> None:
        if key == self._active:
            return
        old = self._active
        self._active = key
        self._buttons[old].set_active(False)
        self._buttons[key].set_active(True)
        self.tab_changed.emit(key)

    def set_active(self, key: str) -> None:
        self._on_tab(key)


class _TabButton(QtWidgets.QWidget):
    """Single tab button — circle + label."""

    clicked_tab = QtCore.Signal(str)

    def __init__(self, key: str, label: str, active: bool = False,
                 parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._key    = key
        self._label  = label
        self._active = active
        self.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.setMinimumHeight(56)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.setAlignment(QtCore.Qt.AlignCenter)

        self._dot   = _TabDot(active)
        self._lbl   = QtWidgets.QLabel(label)
        self._lbl.setAlignment(QtCore.Qt.AlignCenter)
        self._lbl.setStyleSheet(
            f"font-size: 12px; font-weight: {'700' if active else '400'};"
            f"color: {'#F2C700' if active else PALETTE['mist']};"
            f"background: transparent;"
        )
        layout.addWidget(self._dot,  0, QtCore.Qt.AlignCenter)
        layout.addWidget(self._lbl,  0, QtCore.Qt.AlignCenter)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._dot.set_active(active)
        self._lbl.setStyleSheet(
            f"font-size: 12px; font-weight: {'700' if active else '400'};"
            f"color: {'#F2C700' if active else PALETTE['mist']};"
            f"background: transparent;"
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        self.clicked_tab.emit(self._key)


class _TabDot(QtWidgets.QWidget):
    """Circle: gold+filled when active, dark surface when inactive."""

    def __init__(self, active: bool = False,
                 parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._active = active
        self.setFixedSize(38, 38)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        r = min(cx, cy) - 2
        if self._active:
            painter.setBrush(QtGui.QColor(PALETTE["signal"]))
            painter.setPen(QtCore.Qt.NoPen)
        else:
            painter.setBrush(QtGui.QColor(PALETTE["surface"]))
            painter.setPen(QtGui.QPen(QtGui.QColor(PALETTE["border"]), 1))
        painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)
        painter.end()


# ──────────────────────────────────────────────────────────────────────────────
# PersonaWidget  (AI avatar bubble, inspo Screen 2)
# ──────────────────────────────────────────────────────────────────────────────
class PersonaWidget(QtWidgets.QWidget):
    """Animated AI avatar + speech bubble — chat style."""

    MAX_HISTORY = 4

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(100)
        self._lines: List[str] = []
        self._blink = 0.0
        self._mood  = "calm"

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(120)

    def say(self, text: str, mood: str = "") -> None:
        cleaned = " ".join(str(text).split())
        if not cleaned:
            return
        if not self._lines or self._lines[-1] != cleaned:
            self._lines.append(cleaned)
            del self._lines[: -self.MAX_HISTORY]
        if mood:
            self._mood = mood
        self.update()

    def last_line(self) -> str:
        return self._lines[-1] if self._lines else ""

    def _animate(self) -> None:
        self._blink = (self._blink + 0.05) % (2 * math.pi)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        h         = self.height()
        face_size = min(88, h - 8)
        face_rect = QtCore.QRectF(6, (h - face_size) / 2, face_size, face_size)

        # Avatar background colour
        gold    = QtGui.QColor(PALETTE["signal"])
        surface = QtGui.QColor(PALETTE["deep_sea"])

        # Dark surface circle
        painter.setBrush(surface)
        painter.setPen(QtGui.QPen(gold, 2))
        painter.drawRoundedRect(face_rect, face_size * 0.28, face_size * 0.28)

        # Star/spark icon  (simple ✦ drawn as lines)
        painter.setPen(QtGui.QPen(gold, max(2.0, face_size * 0.04)))
        fc = face_rect.center()
        arm = face_size * 0.20
        painter.drawLine(
            QtCore.QPointF(fc.x(), fc.y() - arm),
            QtCore.QPointF(fc.x(), fc.y() + arm))
        painter.drawLine(
            QtCore.QPointF(fc.x() - arm, fc.y()),
            QtCore.QPointF(fc.x() + arm, fc.y()))
        diag = arm * 0.65
        painter.setPen(QtGui.QPen(gold, max(1.5, face_size * 0.025)))
        painter.drawLine(
            QtCore.QPointF(fc.x() - diag, fc.y() - diag),
            QtCore.QPointF(fc.x() + diag, fc.y() + diag))
        painter.drawLine(
            QtCore.QPointF(fc.x() + diag, fc.y() - diag),
            QtCore.QPointF(fc.x() - diag, fc.y() + diag))

        # Bubble
        bubble = QtCore.QRectF(face_size + 18, 6,
                               max(40.0, self.width() - face_size - 24),
                               h - 12)
        painter.setBrush(QtGui.QBrush(surface))
        painter.setPen(QtGui.QPen(QtGui.QColor(PALETTE["border"]), 1))
        painter.drawRoundedRect(bubble, 14, 14)

        painter.setPen(QtGui.QPen(QtGui.QColor(PALETTE["paper"])))
        font = QtGui.QFont()
        font.setPointSizeF(max(9.5, h * 0.072))
        painter.setFont(font)
        lines = self._lines[-2:] or ["Ready when you are."]
        text  = "\n".join(lines)
        painter.drawText(
            bubble.adjusted(12, 8, -12, -8),
            int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                | QtCore.Qt.TextWordWrap),
            text,
        )
        painter.end()


# ──────────────────────────────────────────────────────────────────────────────
# QrCard  (white card with QR, code, address, wifi hint)
# ──────────────────────────────────────────────────────────────────────────────
class QrCard(QtWidgets.QWidget):
    """White QR card matching inspo Hub screen."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._pixmap:  Optional[QtGui.QPixmap] = None
        self._code    = ""
        self._address = ""
        self._wifi    = ""
        self._hint    = "Phones must be on the same WiFi as this handheld."
        self._status  = ""
        self.setMinimumSize(280, 340)

    def set_join(self, code: str, address: str, png_bytes: Optional[bytes],
                 wifi: str = "", hint: str = "") -> None:
        self._code    = code    or ""
        self._address = address or ""
        self._wifi    = wifi    or ""
        if hint:
            self._hint = hint
        if png_bytes:
            pixmap = QtGui.QPixmap()
            if pixmap.loadFromData(png_bytes, "PNG"):
                self._pixmap = pixmap
        self.update()

    def set_status(self, text: str) -> None:
        self._status = text
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)

        # White card
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 16, 16)

        gold = QtGui.QColor(PALETTE["signal"])
        ink  = QtGui.QColor(PALETTE["ink"])

        font = QtGui.QFont()
        font.setBold(True)
        font.setPointSizeF(max(12.0, self.height() * 0.048))
        painter.setFont(font)
        painter.setPen(QtGui.QPen(ink))
        painter.drawText(
            QtCore.QRectF(12, 10, self.width() - 24, 30),
            QtCore.Qt.AlignCenter, "Scan to join",
        )

        top = 46
        if self._pixmap is not None:
            side   = min(self.width() - 48,
                         max(100.0, self.height() - 180.0))
            target = QtCore.QRectF((self.width() - side) / 2, top, side, side)
            painter.drawPixmap(target, self._pixmap,
                               QtCore.QRectF(self._pixmap.rect()))
            top += side + 10

        font.setPointSizeF(max(14.0, self.height() * 0.058))
        painter.setFont(font)
        painter.setPen(gold)
        painter.drawText(
            QtCore.QRectF(12, top, self.width() - 24, 36),
            QtCore.Qt.AlignCenter,
            f"Code  {self._code}" if self._code else "",
        )
        top += 34

        font.setBold(False)
        font.setPointSizeF(max(9.0, self.height() * 0.038))
        painter.setFont(font)
        painter.setPen(QtGui.QPen(ink))
        painter.drawText(
            QtCore.QRectF(12, top, self.width() - 24, 24),
            int(QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap),
            self._address,
        )
        top += 22

        if self._wifi:
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QtGui.QPen(QtGui.QColor(PALETTE["warning"])))
            painter.drawText(
                QtCore.QRectF(12, top, self.width() - 24, 22),
                int(QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap),
                f"WiFi: {self._wifi}",
            )
            top += 20

        font.setBold(False)
        font.setPointSizeF(max(8.5, self.height() * 0.032))
        painter.setFont(font)
        painter.setPen(QtGui.QPen(QtGui.QColor(PALETTE["mist"])))
        painter.drawText(
            QtCore.QRectF(12, top, self.width() - 24, 40),
            int(QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap),
            self._status or self._hint,
        )
        painter.end()


# ──────────────────────────────────────────────────────────────────────────────
# TileGrid  (5×5 bingo grid)
# ──────────────────────────────────────────────────────────────────────────────
class TileGrid(QtWidgets.QWidget):
    tile_clicked = QtCore.Signal(int)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._cells:   List[Dict[str, Any]] = []
        self._columns  = 5
        self.setMinimumSize(280, 280)

    def set_cells(self, cells: Sequence[Dict[str, Any]]) -> None:
        self._cells = list(cells)
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        index = self._index_at(event.position())
        if index is not None:
            self.tile_clicked.emit(index)

    def _index_at(self, point: QtCore.QPointF) -> Optional[int]:
        if not self._cells:
            return None
        rows   = max(1, math.ceil(len(self._cells) / self._columns))
        cell_w = self.width()  / self._columns
        cell_h = self.height() / rows
        col    = int(point.x() // cell_w)
        row    = int(point.y() // cell_h)
        index  = row * self._columns + col
        return index if 0 <= index < len(self._cells) else None

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        if not self._cells:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rows   = max(1, math.ceil(len(self._cells) / self._columns))
        cell_w = self.width()  / self._columns
        cell_h = self.height() / rows
        font   = QtGui.QFont()
        font.setPointSizeF(max(7.5, min(cell_w, cell_h) * 0.11))
        painter.setFont(font)
        gold    = QtGui.QColor(PALETTE["signal"])
        surface = QtGui.QColor(PALETTE["deep_sea"])
        misted  = QtGui.QColor(PALETTE["mist"])
        paper   = QtGui.QColor(PALETTE["paper"])

        for index, cell in enumerate(self._cells):
            col  = index % self._columns
            row  = index // self._columns
            rect = QtCore.QRectF(col * cell_w + 3, row * cell_h + 3,
                                 cell_w - 6, cell_h - 6)
            done = bool(cell.get("done"))
            painter.setBrush(QtGui.QBrush(gold if done else surface))
            painter.setPen(QtGui.QPen(gold if done else misted, 1.5))
            painter.drawRoundedRect(rect, 10, 10)
            painter.setPen(QtGui.QPen(QtGui.QColor("#0A0A0A") if done else paper))
            painter.drawText(
                rect.adjusted(4, 4, -4, -4),
                int(QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap),
                str(cell.get("text", "")),
            )
        painter.end()


# ──────────────────────────────────────────────────────────────────────────────
# TickerList  — live feed of game submissions
# ──────────────────────────────────────────────────────────────────────────────
class TickerList(QtWidgets.QListWidget):
    MAX_ROWS = 40

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setAlternatingRowColors(False)

    def push(self, text: str) -> None:
        self.insertItem(0, text)
        if self.count() > self.MAX_ROWS:
            self.takeItem(self.count() - 1)


# ──────────────────────────────────────────────────────────────────────────────
# CountdownLabel  — mm:ss timer
# ──────────────────────────────────────────────────────────────────────────────
class CountdownLabel(QtWidgets.QLabel):
    """mm:ss countdown; turns amber < 60 s, red at zero."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("--:--", parent)
        self.setObjectName("Mono")
        self.setAlignment(QtCore.Qt.AlignCenter)
        self._remaining: Optional[float] = None
        self._label    = ""
        self._timer    = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def set_remaining(self, seconds: Optional[float], label: str = "") -> None:
        self._remaining = seconds
        self._label     = label
        self._render()

    def _tick(self) -> None:
        if self._remaining is not None and self._remaining > 0:
            self._remaining = max(0.0, self._remaining - 1.0)
            self._render()

    def _render(self) -> None:
        if self._remaining is None:
            self.setText("--:--")
            self.setStyleSheet("")
            return
        secs  = int(self._remaining)
        mins  = secs // 60
        secs  = secs  % 60
        text  = f"{self._label}  " if self._label else ""
        text += f"{mins:02d}:{secs:02d}"
        self.setText(text)
        if self._remaining <= 0:
            self.setStyleSheet(f"color: {PALETTE['danger']};")
        elif self._remaining < 60:
            self.setStyleSheet(f"color: {PALETTE['warning']};")
        else:
            self.setStyleSheet(f"color: {PALETTE['signal']};")


# ──────────────────────────────────────────────────────────────────────────────
# LogView  — bounded scrolling plain-text log
# ──────────────────────────────────────────────────────────────────────────────
class LogView(QtWidgets.QPlainTextEdit):
    MAX_BLOCKS = 400

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(self.MAX_BLOCKS)
        self.setStyleSheet(
            f"font-family: Consolas, monospace; font-size: 12px;"
            f"background-color: {PALETTE['deep_sea']};"
            f"color: {PALETTE['mist']};"
            f"border: 1px solid {PALETTE['border']}; border-radius: 10px;"
        )

    def append_line(self, text: str) -> None:
        self.appendPlainText(text)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


# ──────────────────────────────────────────────────────────────────────────────
# NearbyChip  — small pill for "Café / Food / Hotel"
# ──────────────────────────────────────────────────────────────────────────────
class NearbyChip(QtWidgets.QPushButton):
    """Rounded pill chip for nearby-category filter."""

    def __init__(self, icon: str, label: str,
                 parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(f"{icon}  {label}", parent)
        self.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {PALETTE['deep_sea']};"
            f"  color: {PALETTE['paper']};"
            f"  border: 1px solid {PALETTE['border']};"
            f"  border-radius: 18px;"
            f"  padding: 8px 16px;"
            f"  font-size: 14px;"
            f"  font-weight: 500;"
            f"}}"
            f"QPushButton:pressed {{"
            f"  background-color: {PALETTE['signal']};"
            f"  color: #0A0A0A;"
            f"}}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# ChatBubble  — single message in the AI chat (inspo Screen 2)
# ──────────────────────────────────────────────────────────────────────────────
class ChatBubble(QtWidgets.QWidget):
    """A single chat message row: avatar + bubble, or right-aligned user bubble."""

    def __init__(self, text: str, sender: str = "ai", timestamp: str = "",
                 parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._text      = text
        self._sender    = sender   # "ai" or "user"
        self._timestamp = timestamp
        self._build()

    def _build(self) -> None:
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(8)

        bubble = QtWidgets.QLabel(self._text)
        bubble.setWordWrap(True)
        bubble.setContentsMargins(14, 10, 14, 10)

        if self._sender == "ai":
            bubble.setStyleSheet(
                f"background-color: {PALETTE['deep_sea']};"
                f"color: {PALETTE['paper']};"
                f"border-radius: 14px 14px 14px 4px;"
                f"font-size: 15px;"
            )
            # Avatar icon on the left
            avatar = _AiAvatar()
            v = QtWidgets.QVBoxLayout()
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            v.addWidget(bubble)
            ts_lbl = QtWidgets.QLabel(
                f"AI · {self._timestamp}" if self._timestamp else "AI"
            )
            ts_lbl.setStyleSheet(
                f"color: {PALETTE['mist']}; font-size: 11px;"
                f"background: transparent;"
            )
            v.addWidget(ts_lbl)
            outer.addWidget(avatar, 0, QtCore.Qt.AlignBottom)
            outer.addLayout(v, 1)
            outer.addStretch(0)
        else:
            bubble.setStyleSheet(
                f"background-color: {PALETTE['signal']};"
                f"color: #0A0A0A;"
                f"border-radius: 14px 14px 4px 14px;"
                f"font-size: 15px;"
                f"font-weight: 600;"
            )
            ts_lbl = QtWidgets.QLabel(
                f"You · {self._timestamp}" if self._timestamp else "You"
            )
            ts_lbl.setStyleSheet(
                f"color: {PALETTE['mist']}; font-size: 11px;"
                f"background: transparent;"
            )
            v = QtWidgets.QVBoxLayout()
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            v.addWidget(bubble, 0, QtCore.Qt.AlignRight)
            v.addWidget(ts_lbl, 0, QtCore.Qt.AlignRight)
            outer.addStretch(0)
            outer.addLayout(v, 1)


class _AiAvatar(QtWidgets.QWidget):
    """Small gold-ringed AI icon for the chat."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(36, 36)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        r      = min(cx, cy) - 1
        gold   = QtGui.QColor(PALETTE["signal"])
        dark   = QtGui.QColor(PALETTE["deep_sea"])

        painter.setBrush(dark)
        painter.setPen(QtGui.QPen(gold, 2))
        painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)

        # Spark lines
        arm  = r * 0.42
        diag = arm * 0.68
        painter.setPen(QtGui.QPen(gold, max(1.5, r * 0.1)))
        painter.drawLine(QtCore.QPointF(cx, cy - arm), QtCore.QPointF(cx, cy + arm))
        painter.drawLine(QtCore.QPointF(cx - arm, cy), QtCore.QPointF(cx + arm, cy))
        painter.setPen(QtGui.QPen(gold, max(1.0, r * 0.07)))
        painter.drawLine(QtCore.QPointF(cx - diag, cy - diag),
                         QtCore.QPointF(cx + diag, cy + diag))
        painter.drawLine(QtCore.QPointF(cx + diag, cy - diag),
                         QtCore.QPointF(cx - diag, cy + diag))
        painter.end()


# ──────────────────────────────────────────────────────────────────────────────
# GameTileCard  — mini-game card for the Hub grid (inspo Screen 3)
# ──────────────────────────────────────────────────────────────────────────────
class GameTileCard(QtWidgets.QWidget):
    """A tappable game card: icon circle + title + subtitle/status."""

    clicked = QtCore.Signal(str)

    def __init__(self, key: str, icon: str, title: str, subtitle: str = "",
                 active: bool = False,
                 parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._key    = key
        self._active = active
        self.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))

        self.setStyleSheet(
            f"background-color: {PALETTE['deep_sea']};"
            f"border-radius: 16px;"
            f"border: 1px solid {PALETTE['border']};"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(8)
        layout.setAlignment(QtCore.Qt.AlignHCenter)

        # Icon circle
        icon_lbl = QtWidgets.QLabel(icon)
        icon_lbl.setAlignment(QtCore.Qt.AlignCenter)
        icon_lbl.setStyleSheet(
            f"font-size: 24px;"
            f"background-color: {PALETTE['night']};"
            f"border-radius: 26px;"
            f"padding: 10px;"
            f"color: {PALETTE['signal']};"
        )
        icon_lbl.setFixedSize(52, 52)
        layout.addWidget(icon_lbl, 0, QtCore.Qt.AlignHCenter)

        title_lbl = QtWidgets.QLabel(title)
        title_lbl.setAlignment(QtCore.Qt.AlignCenter)
        title_lbl.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {PALETTE['paper']};"
            f"background: transparent; border: none;"
        )
        layout.addWidget(title_lbl)

        if subtitle:
            sub_lbl = QtWidgets.QLabel(subtitle)
            sub_lbl.setAlignment(QtCore.Qt.AlignCenter)
            gold = PALETTE['signal'] if active else PALETTE['mist']
            sub_lbl.setStyleSheet(
                f"font-size: 12px; color: {gold}; background: transparent; border: none;"
            )
            layout.addWidget(sub_lbl)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        self.clicked.emit(self._key)
