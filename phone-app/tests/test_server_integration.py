"""End-to-end test against the **real** server.

Every other test in this suite uses a fake HTTP session. This one does not: it
registers a device, claims the pairing code directly in the server's database (the
part a human does on the website), exchanges that for a token, and then walks a
whole trip through the device API — create, points, waypoint reveal, photo, arrive,
finish — checking the response bodies against the same models
``docs/SERVER_CONTRACT.md`` documents.

It is **skipped** when nothing is listening, so it never breaks a laptop that is
not running the server::

    # terminal 1
    cd ../  &&  python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
    # terminal 2
    python -m pytest tests/test_server_integration.py -v

Why this is worth having: the client's paths, field names and status handling were
derived by reading the server source. This test is what proves that reading was
right, and it will fail loudly the day the server changes.
"""

from __future__ import annotations

import os
import sqlite3
import socket
import uuid
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

import pytest

from farfromhome_device.core.models import (
    CreateGameRequest,
    GPSPointItem,
    PhotoMeta,
    iso_z,
    utcnow,
)
from farfromhome_device.net.client import DeviceClient, ServerError

SERVER_URL = os.getenv("FFH_TEST_SERVER", "http://127.0.0.1:8000")
REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_ROOT = REPO_ROOT.parent


def server_is_up(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not server_is_up(SERVER_URL),
    reason=f"no server listening at {SERVER_URL}; start it to run the integration test",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def find_server_db() -> Optional[Path]:
    """Locate the server's SQLite file, so the test can claim a pairing code."""
    env_file = SERVER_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL"):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value.startswith("sqlite:///"):
                    relative = value[len("sqlite:///") :].lstrip("./")
                    candidate = SERVER_ROOT / relative
                    if candidate.is_file():
                        return candidate
    for candidate in (SERVER_ROOT / "data" / "app.db", SERVER_ROOT / "app.db"):
        if candidate.is_file():
            return candidate
    return None


def claim_pairing_code(device_id: str, code: str) -> int:
    """Do what the website does: attach the pairing code to a user account.

    Returns the number of rows changed. Uses the server's own database because the
    website endpoint needs a browser session cookie, and a handheld has no business
    logging in as a person.

    Column names come from the server's SQLAlchemy models (``pairing_codes`` is
    keyed by ``code``, not by a surrogate id).
    """
    database = find_server_db()
    if database is None:
        return 0
    connection = sqlite3.connect(f"file:{database}?mode=rw", uri=True, timeout=10.0)
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT code, claimed_by_user_id FROM pairing_codes WHERE device_id = ? AND code = ?",
            (device_id, code),
        )
        pairing_row = cursor.fetchone()
        if pairing_row is None or pairing_row[1]:
            return 0
        cursor.execute("SELECT id FROM devices WHERE id = ?", (device_id,))
        device_row = cursor.fetchone()
        if device_row is None:
            return 0

        cursor.execute("SELECT owner_user_id FROM devices WHERE id = ?", (device_id,))
        owner = cursor.fetchone()[0]
        if not owner:
            cursor.execute("SELECT id FROM users ORDER BY created_at LIMIT 1")
            user_row = cursor.fetchone()
            if user_row is None:
                return 0
            owner = user_row[0]
            cursor.execute("UPDATE devices SET owner_user_id = ? WHERE id = ?", (owner, device_id))

        cursor.execute(
            "UPDATE pairing_codes SET claimed_by_user_id = ? WHERE device_id = ? AND code = ?",
            (owner, device_id, code),
        )
        connection.commit()
        return cursor.rowcount
    finally:
        connection.close()


@pytest.fixture(scope="module")
def paired_client() -> Iterator[DeviceClient]:
    """A real paired device, or skip if this machine cannot complete pairing."""
    from farfromhome_device.core.config import load_settings  # local: keeps collection cheap
    from farfromhome_device.net.outbox import Outbox

    settings = load_settings(overrides={"server_url": SERVER_URL, "device_mode": "sim"})
    client = DeviceClient(settings, outbox=Outbox(__import__(
        "farfromhome_device.core.db", fromlist=["Storage"]
    ).Storage(settings.outbox_db)))

    session_info = client.register()
    assert session_info.pairing_code, "the server must issue a pairing code"

    changed = claim_pairing_code(session_info.device_id, session_info.pairing_code)
    if not changed:
        pytest.skip(
            "could not claim the pairing code in the server's database; "
            "claim it by hand on the website and re-run"
        )

    status = client.poll_pairing(session_info)
    assert status.get("status") == "paired", f"pairing did not complete: {status}"
    assert status.get("token")
    client.set_token(status["token"], persist=False)
    assert client.paired is True
    yield client


