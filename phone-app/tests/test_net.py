"""Networking tests: pairing, the REST client's status handling, and the WebSocket.

These run against fakes rather than a live server, so they are fast and deterministic.
The end-to-end check against the real server lives in ``test_server_integration.py``
and is skipped when nothing is listening.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest

from farfromhome_device.core.db import Storage
from farfromhome_device.core.models import (
    CreateGameRequest,
    GPSPointItem,
    PhotoMeta,
    Plan,
    Position,
)
from farfromhome_device.net.client import DeviceClient
from farfromhome_device.net.outbox import Outbox
from farfromhome_device.net.pairing import (
    NotPaired,
    PairingClient,
    PairingError,
    clear_token,
    read_token,
    write_token,
)
from farfromhome_device.net.syncd import SOS_RETRY_SECONDS, SyncDaemon, SyncStatus


# --------------------------------------------------------------------------- #
# a fake HTTP session, so no test ever opens a socket
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.reason = "OK" if status_code < 400 else "Error"
        self.content = self.text.encode("utf-8")

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Records every call and replies from a queue or a routing table."""

    def __init__(self) -> None:
        self.headers: Dict[str, str] = {}
        self.calls: List[Dict[str, Any]] = []
        self.routes: Dict[str, Any] = {}
        self.queue: List[Any] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, "kwargs": kwargs, "headers": dict(self.headers)})
        if self.queue:
            reply = self.queue.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        for fragment, reply in self.routes.items():
            if fragment in url:
                if callable(reply):
                    return reply(method, url, kwargs)
                if isinstance(reply, Exception):
                    raise reply
                return reply
        return FakeResponse(404, {"detail": "not found"})

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("POST", url, **kwargs)


@pytest.fixture()
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture()
def client(settings: Any, storage: Storage, session: FakeSession) -> DeviceClient:
    outbox = Outbox(storage)
    instance = DeviceClient(settings, outbox=outbox, session=session)
    instance.set_token("test-token", persist=False)
    return instance


# --------------------------------------------------------------------------- #
# pairing
# --------------------------------------------------------------------------- #
def test_register_returns_a_code(settings: Any, session: FakeSession) -> None:
    session.routes["/device/register"] = FakeResponse(
        200, {"device_id": "d1", "pairing_code": "K7P2QX", "poll_secret": "s3cret"}
    )
    pairing = PairingClient(settings, session=session)
    info = pairing.register("Gizmo-01")
    assert info.pairing_code == "K7P2QX"
    assert info.device_id == "d1"
    assert info.display()["instruction"].count("K7P2QX") == 1
    # Registration must not send an Authorization header.
    assert "Authorization" not in session.calls[0]["headers"]


def test_register_reports_an_unreachable_server(settings: Any, session: FakeSession) -> None:
    import requests

    session.queue = [requests.ConnectionError("no route to host")]
    pairing = PairingClient(settings, session=session)
    with pytest.raises(PairingError) as error:
        pairing.register()
    assert "cannot reach the server" in str(error.value)


def test_poll_reports_pending_then_paired(settings: Any, session: FakeSession) -> None:
    session.routes["/device/pairing/d1"] = FakeResponse(200, {"status": "pending"})
    pairing = PairingClient(settings, session=session)
    from farfromhome_device.net.pairing import PairingSession

    info = PairingSession(device_id="d1", pairing_code="K7P2QX", poll_secret="s")
    assert pairing.poll_once(info)["status"] == "pending"

    session.routes["/device/pairing/d1"] = FakeResponse(200, {"status": "paired", "token": "tok"})
    assert pairing.poll_once(info)["status"] == "paired"


def test_a_bad_poll_secret_is_treated_as_expired(settings: Any, session: FakeSession) -> None:
    session.routes["/device/pairing/d1"] = FakeResponse(401, {"detail": "Invalid secret"})
    pairing = PairingClient(settings, session=session)
    from farfromhome_device.net.pairing import PairingSession

    info = PairingSession(device_id="d1", pairing_code="K", poll_secret="wrong")
    assert pairing.poll_once(info)["status"] == "expired"


def test_wait_for_claim_saves_the_token(settings: Any, session: FakeSession) -> None:
    session.routes["/device/pairing/d1"] = FakeResponse(200, {"status": "paired", "token": "saved-token"})
    pairing = PairingClient(settings, session=session)
    from farfromhome_device.net.pairing import PairingSession

    info = PairingSession(device_id="d1", pairing_code="K", poll_secret="s")
    token = pairing.wait_for_claim(info, max_attempts=2)
    assert token == "saved-token"
    assert read_token(settings.token_file) == "saved-token"


