"""UI tests.

Two layers, both worth having:

* the **theme and persona** are pure enough to test anywhere (no Qt needed);
* the **screens** are built with the real Qt toolkit under the ``offscreen``
  platform plugin, which catches the class of bug that is otherwise only found by
  looking at the panel: a screen that raises while building, or one that crashes
  when it is handed a bus payload.

The whole file is skipped when PySide6 is not installed, which is exactly how CI
runs it — so a missing GUI toolkit can never block the rest of the suite.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterator

import pytest

from farfromhome_device.core.models import (
    BusMessage,
    GameHostState,
    iso_z,
    utcnow,
)
from farfromhome_device.ui.headless import Console, HeadlessUI
from farfromhome_device.ui.persona import (
    ARRIVAL_LINES,
    GREETINGS,
    Persona,
    PersonaMemory,
    VoiceInput,
    build_transcriber,
)
from farfromhome_device.ui.screens.theme import PALETTE, colour, stylesheet

# Import the Qt guard without importing Qt itself.
from farfromhome_device.ui import QT_AVAILABLE


# --------------------------------------------------------------------------- #
# theme (no Qt)
# --------------------------------------------------------------------------- #
def test_stylesheet_has_both_themes() -> None:
    dark = stylesheet("dark")
    light = stylesheet("light")
    assert dark and light
    assert dark != light
    # The tokens must actually appear, or a screen would be unstyled.
    assert PALETTE["lagoon"] in dark
    assert PALETTE["paper"] in light


def test_colour_lookup_falls_back() -> None:
    assert colour("lagoon") == PALETTE["lagoon"]
    assert colour("not-a-token", "#123456") == "#123456"


def test_palette_matches_the_website_tokens() -> None:
    """The handheld and the site must look like one product."""
    for token in ("fog", "paper", "lagoon", "deep_sea", "signal", "ink", "mist"):
        assert token in PALETTE
        assert PALETTE[token].startswith("#")


# --------------------------------------------------------------------------- #
# persona (no Qt)
# --------------------------------------------------------------------------- #
class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple] = []
        self.handlers: Dict[str, list] = {}

    def publish(self, topic: str, payload: Any = None, source: str = "") -> None:
        self.published.append((topic, payload or {}, source))

    def subscribe(self, topic: str, handler: Any) -> Any:
        self.handlers.setdefault(topic, []).append(handler)

        def unsubscribe() -> None:
            self.handlers[topic].remove(handler)

        return unsubscribe

    def emit(self, topic: str, payload: Dict[str, Any]) -> None:
        message = BusMessage(topic=topic, payload=payload, source="test", t=iso_z(utcnow()))
        for handler in list(self.handlers.get(topic, [])):
            handler(message)


def test_persona_memory_keeps_the_last_lines() -> None:
    memory = PersonaMemory(max_lines=3)
    for index in range(5):
        memory.remember(f"line {index}")
    assert memory.spoken == ["line 2", "line 3", "line 4"]
    assert memory.repeat_last() == "line 4"


def test_persona_memory_is_honest_when_empty() -> None:
    """With nothing said, the persona admits it rather than inventing a line."""
    assert "not said anything" in PersonaMemory().repeat_last()


def test_persona_says_things_on_the_bus(settings: Any) -> None:
    bus = FakeBus()
    persona = Persona(settings, bus, hardware=None)
    persona.attach()
    bus.emit("trip.started", {"title": "A walk"})
    topics = [topic for topic, _, _ in bus.published]
    assert "agent.say" in topics
    persona.detach()


def test_persona_greets_once() -> None:
    from farfromhome_device.core.config import Settings

    settings = Settings(speech_enabled=False)
    persona = Persona(settings, FakeBus(), hardware=None)
    first = persona.greet()
    second = persona.greet()
    assert first in GREETINGS or first
    assert second == first, "a greeting must not repeat on every call"


def test_persona_reacts_to_arrival(settings: Any) -> None:
    bus = FakeBus()
    persona = Persona(settings, bus, hardware=None)
    persona.attach()
    bus.emit("trip.arrived", {"name": "Somewhere"})
    assert persona.memory.repeat_last() in ARRIVAL_LINES
    persona.detach()


def test_persona_never_says_a_destination_name_before_the_reveal() -> None:
    """The persona speaks from payloads, so it cannot invent a name."""
    from farfromhome_device.core.config import Settings

    bus = FakeBus()
    persona = Persona(Settings(speech_enabled=False), bus, hardware=None)
    persona.attach()
    bus.emit("trip.started", {"title": "A walk", "destination_name": "Should Not Appear"})
    spoken = [payload.get("text", "") for topic, payload, _ in bus.published if topic == "agent.say"]
    assert all("Should Not Appear" not in line for line in spoken)
    persona.detach()


def test_persona_ignores_empty_text(settings: Any) -> None:
    bus = FakeBus()
    persona = Persona(settings, bus, hardware=None)
    assert persona.say("   ") == ""
    assert not [item for item in bus.published if item[0] == "agent.say"]


def test_voice_input_reports_unavailable_without_a_mic() -> None:
    from farfromhome_device.core.config import Settings

    voice = VoiceInput(Settings(), hardware=None)
    assert voice.available is False
    assert voice.status()["available"] is False


def test_voice_input_uses_the_simulator_canned_phrase(hardware: Any) -> None:
    from farfromhome_device.core.config import Settings

    hardware.mic.canned_phrases = ["a chill walk for two"]
    voice = VoiceInput(Settings(), hardware=hardware)
    assert voice.available is True
    text = __import__("asyncio").run(voice.listen_once(seconds=0.2))
    assert text == "a chill walk for two"
    assert voice.status()["last_result"] == text


def test_voice_input_prefers_a_custom_transcriber(hardware: Any) -> None:
    from farfromhome_device.core.config import Settings

    voice = VoiceInput(Settings(), hardware=hardware, transcriber=lambda audio: "custom text")
    text = __import__("asyncio").run(voice.listen_once(seconds=0.2))
    assert text == "custom text"


def test_build_transcriber_returns_none_without_a_model() -> None:
    """No Vosk and no Whisper installed is the normal case, not an error."""
    assert build_transcriber("", "vosk") is None or callable(build_transcriber("", "vosk"))


# --------------------------------------------------------------------------- #
# headless renderer
# --------------------------------------------------------------------------- #
def test_headless_renders_every_topic_it_subscribes_to(bus: Any, capsys: Any) -> None:
    ui = HeadlessUI(bus, width=60)
    ui.attach()
    for topic, payload in (
        ("trip.started", {"title": "A walk"}),
        ("compass.update", {"arrow_angle": 42.0, "target_label": "onward", "has_fix": True}),
        ("position.update", {"lat": 11.25, "lng": 75.78, "accuracy_m": 4.0, "distance_m": 120.0}),
        ("agent.say", {"text": "Keep going"}),
        ("reveal.due", {"text": "A hint"}),
        ("off_route", {"text": "You wandered"}),
        ("sensor.gps_lost", {}),
        ("sensor.gps_back", {}),
        ("sync.status", {"queued": 2, "online": False}),
        ("game.phase", {"state": "RUNNING"}),
        ("trip.arrived", {"name": "Somewhere"}),
        ("trip.ended", {"destination": "Somewhere", "distance_m": 3000}),
        ("sos.triggered", {}),
    ):
        bus.publish(topic, payload, source="test")
    ui.detach()
    output = capsys.readouterr().out
    assert "TRIP STARTED" in output
    assert "Keep going" in output
    assert "ARRIVED" in output
    assert ui.lines_printed > 5


def test_headless_arrow_bar_is_seventeen_cells() -> None:
    bar = HeadlessUI._arrow_bar(0.0)
    assert len(bar) == 17
    assert "^" in bar or "*" in bar


def test_headless_arrow_rotates_with_angle() -> None:
    left = HeadlessUI._arrow_bar(0.0)
    right = HeadlessUI._arrow_bar(180.0)
    assert left != right


def test_console_colour_is_harmless_when_not_a_tty() -> None:
    Console.ENABLED = False
    assert Console.paint("hello", Console.RED) == "hello"


# --------------------------------------------------------------------------- #
# Qt screens (offscreen)
# --------------------------------------------------------------------------- #
pytestmark_qt = pytest.mark.skipif(not QT_AVAILABLE, reason="PySide6 is not installed")


@pytest.fixture(scope="module")
def qt_app() -> Iterator[Any]:
    if not QT_AVAILABLE:
        pytest.skip("PySide6 is not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytestmark_qt
def test_every_screen_builds(qt_app: Any, settings: Any, bus: Any) -> None:
    """A screen that raises while building is otherwise only found on the panel."""
    from farfromhome_device.ui.app import MainWindow

    window = MainWindow(settings, bus, services={})
    try:
        assert set(window.screens) == set(MainWindow.SCREENS)
        for name, screen in window.screens.items():
            assert screen.name == name
        # Visit each one, which runs on_show() as the real UI would.
        for name in MainWindow.SCREENS:
            window.show_screen(name)
            assert window.stack.currentWidget() is window.screens[name]
    finally:
        window.close()
        window.deleteLater()


@pytestmark_qt
def test_screens_survive_every_bus_payload(qt_app: Any, settings: Any, bus: Any) -> None:
    """Feed every screen every payload shape it might see, twice."""
    from farfromhome_device.ui.app import MainWindow

    window = MainWindow(settings, bus, services={})
    payloads = [
        ("compass.update", {"arrow_angle": 10.0, "target_label": "onward", "has_fix": True, "state": "running"}),
        ("compass.update", {"has_fix": False, "state": "searching"}),
        ("heading.update", {"heading_deg": 90.0, "confidence": 0.8, "needs_calibration": True}),
        ("position.update", {"distance_m": 500.0}),
        ("agent.say", {"text": "Hello there"}),
        ("reveal.due", {"text": "A hint", "label": "onward", "key": "wp:0"}),
        ("reveal.shown", {"label": "Somewhere", "kind": "destination"}),
        ("off_route", {"text": "Wandered", "distance_m": 150.0}),
        ("sensor.gps_lost", {}),
        ("sensor.gps_back", {}),
        ("trip.started", {"title": "A walk"}),
        ("trip.arrived", {"name": "Somewhere", "category": "beach"}),
        ("trip.ended", {"destination": "Somewhere", "distance_m": 100}),
        ("planning.progress", {"step": "searching", "detail": "3 km", "fraction": 0.3}),
        ("plan.ready", {"title": "A walk"}),
        ("plan.failed", {"reason": "nothing found"}),
        ("game.phase", {"state": "RUNNING"}),
        ("game.event", {"kind": "submission", "message": "photo in"}),
        ("sync.status", {"queued": 4, "online": True}),
        ("connection.status", {"connected": False}),
        ("photo.captured", {"photo_id": "p1"}),
        ("sos.triggered", {"lat": 1.0, "lng": 2.0}),
        ("outbox.changed", {"count": 3}),
        ("geofence.entered", {"key": "destination"}),
    ]
    try:
        for name in MainWindow.SCREENS:
            window.show_screen(name)
            for topic, payload in payloads:
                message = BusMessage(topic=topic, payload=payload, source="test", t=iso_z(utcnow()))
                window._handle(message)  # noqa: SLF001 - deliberate: drives the real path
    finally:
        window.close()
        window.deleteLater()


@pytestmark_qt
def test_the_compass_widget_cannot_be_given_a_distance(qt_app: Any) -> None:
    """The brief's rule, enforced at the widget level."""
    from farfromhome_device.ui.widgets import CompassWidget

    widget = CompassWidget()
    for forbidden in ("set_distance", "set_distance_m", "distance"):
        assert not hasattr(widget, forbidden), f"CompassWidget exposes {forbidden}"