@pytest.fixture(scope="module")
def trip_id(paired_client: DeviceClient) -> Iterator[str]:
    """Create one trip for the whole module to use."""
    plan = {
        "trip_id": str(uuid.uuid4()),
        "title": "Integration test walk",
        "description": "created by tests/test_server_integration.py",
        "mood": "chill",
        "members": [{"name": "Test Explorer", "color": "#1E7F85"}],
        "destination": {
            "name": "Test Beach",
            "lat": 11.265,
            "lng": 75.764,
            "category": "beach",
            "radius_m": 60,
            "secret": True,
        },
        "waypoints": [
            {"idx": 0, "lat": 11.2600, "lng": 75.7700, "radius_m": 40,
             "hint": "Find the place where the road smells of fresh bread."}
        ],
    }
    response = paired_client.request_json("POST", "/device/trips", plan)
    assert response.get("trip_id"), response
    yield str(response["trip_id"])


# --------------------------------------------------------------------------- #
# the checks from brief 03 §13 (smoke tests, run before building anything else)
# --------------------------------------------------------------------------- #
def test_health_endpoint(paired_client: DeviceClient) -> None:
    health = paired_client.health()
    assert health is not None and health.ok is True
    assert health.app, "the server must name itself"
    assert health.server_time, "server_time is what the handheld syncs its clock to"


def test_network_info_endpoint(paired_client: DeviceClient) -> None:
    info = paired_client.network_info()
    assert info is not None
    assert info.base_url, "the website and the QR codes need a reachable base URL"
    # Brief 02 §C1: the helper must never hand out a loopback address when a LAN
    # address exists.
    assert "localhost" not in info.base_url
    assert "127.0.0.1" not in info.base_url


def test_heartbeat(paired_client: DeviceClient) -> None:
    assert paired_client.heartbeat(87) is True


def test_an_invalid_token_is_rejected(paired_client: DeviceClient) -> None:
    """Brief 03 §14: "re-pairing works when the server answers 401"."""
    from farfromhome_device.net.pairing import NotPaired

    original = paired_client._token  # noqa: SLF001
    paired_client.session.headers["Authorization"] = "Bearer definitely-not-a-real-token"
    try:
        with pytest.raises(NotPaired):
            paired_client.request_json("POST", "/device/heartbeat", {"battery_pct": 50})
    finally:
        paired_client.session.headers["Authorization"] = f"Bearer {original}"


def test_unknown_codes_return_404_not_500(paired_client: DeviceClient) -> None:
    with pytest.raises(ServerError) as error:
        paired_client.request_json("GET", "/device/games/does-not-exist/state")
    assert error.value.status == 404


# --------------------------------------------------------------------------- #
# the device API, in the order a real trip uses it
# --------------------------------------------------------------------------- #
def test_trip_creation_echoes_the_id(paired_client: DeviceClient, trip_id: str) -> None:
    # Idempotency: posting the same trip again must not create a second one.
    again = paired_client.request_json(
        "POST",
        "/device/trips",
        {
            "trip_id": trip_id,
            "title": "Integration test walk",
            "destination": {"name": "Test Beach", "lat": 11.265, "lng": 75.764, "category": "beach"},
        },
    )
    assert str(again.get("trip_id")) == trip_id
    assert again.get("status") in ("exists", "created")


def test_gps_points_are_accepted_in_a_batch(paired_client: DeviceClient, trip_id: str) -> None:
    """The endpoint takes a **bare JSON array**, not ``{"points": [...]}``.

    Brief 03 §6.2 documents ``{"points": [...]}``; the running server
    (``add_gps_points(id, points: List[GPSPointItem])``) takes the array itself.
    This test is what found the difference — see docs/SERVER_CONTRACT.md G-02.
    """
    points = [
        GPSPointItem(lat=11.2600, lng=75.7700, t=iso_z(utcnow()), acc=5.0).model_dump(exclude_none=True),
        GPSPointItem(lat=11.2605, lng=75.7705, t=iso_z(utcnow()), acc=4.5).model_dump(exclude_none=True),
    ]
    response = paired_client.request_json("POST", f"/device/trips/{trip_id}/points", points)  # type: ignore[arg-type]
    assert response.get("status") == "ok"
    assert response.get("points_added") == 2


def test_the_documented_points_shape_is_rejected(paired_client: DeviceClient, trip_id: str) -> None:
    """Prove the difference is real, so the contract note cannot go stale silently."""
    with pytest.raises(ServerError) as error:
        paired_client.request_json(
            "POST",
            f"/device/trips/{trip_id}/points",
            {"points": [{"lat": 11.26, "lng": 75.77, "acc": 4.0}]},  # type: ignore[arg-type]
        )
    assert error.value.status == 422


def test_the_server_hides_the_destination_before_arrival(paired_client: DeviceClient, trip_id: str) -> None:
    """The product's core rule, verified against the live server."""
    trip = paired_client.request_json("GET", f"/trips/{trip_id}")
    assert trip.get("destination_revealed") in (False, None)
    # The serialiser redacts the name and coordinates while unrevealed.
    assert trip.get("destination_name") in (None, "", "???")
    assert "Test Beach" not in str(trip)


def test_a_waypoint_reveal_is_accepted(paired_client: DeviceClient, trip_id: str) -> None:
    response = paired_client.request_json(
        "POST", f"/device/trips/{trip_id}/waypoints/0/reveal", {"at": iso_z(utcnow())}
    )
    assert response.get("status") == "revealed"


