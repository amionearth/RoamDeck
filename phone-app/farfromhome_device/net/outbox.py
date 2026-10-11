"""The offline outbox and the background sync worker.

This is the heart of "offline-capable at runtime" (brief §1.3, §7, 03 §5). Rules
implemented here, one for one:

============  ==========================================================================
one queue     Rows are sent in ``id`` order. If the head is backing off, everything
              behind it waits — a trip is always created before its points.
backoff       2, 4, 8, 16, 32, then 60 seconds, per row.
photos        The file stays on disk; the row points at it. The local file is deleted
              only after the server confirms it **and** the trip is finished.
SOS           The single exception: sent immediately with a short timeout, *and*
              queued (see :meth:`Outbox.queue_sos` and :mod:`~.client`).
power loss    SQLite in WAL mode with a commit per insert (see ``core.db``).
idempotency   Every write carries a client-generated UUID, so retries are safe.
============  ==========================================================================

Status handling from the brief's table in 03 §4:

======  ====================================================
status  what we do
======  ====================================================
2xx     done, delete the row
401     stop immediately, raise :class:`NotPaired`
409     final (duplicate or ``game_closed``) — delete the row
400/422 wrong request — log, drop, never retry
404     retry (the trip may not exist yet), unless it is a game lookup
408/425/429/5xx/network  retry with backoff
======  ====================================================
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..core.bus import Bus
from ..core.db import Storage

logger = logging.getLogger("ffh.net.outbox")

#: Statuses that mean "give up, this request can never succeed".
PERMANENT_STATUSES = frozenset({400, 401, 403, 405, 410, 413, 415, 422})

#: Statuses worth retrying.
RETRY_STATUSES = frozenset({404, 408, 425, 429, 500, 502, 503, 504, 507, 509})

#: 409 means duplicate **or** game closed. The brief says treat it as final.
FINAL_STATUSES = frozenset({409})

BACKOFF_SEQUENCE = (2, 4, 8, 16, 32, 60)


def backoff_seconds(attempts: int) -> float:
    """2, 4, 8, 16, 32, then 60 forever."""
    if attempts < len(BACKOFF_SEQUENCE):
        return float(BACKOFF_SEQUENCE[max(0, attempts)])
    return 60.0


@dataclass
class SendResult:
    """What happened to one outbox row."""

    outcome: str  # "done" | "retry" | "dropped" | "unauthorised" | "skipped"
    status: Optional[int] = None
    message: str = ""
    payload: Optional[Dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.outcome == "done"


class Outbox:
    """Wraps :class:`~farfromhome_device.core.db.Storage` with queue semantics."""

    def __init__(self, storage: Storage, bus: Optional[Bus] = None) -> None:
        self.storage = storage
        self.bus = bus
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # enqueue
    # ------------------------------------------------------------------ #
    def enqueue(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        file_path: Optional[str] = None,
        form: Optional[Dict[str, str]] = None,
        kind: str = "json",
        ref_id: Optional[str] = None,
    ) -> int:
        """Queue one request.

        ``body`` may be a dict **or a list**: the server's GPS points endpoint takes
        a bare JSON array. Both survive the round trip through the ``body`` TEXT
        column.
        """
        row_id = self.storage.outbox_add(
            method=method,
            path=path,
            body=body,
            file_path=file_path,
            form=form,
            kind=kind,
            ref_id=ref_id,
        )
        logger.debug("queued #%s %s %s", row_id, method, path)
        self._announce()
        return row_id

    def queue_sos(self, body: Dict[str, Any]) -> int:
        """Queue the backup copy of an SOS. The immediate attempt is the client's job."""
        return self.enqueue("POST", "/device/sos", body=body, kind="sos", ref_id="sos")

    def _announce(self) -> None:
        if self.bus:
            self.bus.publish(
                "sync.status",
                {"queued": self.storage.outbox_count(), "pending": True},
                source="outbox",
            )
            self.bus.publish("outbox.changed", {"count": self.storage.outbox_count()}, source="outbox")

    # ------------------------------------------------------------------ #
    # drain
    # ------------------------------------------------------------------ #
    def head(self) -> Optional[Any]:
        return self.storage.outbox_head()

    def pending(self) -> int:
        return self.storage.outbox_count()

    def wait_seconds(self) -> Optional[float]:
        return self.storage.outbox_wait_seconds()

    def peek(self, limit: int = 20) -> List[Any]:
        return self.storage.outbox_peek(limit)

    def flush_once(self, sender: Callable[[Any], SendResult]) -> Optional[SendResult]:
        """Send the head row, if it is due. Returns ``None`` when there is nothing to do.

        ``sender`` performs the actual HTTP call for one row.
        """
        with self._lock:
            row = self.storage.outbox_head()
            if row is None:
                return None

            attempts = int(row["attempts"] or 0)
            try:
                result = sender(row)
            except Exception as exc:  # a sender bug must not lose the row
                logger.exception("outbox sender raised for row %s", row["id"])
                result = SendResult("retry", message=str(exc))

            if result.outcome == "done":
                self._on_done(row, result)
            elif result.outcome == "dropped":
                logger.warning(
                    "dropping outbox row %s (%s %s): %s",
                    row["id"], row["method"], row["path"], result.message,
                )
                self.storage.log_event(
                    "outbox.dropped",
                    {"path": row["path"], "status": result.status, "message": result.message},
                )
                self.storage.outbox_done(row["id"])
            elif result.outcome == "unauthorised":
                # Leave the row in place: after re-pairing we want to send it.
                logger.warning("outbox: server rejected our token (HTTP 401); re-pairing required")
            elif result.outcome == "skipped":
                pass
            else:
                wait = self.storage.outbox_retry_later(
                    row["id"], attempts, status=result.status, error=result.message
                )
                logger.debug(
                    "retrying outbox row %s in %.0f s (status=%s)", row["id"], wait, result.status
                )

            self._announce()
            return result

    def _on_done(self, row: Any, result: SendResult) -> None:
        if row["kind"] == "photo" and result.payload:
            photo_id = result.payload.get("photo_id")
            if photo_id:
                self.storage.mark_photo_uploaded(photo_id, result.payload.get("thumb_url", ""))
        self.storage.outbox_done(row["id"])

    def drain(
        self,
        sender: Callable[[Any], SendResult],
        max_rows: int = 200,
        max_seconds: float = 30.0,
    ) -> int:
        """Send rows until the queue is empty, the head is backing off, or we hit a limit."""
        deadline = time.monotonic() + max_seconds
        sent = 0
        for _ in range(max_rows):
            if time.monotonic() > deadline:
                break
            result = self.flush_once(sender)
            if result is None:
                break
            if result.outcome == "unauthorised":
                break
            if result.outcome != "done":
                break  # strict order: the head is waiting
            sent += 1
        return sent

    # ------------------------------------------------------------------ #
    # decisions (pure functions, unit-tested directly)
    # ------------------------------------------------------------------ #
    @staticmethod
    def classify(status: int, error_code: str = "") -> str:
        """Map an HTTP status onto an outbox outcome.

        Kept static and side-effect free so the brief's status table can be tested
        line by line.
        """
        if 200 <= status < 300:
            return "done"
        if status == 401:
            return "unauthorised"
        if status in FINAL_STATUSES:
            return "done"  # 409: duplicate or game_closed -> final, remove from the queue
        if status == 404:
            # A trip create may still be in the queue behind us; retry. A game
            # lookup for a session the server never created is final, and the
            # caller distinguishes that by passing error_code.
            return "retry"
        if status in PERMANENT_STATUSES:
            return "dropped"
        if status in RETRY_STATUSES:
            return "retry"
        if status >= 500:
            return "retry"
        return "dropped"

    def reset_backoff(self) -> None:
        """Force every row to be due now (the "Retry" button on the connection banner)."""
        self.storage.outbox_force_now()
        self._announce()

    # ------------------------------------------------------------------ #
    def status(self) -> Dict[str, Any]:
        rows = self.peek(10)
        return {
            "queued": self.pending(),
            "next_in_s": self.wait_seconds(),
            "head": None
            if not rows
            else {
                "id": rows[0]["id"],
                "method": rows[0]["method"],
                "path": rows[0]["path"],
                "attempts": rows[0]["attempts"],
                "last_status": rows[0]["last_status"],
                "last_error": rows[0]["last_error"],
            },
            "items": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "path": row["path"],
                    "attempts": row["attempts"],
                    "next_try_in_s": max(0.0, round(float(row["next_try"]) - time.time(), 1)),
                    "last_status": row["last_status"],
                }
                for row in rows
            ],
        }


def encode_body(body: Optional[Dict[str, Any]]) -> Optional[str]:
    return json.dumps(body) if body is not None else None


def decode_body(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None