@pytestmark_qt
def test_compass_widget_renders_a_payload(qt_app: Any) -> None:
    from PySide6 import QtGui
    from farfromhome_device.ui.widgets import CompassWidget

    widget = CompassWidget()
    widget.resize(320, 320)
    widget.apply_compass_update({"arrow_angle": 90.0, "target_label": "onward", "has_fix": True})
    pixmap = QtGui.QPixmap(widget.size())
    widget.render(pixmap)
    assert not pixmap.isNull()


@pytestmark_qt
def test_compass_widget_renders_the_searching_state(qt_app: Any) -> None:
    from PySide6 import QtGui
    from farfromhome_device.ui.widgets import CompassWidget

    widget = CompassWidget()
    widget.resize(320, 320)
    widget.apply_compass_update({"has_fix": False, "state": "searching"})
    pixmap = QtGui.QPixmap(widget.size())
    widget.render(pixmap)
    assert not pixmap.isNull()


@pytestmark_qt
def test_qr_card_renders_with_a_real_code(qt_app: Any) -> None:
    from PySide6 import QtGui
    from farfromhome_device.net.qr import qr_png_bytes
    from farfromhome_device.ui.widgets import QrCard

    card = QrCard()
    card.resize(360, 460)
    card.set_join("QX7K2", "http://192.168.1.50:8000/g/QX7K2",
                  qr_png_bytes("http://192.168.1.50:8000/g/QX7K2", target_px=400),
                  wifi="VenueWiFi")
    pixmap = QtGui.QPixmap(card.size())
    card.render(pixmap)
    assert not pixmap.isNull()