def test_wait_for_claim_gives_up_on_expiry(settings: Any, session: FakeSession) -> None:
    session.routes["/device/pairing/d1"] = FakeResponse(401, {"detail": "gone"})
    pairing = PairingClient(settings, session=session)
    from farfromhome_device.net.pairing import PairingSession

    info = PairingSession(device_id="d1", pairing_code="K", poll_secret="s")
    assert pairing.wait_for_claim(info, max_attempts=3) is None


def test_token_file_round_trip(settings: Any) -> None:
    assert read_token(settings.token_file) is None
    write_token(settings.token_file, "abc123")
    assert read_token(settings.token_file) == "abc123"
    clear_token(settings.token_file)
    assert read_token(settings.token_file) is None


def test_clear_token_is_idempotent(settings: Any) -> None:
    clear_token(settings.token_file)
    clear_token(settings.token_file)


# --------------------------------------------------------------------------- #
# the client
# --------------------------------------------------------------------------- #
def test_client_sends_the_bearer_token(client: DeviceClient, session: FakeSession) -> None:
    session.routes["/device/heartbeat"] = FakeResponse(200, {"status": "ok"})
    assert client.heartbeat(80) is True
    assert session.calls[0]["headers"]["Authorization"] == "Bearer test-token"


def test_client_forgets_the_token_on_401(client: DeviceClient, session: FakeSession, settings: Any) -> None:
    session.routes["/device/heartbeat"] = FakeResponse(401, {"detail": "bad token"})
    lost: List[bool] = []
    client.on_token_lost = lambda: lost.append(True)
    with pytest.raises(NotPaired):
        client.heartbeat(80)
    assert client.paired is False
    assert lost == [True]
    assert read_token(settings.token_file) is None


def test_health_returns_none_when_unreachable(client: DeviceClient, session: FakeSession) -> None:
    import requests

    session.queue = [requests.ConnectionError("down")]
    assert client.health() is None


def test_health_parses_the_payload(client: DeviceClient, session: FakeSession) -> None:
    session.routes["/api/health"] = FakeResponse(200, {"ok": True, "app": "FarFromHome"})
    health = client.health()
    assert health is not None and health.ok is True and health.app == "FarFromHome"


def test_network_info_is_parsed(client: DeviceClient, session: FakeSession) -> None:
    session.routes["/api/network-info"] = FakeResponse(
        200, {"lan_ip": "192.168.1.50", "port": 8000, "base_url": "http://192.168.1.50:8000", "candidates": []}
    )
    info = client.network_info()
    assert info is not None and info.base_url == "http://192.168.1.50:8000"


# --------------------------------------------------------------------------- #
# queued writes use the server's real paths
# --------------------------------------------------------------------------- #
def test_queue_trip_posts_the_plan(client: DeviceClient, sample_plan: Plan) -> None:
    row_id = client.queue_trip(sample_plan)
    row = client.outbox.peek(1)[0]
    assert row["id"] == row_id
    assert row["path"] == "/device/trips"
    body = json.loads(row["body"])
    assert body["trip_id"] == sample_plan.trip_id
    assert body["destination"]["name"] == sample_plan.destination.name
    assert body["members"]


def test_queue_points_uses_the_wire_format(client: DeviceClient) -> None:
    """The body is a bare array, which is what the running server validates."""
    client.queue_points("t1", [Position(lat=11.1, lng=75.1, accuracy_m=4.0)])
    row = client.outbox.peek(1)[0]
    assert row["path"] == "/device/trips/t1/points"
    body = json.loads(row["body"])
    assert isinstance(body, list), "the server takes a bare list, not {'points': [...]}"
    point = body[0]
    assert set(point) == {"lat", "lng", "t", "acc"}
    assert point["t"].endswith("Z")


def test_queue_points_accepts_wire_items(client: DeviceClient) -> None:
    client.queue_points("t1", [GPSPointItem(lat=11.1, lng=75.1)])
    body = json.loads(client.outbox.peek(1)[0]["body"])
    assert body[0]["lat"] == 11.1


