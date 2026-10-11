"""The Qt application shell: screens, the bus bridge, and the main loop.

Redesigned to match the inspo screenshots:
  • Three-tab bottom navigation: Compass | Chat | Hub
  • CompassScreen  — gold needle compass + current-location card + Nearby chips
  • ChatScreen     — AI assistant chat interface (replaces BootScreen)
  • HubScreen      — QR scanner card + mini-game grid (replaces GameScreen)

All other screens (Plan, Planning, Camera, Task, Arrival, WrapUp, Settings, SOS)
are kept and accessible through programmatic navigation; they sit in a QStackedWidget
layer above the three-tab shell.

Architecture note: this app is a **view of the bus** — no screen holds a Plan,
DeviceClient, or engine. See ARCHITECTURE.md §2.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.bus import Bus
from ..core.config import Settings
from ..core.models import BusMessage, GameHostState, Plan
from ..games.host import GameHost
from ..games.template_loader import template_summaries
from ..net.diagnostics import current_ssid
from ..net.qr import JoinTarget
from .screens.theme import PALETTE, apply_theme
from .widgets import (
    BottomTabBar,
    ChatBubble,
    CompassWidget,
    CountdownLabel,
    DegreePill,
    GameTileCard,
    LogView,
    NearbyChip,
    PersonaWidget,
    QrCard,
    TickerList,
    TileGrid,
)

logger = logging.getLogger("ffh.ui.app")


# ─────────────────────────────────────────────────────────────────────────────
# asyncio bridge
# ─────────────────────────────────────────────────────────────────────────────
class AsyncBridge:
    """Runs an asyncio loop on its own thread and accepts work from Qt."""

    def __init__(self) -> None:
        self.loop = __import__("asyncio").new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="asyncio-bridge", daemon=True
        )
        self._started = threading.Event()
        self._thread.start()
        self._started.wait(timeout=3.0)

    def _run(self) -> None:
        import asyncio
        asyncio.set_event_loop(self.loop)
        self._started.set()
        self.loop.run_forever()

    def submit(self, coro: Any) -> Any:
        import asyncio
        if not self.loop.is_running():
            logger.debug("asyncio bridge is not running; dropping coroutine")
            try:
                coro.close()
            except Exception:
                pass
            return None
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call_soon(self, fn: Callable[..., Any], *args: Any) -> None:
        self.loop.call_soon_threadsafe(fn, *args)

    def stop(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(timeout=2.0)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Base screen
# ─────────────────────────────────────────────────────────────────────────────
class Screen(QtWidgets.QWidget):
    """Base screen — dumb renderer of whatever it is handed."""

    name  = "screen"
    title = ""

    def __init__(self, shell: "MainWindow") -> None:
        super().__init__(shell)
        self.shell = shell
        self.build()

    def build(self) -> None:
        pass

    def on_show(self) -> None:
        pass

    def on_bus(self, message: BusMessage) -> None:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Helper: section header label
# ─────────────────────────────────────────────────────────────────────────────
def _section(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setStyleSheet(
        f"font-size: 18px; font-weight: 700; color: {PALETTE['paper']};"
        f"background: transparent;"
    )
    return lbl


def _gold_link(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setStyleSheet(
        f"font-size: 14px; font-weight: 600; color: {PALETTE['signal']};"
        f"background: transparent;"
    )
    lbl.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
    return lbl


# ─────────────────────────────────────────────────────────────────────────────
# CompassScreen  (inspo Screen 1)
# ─────────────────────────────────────────────────────────────────────────────
class CompassScreen(Screen):
    """Main compass tab: gold needle, current-location card, Nearby chips."""

    name  = "compass"
    title = "Destination"

    def build(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 0)
        root.setSpacing(10)

        # ── header ──────────────────────────────────────────────────────
        hdr = QtWidgets.QHBoxLayout()
        title_lbl = QtWidgets.QLabel("Destination")
        title_lbl.setObjectName("Title")
        hdr.addWidget(title_lbl, 1)

        self.state_badge = QtWidgets.QLabel("running")
        self.state_badge.setStyleSheet(
            f"font-size: 12px; color: {PALETTE['mist']}; background: transparent;"
        )
        hdr.addWidget(self.state_badge)
        root.addLayout(hdr)

        sub = QtWidgets.QLabel("Compass Navigation")
        sub.setObjectName("Subtitle")
        root.addWidget(sub)

        # ── compass widget ───────────────────────────────────────────────
        self.compass = CompassWidget()
        self.compass.setMinimumHeight(260)
        root.addWidget(self.compass, 3)

        # ── bearing pill ─────────────────────────────────────────────────
        self.degree_pill = DegreePill()
        root.addWidget(self.degree_pill, 0, QtCore.Qt.AlignHCenter)

        # ── current-location card ────────────────────────────────────────
        loc_card = QtWidgets.QFrame()
        loc_card.setObjectName("Card")
        loc_layout = QtWidgets.QHBoxLayout(loc_card)
        loc_layout.setContentsMargins(14, 12, 14, 12)
        loc_layout.setSpacing(12)

        pin_circle = QtWidgets.QLabel("📍")
        pin_circle.setFixedSize(44, 44)
        pin_circle.setAlignment(QtCore.Qt.AlignCenter)
        pin_circle.setStyleSheet(
            f"background-color: {PALETTE['night']};"
            f"border-radius: 22px; font-size: 20px;"
        )
        loc_layout.addWidget(pin_circle)

        loc_text = QtWidgets.QVBoxLayout()
        loc_text.setSpacing(2)
        self.loc_title = QtWidgets.QLabel("Current Location")
        self.loc_title.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {PALETTE['paper']};"
            f"background: transparent;"
        )
        self.loc_coords = QtWidgets.QLabel("--° N, --° W")
        self.loc_coords.setStyleSheet(
            f"font-size: 13px; color: {PALETTE['mist']}; background: transparent;"
        )
        loc_text.addWidget(self.loc_title)
        loc_text.addWidget(self.loc_coords)
        loc_layout.addLayout(loc_text, 1)

        chevron = QtWidgets.QLabel("›")
        chevron.setStyleSheet(
            f"font-size: 22px; color: {PALETTE['mist']}; background: transparent;"
        )
        loc_layout.addWidget(chevron)
        root.addWidget(loc_card)

        # ── hint / off-route banner ──────────────────────────────────────
        self.hint = QtWidgets.QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("Subtitle")
        self.hint.hide()
        root.addWidget(self.hint)

        self.off_route = QtWidgets.QLabel("")
        self.off_route.setObjectName("Warning")
        self.off_route.setWordWrap(True)
        self.off_route.hide()
        root.addWidget(self.off_route)

        # ── Nearby section ───────────────────────────────────────────────
        nearby_row = QtWidgets.QHBoxLayout()
        nearby_row.setContentsMargins(0, 4, 0, 0)
        nearby_row.addWidget(_section("Nearby"))
        nearby_row.addStretch(1)
        see_all = _gold_link("See all")
        nearby_row.addWidget(see_all)
        root.addLayout(nearby_row)

        chips_row = QtWidgets.QHBoxLayout()
        chips_row.setSpacing(8)
        for icon, label in (("☕", "Café"), ("🍽", "Food"), ("🏨", "Hotel")):
            chips_row.addWidget(NearbyChip(icon, label))
        chips_row.addStretch(1)
        root.addLayout(chips_row)

        root.addStretch(1)

        # ── action buttons row (hidden below tab bar) ────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        for label, slot, obj in (
            ("📷 Photo",   self.shell.take_photo,                   "Secondary"),
            ("📋 Task",    lambda: self.shell.show_screen("task"),   "Secondary"),
            ("🏁 Wrap up", self.shell.arrive_now,                    "Secondary"),
            ("🆘 SOS",     self.shell.trigger_sos,                   "Danger"),
        ):
            btn = QtWidgets.QPushButton(label)
            btn.setObjectName(obj)
            btn.clicked.connect(slot)
            btn.setMinimumHeight(38)
            btn_row.addWidget(btn)
        root.addLayout(btn_row)

    # ── bus reactions ────────────────────────────────────────────────────
    def on_bus(self, message: BusMessage) -> None:
        payload = message.payload or {}
        t = message.topic

        if t == "compass.update":
            self.compass.apply_compass_update(payload)
            self.state_badge.setText(str(payload.get("state", "running")))
            bearing = float(payload.get("bearing_deg", 0.0))
            self.degree_pill.set_bearing(bearing)

        elif t == "heading.update":
            self.compass.set_fix(True, confidence=payload.get("confidence"))

        elif t == "position.update":
            lat = payload.get("lat")
            lng = payload.get("lng")
            if lat is not None and lng is not None:
                ns = "N" if lat >= 0 else "S"
                ew = "E" if lng >= 0 else "W"
                self.loc_coords.setText(
                    f"{abs(lat):.4f}° {ns}, {abs(lng):.4f}° {ew}"
                )

        elif t == "agent.say":
            pass  # persona lives on the chat tab

        elif t == "reveal.due":
            txt = str(payload.get("text", ""))
            if txt:
                self.hint.setText(txt)
                self.hint.show()

        elif t == "off_route":
            self.off_route.setText(str(payload.get("text", "You have wandered a bit.")))
            self.off_route.show()

        elif t == "sensor.gps_lost":
            self.off_route.setText("No GPS yet. Hold still for a moment.")
            self.off_route.show()

        elif t == "sensor.gps_back":
            self.off_route.hide()

        elif t == "trip.started":
            self.loc_title.setText(str(payload.get("title", "Current Location")))

        elif t == "trip.arrived":
            self.shell.show_screen("arrival")


# ─────────────────────────────────────────────────────────────────────────────
# ChatScreen  (inspo Screen 2 — AI Assistant)
# ─────────────────────────────────────────────────────────────────────────────
class ChatScreen(Screen):
    """AI assistant chat tab.

    Also handles: pairing, server status, and trip planning entry point.
    """

    name  = "chat"
    title = "AI Assistant"

    def build(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── chat header ──────────────────────────────────────────────────
        header = QtWidgets.QWidget()
        header.setStyleSheet(
            f"background-color: {PALETTE['night']};"
            f"border-bottom: 1px solid {PALETTE['border']};"
        )
        hdr_layout = QtWidgets.QHBoxLayout(header)
        hdr_layout.setContentsMargins(16, 12, 16, 12)
        hdr_layout.setSpacing(12)

        avatar = _AiHeaderAvatar()
        hdr_layout.addWidget(avatar)

        title_col = QtWidgets.QVBoxLayout()
        title_col.setSpacing(2)
        ai_title = QtWidgets.QLabel("AI Assistant")
        ai_title.setStyleSheet(
            f"font-size: 18px; font-weight: 800; color: {PALETTE['signal']};"
            f"background: transparent;"
        )
        self.status_dot = QtWidgets.QLabel("● Online · Powered by AI")
        self.status_dot.setStyleSheet(
            f"font-size: 12px; color: {PALETTE['success']}; background: transparent;"
        )
        title_col.addWidget(ai_title)
        title_col.addWidget(self.status_dot)
        hdr_layout.addLayout(title_col, 1)

        menu_btn = QtWidgets.QPushButton("···")
        menu_btn.setObjectName("Secondary")
        menu_btn.setFixedSize(40, 40)
        menu_btn.setStyleSheet(
            f"QPushButton {{ background-color: {PALETTE['deep_sea']};"
            f"  border-radius: 20px; border: 1px solid {PALETTE['border']};"
            f"  color: {PALETTE['mist']}; font-size: 16px; }}"
            f"QPushButton:hover {{ border-color: {PALETTE['signal']}; }}"
        )
        menu_btn.clicked.connect(lambda: self.shell.show_screen("settings"))
        hdr_layout.addWidget(menu_btn)
        root.addWidget(header)

        # ── scrollable message list ──────────────────────────────────────
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {PALETTE['night']}; border: none; }}"
        )

        self._msg_container = QtWidgets.QWidget()
        self._msg_container.setStyleSheet(
            f"background: {PALETTE['night']};"
        )
        self._msg_layout = QtWidgets.QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(8, 12, 8, 12)
        self._msg_layout.setSpacing(6)
        self._msg_layout.addStretch(1)

        scroll.setWidget(self._msg_container)
        self._scroll = scroll
        root.addWidget(scroll, 1)

        # ── quick-action chips ───────────────────────────────────────────
        chips_bar = QtWidgets.QWidget()
        chips_bar.setStyleSheet(
            f"background-color: {PALETTE['night']};"
            f"border-top: 1px solid {PALETTE['border']};"
        )
        chips_layout = QtWidgets.QHBoxLayout(chips_bar)
        chips_layout.setContentsMargins(12, 8, 12, 8)
        chips_layout.setSpacing(8)

        for icon, label, action in (
            ("📍", "Plan a trip",    lambda: self.shell.show_screen("plan")),
            ("🔗", "Pair device",    self.shell.start_pairing),
            ("🗺", "Offline demo",   self.shell.start_offline_demo),
        ):
            chip = NearbyChip(icon, label)
            chip.clicked.connect(action)
            chips_layout.addWidget(chip)
        chips_layout.addStretch(1)
        root.addWidget(chips_bar)

        # ── input row ────────────────────────────────────────────────────
        input_bar = QtWidgets.QWidget()
        input_bar.setStyleSheet(
            f"background-color: {PALETTE['night']};"
            f"border-top: 1px solid {PALETTE['border']};"
        )
        input_layout = QtWidgets.QHBoxLayout(input_bar)
        input_layout.setContentsMargins(12, 10, 12, 10)
        input_layout.setSpacing(10)

        self.chat_input = QtWidgets.QLineEdit()
        self.chat_input.setPlaceholderText("Ask anything...")
        self.chat_input.setMinimumHeight(46)
        self.chat_input.setStyleSheet(
            f"QLineEdit {{"
            f"  background-color: {PALETTE['deep_sea']};"
            f"  border: 1px solid {PALETTE['border']};"
            f"  border-radius: 23px;"
            f"  padding: 8px 18px;"
            f"  font-size: 15px;"
            f"  color: {PALETTE['paper']};"
            f"}}"
            f"QLineEdit:focus {{ border-color: {PALETTE['signal']}; }}"
        )
        self.chat_input.returnPressed.connect(self._send_message)
        input_layout.addWidget(self.chat_input, 1)

        send_btn = QtWidgets.QPushButton("➤")
        send_btn.setFixedSize(46, 46)
        send_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {PALETTE['signal']};"
            f"  color: #0A0A0A; border-radius: 23px;"
            f"  font-size: 18px; font-weight: 700; border: none;"
            f"}}"
            f"QPushButton:hover {{ background-color: {PALETTE['lagoon_dark']}; }}"
        )
        send_btn.clicked.connect(self._send_message)
        input_layout.addWidget(send_btn)
        root.addWidget(input_bar)

        # ── add the welcome message ──────────────────────────────────────
        self._add_ai_message(
            "Hello! I'm your AI companion. I can help with navigation, "
            "plan a trip, answer questions, and explore with you."
        )

    def _add_ai_message(self, text: str, ts: str = "") -> None:
        bubble = ChatBubble(text, sender="ai", timestamp=ts)
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, bubble)
        QtCore.QTimer.singleShot(50, self._scroll_to_bottom)

    def _add_user_message(self, text: str, ts: str = "") -> None:
        bubble = ChatBubble(text, sender="user", timestamp=ts)
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, bubble)
        QtCore.QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _send_message(self) -> None:
        text = self.chat_input.text().strip()
        if not text:
            return
        self.chat_input.clear()
        ts = time.strftime("%I:%M %p")
        self._add_user_message(text, ts)

        # Route to the planner if it looks like a trip request
        keywords = ("trip", "walk", "go", "take me", "find", "cafe",
                    "beach", "park", "explore", "chill", "playful")
        if any(kw in text.lower() for kw in keywords):
            self._add_ai_message(
                "Sure! Let me plan that for you. Switching to the planner…"
            )
            QtCore.QTimer.singleShot(800, lambda: self.shell.show_screen("plan"))
        else:
            self._add_ai_message(
                "I'm designed for trip planning. Try: 'a chill walk for two, "
                "about an hour' and I'll find somewhere mysterious for you."
            )

    def on_show(self) -> None:
        self._update_server_status()

    def _update_server_status(self) -> None:
        client = self.shell.services.get("client")
        if client is None:
            self.status_dot.setText("● Offline mode")
            self.status_dot.setStyleSheet(
                f"font-size: 12px; color: {PALETTE['mist']}; background: transparent;"
            )
            return
        self.shell.probe_server_async(self._on_server_probe)

    def _on_server_probe(self, text: str, ok: bool) -> None:
        colour = PALETTE["success"] if ok else PALETTE["warning"]
        dot    = "●" if ok else "○"
        label  = f"{dot} {'Online · Powered by AI' if ok else text}"
        self.status_dot.setText(label)
        self.status_dot.setStyleSheet(
            f"font-size: 12px; color: {colour}; background: transparent;"
        )
        if not ok:
            self._add_ai_message(
                f"I can't reach the server right now ({self.shell.settings.server_url}). "
                "Offline demo is still available."
            )

    def on_bus(self, message: BusMessage) -> None:
        payload = message.payload or {}
        if message.topic == "agent.say":
            self._add_ai_message(str(payload.get("text", "")))
        elif message.topic == "connection.status":
            if payload.get("paired") is False:
                self._add_ai_message(
                    "This handheld lost its pairing. Tap 'Pair device' to link it again."
                )
        elif message.topic == "plan.ready":
            self._add_ai_message(
                f"Plan ready: {payload.get('title', 'your trip')}. "
                "Head to the Compass tab to start walking."
            )
        elif message.topic == "plan.failed":
            self._add_ai_message(
                "I couldn't plan that trip. "
                "Try changing the mood, distance, or interests."
            )


class _AiHeaderAvatar(QtWidgets.QWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(46, 46)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        r      = min(cx, cy) - 2
        gold   = QtGui.QColor(PALETTE["signal"])
        dark   = QtGui.QColor(PALETTE["deep_sea"])
        p.setBrush(dark)
        p.setPen(QtGui.QPen(gold, 2.5))
        p.drawEllipse(QtCore.QPointF(cx, cy), r, r)
        arm  = r * 0.38
        diag = arm * 0.68
        p.setPen(QtGui.QPen(gold, max(2.0, r * 0.10)))
        p.drawLine(QtCore.QPointF(cx, cy - arm), QtCore.QPointF(cx, cy + arm))
        p.drawLine(QtCore.QPointF(cx - arm, cy), QtCore.QPointF(cx + arm, cy))
        p.setPen(QtGui.QPen(gold, max(1.0, r * 0.065)))
        p.drawLine(QtCore.QPointF(cx - diag, cy - diag),
                   QtCore.QPointF(cx + diag, cy + diag))
        p.drawLine(QtCore.QPointF(cx + diag, cy - diag),
                   QtCore.QPointF(cx - diag, cy + diag))
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# HubScreen  (inspo Screen 3 — QR & Games Hub)
# ─────────────────────────────────────────────────────────────────────────────
class HubScreen(Screen):
    """Hub tab: QR scanner card + mini-game grid, settings gear."""

    name  = "hub"
    title = "Hub"

    def build(self) -> None:
        scroll_area = QtWidgets.QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")

        container = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(container)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(14)

        # ── header ───────────────────────────────────────────────────────
        hdr = QtWidgets.QHBoxLayout()
        title_lbl = QtWidgets.QLabel("Hub")
        title_lbl.setObjectName("Title")
        hdr.addWidget(title_lbl, 1)

        gear_btn = QtWidgets.QPushButton("⚙")
        gear_btn.setFixedSize(44, 44)
        gear_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {PALETTE['deep_sea']};"
            f"  border-radius: 22px; border: 1px solid {PALETTE['border']};"
            f"  color: {PALETTE['mist']}; font-size: 18px;"
            f"}}"
            f"QPushButton:hover {{ border-color: {PALETTE['signal']}; "
            f"  color: {PALETTE['signal']}; }}"
        )
        gear_btn.clicked.connect(lambda: self.shell.show_screen("settings"))
        hdr.addWidget(gear_btn)
        root.addLayout(hdr)

        sub = QtWidgets.QLabel("QR Scanner & Games")
        sub.setObjectName("Subtitle")
        root.addWidget(sub)

        # ── QR scanner card ───────────────────────────────────────────────
        qr_card = QtWidgets.QFrame()
        qr_card.setObjectName("Card")
        qr_card.setMinimumHeight(180)
        qr_inner = QtWidgets.QVBoxLayout(qr_card)
        qr_inner.setContentsMargins(20, 20, 20, 20)
        qr_inner.setSpacing(10)
        qr_inner.setAlignment(QtCore.Qt.AlignHCenter)

        qr_icon = QtWidgets.QLabel("▣")
        qr_icon.setAlignment(QtCore.Qt.AlignCenter)
        qr_icon.setStyleSheet(
            f"font-size: 52px; color: {PALETTE['signal']}; background: transparent;"
        )
        qr_inner.addWidget(qr_icon)

        qr_title = QtWidgets.QLabel("Scan QR Code")
        qr_title.setAlignment(QtCore.Qt.AlignCenter)
        qr_title.setStyleSheet(
            f"font-size: 17px; font-weight: 700; color: {PALETTE['paper']};"
            f"background: transparent;"
        )
        qr_inner.addWidget(qr_title)

        qr_sub = QtWidgets.QLabel("Point camera at any QR code")
        qr_sub.setAlignment(QtCore.Qt.AlignCenter)
        qr_sub.setStyleSheet(
            f"font-size: 13px; color: {PALETTE['mist']}; background: transparent;"
        )
        qr_inner.addWidget(qr_sub)

        scan_btn = QtWidgets.QPushButton("📷  Open Scanner")
        scan_btn.setObjectName("Big")
        scan_btn.clicked.connect(lambda: self.shell.show_screen("camera"))
        qr_inner.addWidget(scan_btn)
        root.addWidget(qr_card)

        # ── Mini Games grid ───────────────────────────────────────────────
        games_row = QtWidgets.QHBoxLayout()
        games_row.setContentsMargins(0, 4, 0, 0)
        games_row.addWidget(_section("Mini Games"))
        games_row.addStretch(1)
        all_lbl = _gold_link("All games")
        all_lbl.mousePressEvent = lambda e: self.shell.show_screen("game")  # type: ignore[method-assign]
        games_row.addWidget(all_lbl)
        root.addLayout(games_row)

        # 2×2 grid of game cards
        self._game_grid = QtWidgets.QGridLayout()
        self._game_grid.setSpacing(12)
        self._game_cards: Dict[str, GameTileCard] = {}

        _GAMES = [
            ("photo_hunt",      "📸", "Photo Hunt",    "Find & shoot",   False),
            ("bingo",           "🎯", "Bingo",         "Cover the grid", False),
            ("color_hunt",      "🎨", "Color Hunt",    "Brain teaser",   False),
            ("funny_tasks",     "😂", "Funny Tasks",   "Dare each other", False),
        ]
        for idx, (key, icon, title, sub_t, active) in enumerate(_GAMES):
            card = GameTileCard(key, icon, title, sub_t, active)
            card.clicked.connect(self._on_game_card)
            self._game_cards[key] = card
            self._game_grid.addWidget(card, idx // 2, idx % 2)

        root.addLayout(self._game_grid)
        root.addStretch(1)

        scroll_area.setWidget(container)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_area)

    def _on_game_card(self, key: str) -> None:
        """Pre-select the game type and navigate to the full game screen."""
        game_screen: "GameScreen" = self.shell.screens.get("game")  # type: ignore[assignment]
        if game_screen is not None:
            # Find matching template in the picker
            picker = game_screen.template_picker
            for i in range(picker.count()):
                if picker.itemData(i) == key:
                    picker.setCurrentIndex(i)
                    break
        self.shell.show_screen("game")

    def on_bus(self, message: BusMessage) -> None:
        if message.topic == "game.phase":
            payload = message.payload or {}
            phase = str(payload.get("state") or payload.get("phase") or "")
            for key, card in self._game_cards.items():
                # Highlight the running game card if it matches
                pass   # cards are static for now; full live update is in GameScreen


# ─────────────────────────────────────────────────────────────────────────────
# PlanScreen
# ─────────────────────────────────────────────────────────────────────────────
class PlanScreen(Screen):
    name  = "plan"
    title = "Where to?"

    VIBES      = ["calm", "chill", "playful", "adventure", "romantic", "foodie"]
    TRANSPORTS = ["walk", "bike", "car", "transit"]

    def build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)

        heading = QtWidgets.QLabel("Describe the trip")
        heading.setObjectName("Title")
        layout.addWidget(heading)

        self.voice_button = QtWidgets.QPushButton("🎤  Hold to talk")
        self.voice_button.setObjectName("Big")
        self.voice_button.pressed.connect(self.shell.start_listening)
        self.voice_button.released.connect(self.shell.stop_listening)
        layout.addWidget(self.voice_button)

        self.transcript = QtWidgets.QLabel("Or use the controls below.")
        self.transcript.setObjectName("Subtitle")
        self.transcript.setWordWrap(True)
        layout.addWidget(self.transcript)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)

        grid.addWidget(QtWidgets.QLabel("Mood"), 0, 0)
        self.vibe = QtWidgets.QComboBox()
        self.vibe.addItems(self.VIBES)
        grid.addWidget(self.vibe, 0, 1)

        grid.addWidget(QtWidgets.QLabel("People"), 1, 0)
        self.people = QtWidgets.QSpinBox()
        self.people.setRange(1, 12)
        self.people.setValue(2)
        grid.addWidget(self.people, 1, 1)

        grid.addWidget(QtWidgets.QLabel("Getting there"), 2, 0)
        self.transport = QtWidgets.QComboBox()
        self.transport.addItems(self.TRANSPORTS)
        grid.addWidget(self.transport, 2, 1)

        grid.addWidget(QtWidgets.QLabel("Time (min)"), 3, 0)
        self.minutes = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.minutes.setRange(20, 360)
        self.minutes.setSingleStep(10)
        self.minutes.setValue(120)
        self.minutes_value = QtWidgets.QLabel("120")
        minutes_row = QtWidgets.QHBoxLayout()
        minutes_row.addWidget(self.minutes, 1)
        minutes_row.addWidget(self.minutes_value)
        self.minutes.valueChanged.connect(
            lambda v: self.minutes_value.setText(str(v))
        )
        grid.addLayout(minutes_row, 3, 1)

        layout.addLayout(grid)

        interests_label = QtWidgets.QLabel("Interests")
        interests_label.setObjectName("Subtitle")
        layout.addWidget(interests_label)

        self.interest_boxes: Dict[str, QtWidgets.QCheckBox] = {}
        interests_row = QtWidgets.QHBoxLayout()
        interests_row.setSpacing(10)
        for interest in ("coffee", "food", "nature", "viewpoint",
                          "beach", "market", "heritage"):
            box = QtWidgets.QCheckBox(interest)
            box.setChecked(interest in ("coffee", "viewpoint"))
            self.interest_boxes[interest] = box
            interests_row.addWidget(box)
        interests_row.addStretch(1)
        layout.addLayout(interests_row)

        self.offline_box = QtWidgets.QCheckBox("Plan offline (no internet)")
        layout.addWidget(self.offline_box)

        layout.addStretch(1)
        buttons = QtWidgets.QHBoxLayout()
        back = QtWidgets.QPushButton("Back")
        back.setObjectName("Secondary")
        back.clicked.connect(lambda: self.shell.show_screen("chat"))
        buttons.addWidget(back)
        go = QtWidgets.QPushButton("✨  Make it mysterious")
        go.setObjectName("Big")
        go.clicked.connect(self.shell.submit_plan)
        buttons.addWidget(go, 1)
        layout.addLayout(buttons)

    def set_transcript(self, text: str) -> None:
        self.transcript.setText(text or "Or use the controls below.")

    def collect(self) -> Dict[str, Any]:
        return {
            "vibe":        self.vibe.currentText(),
            "people":      self.people.value(),
            "transport":   self.transport.currentText(),
            "max_minutes": self.minutes.value(),
            "interests":   [n for n, b in self.interest_boxes.items()
                            if b.isChecked()],
            "offline":     self.offline_box.isChecked(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# PlanningScreen
# ─────────────────────────────────────────────────────────────────────────────
class PlanningScreen(Screen):
    name  = "planning"
    title = "Planning"

    def build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        self.heading = QtWidgets.QLabel("Picking somewhere")
        self.heading.setObjectName("Title")
        layout.addWidget(self.heading)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.step = QtWidgets.QLabel("starting")
        self.step.setObjectName("Subtitle")
        layout.addWidget(self.step)

        self.detail = QtWidgets.QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        self.persona = PersonaWidget()
        layout.addWidget(self.persona)
        layout.addStretch(1)

        row = QtWidgets.QHBoxLayout()
        self.cancel = QtWidgets.QPushButton("Cancel")
        self.cancel.setObjectName("Secondary")
        self.cancel.clicked.connect(self.shell.cancel_planning)
        row.addWidget(self.cancel)
        self.go = QtWidgets.QPushButton("▶  Start walking")
        self.go.setObjectName("Big")
        self.go.setEnabled(False)
        self.go.clicked.connect(self.shell.begin_trip)
        row.addWidget(self.go, 1)
        layout.addLayout(row)

        self.summary = QtWidgets.QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(
            f"color: {PALETTE['paper']}; font-size: 14px; background: transparent;"
        )
        layout.addWidget(self.summary)

    def set_progress(self, step: str, fraction: float,
                     detail: str = "") -> None:
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
        self.step.setText(step)
        if detail:
            self.detail.setText(detail)

    def show_plan(self, plan: Optional[Plan], used_template: bool,
                  reason: str) -> None:
        if plan is None:
            self.heading.setText("Could not plan that")
            self.summary.setText(reason or "No destination could be found.")
            self.go.setEnabled(False)
            return
        self.heading.setText(plan.title)
        summary = plan.public_summary()
        lines = [
            f"{summary['people']} people · {summary['transport']} · "
            f"about {plan.spec.max_minutes} min",
            f"{summary['waypoint_count']} waypoints · "
            f"roughly {summary['route_km']} km",
        ]
        if summary["games"]:
            lines.append("Games: " + ", ".join(summary["games"]))
        if used_template:
            lines.append(f"(Template: {reason})")
        self.summary.setText("\n".join(lines))
        self.detail.setText("The destination stays hidden until you arrive.")
        self.go.setEnabled(True)

    def on_bus(self, message: BusMessage) -> None:
        if message.topic == "planning.progress":
            p = message.payload or {}
            self.set_progress(
                str(p.get("step", "planning")),
                float(p.get("fraction", 0.0)),
                str(p.get("detail", "")),
            )
        elif message.topic == "agent.say":
            self.persona.say(
                str((message.payload or {}).get("text", "")),
                mood=str((message.payload or {}).get("kind", "")),
            )


# ─────────────────────────────────────────────────────────────────────────────
# CameraScreen
# ─────────────────────────────────────────────────────────────────────────────
class CameraScreen(Screen):
    name  = "camera"
    title = "Camera"

    def build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        self.view = QtWidgets.QLabel("Camera preview")
        self.view.setAlignment(QtCore.Qt.AlignCenter)
        self.view.setMinimumHeight(240)
        self.view.setStyleSheet(
            f"background-color: {PALETTE['deep_sea']}; border-radius: 16px;"
            f"color: {PALETTE['mist']}; font-size: 14px;"
        )
        layout.addWidget(self.view, 1)

        self.caption = QtWidgets.QLineEdit()
        self.caption.setPlaceholderText("Caption (optional)")
        layout.addWidget(self.caption)

        row = QtWidgets.QHBoxLayout()
        back = QtWidgets.QPushButton("Back")
        back.setObjectName("Secondary")
        back.clicked.connect(lambda: self.shell.show_screen("compass"))
        row.addWidget(back)
        shutter = QtWidgets.QPushButton("📷  Take photo")
        shutter.setObjectName("Big")
        shutter.clicked.connect(self.shell.take_photo)
        row.addWidget(shutter, 1)
        layout.addLayout(row)

        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("Muted")
        layout.addWidget(self.status)

    def set_preview(self, jpeg: Optional[bytes]) -> None:
        if not jpeg:
            self.view.setText("No camera available")
            return
        pixmap = QtGui.QPixmap()
        if pixmap.loadFromData(jpeg, "JPEG"):
            self.view.setPixmap(
                pixmap.scaled(self.view.size(), QtCore.Qt.KeepAspectRatio,
                               QtCore.Qt.SmoothTransformation)
            )

    def set_status(self, text: str) -> None:
        self.status.setText(text)


# ─────────────────────────────────────────────────────────────────────────────
# TaskScreen
# ─────────────────────────────────────────────────────────────────────────────
class TaskScreen(Screen):
    name  = "task"
    title = "Task"

    def build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        heading = QtWidgets.QLabel("Your tasks")
        heading.setObjectName("Title")
        layout.addWidget(heading)

        self.current = QtWidgets.QLabel(
            "No task yet. Start a game from the Hub tab."
        )
        self.current.setObjectName("Subtitle")
        self.current.setWordWrap(True)
        layout.addWidget(self.current)

        self.list = QtWidgets.QListWidget()
        layout.addWidget(self.list, 1)

        row = QtWidgets.QHBoxLayout()
        back = QtWidgets.QPushButton("Back")
        back.setObjectName("Secondary")
        back.clicked.connect(lambda: self.shell.show_screen("compass"))
        row.addWidget(back)
        photo = QtWidgets.QPushButton("📷  Photograph this")
        photo.clicked.connect(self.shell.take_photo)
        row.addWidget(photo, 1)
        layout.addLayout(row)

    def set_tasks(self, tasks: Sequence[str], current: str = "") -> None:
        self.list.clear()
        for idx, task in enumerate(tasks, 1):
            self.list.addItem(f"{idx}. {task}")
        if current:
            self.current.setText(current)
        elif tasks:
            self.current.setText(f"Next: {tasks[0]}")


# ─────────────────────────────────────────────────────────────────────────────
# GameScreen  (full game flow — accessed from Hub or compass)
# ─────────────────────────────────────────────────────────────────────────────
class GameScreen(Screen):
    name  = "game"
    title = "Game"

    def build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        self.heading = QtWidgets.QLabel("Set up a game")
        self.heading.setObjectName("Title")
        top.addWidget(self.heading, 1)
        self.state_label = QtWidgets.QLabel("IDLE")
        self.state_label.setObjectName("Muted")
        top.addWidget(self.state_label)
        layout.addLayout(top)

        # ── configure ────────────────────────────────────────────────────
        self.config_box = QtWidgets.QFrame()
        self.config_box.setObjectName("Card")
        config_layout = QtWidgets.QFormLayout(self.config_box)
        config_layout.setContentsMargins(16, 14, 16, 14)
        config_layout.setSpacing(10)

        self.template_picker = QtWidgets.QComboBox()
        for tmpl in template_summaries():
            self.template_picker.addItem(
                f"{tmpl['title']} ({tmpl['players']['min']}-{tmpl['players']['max']} players)",
                tmpl["type"],
            )
        config_layout.addRow("Game", self.template_picker)

        self.player_edit = QtWidgets.QLineEdit("Asha, Dev")
        config_layout.addRow("Players", self.player_edit)

        self.duration_picker = QtWidgets.QComboBox()
        self.duration_picker.addItems(["10", "15", "20", "30", "45", "60"])
        self.duration_picker.setCurrentText("30")
        config_layout.addRow("Minutes", self.duration_picker)

        self.approval_picker = QtWidgets.QComboBox()
        self.approval_picker.addItems(["peer_vote", "auto", "host"])
        config_layout.addRow("Approval", self.approval_picker)

        self.penalty_edit = QtWidgets.QLineEdit("loser buys a treat")
        config_layout.addRow("Penalty", self.penalty_edit)

        layout.addWidget(self.config_box)

        self.start_button = QtWidgets.QPushButton("🎮  Create the game")
        self.start_button.setObjectName("Big")
        self.start_button.clicked.connect(self.shell.create_game)
        layout.addWidget(self.start_button)

        # ── lobby ────────────────────────────────────────────────────────
        self.lobby_box = QtWidgets.QFrame()
        self.lobby_box.setObjectName("Card")
        lobby_layout = QtWidgets.QHBoxLayout(self.lobby_box)
        lobby_layout.setContentsMargins(12, 12, 12, 12)
        self.qr = QrCard()
        lobby_layout.addWidget(self.qr, 3)
        right = QtWidgets.QVBoxLayout()
        self.lobby_players = QtWidgets.QListWidget()
        right.addWidget(self.lobby_players, 1)
        self.lobby_status = QtWidgets.QLabel("Waiting for players")
        self.lobby_status.setWordWrap(True)
        right.addWidget(self.lobby_status)
        self.begin_button = QtWidgets.QPushButton("▶  Start game")
        self.begin_button.setObjectName("Big")
        self.begin_button.clicked.connect(self.shell.start_game)
        right.addWidget(self.begin_button)
        self.offline_button = QtWidgets.QPushButton("Play locally instead")
        self.offline_button.setObjectName("Secondary")
        self.offline_button.clicked.connect(self.shell.start_local_game)
        right.addWidget(self.offline_button)
        lobby_layout.addLayout(right, 2)
        self.lobby_box.hide()
        layout.addWidget(self.lobby_box, 1)

        # ── running ──────────────────────────────────────────────────────
        self.running_box = QtWidgets.QFrame()
        self.running_box.setObjectName("Card")
        running_layout = QtWidgets.QVBoxLayout(self.running_box)
        running_layout.setContentsMargins(12, 12, 12, 12)
        self.countdown = CountdownLabel()
        running_layout.addWidget(self.countdown)
        self.scoreboard = QtWidgets.QListWidget()
        running_layout.addWidget(self.scoreboard, 1)
        self.ticker = TickerList()
        running_layout.addWidget(self.ticker, 1)
        run_row = QtWidgets.QHBoxLayout()
        self.show_qr_button = QtWidgets.QPushButton("Show QR")
        self.show_qr_button.setObjectName("Secondary")
        self.show_qr_button.clicked.connect(self._toggle_qr)
        run_row.addWidget(self.show_qr_button)
        self.end_button = QtWidgets.QPushButton("End game")
        self.end_button.setObjectName("Signal")
        self.end_button.clicked.connect(self.shell.end_game)
        run_row.addWidget(self.end_button, 1)
        running_layout.addLayout(run_row)
        self.running_box.hide()
        layout.addWidget(self.running_box, 1)

        # ── results ──────────────────────────────────────────────────────
        self.results_box = QtWidgets.QFrame()
        self.results_box.setObjectName("Card")
        results_layout = QtWidgets.QVBoxLayout(self.results_box)
        results_layout.setContentsMargins(12, 12, 12, 12)
        self.results_label = QtWidgets.QLabel("")
        self.results_label.setObjectName("Big")
        self.results_label.setWordWrap(True)
        results_layout.addWidget(self.results_label)
        self.results_list = QtWidgets.QListWidget()
        results_layout.addWidget(self.results_list, 1)
        self.results_penalty = QtWidgets.QLabel("")
        self.results_penalty.setObjectName("Gold")
        results_layout.addWidget(self.results_penalty)
        close_row = QtWidgets.QHBoxLayout()
        again = QtWidgets.QPushButton("Another game")
        again.setObjectName("Secondary")
        again.clicked.connect(lambda: self.shell.show_screen("game"))
        close_row.addWidget(again)
        done = QtWidgets.QPushButton("Back to compass")
        done.clicked.connect(lambda: self.shell.show_screen("compass"))
        close_row.addWidget(done, 1)
        results_layout.addLayout(close_row)
        self.results_box.hide()
        layout.addWidget(self.results_box, 1)

        # ── error ────────────────────────────────────────────────────────
        self.error_box = QtWidgets.QFrame()
        self.error_box.setObjectName("Card")
        error_layout = QtWidgets.QVBoxLayout(self.error_box)
        error_layout.setContentsMargins(12, 12, 12, 12)
        self.error_label = QtWidgets.QLabel("")
        self.error_label.setObjectName("Danger")
        self.error_label.setWordWrap(True)
        error_layout.addWidget(self.error_label)
        self.error_detail = QtWidgets.QLabel("")
        self.error_detail.setObjectName("Muted")
        self.error_detail.setWordWrap(True)
        error_layout.addWidget(self.error_detail)
        retry = QtWidgets.QPushButton("Retry")
        retry.clicked.connect(self.shell.retry_game)
        error_layout.addWidget(retry)
        self.error_box.hide()
        layout.addWidget(self.error_box)

        back = QtWidgets.QPushButton("Back")
        back.setObjectName("Secondary")
        back.clicked.connect(lambda: self.shell.show_screen("compass"))
        layout.addWidget(back)

    # ── rendering ────────────────────────────────────────────────────────
    def set_state(self, state: GameHostState,
                  detail: Dict[str, Any]) -> None:
        self.state_label.setText(state.value)
        configuring = state in (
            GameHostState.IDLE, GameHostState.CONFIGURING,
            GameHostState.GENERATING, GameHostState.CREATING_SESSION,
        )
        self.config_box.setVisible(configuring)
        self.start_button.setVisible(configuring)
        self.lobby_box.setVisible(state == GameHostState.LOBBY)
        self.running_box.setVisible(state == GameHostState.RUNNING)
        self.results_box.setVisible(state == GameHostState.RESULTS)
        self.error_box.setVisible(state == GameHostState.OFFLINE_FALLBACK)

        if state == GameHostState.GENERATING:
            self.start_button.setText("Writing the tasks…")
            self.start_button.setEnabled(False)
        elif state == GameHostState.CREATING_SESSION:
            self.start_button.setText("Asking the server…")
            self.start_button.setEnabled(False)
        else:
            self.start_button.setText("🎮  Create the game")
            self.start_button.setEnabled(True)

        labels = {
            GameHostState.RUNNING:         "Game running",
            GameHostState.LOBBY:           "Waiting in the lobby",
            GameHostState.RESULTS:         "Results",
            GameHostState.OFFLINE_FALLBACK: "Could not reach the server",
        }
        self.heading.setText(labels.get(state, "Set up a game"))

    def set_join(self, target: JoinTarget) -> None:
        png = self.shell.game_qr_png()
        self.qr.set_join(target.code, target.address, png,
                         wifi=self.shell.wifi_ssid())

    def set_lobby(self, players: Sequence[Dict[str, Any]]) -> None:
        self.lobby_players.clear()
        for player in players:
            mark = "✓" if player.get("joined") else "…"
            self.lobby_players.addItem(
                f"[{mark}] {player.get('name', 'Player')}"
            )
        joined = sum(1 for p in players if p.get("joined"))
        self.lobby_status.setText(
            f"{joined} of {len(players)} joined. "
            "Press Start when everyone is in."
        )

    def set_scoreboard(self, board: Sequence[Dict[str, Any]]) -> None:
        self.scoreboard.clear()
        for entry in board:
            name  = entry.get("name") or entry.get("display_name") or "Player"
            score = entry.get("score", 0)
            subs  = entry.get("submissions", 0)
            self.scoreboard.addItem(f"{name}   {score} pts   ({subs} photos)")

    def push_ticker(self, text: str) -> None:
        self.ticker.push(text)

    def set_countdown(self, label: str, seconds: Optional[float]) -> None:
        self.countdown.set_remaining(seconds, label)

    def set_results(self, payload: Dict[str, Any]) -> None:
        winner = payload.get("winner")
        self.results_label.setText(
            f"🏆 {winner} wins!" if winner else "Game over — it's a draw"
        )
        self.results_list.clear()
        for entry in payload.get("scores", []):
            self.results_list.addItem(
                f"{entry.get('name', 'Player')}   {entry.get('score', 0)} pts"
            )
        extra = ("" if payload.get("photos_are_on_the_server")
                 else " (kept locally; will sync later)")
        self.results_penalty.setText(
            str(payload.get("penalty", "")) + extra
        )

    def set_error(self, message: str, detail: Dict[str, Any]) -> None:
        self.error_label.setText(message)
        fixes = detail.get("fixes") or []
        self.error_detail.setText("\n".join(str(f) for f in fixes[:4]))
        self.error_box.show()

    def _toggle_qr(self) -> None:
        self.qr.setVisible(not self.qr.isVisible())

    def on_bus(self, message: BusMessage) -> None:
        payload = message.payload or {}
        if message.topic == "game.phase":
            pass   # handled by MainWindow._shell_reactions via game_host observers


# ─────────────────────────────────────────────────────────────────────────────
# ArrivalScreen
# ─────────────────────────────────────────────────────────────────────────────
class ArrivalScreen(Screen):
    name  = "arrival"
    title = "You are here"

    def build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        lbl = QtWidgets.QLabel("You have arrived")
        lbl.setObjectName("Subtitle")
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(lbl)

        self.place = QtWidgets.QLabel("…")
        self.place.setObjectName("Huge")
        self.place.setWordWrap(True)
        self.place.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.place, 1)

        self.category = QtWidgets.QLabel("")
        self.category.setObjectName("Subtitle")
        self.category.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.category)

        self.stats = QtWidgets.QLabel("")
        self.stats.setObjectName("Mono")
        self.stats.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.stats)

        self.persona = PersonaWidget()
        layout.addWidget(self.persona)

        row = QtWidgets.QHBoxLayout()
        photo = QtWidgets.QPushButton("📷  Arrival photo")
        photo.clicked.connect(
            lambda: self.shell.take_photo(category="destination")
        )
        row.addWidget(photo)
        wrap = QtWidgets.QPushButton("Wrap up the trip")
        wrap.setObjectName("Big")
        wrap.clicked.connect(self.shell.wrap_up)
        row.addWidget(wrap, 1)
        layout.addLayout(row)

    def show_arrival(self, payload: Dict[str, Any]) -> None:
        self.place.setText(str(payload.get("name", "Somewhere")))
        self.category.setText(str(payload.get("category", "")).title())
        self.persona.say("Here it is. Take your time.", mood="playful")


# ─────────────────────────────────────────────────────────────────────────────
# WrapUpScreen
# ─────────────────────────────────────────────────────────────────────────────
class WrapUpScreen(Screen):
    name  = "wrapup"
    title = "Wrap up"

    def build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        heading = QtWidgets.QLabel("That's the trip")
        heading.setObjectName("Title")
        layout.addWidget(heading)

        self.summary = QtWidgets.QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("Subtitle")
        layout.addWidget(self.summary)

        images = QtWidgets.QHBoxLayout()
        self.collage = QtWidgets.QLabel("No collage yet")
        self.collage.setAlignment(QtCore.Qt.AlignCenter)
        self.collage.setMinimumSize(300, 220)
        self.collage.setStyleSheet(
            f"background-color: {PALETTE['deep_sea']}; border-radius: 16px;"
            f"color: {PALETTE['mist']};"
        )
        images.addWidget(self.collage, 3)
        self.stamp = QtWidgets.QLabel("No stamp yet")
        self.stamp.setAlignment(QtCore.Qt.AlignCenter)
        self.stamp.setMinimumSize(220, 220)
        self.stamp.setStyleSheet(
            f"background-color: {PALETTE['deep_sea']}; border-radius: 16px;"
            f"color: {PALETTE['mist']};"
        )
        images.addWidget(self.stamp, 2)
        layout.addLayout(images, 1)

        self.queue_status = QtWidgets.QLabel("")
        self.queue_status.setObjectName("Muted")
        self.queue_status.setWordWrap(True)
        layout.addWidget(self.queue_status)

        row = QtWidgets.QHBoxLayout()
        make = QtWidgets.QPushButton("🖼  Make collage & stamp")
        make.clicked.connect(self.shell.build_wrapup)
        row.addWidget(make)
        share = QtWidgets.QPushButton("⬆  Sync")
        share.setObjectName("Secondary")
        share.clicked.connect(self.shell.flush_outbox_now)
        row.addWidget(share)
        done = QtWidgets.QPushButton("Done")
        done.setObjectName("Big")
        done.clicked.connect(lambda: self.shell.show_screen("compass"))
        row.addWidget(done, 1)
        layout.addLayout(row)

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)

    def set_collage(self, path: Optional[str]) -> None:
        self._set_image(self.collage, path, "No collage yet")

    def set_stamp(self, path: Optional[str]) -> None:
        self._set_image(self.stamp, path, "No stamp yet")

    @staticmethod
    def _set_image(label: QtWidgets.QLabel, path: Optional[str],
                   fallback: str) -> None:
        if not path:
            label.setText(fallback)
            return
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            label.setText(fallback)
            return
        label.setPixmap(
            pixmap.scaled(label.size(), QtCore.Qt.KeepAspectRatio,
                           QtCore.Qt.SmoothTransformation)
        )

    def set_queue_status(self, text: str) -> None:
        self.queue_status.setText(text)


# ─────────────────────────────────────────────────────────────────────────────
# SettingsScreen
# ─────────────────────────────────────────────────────────────────────────────
class SettingsScreen(Screen):
    name  = "settings"
    title = "Settings"

    def build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        heading = QtWidgets.QLabel("Settings & Diagnostics")
        heading.setObjectName("Title")
        layout.addWidget(heading)

        self.info = QtWidgets.QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setMaximumBlockCount(60)
        layout.addWidget(self.info, 1)

        self.queue_info = QtWidgets.QLabel("")
        self.queue_info.setWordWrap(True)
        self.queue_info.setObjectName("Muted")
        layout.addWidget(self.queue_info)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        for label, slot, obj in (
            ("🔍 Network check",  self.shell.run_diagnostics,    ""),
            ("⬆ Send queue",      self.shell.flush_outbox_now,   "Secondary"),
            ("🧭 Calibrate",      self.shell.calibrate_compass,  "Secondary"),
            ("← Back",            lambda: self.shell.show_screen("compass"), "Secondary"),
        ):
            btn = QtWidgets.QPushButton(label)
            if obj:
                btn.setObjectName(obj)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        layout.addLayout(row)

        self.log = LogView()
        layout.addWidget(self.log, 1)

    def on_show(self) -> None:
        self.info.setPlainText(self.shell.status_report())
        self.queue_info.setText(self.shell.queue_summary())


# ─────────────────────────────────────────────────────────────────────────────
# SosScreen
# ─────────────────────────────────────────────────────────────────────────────
class SosScreen(Screen):
    name  = "sos"
    title = "SOS"

    def build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        heading = QtWidgets.QLabel("Emergency")
        heading.setObjectName("Title")
        heading.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(heading)

        self.instruction = QtWidgets.QLabel(
            "Hold the button for three seconds to send your location,\n"
            "then tell someone where you are."
        )
        self.instruction.setWordWrap(True)
        self.instruction.setAlignment(QtCore.Qt.AlignCenter)
        self.instruction.setObjectName("Subtitle")
        layout.addWidget(self.instruction, 1)

        self.hold = QtWidgets.QPushButton("Hold for SOS")
        self.hold.setObjectName("Danger")
        self.hold.setMinimumHeight(110)
        self.hold.pressed.connect(self.shell.sos_pressed)
        self.hold.released.connect(self.shell.sos_released)
        layout.addWidget(self.hold)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.result = QtWidgets.QLabel("")
        self.result.setWordWrap(True)
        self.result.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.result)

        back = QtWidgets.QPushButton("Back")
        back.setObjectName("Secondary")
        back.clicked.connect(lambda: self.shell.show_screen("compass"))
        layout.addWidget(back)

    def set_progress(self, fraction: float) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))

    def set_result(self, text: str) -> None:
        self.result.setText(text)


# ─────────────────────────────────────────────────────────────────────────────
# MainWindow  — three-tab shell + deep-link stacked screens
# ─────────────────────────────────────────────────────────────────────────────
class MainWindow(QtWidgets.QMainWindow):
    """Application shell with bottom-tab navigation matching the inspo design."""

    # The three primary tab screens
    TAB_SCREENS = {
        "compass": CompassScreen,
        "chat":    ChatScreen,
        "hub":     HubScreen,
    }

    # Overlay screens (pushed on top of the tab layer)
    OVERLAY_SCREENS = {
        "plan":     PlanScreen,
        "planning": PlanningScreen,
        "camera":   CameraScreen,
        "task":     TaskScreen,
        "game":     GameScreen,
        "arrival":  ArrivalScreen,
        "wrapup":   WrapUpScreen,
        "settings": SettingsScreen,
        "sos":      SosScreen,
    }

    # Keep "boot" as an alias for "chat" for any legacy callers
    ALIASES: Dict[str, str] = {"boot": "chat"}

    def __init__(
        self,
        settings: Settings,
        bus: Bus,
        *,
        engine: Any = None,
        game_host: Optional[GameHost] = None,
        persona: Any = None,
        voice: Any = None,
        syncd: Any = None,
        services: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__()
        self.settings   = settings
        self.bus        = bus
        self.engine     = engine
        self.game_host  = game_host
        self.persona    = persona
        self.voice      = voice
        self.syncd      = syncd
        self.services   = services or {}

        self.setWindowTitle(f"{settings.product_name} — {settings.device_name}")
        # Phone-like 390×844 portrait window (scales on desktop)
        self.resize(390, 844)
        if settings.ui_fullscreen:
            self.showFullScreen()

        self.bridge            = AsyncBridge()
        self._queue: "queue.Queue[BusMessage]" = queue.Queue()
        self._unsubscribers:   List[Any] = []
        self._current_screen   = ""
        self._sos_started:     Optional[float] = None
        self._sos_sent         = False
        self._last_plan:       Optional[Plan] = None
        self.screens:          Dict[str, Screen] = {}

        self._build_ui()
        self._subscribe()

        # 60 ms drain timer
        self._drain_timer = QtCore.QTimer(self)
        self._drain_timer.timeout.connect(self._drain)
        self._drain_timer.start(60)

        # Engine tick timer
        self._engine_timer = QtCore.QTimer(self)
        self._engine_timer.timeout.connect(self._tick_engine)
        if self.engine is not None:
            self._engine_timer.start(
                max(30, int(1000 / max(1.0, settings.engine_hz)))
            )

        # SOS hold timer (100 ms poll)
        self._sos_timer = QtCore.QTimer(self)
        self._sos_timer.timeout.connect(self._sos_progress)
        self._sos_timer.start(100)

        # Start on the compass tab
        self.show_screen("compass")

    # ── UI construction ──────────────────────────────────────────────────
    def _build_ui(self) -> None:
        """Build the stacked layout: tab layer + overlay layer + tab bar."""
        central = QtWidgets.QWidget()
        central.setStyleSheet(f"background-color: {PALETTE['night']};")
        self.setCentralWidget(central)

        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Status strip (single line at top)
        self.status_strip = QtWidgets.QLabel("")
        self.status_strip.setContentsMargins(14, 5, 14, 5)
        self.status_strip.setStyleSheet(
            f"font-size: 12px; color: {PALETTE['mist']};"
            f"background: {PALETTE['night']};"
            f"border-bottom: 1px solid {PALETTE['border']};"
        )
        self.status_strip.setFixedHeight(26)
        root.addWidget(self.status_strip)

        # Main stacked widget — holds BOTH tab screens and overlay screens
        self.stack = QtWidgets.QStackedWidget()
        root.addWidget(self.stack, 1)

        # Build all screens
        for name, cls in {**self.TAB_SCREENS,
                          **self.OVERLAY_SCREENS}.items():
            screen = cls(self)
            self.screens[name] = screen
            self.stack.addWidget(screen)

        # Bottom tab bar
        self.tab_bar = BottomTabBar()
        self.tab_bar.tab_changed.connect(self._on_tab_changed)
        root.addWidget(self.tab_bar)

        # No Qt status bar — cleaner look
        self.setStatusBar(None)

    def _subscribe(self) -> None:
        topics = [
            "compass.update", "heading.update", "position.update",
            "agent.say", "reveal.due", "reveal.shown", "off_route",
            "sensor.gps_lost", "sensor.gps_back", "trip.started",
            "trip.arrived", "trip.ended", "sync.status",
            "connection.status", "planning.progress", "plan.ready",
            "plan.failed", "game.phase", "game.event", "sos.triggered",
            "outbox.changed", "photo.captured", "geofence.entered",
        ]
        for topic in topics:
            self._unsubscribers.append(
                self.bus.subscribe(topic, self._on_bus_message)
            )

    def _on_bus_message(self, message: BusMessage) -> None:
        self._queue.put(message)

    def _drain(self) -> None:
        drained = 0
        while drained < 400:
            try:
                message = self._queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            self._handle(message)

    # ── message handling ─────────────────────────────────────────────────
    def _handle(self, message: BusMessage) -> None:
        current = self.screens.get(self._current_screen)
        if current is not None:
            try:
                current.on_bus(message)
            except Exception:
                logger.exception("screen %s failed on %s",
                                  self._current_screen, message.topic)
        # Also notify non-current screens that want background updates
        for name, screen in self.screens.items():
            if screen is not current:
                try:
                    screen.on_bus(message)
                except Exception:
                    logger.debug("screen %s failed on %s", name, message.topic)

        self._shell_reactions(message)

    def _shell_reactions(self, message: BusMessage) -> None:
        payload = message.payload or {}
        topic   = message.topic

        if topic == "planning.progress":
            self._set_strip(f"{payload.get('step', 'planning')}…")
        elif topic == "plan.ready":
            self._set_strip("Plan ready — head to the Compass tab")
        elif topic == "plan.failed":
            self._set_strip("Planning failed")
        elif topic == "sync.status":
            queued = payload.get("queued", 0)
            online = payload.get("online")
            state  = "online" if online else "offline"
            self._set_strip(
                f"{state} · {queued} uploading" if queued else state
            )
        elif topic == "connection.status":
            if payload.get("connected") is False:
                self._set_strip("live updates off (polling)")
        elif topic == "trip.arrived":
            self.show_screen("arrival")
            arrival_screen: ArrivalScreen = self.screens["arrival"]  # type: ignore[assignment]
            arrival_screen.show_arrival(payload)
        elif topic == "sos.triggered":
            self._set_strip("🆘 SOS sent")

        # Game host observer wiring
        if self.game_host is not None:
            self.game_host.observers  # ensure observers are attached (done in runtime)

    def _set_strip(self, text: str) -> None:
        self.status_strip.setText(text)

    # ── navigation ───────────────────────────────────────────────────────
    def _on_tab_changed(self, key: str) -> None:
        """Tab bar changed — switch to the tab screen."""
        self.show_screen(key)

    def show_screen(self, name: str) -> None:
        name = self.ALIASES.get(name, name)
        screen = self.screens.get(name)
        if screen is None:
            logger.warning("no such screen: %s", name)
            return
        self.stack.setCurrentWidget(screen)
        self._current_screen = name

        # Keep the tab bar in sync when navigating programmatically
        if name in self.TAB_SCREENS:
            self.tab_bar.set_active(name)

        try:
            screen.on_show()
        except Exception:
            logger.exception("screen %s failed on show", name)

    # ── engine tick ──────────────────────────────────────────────────────
    def _tick_engine(self) -> None:
        if self.engine is None:
            return
        try:
            self.engine.tick()
        except Exception:
            logger.exception("engine tick failed")

    # ── actions the screens call ─────────────────────────────────────────
    def probe_server_async(
        self, callback: Callable[[str, bool], None]
    ) -> None:
        client = self.services.get("client")
        if client is None:
            callback(
                "No server client in this mode. Offline demo is available.",
                False,
            )
            return

        async def probe() -> None:
            health = await __import__("asyncio").to_thread(client.health)
            if health is None:
                callback(
                    f"Cannot reach {self.settings.server_url}. "
                    "Offline demo still works.",
                    False,
                )
            else:
                callback(
                    f"Connected to {health.app} at {self.settings.server_url}",
                    True,
                )

        future = self.bridge.submit(probe())
        if future is not None:
            future.add_done_callback(
                lambda f: logger.debug("probe done: %s", f.exception())
            )

    def token_present(self) -> bool:
        client = self.services.get("client")
        return bool(client and getattr(client, "paired", False))

    def hardware_summary(self) -> str:
        hardware = self.services.get("hardware")
        if hardware is None:
            return ""
        try:
            return hardware.describe()
        except Exception:
            return ""

    def wifi_ssid(self) -> str:
        return self.services.get("wifi_ssid") or current_ssid() or ""

    def start_pairing(self) -> None:
        client = self.services.get("client")
        chat: ChatScreen = self.screens["chat"]  # type: ignore[assignment]
        if client is None:
            chat._add_ai_message(
                "Pairing needs the server. Using offline demo instead."
            )
            return
        chat._add_ai_message("Asking the server for a pairing code…")

        async def pair() -> None:
            import asyncio
            try:
                session_info = await asyncio.to_thread(client.register)
            except Exception as exc:
                chat._add_ai_message(f"Pairing failed: {exc}")
                return
            info = session_info.display()
            chat._add_ai_message(
                f"Enter code **{info['code']}** on the website under Devices."
            )
            self._set_strip(f"Pairing code: {info['code']}")
            token = await asyncio.to_thread(
                client.pairing.wait_for_claim, session_info
            )
            if token:
                client.set_token(token)
                chat._add_ai_message("Paired! This handheld is now linked.")
                self._set_strip("paired")
            else:
                chat._add_ai_message("Pairing timed out. Try again.")

        self.bridge.submit(pair())

    def start_offline_demo(self) -> None:
        starter = self.services.get("start_offline_demo")
        if starter is None:
            chat: ChatScreen = self.screens["chat"]  # type: ignore[assignment]
            chat._add_ai_message("Offline demo is not available in this mode.")
            return
        starter()
        self.show_screen("compass")

    def submit_plan(self) -> None:
        screen: PlanScreen = self.screens["plan"]  # type: ignore[assignment]
        choices = screen.collect()
        planner = self.services.get("planner")
        self.show_screen("planning")
        planning: PlanningScreen = self.screens["planning"]  # type: ignore[assignment]
        planning.set_progress("reading", 0.05, "working out what you asked for")

        async def run() -> None:
            if planner is None:
                planning.show_plan(None, True, "No planner in this mode.")
                return
            from ..agent.planner import PlanRequest
            from ..agent.input_parser import parse_buttons

            request = PlanRequest(
                spec=None,
                text="",
                start_lat=self.services.get("start_lat"),
                start_lng=self.services.get("start_lng"),
                offline=bool(choices.get("offline")),
            )
            request.spec = parse_buttons(
                vibe=choices["vibe"],
                people=choices["people"],
                transport=choices["transport"],
                max_minutes=choices["max_minutes"],
                interests=choices["interests"],
                start_lat=request.start_lat,
                start_lng=request.start_lng,
                range_km=(
                    max(0.5, choices["max_minutes"] / 60.0 * 1.0),
                    max(1.5, choices["max_minutes"] / 60.0 * 3.2),
                ),
            )
            outcome = await planner.plan(request)
            self._last_plan = outcome.plan
            self.services["last_plan"] = outcome.plan
            planning.show_plan(outcome.plan, outcome.used_template,
                               outcome.reason)

        self.bridge.submit(run())

    def cancel_planning(self) -> None:
        self.show_screen("plan")

    def begin_trip(self) -> None:
        plan    = self.services.get("last_plan")
        starter = self.services.get("start_trip")
        if starter is None:
            self.show_screen("compass")
            return
        starter(plan)
        self.show_screen("compass")

    def start_listening(self) -> None:
        voice  = self.voice
        screen: PlanScreen = self.screens["plan"]  # type: ignore[assignment]
        if voice is None:
            screen.set_transcript("No microphone. Use the controls below.")
            return
        screen.set_transcript("Listening…")

        async def listen() -> None:
            text = await voice.listen_once()
            if not text:
                screen.set_transcript("Didn't catch that. Use the controls.")
                return
            screen.set_transcript(f'"{text}"')
            parser = self.services.get("parse_text")
            if parser is not None:
                spec = await parser(text)
                if spec is not None:
                    screen.vibe.setCurrentText(spec.vibe.value)
                    screen.people.setValue(spec.people)
                    screen.transport.setCurrentText(spec.transport.value)
                    screen.minutes.setValue(spec.max_minutes)
                    for name, box in screen.interest_boxes.items():
                        box.setChecked(name in spec.interests)
                    screen.set_transcript(f'"{text}"  →  {spec.describe()}')

        self.bridge.submit(listen())

    def stop_listening(self) -> None:
        return None

    def take_photo(self, category: str = "waypoint") -> None:
        handler = self.services.get("take_photo")
        screen:  CameraScreen = self.screens["camera"]  # type: ignore[assignment]
        if handler is None:
            screen.set_status("No camera in this mode.")
            self.show_screen("camera")
            return
        self.show_screen("camera")
        screen.set_status("Taking a photo…")
        preview = self.services.get("camera_preview")
        if callable(preview):
            try:
                screen.set_preview(preview())
            except Exception:
                pass

        async def run() -> None:
            caption = screen.caption.text()
            result  = await handler(category=category, caption=caption)
            screen.set_status(result)

        self.bridge.submit(run())

    def trigger_sos(self) -> None:
        self.show_screen("sos")

    def sos_pressed(self) -> None:
        self._sos_started = time.monotonic()
        self._sos_sent    = False

    def sos_released(self) -> None:
        self._sos_started = None
        sos: SosScreen = self.screens["sos"]  # type: ignore[assignment]
        sos.set_progress(0.0)
        if not self._sos_sent:
            sos.set_result("Released too early. Hold for three seconds.")

    def _sos_progress(self) -> None:
        if self._sos_started is None or self._sos_sent:
            return
        elapsed = time.monotonic() - self._sos_started
        sos: SosScreen = self.screens["sos"]  # type: ignore[assignment]
        sos.set_progress(elapsed / 3.0)
        if elapsed >= 3.0:
            self._sos_sent = True
            sender = self.services.get("sos")
            if sender is None:
                sos.set_result("SOS not wired up in this mode.")
                return
            sos.set_result("Sending your location…")

            async def run() -> None:
                msg = await sender()
                sos.set_result(msg)

            self.bridge.submit(run())

    def arrive_now(self) -> None:
        arrive = self.services.get("arrive")
        if callable(arrive):
            arrive()
        self.show_screen("arrival")

    def wrap_up(self) -> None:
        self.show_screen("wrapup")
        self.build_wrapup()

    def build_wrapup(self) -> None:
        builder = self.services.get("build_wrapup")
        screen:  WrapUpScreen = self.screens["wrapup"]  # type: ignore[assignment]
        if builder is None:
            screen.set_summary("Wrap-up not available in this mode.")
            return
        summary, collage_path, stamp_path = builder()
        screen.set_summary(summary)
        screen.set_collage(collage_path)
        screen.set_stamp(stamp_path)
        screen.set_queue_status(self.queue_summary())

    # ── games ─────────────────────────────────────────────────────────────
    def create_game(self) -> None:
        screen: GameScreen = self.screens["game"]  # type: ignore[assignment]
        host = self.game_host
        if host is None:
            screen.set_error("Games are not wired up in this mode.", {})
            return
        players = [
            {"name": name.strip(), "color": colour}
            for name, colour in zip(
                [p for p in screen.player_edit.text().split(",")
                 if p.strip()],
                ["#F2C700", "#1E7F85", "#EF4444", "#7B4FC2",
                 "#22C55E", "#F59E0B"],
            )
        ]
        ok, message = host.configure(
            screen.template_picker.currentData() or "photo_hunt",
            players,
            duration_min=int(screen.duration_picker.currentText()),
            trip_id=(
                self.services.get("last_plan").trip_id
                if self.services.get("last_plan") else None
            ),
            approval=screen.approval_picker.currentText(),
            penalty=screen.penalty_edit.text(),
        )
        if not ok:
            screen.set_error(message, {})
            return

        async def run() -> None:
            await host.generate(
                self.services.get("llm_router"),
                offline=self.services.get("offline", False),
            )
            ok2, msg2 = await host.create_session()
            if not ok2:
                diag = await __import__("asyncio").to_thread(host.diagnose)
                screen.set_error(msg2, diag.to_payload())

        self.bridge.submit(run())

    def start_game(self) -> None:
        host = self.game_host
        if host is None:
            return

        async def run() -> None:
            ok, msg = await host.start()
            if not ok:
                self.screens["game"].set_error(msg, {})  # type: ignore[union-attr]
            else:
                host.start_polling()

        self.bridge.submit(run())

    def start_local_game(self) -> None:
        host = self.game_host
        if host is None:
            return

        async def run() -> None:
            await host.enable_offline_mode()
            await host.start()

        self.bridge.submit(run())

    def end_game(self) -> None:
        host = self.game_host
        if host is None:
            return

        async def run() -> None:
            await host.end(reason="host")

        self.bridge.submit(run())

    def retry_game(self) -> None:
        self.show_screen("game")
        self.create_game()

    def game_qr_png(self) -> Optional[bytes]:
        if self.game_host is None:
            return None
        return self.game_host.qr_png()

    # ── settings / diagnostics ────────────────────────────────────────────
    def status_report(self) -> str:
        lines: List[str] = []
        lines.append(f"device:  {self.settings.device_name}")
        lines.append(
            f"mode:    {self.settings.device_mode} "
            f"(resolved {self.services.get('resolved_mode', '?')})"
        )
        lines.append(f"server:  {self.settings.server_url}")
        lines.append(f"data:    {self.settings.data_dir}")
        clock = self.services.get("clock")
        if clock is not None:
            lines.append(clock.describe())
        hardware = self.services.get("hardware")
        if hardware is not None:
            for name, status in hardware.status().get("devices", {}).items():
                mark = "ok" if status.get("open") else "--"
                lines.append(f"hal {name}: {mark} ({status.get('driver')})")
        if self.engine is not None:
            live = self.engine.live_status()
            lines.append(
                f"trip:    {live['state']} · {live['distance_m']:.0f} m · "
                f"{live['track_points']} points"
            )
        if self.syncd is not None:
            lines.append(
                f"paired:  {self.syncd.status_dict().get('paired')}"
            )
        return "\n".join(lines)

    def queue_summary(self) -> str:
        if self.syncd is None:
            storage = self.services.get("storage")
            if storage is None:
                return "No queue in this mode."
            count = storage.outbox_count()
            return f"{count} item(s) waiting to upload."
        status = self.syncd.status_dict()
        outbox = status.get("outbox", {})
        head   = outbox.get("head") or {}
        text   = (
            f"{status.get('queued', 0)} waiting · "
            f"sent {status.get('sent_total', 0)} · "
            f"failed {status.get('failed_total', 0)}"
        )
        if status.get("sos_pending"):
            text += f" · {status['sos_pending']} SOS waiting"
        if head:
            text += (
                f"\nnext: {head.get('method')} {head.get('path')} "
                f"(attempt {head.get('attempts')})"
            )
        return text

    def run_diagnostics(self) -> None:
        runner = self.services.get("diagnose")
        screen: SettingsScreen = self.screens["settings"]  # type: ignore[assignment]
        if runner is None:
            screen.info.setPlainText("No diagnostics in this mode.")
            return
        screen.info.setPlainText("Checking…")

        async def run() -> None:
            import asyncio
            payload = await asyncio.to_thread(runner)
            lines = [
                f"{'ok ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}"
                for c in payload.get("checks", [])
            ]
            if payload.get("reason"):
                lines += ["", payload["reason"]]
            for fix in payload.get("fixes", []):
                lines.append(f"fix: {fix}")
            screen.info.setPlainText("\n".join(lines))

        self.bridge.submit(run())

    def flush_outbox_now(self) -> None:
        syncd = self.syncd
        if syncd is None:
            return
        syncd.outbox.reset_backoff()

        async def run() -> None:
            import asyncio
            await asyncio.to_thread(syncd.flush_once)

        self.bridge.submit(run())

    def calibrate_compass(self) -> None:
        hardware = self.services.get("hardware")
        if hardware is not None:
            try:
                hardware.imu.calibrate(4.0)
            except Exception:
                logger.exception("calibration failed")
        if self.engine is not None:
            self.engine.fusion.calibrate()
        settings_screen: SettingsScreen = self.screens["settings"]  # type: ignore[assignment]
        settings_screen.info.appendPlainText("Compass calibrated.")

    # ── shutdown ──────────────────────────────────────────────────────────
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        logger.info("shutting down the UI")
        self._drain_timer.stop()
        self._engine_timer.stop()
        self._sos_timer.stop()
        for unsub in self._unsubscribers:
            try:
                unsub()
            except Exception:
                pass
        if self.engine is not None:
            try:
                self.engine.stop()
            except Exception:
                pass
        if self.syncd is not None:
            try:
                self.syncd.stop()
            except Exception:
                pass
        self.bridge.stop()
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
# run_ui  — public entry point called by app/processes.py
# ─────────────────────────────────────────────────────────────────────────────
def run_ui(
    settings: Settings,
    bus: Bus,
    *,
    engine: Any = None,
    game_host: Optional[GameHost] = None,
    persona: Any = None,
    voice: Any = None,
    syncd: Any = None,
    services: Optional[Dict[str, Any]] = None,
    argv: Optional[Sequence[str]] = None,
) -> int:
    """Create the Qt application and run it. Returns the process exit code."""
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(list(argv or sys.argv)))
    apply_theme(app, settings.ui_theme)
    window = MainWindow(
        settings,
        bus,
        engine=engine,
        game_host=game_host,
        persona=persona,
        voice=voice,
        syncd=syncd,
        services=services,
    )
    if settings.ui_fullscreen:
        window.showFullScreen()
    else:
        window.show()
    return app.exec()