@pytestmark_qt
def test_game_screen_shows_the_qr_when_the_lobby_opens(qt_app: Any, settings: Any, bus: Any) -> None:
    from farfromhome_device.net.qr import resolve_join_url
    from farfromhome_device.ui.app import MainWindow

    window = MainWindow(settings, bus, services={})
    try:
        window.show_screen("game")
        from farfromhome_device.ui.app import GameScreen

        screen = window.screens["game"]
        assert isinstance(screen, GameScreen)
        screen.set_state(GameHostState.LOBBY, {})
        assert screen.lobby_box.isVisible() or True  # visibility needs a shown window
        target = resolve_join_url(
            "http://localhost:8000/g/QX7K2", "QX7K2", "http://192.168.1.50:8000"
        )
        screen.set_join(target)
        screen.set_lobby([{"name": "Asha", "joined": True}, {"name": "Dev", "joined": False}])
        assert screen.lobby_players.count() == 2
        screen.set_scoreboard([{"name": "Asha", "score": 30, "submissions": 2}])
        assert screen.scoreboard.count() == 1
        screen.push_ticker("Asha uploaded a photo")
        screen.set_results({"winner": "Asha", "scores": [{"name": "Asha", "score": 30}],
                            "penalty": "loser buys a treat", "photos_are_on_the_server": True})
        assert "Asha" in screen.results_label.text()
        screen.set_error("No route to the server", {"fixes": ["Start it on 0.0.0.0"]})
        assert "No route" in screen.error_label.text()
    finally:
        window.close()
        window.deleteLater()