def test_queue_photo_keeps_the_file(client: DeviceClient, tmp_path: Any) -> None:
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpeg bytes")
    client.queue_photo("t1", photo, PhotoMeta(photo_id="p1", member_name="Asha"))
    row = client.outbox.peek(1)[0]
    assert row["kind"] == "photo"
    assert row["file_path"] == str(photo)
    form = json.loads(row["form"])
    assert form["photo_id"] == "p1"
    assert form["member_name"] == "Asha"


def test_queue_reveal_and_arrive_and_finish(client: DeviceClient) -> None:
    client.queue_waypoint_reveal("t1", 2)
    client.queue_arrive("t1", 11.2, 75.7)
    client.queue_finish("t1", 5120.0, "a good walk")
    paths = [row["path"] for row in client.outbox.peek(5)]
    assert paths == [
        "/device/trips/t1/waypoints/2/reveal",
        "/device/trips/t1/arrive",
        "/device/trips/t1/finish",
    ]
    assert json.loads(client.outbox.peek(3)[2]["body"])["distance_m"] == 5120.0


# --------------------------------------------------------------------------- #
# direct calls
# --------------------------------------------------------------------------- #
def test_create_game_sends_the_contract_body(client: DeviceClient, session: FakeSession) -> None:
    session.routes["/device/games"] = FakeResponse(
        200,
        {
            "game_id": "g1",
            "code": "QX7K2",
            "join_url": "http://192.168.1.50:8000/g/QX7K2",
            "qr_png_url": "/api/games/g1/qr.png",
            "status": "open",
            "ends_at": None,
        },
    )
    request = CreateGameRequest(
        trip_id="t1",
        type="photo_hunt",
        duration_s=600,
        params={"approval": "peer_vote"},
        players=[{"name": "Asha", "color": "#D32F2F"}],
        tasks=[{"text": "something red", "item_key": "red", "points": 10}],
    )
    response = client.create_game(request)
    assert response.code == "QX7K2"
    sent = session.calls[0]["kwargs"]["json"]
    assert sent["type"] == "photo_hunt"
    assert sent["players"][0]["name"] == "Asha"
    # The running server mints its own game id; we must not claim to control it.
    assert "game_id" not in sent


def test_start_and_end_game_hit_the_right_paths(client: DeviceClient, session: FakeSession) -> None:
    session.routes["/start"] = FakeResponse(200, {"status": "running"})
    session.routes["/end"] = FakeResponse(200, {"status": "ended"})
    client.start_game("g1")
    client.end_game("g1")
    urls = [call["url"] for call in session.calls]
    assert urls[0].endswith("/api/device/games/g1/start")
    assert urls[1].endswith("/api/device/games/g1/end")


def test_game_state_parses_the_servers_shape(client: DeviceClient, session: FakeSession) -> None:
    """The server returns ``scores`` keyed by player id, not a players array."""
    session.routes["/state"] = FakeResponse(
        200, {"status": "running", "ends_at": None, "scores": {"p1": 20, "p2": 10}, "submissions_count": 3}
    )
    state = client.game_state("g1")
    assert state.status == "running"
    assert state.scores["p1"] == 20
    roster = [{"name": "Asha", "color": "#fff"}, {"name": "Dev", "color": "#000"}]
    normalised = state.normalised(roster, {"Asha": "p1", "Dev": "p2"})
    assert normalised[0]["name"] == "Asha"
    assert normalised[0]["score"] == 20


def test_game_state_accepts_a_players_array(client: DeviceClient, session: FakeSession) -> None:
    """Brief 03 §7.3 asks for a players array; accept it when a server provides one."""
    session.routes["/state"] = FakeResponse(
        200,
        {
            "status": "running",
            "players": [{"name": "Asha", "joined": True, "score": 5, "submissions": 1}],
            "pending_votes": 2,
        },
    )
    state = client.game_state("g1")
    normalised = state.normalised()
    assert normalised[0]["name"] == "Asha"
    assert normalised[0]["joined"] is True


def test_sos_is_sent_immediately_and_queued(client: DeviceClient, session: FakeSession) -> None:
    """Brief 03 §9: "Call the endpoint immediately ... and also queue it for retry." """
    session.routes["/device/sos"] = FakeResponse(200, {"status": "sos_logged"})
    row_id, delivered = client.sos(11.25, 75.78, "t1")
    assert delivered is True
    assert row_id > 0
    assert client.outbox.pending() == 1  # the backup copy
    assert client.outbox.peek(1)[0]["kind"] == "sos"
    # It used the short SOS timeout, not the JSON default.
    assert session.calls[0]["kwargs"]["timeout"] == client.settings.sos_timeout_s


