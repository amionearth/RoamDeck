"""Headless rendering of the same screens.

Two uses:

* ``run.py --headless`` on a laptop or over SSH, and in CI, where Qt is unavailable
  or unwanted (brief §0.3's "runs on a laptop" requirement is the *floor*, not the
  ceiling);
* a Pi with no display attached (``systemd`` unit starts with no ``DISPLAY``), where
  the user still gets the agent's voice and the console log.

It deliberately renders the *same* payloads the Qt screens render, from the same
bus topics, so a bug in the display logic shows up in both.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from ..core.bus import Bus
from ..core.models import BusMessage

logger = logging.getLogger("ffh.ui.headless")


class Console:
    """Minimal ANSI colouring that stays readable when piped to a file."""

    ENABLED = sys.stdout.isatty()

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"

    @classmethod
    def paint(cls, text: str, colour: str) -> str:
        if not cls.ENABLED:
            return text
        return f"{colour}{text}{cls.RESET}"


class HeadlessUI:
    """Prints compass, hint, score and status lines as bus messages arrive."""

    def __init__(self, bus: Bus, width: int = 78, quiet_topics: Optional[Sequence[str]] = None) -> None:
        self.bus = bus
        self.width = width
        self.quiet = set(quiet_topics or ["heading.update", "position.update"])
        self._unsubscribers: List[Any] = []
        self._lock = threading.Lock()
        self._last_compass_print = 0.0
        self._last_position_print = 0.0
        self._stop = False
        self.lines_printed = 0

    # ------------------------------------------------------------------ #
    def attach(self) -> None:
        topics = [
            "trip.started", "trip.arrived", "trip.ended", "plan.ready", "plan.failed",
            "planning.progress", "compass.update", "position.update", "heading.update",
            "reveal.due", "reveal.shown", "geofence.entered", "off_route", "agent.say",
            "sensor.gps_lost", "sensor.gps_back", "game.phase", "game.event",
            "sync.status", "connection.status", "sos.triggered", "photo.captured",
        ]
        for topic in topics:
            self._unsubscribers.append(self.bus.subscribe(topic, self._on_message))

    def detach(self) -> None:
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:
                pass
        self._unsubscribers.clear()

    # ------------------------------------------------------------------ #
    def _print(self, text: str) -> None:
        with self._lock:
            self.lines_printed += 1
            print(text, flush=True)

    def banner(self, text: str) -> None:
        line = "=" * self.width
        self._print(Console.paint(line, Console.CYAN))
        self._print(Console.paint(text.center(self.width), Console.BOLD))
        self._print(Console.paint(line, Console.CYAN))

    def status_line(self, label: str, value: str, colour: str = "") -> None:
        prefix = f"{label:<14}"
        text = f"{prefix}{value}"
        self._print(Console.paint(text, colour) if colour else text)

    # ------------------------------------------------------------------ #
    def _on_message(self, message: BusMessage) -> None:
        topic = message.topic
        payload = message.payload or {}

        if topic == "compass.update":
            self._compass(payload)
        elif topic == "position.update":
            self._position(payload)
        elif topic == "heading.update":
            if payload.get("needs_calibration"):
                self.status_line("compass", "needs calibration - wave a figure of eight", Console.YELLOW)
        elif topic == "agent.say":
            self.status_line("agent", str(payload.get("text", "")), Console.CYAN)
        elif topic == "reveal.due":
            self.status_line("hint", str(payload.get("text", "")), Console.YELLOW)
        elif topic == "reveal.shown":
            self.status_line("REVEAL", str(payload.get("label", "")), Console.GREEN)
        elif topic == "geofence.entered":
            self.status_line("arrived at", str(payload.get("key", "")), Console.GREEN)
        elif topic == "off_route":
            self.status_line("off route", str(payload.get("text", "")), Console.YELLOW)
        elif topic == "sensor.gps_lost":
            self.status_line("gps", "lost - arrow frozen", Console.YELLOW)
        elif topic == "sensor.gps_back":
            self.status_line("gps", "back", Console.GREEN)
        elif topic == "trip.started":
            self.banner(f"TRIP STARTED  {payload.get('title', '')}")
        elif topic == "trip.arrived":
            self.banner(f"ARRIVED  {payload.get('name', '')}")
        elif topic == "trip.ended":
            self.banner("TRIP FINISHED")
            for key in ("destination", "distance_m", "duration_s", "photos"):
                if key in payload:
                    self.status_line(key, str(payload[key]))
        elif topic == "planning.progress":
            self.status_line(str(payload.get("step", "planning")), str(payload.get("detail", "")))
        elif topic == "plan.ready":
            self.status_line("plan", f"{payload.get('title')} · {payload.get('route_km')} km")
        elif topic == "plan.failed":
            self.status_line("plan", f"failed: {payload.get('reason')}", Console.RED)
        elif topic == "sos.triggered":
            self.status_line("SOS", "sent", Console.RED)
        elif topic == "photo.captured":
            self.status_line("photo", str(payload.get("photo_id", ""))[:12])
        elif topic == "game.phase":
            phase = payload.get("state") or payload.get("phase")
            self.status_line("game", str(phase))
        elif topic == "game.event":
            event = payload.get("event") or payload.get("kind")
            self.status_line("game event", str(event))
        elif topic == "sync.status":
            queued = payload.get("queued", 0)
            if queued:
                self.status_line("sync", f"{queued} waiting ({'online' if payload.get('online') else 'offline'})")
        elif topic == "connection.status":
            if payload.get("connected") is False:
                self.status_line("live", "websocket down, polling instead", Console.YELLOW)

    # ------------------------------------------------------------------ #
    def _compass(self, payload: Dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last_compass_print < 1.0:
            return
        self._last_compass_print = now
        if not payload.get("has_fix", True):
            self.status_line("compass", "searching for GPS", Console.YELLOW)
            return
        arrow = float(payload.get("arrow_angle", 0.0))
        label = str(payload.get("target_label", "next stop"))
        bar = self._arrow_bar(arrow)
        self.status_line("compass", f"{bar}  {arrow:5.0f} deg  ->  {label}")

    def _position(self, payload: Dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last_position_print < 8.0:
            return
        self._last_position_print = now
        self.status_line(
            "position",
            f"{payload.get('lat', 0):.5f},{payload.get('lng', 0):.5f} "
            f"+-{payload.get('accuracy_m', 0):.0f} m · {payload.get('distance_m', 0):.0f} m walked",
        )

    @staticmethod
    def _arrow_bar(angle: float, width: int = 17) -> str:
        """A tiny ASCII compass: ``<--- ^ --->`` with the marker where you point."""
        angle = angle % 360.0
        index = int(round((angle / 360.0) * (width - 1)))
        cells = ["-"] * width
        cells[(width - 1) // 2] = "v"
        cells[index] = "^" if cells[index] == "-" else "*"
        return "".join(cells)

    def status(self) -> Dict[str, Any]:
        return {"mode": "headless", "lines_printed": self.lines_printed}


def run_headless(
    bus: Bus,
    stop_event: Optional[threading.Event] = None,
    banner_text: str = "FarFromHome headless",
) -> int:
    """Attach, print, and block until interrupted."""
    ui = HeadlessUI(bus)
    ui.attach()
    ui.banner(banner_text)
    try:
        while stop_event is None or not stop_event.is_set():
            time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    finally:
        ui.detach()
    return 0