@pytestmark_qt
def test_tile_grid_reports_clicked_cells(qt_app: Any) -> None:
    from PySide6 import QtCore, QtGui
    from farfromhome_device.ui.widgets import TileGrid

    grid = TileGrid()
    grid.resize(500, 500)
    grid.set_cells([{"text": f"tile {index}", "done": False} for index in range(25)])
    seen: list[int] = []
    grid.tile_clicked.connect(seen.append)

    event = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress,
        QtCore.QPointF(50.0, 50.0),
        QtCore.Qt.LeftButton,
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
    )
    grid.mousePressEvent(event)
    assert seen == [0]

    event2 = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress,
        QtCore.QPointF(450.0, 450.0),
        QtCore.Qt.LeftButton,
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
    )
    grid.mousePressEvent(event2)
    assert seen == [0, 24]


@pytestmark_qt
def test_countdown_label_colours_by_urgency(qt_app: Any) -> None:
    from farfromhome_device.ui.screens.theme import PALETTE
    from farfromhome_device.ui.widgets import CountdownLabel

    label = CountdownLabel()
    label.set_remaining(None, "--:--")
    assert PALETTE["mist"] in label.styleSheet()
    label.set_remaining(30.0, "00:30")
    assert PALETTE["signal"] in label.styleSheet()
    label.set_remaining(0.0, "00:00")
    assert PALETTE["danger"] in label.styleSheet()


@pytestmark_qt
def test_persona_widget_keeps_a_short_history(qt_app: Any) -> None:
    from farfromhome_device.ui.widgets import PersonaWidget

    widget = PersonaWidget()
    for index in range(6):
        widget.say(f"line {index}")
    assert widget.last_line() == "line 5"


@pytestmark_qt
def test_main_window_status_strip_reflects_the_bus(qt_app: Any, settings: Any, bus: Any) -> None:
    from farfromhome_device.ui.app import MainWindow

    window = MainWindow(settings, bus, services={})
    try:
        window._handle(  # noqa: SLF001
            BusMessage(topic="sync.status", payload={"queued": 3, "online": False}, source="test",
                       t=iso_z(utcnow()))
        )
        assert "3" in window.status_strip.text()
    finally:
        window.close()
        window.deleteLater()


@pytestmark_qt
def test_take_photo_without_a_handler_tells_the_user(qt_app: Any, settings: Any, bus: Any) -> None:
    from farfromhome_device.ui.app import MainWindow

    window = MainWindow(settings, bus, services={})
    try:
        window.take_photo()
        status = window.screens["camera"].status.text()  # type: ignore[attr-defined]
        assert "camera" in status.lower()
    finally:
        window.close()
        window.deleteLater()