def test_sos_still_queues_when_the_server_is_down(client: DeviceClient, session: FakeSession) -> None:
    import requests

    session.queue = [requests.ConnectionError("no signal")]
    row_id, delivered = client.sos(11.25, 75.78, "t1")
    assert delivered is False
    assert row_id > 0
    assert client.outbox.pending() == 1


# --------------------------------------------------------------------------- #
# the outbox sender
# --------------------------------------------------------------------------- #
def test_send_outbox_row_maps_statuses(client: DeviceClient, session: FakeSession) -> None:
    client.queue_finish("t1", 100.0)
    row = client.outbox.peek(1)[0]

    session.routes["/finish"] = FakeResponse(200, {"status": "finished"})
    assert client.send_outbox_row(row).outcome == "done"

    session.routes["/finish"] = FakeResponse(409, {"error": "duplicate"})
    assert client.send_outbox_row(row).outcome == "done"

    session.routes["/finish"] = FakeResponse(422, {"detail": "bad body"})
    assert client.send_outbox_row(row).outcome == "dropped"

    session.routes["/finish"] = FakeResponse(503, {"detail": "later"})
    assert client.send_outbox_row(row).outcome == "retry"


def test_send_outbox_row_reports_an_unreachable_server(client: DeviceClient, session: FakeSession) -> None:
    import requests

    client.queue_finish("t1", 100.0)
    row = client.outbox.peek(1)[0]
    session.queue = [requests.ConnectionError("wifi gone")]
    result = client.send_outbox_row(row)
    assert result.outcome == "retry"
    assert result.status is None


def test_send_outbox_row_handles_a_401(client: DeviceClient, session: FakeSession) -> None:
    client.queue_finish("t1", 100.0)
    row = client.outbox.peek(1)[0]
    session.queue = [FakeResponse(401, {"detail": "invalid device token"})]
    assert client.send_outbox_row(row).outcome == "unauthorised"


def test_send_outbox_row_uploads_a_photo_as_multipart(client: DeviceClient, session: FakeSession, tmp_path: Any) -> None:
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    client.queue_photo("t1", photo, PhotoMeta(photo_id="p1"))
    row = client.outbox.peek(1)[0]
    session.routes["/photos"] = FakeResponse(200, {"status": "uploaded", "photo_id": "p1"})
    result = client.send_outbox_row(row)
    assert result.outcome == "done"
    assert "files" in session.calls[0]["kwargs"]
    assert session.calls[0]["kwargs"]["timeout"] == client.settings.photo_timeout_s


# --------------------------------------------------------------------------- #
# syncd
# --------------------------------------------------------------------------- #
def test_sync_status_serialises(settings: Any) -> None:
    status = SyncStatus(online=True, queued=3, sent_total=7)
    payload = status.to_payload()
    assert payload["online"] is True
    assert payload["queued"] == 3
    assert payload["sos_pending"] == 0


def test_syncd_flushes_and_marks_online(client: DeviceClient, storage: Storage, session: FakeSession, settings: Any) -> None:
    session.routes["/api/health"] = FakeResponse(200, {"ok": True, "app": "FarFromHome", "server_time": None})
    session.routes["/finish"] = FakeResponse(200, {"status": "finished"})
    client.queue_finish("t1", 10.0)
    daemon = SyncDaemon(settings, storage, client)
    assert daemon.check_connection() is True
    assert daemon.flush_once() is True
    assert daemon.status.sent_total == 1
    assert daemon.outbox.pending() == 0


def test_syncd_reports_offline_when_health_fails(client: DeviceClient, storage: Storage, session: FakeSession, settings: Any) -> None:
    import requests

    session.queue = [requests.ConnectionError("down")]
    daemon = SyncDaemon(settings, storage, client)
    assert daemon.check_connection() is False
    assert daemon.status.online is False
    assert "did not answer" in daemon.status.last_error


