"""Outbox tests: the brief's rules, asserted one by one.

Brief 03 §4 has a status table and §5 has the queue rules. This file turns each
row and bullet into an assertion, because this is the code that decides whether a
photo survives a patchy connection.
"""

from __future__ import annotations

import json
from typing import Any, List

import pytest

from farfromhome_device.core.db import Storage
from farfromhome_device.net.outbox import BACKOFF_SEQUENCE, Outbox, SendResult, backoff_seconds


@pytest.fixture()
def outbox(storage: Storage, bus: Any) -> Outbox:
    return Outbox(storage, bus=bus)


def sender_returning(result: SendResult) -> Any:
    def sender(row: Any) -> SendResult:
        return result

    return sender


# --------------------------------------------------------------------------- #
# ordering
# --------------------------------------------------------------------------- #
def test_rows_are_sent_oldest_first(outbox: Outbox) -> None:
    for index in range(5):
        outbox.enqueue("POST", f"/thing/{index}", {"n": index})

    seen: List[str] = []

    def sender(row: Any) -> SendResult:
        seen.append(row["path"])
        return SendResult("done", status=200)

    outbox.drain(sender)
    assert seen == [f"/thing/{index}" for index in range(5)]


def test_a_failing_head_blocks_everything_behind_it(outbox: Outbox) -> None:
    """Brief 03 §5: "If the first row fails and must wait, everything behind it waits too." """
    outbox.enqueue("POST", "/device/trips", {"trip_id": "t1"})
    outbox.enqueue("POST", "/device/trips/t1/points", {"points": []})

    attempts: List[str] = []

    def sender(row: Any) -> SendResult:
        attempts.append(row["path"])
        return SendResult("retry", status=503, message="server down")

    outbox.flush_once(sender)
    # The second row must not have been tried.
    assert attempts == ["/device/trips"]

    # And while the head is backing off, nothing at all is due.
    assert outbox.head() is None
    assert outbox.pending() == 2


def test_a_trip_is_created_before_its_points(outbox: Outbox) -> None:
    outbox.enqueue("POST", "/device/trips", {"trip_id": "t1"}, kind="trip")
    outbox.enqueue("POST", "/device/trips/t1/points", {"points": []}, kind="points")
    paths = [row["path"] for row in outbox.peek(10)]
    assert paths.index("/device/trips") < paths.index("/device/trips/t1/points")


# --------------------------------------------------------------------------- #
# backoff
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "attempts, expected",
    [(0, 2.0), (1, 4.0), (2, 8.0), (3, 16.0), (4, 32.0), (5, 60.0), (11, 60.0)],
)
def test_backoff_sequence(attempts: int, expected: float) -> None:
    assert backoff_seconds(attempts) == expected


def test_backoff_ladder_matches_the_brief() -> None:
    assert BACKOFF_SEQUENCE == (2, 4, 8, 16, 32, 60)


def test_retry_later_advances_the_next_try(outbox: Outbox, storage: Storage) -> None:
    row_id = outbox.enqueue("POST", "/slow")
    before = outbox.wait_seconds()
    wait = storage.outbox_retry_later(row_id, 0, status=503, error="nope")
    assert wait == 2.0
    after = outbox.wait_seconds()
    assert after is not None and after > (before or 0)


def test_reset_backoff_makes_everything_due_again(outbox: Outbox) -> None:
    row_id = outbox.enqueue("POST", "/slow")
    outbox.storage.outbox_retry_later(row_id, 4)
    assert outbox.head() is None
    outbox.reset_backoff()
    assert outbox.head() is not None


# --------------------------------------------------------------------------- #
# the status table (brief 03 §4)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status, outcome",
    [
        (200, "done"),
        (201, "done"),
        (204, "done"),
        (401, "unauthorised"),
        (409, "done"),      # duplicate or game_closed: final
        (400, "dropped"),   # the request is wrong; never retry
        (422, "dropped"),
        (404, "retry"),     # the trip may not exist yet
        (408, "retry"),
        (425, "retry"),
        (429, "retry"),
        (500, "retry"),
        (503, "retry"),
    ],
)
def test_classify_matches_the_brief(status: int, outcome: str) -> None:
    assert Outbox.classify(status) == outcome


def test_400_is_dropped_not_retried(outbox: Outbox, storage: Storage) -> None:
    outbox.enqueue("POST", "/bad", {"nope": True})
    outbox.flush_once(sender_returning(SendResult("dropped", status=400, message="bad body")))
    assert outbox.pending() == 0
    events = [row["topic"] for row in storage.recent_events(10)]
    assert "outbox.dropped" in events