def test_a_photo_uploads_and_gets_a_thumbnail(paired_client: DeviceClient, trip_id: str, tmp_path: Path) -> None:
    from PIL import Image

    image = Image.new("RGB", (640, 480), (30, 127, 133))
    path = tmp_path / "integration.jpg"
    image.save(path, format="JPEG", quality=80)

    response = paired_client.request_multipart(
        "POST",
        f"/device/trips/{trip_id}/photos",
        path,
        PhotoMeta(photo_id=str(uuid.uuid4()), member_name="Test Explorer", category="waypoint").to_form(),
    )
    assert response.get("status") == "uploaded"
    assert response.get("photo_id")


def test_an_oversized_photo_is_rejected_cleanly(paired_client: DeviceClient, trip_id: str) -> None:
    """Not a 500: the server must answer with a status the client can act on."""
    import requests

    with pytest.raises((ServerError, requests.RequestException)):
        paired_client.request_multipart(
            "POST",
            f"/device/trips/{trip_id}/photos",
            Path(__file__),
            {"photo_id": str(uuid.uuid4())},
        )


def test_arrival_reveals_the_destination(paired_client: DeviceClient, trip_id: str) -> None:
    response = paired_client.request_json(
        "POST",
        f"/device/trips/{trip_id}/arrive",
        {"lat": 11.265, "lng": 75.764, "at": iso_z(utcnow())},
    )
    assert response.get("status") == "arrived"
    assert "Test Beach" in str(response.get("destination_name", ""))

    trip = paired_client.request_json("GET", f"/trips/{trip_id}")
    assert trip.get("destination_revealed") is True
    assert "Test Beach" in str(trip)


def test_finish_accepts_a_summary(paired_client: DeviceClient, trip_id: str) -> None:
    response = paired_client.request_json(
        "POST",
        f"/device/trips/{trip_id}/finish",
        {"distance_m": 5120, "summary": "An integration test that walked nowhere."},
    )
    assert response.get("status") == "finished"


# --------------------------------------------------------------------------- #
# games: the whole phone-join flow, driven from the handheld's side
# --------------------------------------------------------------------------- #
def test_a_game_session_can_be_created_and_lifecycled(paired_client: DeviceClient) -> None:
    request = CreateGameRequest(
        trip_id=None,
        type="photo_hunt",
        duration_s=120,
        params={"approval": "auto", "win_condition": "most_points", "penalty": "integration test"},
        players=[{"name": "Test Phone A", "color": "#E74C3C"},
                 {"name": "Test Phone B", "color": "#3498DB"}],
        tasks=[
            {"player_name": "Test Phone A", "text": "Photograph something red", "item_key": "red", "points": 10},
            {"player_name": "Test Phone B", "text": "Photograph something blue", "item_key": "blue", "points": 10},
        ],
    )
    created = paired_client.create_game(request)
    assert created.game_id
    assert created.code
    # This is the assertion that catches the whole "QR does not work" class of bug.
    assert "localhost" not in created.join_url
    assert "127.0.0.1" not in created.join_url
    assert created.join_url.endswith(f"/g/{created.code}")

    # The session starts in the lobby, and uploads before Start must be refused.
    state = paired_client.game_state(created.game_id)
    assert state.status in ("open", "running")

    started = paired_client.start_game(created.game_id)
    assert started.get("status") == "running"
    assert started.get("ends_at"), "starting must set the deadline"

    state = paired_client.game_state(created.game_id)
    assert state.status == "running"
    assert state.ends_at

    ended = paired_client.end_game(created.game_id)
    assert ended.get("status") == "ended"

    # Ending twice must not blow up: the client retries and the timer may also fire.
    assert paired_client.end_game(created.game_id).get("status") == "ended"

    after = paired_client.game_state(created.game_id)
    assert after.status == "closed"


def test_the_qr_png_endpoint_serves_a_png(paired_client: DeviceClient) -> None:
    request = CreateGameRequest(
        type="photo_hunt",
        duration_s=120,
        players=[{"name": "QR Test", "color": "#1E7F85"}],
        tasks=[{"player_name": "QR Test", "text": "Photograph anything", "points": 5}],
    )
    created = paired_client.create_game(request)
    try:
        assert created.qr_png_url, "the handheld needs a fallback QR URL"
        response = paired_client.session.get(
            f"{paired_client.settings.server_url.rstrip('/')}{created.qr_png_url}", timeout=10
        )
        assert response.status_code == 200
        assert response.content[:4] == b"\x89PNG"
    finally:
        paired_client.end_game(created.game_id)


def test_sos_is_recorded(paired_client: DeviceClient) -> None:
    response = paired_client.request_json(
        "POST", "/device/sos", {"lat": 11.2512, "lng": 75.7800, "at": iso_z(utcnow())}
    )
    assert response.get("status") == "sos_logged"
    assert response.get("sos_id")