def test_sos_rows_retry_on_the_fixed_short_interval(client: DeviceClient, storage: Storage, session: FakeSession, settings: Any) -> None:
    """Brief 03 §9: repeat every 15 seconds until confirmed."""
    assert SOS_RETRY_SECONDS == 15.0
    client.outbox.queue_sos({"lat": 1.0, "lng": 2.0})
    session.routes["/device/sos"] = FakeResponse(503, {"detail": "busy"})
    daemon = SyncDaemon(settings, storage, client)
    assert daemon.flush_once() is False
    row = client.outbox.peek(1)[0]
    assert row["attempts"] == 1
    wait = client.outbox.wait_seconds()
    assert wait is not None and wait <= SOS_RETRY_SECONDS + 1.0


def test_a_resumed_trip_can_be_checked_for_drained_uploads(client: DeviceClient, storage: Storage, settings: Any) -> None:
    drained: List[str] = []
    daemon = SyncDaemon(settings, storage, client)
    daemon.on_trip_drained = drained.append
    assert daemon.check_trip_drained("t1") is True
    assert drained == ["t1"]

    client.queue_finish("t1", 1.0)
    assert daemon.check_trip_drained("t1") is False
    assert drained == ["t1"]


def test_outbox_change_publishes_sync_status(client: DeviceClient, storage: Storage, bus: Any, settings: Any) -> None:
    from farfromhome_device.net.outbox import Outbox

    client.outbox = Outbox(storage, bus=bus)
    daemon = SyncDaemon(settings, storage, client, bus=bus)
    daemon.attach_to_bus()
    bus.publish("ui.command", {"action": "queue_finish", "trip_id": "t1", "distance_m": 10})
    topics = [message.topic for message in bus.recent]
    assert "sync.status" in topics
    assert "outbox.changed" in topics
    daemon.detach()


# --------------------------------------------------------------------------- #
# the websocket client
# --------------------------------------------------------------------------- #
def test_websocket_url_is_built_from_the_server_url(settings: Any) -> None:
    from farfromhome_device.net.websocket import LiveConnection

    connection = LiveConnection(settings, token="tok")
    assert connection.url.startswith("ws://")
    assert "/ws" in connection.url
    assert "token=tok" in connection.url


def test_https_becomes_wss() -> None:
    from farfromhome_device.core.config import Settings
    from farfromhome_device.net.websocket import LiveConnection

    secure = Settings(server_url="https://abc.trycloudflare.com")
    assert LiveConnection(secure).url.startswith("wss://")


def test_the_token_is_hidden_from_logs(settings: Any) -> None:
    from farfromhome_device.net.websocket import LiveConnection, base_url_safe

    connection = LiveConnection(settings, token="secret-token")
    assert "secret-token" not in base_url_safe(connection.url)
    assert "***" in base_url_safe(connection.url)


def test_dispatch_calls_handlers_and_publishes(settings: Any, bus: Any) -> None:
    from farfromhome_device.net.websocket import LiveConnection

    connection = LiveConnection(settings, bus=bus)
    seen: List[tuple] = []
    connection.on_event(lambda event, data: seen.append((event, data)))
    connection._dispatch(json.dumps({"event": "game.submission", "data": {"photo_id": "p1"}}))  # noqa: SLF001
    assert seen == [("game.submission", {"photo_id": "p1"})]
    topics = [message.topic for message in bus.recent]
    assert "game.event" in topics


def test_dispatch_ignores_rubbish(settings: Any) -> None:
    from farfromhome_device.net.websocket import LiveConnection

    connection = LiveConnection(settings)
    connection._dispatch("not json")  # noqa: SLF001
    connection._dispatch(json.dumps({"no_event": True}))  # noqa: SLF001


def test_a_handler_that_raises_does_not_break_the_loop(settings: Any) -> None:
    from farfromhome_device.net.websocket import LiveConnection

    connection = LiveConnection(settings)
    called: List[str] = []

    def bad(event: str, data: Dict[str, Any]) -> None:
        raise RuntimeError("boom")

    connection.on_event(bad)
    connection.on_event(lambda event, data: called.append(event))
    connection._dispatch(json.dumps({"event": "x", "data": {}}))  # noqa: SLF001
    assert called == ["x"]


def test_polling_fallback_runs_and_stops(settings: Any) -> None:
    from farfromhome_device.net.websocket import PollingFallback

    seen: List[int] = []

    async def scenario() -> None:
        fallback = PollingFallback(interval_s=0.01)
        task = fallback.start(lambda: 1, lambda state: seen.append(state))
        await asyncio.sleep(0.05)
        fallback.stop()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert seen, "the polling fallback never called the fetch"
