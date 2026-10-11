"""Tests for the device-contract fixes and the LLM gateway.

These cover the changes made after the handheld's contract report
(``phone-app/docs/SERVER_CONTRACT.md`` §8):

* **G-01** client-supplied ``game_id`` makes the session create idempotent
* **G-02** the GPS points endpoint accepts both the array and the wrapped shape
* **G-05** the photo upload is idempotent on ``photo_id``
* **G-06** a player token stops working the moment the session closes
* **G-07** ``GET /device/games/{id}/state`` returns the players array, pending votes
  and the winner
* the ``/api/llm/*`` gateway holds the keys, rate-limits, caches and degrades to 503

Everything runs against an in-process TestClient with a temporary database, so no
network and no API keys are involved.
"""

from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import pytest

# A temporary database must be in place before the app is imported.
_TMP = tempfile.mkdtemp(prefix="ffh-server-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'test.db'}"
os.environ["DATA_DIR"] = _TMP
os.environ["DEMO_MODE"] = "true"
os.environ["PUBLIC_BASE_URL"] = "http://192.168.1.50:8000"
# The gateway must read as unconfigured, so its tests are deterministic.
for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(key, None)

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.db import SessionLocal  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models.models import Device, Game, GamePlayer, PairingCode, User  # noqa: E402
from backend.app.security import hash_token  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner() -> Iterator[User]:
    db = SessionLocal()
    user = User(
        id=str(uuid.uuid4()),
        username=f"tester-{uuid.uuid4().hex[:8]}",
        display_name="Contract Tester",
        password_hash="x",
        friend_code=uuid.uuid4().hex[:8].upper(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    yield user
    db.close()


@pytest.fixture(scope="module")
def device_token(owner: User) -> str:
    """A paired device, created directly so no browser session is needed."""
    db = SessionLocal()
    token = f"test-token-{uuid.uuid4().hex}"
    device = Device(
        id=str(uuid.uuid4()),
        name="Contract Test Device",
        owner_user_id=owner.id,
        token_hash=hash_token(token),
        online=True,
    )
    db.add(device)
    db.commit()
    db.close()
    return token


@pytest.fixture(scope="module")
def auth(device_token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {device_token}"}


@pytest.fixture(scope="module")
def trip(client: TestClient, auth: Dict[str, str]) -> str:
    trip_id = str(uuid.uuid4())
    response = client.post(
        "/api/device/trips",
        json={
            "trip_id": trip_id,
            "title": "Contract test trip",
            "mood": "chill",
            "members": [{"name": "Tester", "color": "#1E7F85"}],
            "destination": {
                "name": "Hidden Beach",
                "lat": 11.265,
                "lng": 75.764,
                "category": "beach",
                "radius_m": 60,
            },
            "waypoints": [{"idx": 0, "lat": 11.26, "lng": 75.77, "radius_m": 40, "hint": "a hint"}],
        },
        headers=auth,
    )
    assert response.status_code == 200, response.text
    return trip_id


def jpeg_bytes(colour: tuple = (30, 127, 133)) -> bytes:
    from PIL import Image

    image = Image.new("RGB", (320, 240), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


#: The suite is module-scoped and the trip is shared, and points are de-duplicated by
#: timestamp, so each test needs its own second. This hands out distinct ones.
_STAMP_BASE = datetime.now(timezone.utc).replace(microsecond=0)
_stamp_counter = {"n": 0}


def unique_stamp(offset_s: Optional[int] = None) -> str:
    from datetime import timedelta

    if offset_s is None:
        _stamp_counter["n"] += 1
        offset_s = _stamp_counter["n"] * 300
    return (_STAMP_BASE + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# G-02: the points endpoint accepts both shapes
# --------------------------------------------------------------------------- #
def test_points_accepts_the_documented_wrapped_shape(
    client: TestClient, auth: Dict[str, str], trip: str
) -> None:
    """Brief 03 §6.2's ``{"points": [...]}`` now works, alongside the bare array."""
    response = client.post(
        f"/api/device/trips/{trip}/points",
        json={"points": [{"lat": 11.2601, "lng": 75.7701, "t": unique_stamp(), "acc": 4.0}]},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    assert response.json()["points_added"] == 1


def test_points_still_accepts_a_bare_array(client: TestClient, auth: Dict[str, str], trip: str) -> None:
    response = client.post(
        f"/api/device/trips/{trip}/points",
        json=[{"lat": 11.2602, "lng": 75.7702, "t": unique_stamp(offset_s=45), "acc": 4.5}],
        headers=auth,
    )
    assert response.status_code == 200, response.text
    assert response.json()["points_added"] == 1


def test_resending_the_same_points_adds_nothing(client: TestClient, auth: Dict[str, str], trip: str) -> None:
    """De-duplicated by timestamp, so a resend is safe (brief 03 §6.2)."""
    stamp = unique_stamp(offset_s=90)
    body = [{"lat": 11.2610, "lng": 75.7710, "t": stamp, "acc": 4.0}]
    first = client.post(f"/api/device/trips/{trip}/points", json=body, headers=auth)
    second = client.post(f"/api/device/trips/{trip}/points", json=body, headers=auth)
    assert first.json()["points_added"] == 1
    assert second.json()["points_added"] == 0
    assert second.json()["points_received"] == 1


def test_a_float_accuracy_is_still_accepted(client: TestClient, auth: Dict[str, str], trip: str) -> None:
    response = client.post(
        f"/api/device/trips/{trip}/points",
        json=[{"lat": 11.2611, "lng": 75.7711, "t": unique_stamp(offset_s=135), "acc": 6.5}],
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["points_added"] == 1


def test_two_points_in_the_same_second_are_both_kept(
    client: TestClient, auth: Dict[str, str], trip: str
) -> None:
    """De-duplication is per timestamp, not per second.

    A batch uploaded together can easily contain two fixes in the same second, and
    dropping one would quietly lose track data.
    """
    stamp = unique_stamp(offset_s=180)
    response = client.post(
        f"/api/device/trips/{trip}/points",
        json=[
            {"lat": 11.2620, "lng": 75.7720, "t": stamp, "acc": 4.0},
            {"lat": 11.2621, "lng": 75.7721, "t": stamp.replace("Z", ".500Z"), "acc": 4.0},
        ],
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["points_added"] == 2, response.json()


# --------------------------------------------------------------------------- #
# G-05: the photo upload is idempotent
# --------------------------------------------------------------------------- #
def test_a_photo_upload_returns_a_thumb_url(client: TestClient, auth: Dict[str, str], trip: str) -> None:
    photo_id = str(uuid.uuid4())
    response = client.post(
        f"/api/device/trips/{trip}/photos",
        data={"photo_id": photo_id, "member_name": "Tester", "category": "waypoint"},
        files={"file": ("shot.jpg", jpeg_bytes(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "uploaded"
    assert payload["photo_id"] == photo_id, "the server must honour the client's photo_id"
    assert payload["thumb_url"].endswith("/thumb")


def test_reuploading_the_same_photo_id_does_not_duplicate(
    client: TestClient, auth: Dict[str, str], trip: str
) -> None:
    photo_id = str(uuid.uuid4())
    files = {"file": ("shot.jpg", jpeg_bytes((200, 40, 40)), "image/jpeg")}
    first = client.post(
        f"/api/device/trips/{trip}/photos",
        data={"photo_id": photo_id, "category": "waypoint"},
        files=files,
        headers=auth,
    )
    second = client.post(
        f"/api/device/trips/{trip}/photos",
        data={"photo_id": photo_id, "category": "waypoint"},
        files={"file": ("shot.jpg", jpeg_bytes(), "image/jpeg")},
        headers=auth,
    )
    assert first.json()["status"] == "uploaded"
    assert second.json()["status"] == "duplicate"
    assert second.json()["photo_id"] == photo_id

    photos = client.get(f"/api/trips/{trip}/photos").json()
    matching = [photo for photo in photos if photo["id"] == photo_id]
    assert len(matching) == 1, "a retried upload must not create a second row"


def test_the_photo_still_uploads_without_a_photo_id(
    client: TestClient, auth: Dict[str, str], trip: str
) -> None:
    """An older client that omits the field must keep working."""
    response = client.post(
        f"/api/device/trips/{trip}/photos",
        data={"category": "waypoint"},
        files={"file": ("shot.jpg", jpeg_bytes(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "uploaded"
    assert response.json()["photo_id"]


def test_a_photo_id_from_another_trip_is_refused(
    client: TestClient, auth: Dict[str, str], trip: str, owner: User
) -> None:
    other_trip = str(uuid.uuid4())
    client.post(
        "/api/device/trips",
        json={
            "trip_id": other_trip,
            "title": "Other",
            "destination": {"name": "X", "lat": 11.0, "lng": 75.0, "category": "park"},
            "waypoints": [],
        },
        headers=auth,
    )
    photo_id = str(uuid.uuid4())
    files = {"file": ("shot.jpg", jpeg_bytes(), "image/jpeg")}
    client.post(
        f"/api/device/trips/{other_trip}/photos",
        data={"photo_id": photo_id},
        files=files,
        headers=auth,
    )
    response = client.post(
        f"/api/device/trips/{trip}/photos",
        data={"photo_id": photo_id},
        files={"file": ("shot.jpg", jpeg_bytes(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# G-01: a client game_id makes the create idempotent
# --------------------------------------------------------------------------- #
def game_body(game_id: str | None = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "type": "photo_hunt",
        "duration_s": 300,
        "params": {"approval": "auto", "win_condition": "most_points", "penalty": "a treat"},
        "players": [{"name": "Asha", "color": "#D32F2F"}, {"name": "Dev", "color": "#1565C0"}],
        "tasks": [
            {"player_name": "Asha", "text": "Photograph something red", "item_key": "red", "points": 10},
            {"player_name": "Dev", "text": "Photograph something blue", "item_key": "blue", "points": 10},
        ],
    }
    if game_id:
        body["game_id"] = game_id
    return body


def test_a_client_supplied_game_id_is_honoured(client: TestClient, auth: Dict[str, str]) -> None:
    game_id = str(uuid.uuid4())
    response = client.post("/api/device/games", json=game_body(game_id), headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["game_id"] == game_id


def test_creating_the_same_game_twice_returns_the_same_session(
    client: TestClient, auth: Dict[str, str]
) -> None:
    game_id = str(uuid.uuid4())
    first = client.post("/api/device/games", json=game_body(game_id), headers=auth).json()
    second = client.post("/api/device/games", json=game_body(game_id), headers=auth).json()
    assert first["game_id"] == second["game_id"]
    assert first["code"] == second["code"], "a retry must not mint a second join code"

    db = SessionLocal()
    count = db.query(Game).filter(Game.id == game_id).count()
    db.close()
    assert count == 1


def test_the_join_url_is_never_localhost(client: TestClient, auth: Dict[str, str]) -> None:
    """The whole point of G-01's neighbourhood: a QR that a phone can open."""
    created = client.post("/api/device/games", json=game_body(), headers=auth).json()
    assert "localhost" not in created["join_url"]
    assert "127.0.0.1" not in created["join_url"]
    assert created["join_url"].endswith(f"/g/{created['code']}")


# --------------------------------------------------------------------------- #
# G-07: the richer state endpoint
# --------------------------------------------------------------------------- #
def test_state_returns_the_players_array_and_new_fields(client: TestClient, auth: Dict[str, str]) -> None:
    created = client.post("/api/device/games", json=game_body(), headers=auth).json()
    game_id = created["game_id"]
    client.post(f"/api/device/games/{game_id}/start", headers=auth)

    state = client.get(f"/api/device/games/{game_id}/state", headers=auth).json()
    for key in ("players", "pending_votes", "winner", "type", "code", "penalty", "params", "duration_s"):
        assert key in state, f"{key} missing from the state endpoint"
    # The older keys must survive, so nothing reading them breaks.
    assert "scores" in state
    assert "submissions_count" in state

    assert state["status"] == "running"
    assert len(state["players"]) == 2
    for player in state["players"]:
        for field in ("name", "display_name", "joined", "score", "submissions", "completed_tasks", "color"):
            assert field in player, f"{field} missing from a player entry"
    client.post(f"/api/device/games/{game_id}/end", headers=auth)


# --------------------------------------------------------------------------- #
# G-06: closing a session revokes the player links
# --------------------------------------------------------------------------- #
def test_a_player_token_stops_working_when_the_game_closes(client: TestClient, auth: Dict[str, str]) -> None:
    created = client.post("/api/device/games", json=game_body(), headers=auth).json()
    game_id, code = created["game_id"], created["code"]
    client.post(f"/api/device/games/{game_id}/start", headers=auth)

    joined = client.post(f"/api/g/{code}/join", json={"display_name": "Asha"}).json()
    token = joined["token"]
    assert client.get(f"/api/g/{code}/me", headers={"X-Player-Token": token}).status_code == 200

    client.post(f"/api/device/games/{game_id}/end", headers=auth)

    after = client.get(f"/api/g/{code}/me", headers={"X-Player-Token": token})
    assert after.status_code == 409, "a closed session must revoke its player tokens"
    assert after.json()["detail"]["error"] == "game_closed"


def test_an_upload_after_the_close_is_refused(client: TestClient, auth: Dict[str, str]) -> None:
    created = client.post("/api/device/games", json=game_body(), headers=auth).json()
    game_id, code = created["game_id"], created["code"]
    client.post(f"/api/device/games/{game_id}/start", headers=auth)
    joined = client.post(f"/api/g/{code}/join", json={"display_name": "Dev"}).json()
    me = client.get(f"/api/g/{code}/me", headers={"X-Player-Token": joined["token"]}).json()
    task_id = me["tasks"][0]["id"]

    client.post(f"/api/device/games/{game_id}/end", headers=auth)
    response = client.post(
        f"/api/g/{code}/submissions",
        data={"task_id": task_id},
        files={"photo": ("p.jpg", jpeg_bytes(), "image/jpeg")},
        headers={"X-Player-Token": joined["token"]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "game_closed"


def test_the_game_closed_error_is_a_clean_shape(client: TestClient, auth: Dict[str, str]) -> None:
    """The phone page and the handheld both read ``detail.error``."""
    created = client.post("/api/device/games", json=game_body(), headers=auth).json()
    code = created["code"]
    client.post(f"/api/device/games/{created['game_id']}/end", headers=auth)
    response = client.post(f"/api/g/{code}/join", json={"display_name": "Late"})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "game_closed"


# --------------------------------------------------------------------------- #
# the LLM gateway
# --------------------------------------------------------------------------- #
def test_the_gateway_requires_a_device_token(client: TestClient) -> None:
    assert client.post("/api/llm/text", json={"prompt": "hi"}).status_code == 401
    assert client.get("/api/llm/status").status_code == 401


def test_the_gateway_reports_503_when_no_provider_is_configured(
    client: TestClient, auth: Dict[str, str]
) -> None:
    """A supported outcome: the handheld steps past it and uses template content."""
    response = client.post("/api/llm/text", json={"prompt": "hello"}, headers=auth)
    assert response.status_code == 503, response.text
    payload = response.json()["detail"]
    assert payload["error"] == "llm_unavailable"
    assert "GEMINI_API_KEY" in payload["message"]


def test_the_gateway_status_lists_the_chain(client: TestClient, auth: Dict[str, str]) -> None:
    payload = client.get("/api/llm/status", headers=auth).json()
    names = [provider["provider"] for provider in payload["providers"]]
    assert names == ["gemini", "groq", "openrouter"], "the brief's chain, in order"
    assert payload["configured"] is False
    assert payload["limits"]["device_rpm"] >= 1


def test_the_plan_endpoint_carries_no_place_name(client: TestClient, auth: Dict[str, str]) -> None:
    """The gateway cannot leak what it was never told."""
    response = client.post(
        "/api/llm/plan",
        json={"mood": "chill", "people": 2, "distance_km": 2.0, "category": "beach"},
        headers=auth,
    )
    # 503 because no key is configured, and the message must not be a stack trace.
    assert response.status_code == 503
    body = response.text.lower()
    assert "traceback" not in body


def test_the_gateway_caches_identical_requests() -> None:
    """Cache behaviour is testable without a provider, using a fake chain."""
    from backend.app.services.llm_gateway import GatewayResult, LlmGateway, _Cache

    calls: list = []

    class FakeGateway(LlmGateway):
        def _call(self, provider, system, prompt, schema, temperature, max_tokens):  # type: ignore[override]
            calls.append(prompt)
            return '{"ok": true}'

    from backend.app.services.llm_gateway import GatewayProvider

    provider = GatewayProvider(
        name="fake", base_url="http://x", model="m", api_key_env="PATH"
    )
    provider.api_key = lambda: "present"  # type: ignore[method-assign]
    instance = FakeGateway(chain=[provider], cache=_Cache())
    first = instance.complete("same prompt", schema={"type": "object"}, device_id="d1")
    second = instance.complete("same prompt", schema={"type": "object"}, device_id="d1")
    assert first.cached is False
    assert second.cached is True
    assert calls == ["same prompt"], "the provider must be called once"


def test_the_gateway_rate_limits_per_device() -> None:
    from backend.app.services.llm_gateway import GatewayUnavailable, LlmGateway, RateLimited, _Cache, GatewayProvider

    provider = GatewayProvider(name="fake", base_url="http://x", model="m", api_key_env="PATH")
    provider.api_key = lambda: "present"  # type: ignore[method-assign]
    instance = LlmGateway(chain=[provider], cache=_Cache(), device_rpm=2, global_rpm=100)

    class FakeGateway(LlmGateway):
        def _call(self, *args, **kwargs):  # type: ignore[override]
            return "text"

    instance._call = FakeGateway._call.__get__(instance)  # type: ignore[method-assign]
    instance.complete("one", device_id="device-a")
    instance.complete("two", device_id="device-a")
    with pytest.raises(RateLimited):
        instance.complete("three", device_id="device-a")
    # A different device is unaffected.
    assert instance.complete("four", device_id="device-b").text == "text"


def test_extract_json_handles_fenced_output() -> None:
    from backend.app.services.llm_gateway import extract_json

    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Sure:\n{"a": 1}\ndone') == '{"a": 1}'