def test_409_removes_the_row(outbox: Outbox) -> None:
    outbox.enqueue("POST", "/device/games/g1/upload")
    outbox.flush_once(sender_returning(SendResult("done", status=409, message="game_closed")))
    assert outbox.pending() == 0


def test_401_keeps_the_row_for_after_re_pairing(outbox: Outbox) -> None:
    outbox.enqueue("POST", "/device/trips", {"trip_id": "t1"})
    result = outbox.flush_once(sender_returning(SendResult("unauthorised", status=401, message="bad token")))
    assert result is not None and result.outcome == "unauthorised"
    assert outbox.pending() == 1, "a 401 row must survive so it can be sent after re-pairing"


def test_5xx_retries_with_backoff(outbox: Outbox) -> None:
    outbox.enqueue("POST", "/flaky")
    outbox.flush_once(sender_returning(SendResult("retry", status=503, message="down")))
    row = outbox.peek(1)[0]
    assert row["attempts"] == 1
    assert row["last_status"] == 503
    assert row["last_error"] == "down"


def test_a_sender_that_raises_does_not_lose_the_row(outbox: Outbox) -> None:
    outbox.enqueue("POST", "/explodes")

    def sender(row: Any) -> SendResult:
        raise RuntimeError("network stack fell over")

    outbox.flush_once(sender)
    assert outbox.pending() == 1
    assert outbox.peek(1)[0]["attempts"] == 1


# --------------------------------------------------------------------------- #
# photos
# --------------------------------------------------------------------------- #
def test_a_photo_row_keeps_its_file_until_confirmed(outbox: Outbox, storage: Storage, tmp_path: Any) -> None:
    photo = tmp_path / "shot.jpg"
    photo.write_bytes(b"not really a jpeg")
    storage.add_photo(photo_id="p1", local_path=str(photo), trip_id="t1")
    outbox.enqueue(
        "POST", "/device/trips/t1/photos",
        file_path=str(photo), form={"photo_id": "p1"}, kind="photo", ref_id="p1",
    )
    row = outbox.peek(1)[0]
    # The row points at the file; the bytes are not in the queue.
    assert row["file_path"] == str(photo)
    assert row["body"] is None
    assert json.loads(row["form"])["photo_id"] == "p1"
    assert photo.is_file()

    outbox.flush_once(sender_returning(SendResult("done", status=200, payload={"photo_id": "p1", "thumb_url": "/t"})))
    assert outbox.pending() == 0
    assert storage.query_one("SELECT uploaded FROM photos WHERE photo_id='p1'")["uploaded"] == 1
    # The local file stays until the trip is finished (brief 03 §5).
    assert photo.is_file()


def test_local_photo_files_are_deleted_only_after_upload_and_finish(storage: Storage, tmp_path: Any) -> None:
    first = tmp_path / "a.jpg"
    second = tmp_path / "b.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    storage.add_photo(photo_id="p1", local_path=str(first), trip_id="t1")
    storage.add_photo(photo_id="p2", local_path=str(second), trip_id="t1")
    storage.mark_photo_uploaded("p1")

    removed = storage.delete_uploaded_local_files("t1")
    assert removed == 1
    assert not first.exists()
    assert second.exists(), "an unuploaded photo must never be deleted"


# --------------------------------------------------------------------------- #
# SOS
# --------------------------------------------------------------------------- #
def test_sos_is_queued_as_its_own_kind(outbox: Outbox) -> None:
    outbox.queue_sos({"lat": 11.0, "lng": 75.0})
    row = outbox.peek(1)[0]
    assert row["kind"] == "sos"
    assert row["path"] == "/device/sos"
    assert outbox.status()["queued"] == 1


# --------------------------------------------------------------------------- #
# durability
# --------------------------------------------------------------------------- #
def test_the_queue_survives_a_restart(settings: Any) -> None:
    """WAL mode plus a commit per insert (brief 03 §5 "Survive power loss")."""
    store = Storage(settings.outbox_db)
    first = Outbox(store)
    first.enqueue("POST", "/survives", {"a": 1})
    store.close()

    reopened = Storage(settings.outbox_db)
    again = Outbox(reopened)
    assert again.pending() == 1
    assert again.peek(1)[0]["path"] == "/survives"
    assert json.loads(again.peek(1)[0]["body"]) == {"a": 1}
    reopened.close()


def test_status_reports_the_head_and_the_count(outbox: Outbox) -> None:
    outbox.enqueue("POST", "/one")
    outbox.enqueue("POST", "/two")
    status = outbox.status()
    assert status["queued"] == 2
    assert status["head"]["path"] == "/one"
    assert len(status["items"]) == 2
