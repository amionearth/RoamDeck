"""``syncd``: the sync daemon.

Responsibilities (brief §5.0 table, §7):

* drain the outbox in strict order with the documented backoff;
* keep the server's clock offset honest (``GET /api/health`` -> ``server_time``);
* heartbeat every 30 s while online and re-pair on a 401;
* translate engine ``ui.command`` messages into outbox rows, so the engine never
  touches the network;
* publish ``sync.status`` for the UI banner;
* tell the wrap-up code when a trip's uploads are all confirmed, so local photo
  copies can be deleted.

SOS is deliberately not queued here: :meth:`DeviceClient.sos` sends immediately and
queues a backup copy. This daemon only makes sure the backup copy is retried
aggressively — for ``kind == "sos"`` rows it retries at a fixed short interval
instead of the exponential ladder.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..core.bus import Bus
from ..core.clock import Clock
from ..core.config import Settings
from ..core.db import Storage
from ..core.models import iso_z, utcnow
from .client import DeviceClient, ServerUnreachable
from .outbox import Outbox
from .pairing import NotPaired

logger = logging.getLogger("ffh.net.syncd")

#: SOS retry interval: "Repeat every 15 seconds until the server confirms" (03 §9).
SOS_RETRY_SECONDS = 15.0

ONLINE_INTERVAL_S = 2.0
OFFLINE_INTERVAL_S = 8.0


@dataclass
class SyncStatus:
    online: bool = False
    last_success_at: Optional[str] = None
    last_error: str = ""
    queued: int = 0
    sent_total: int = 0
    failed_total: int = 0
    sos_pending: int = 0
    server_time_skew_s: float = 0.0
    consecutive_failures: int = 0

    def to_payload(self) -> Dict[str, Any]:
        return {
            "online": self.online,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "queued": self.queued,
            "sent_total": self.sent_total,
            "failed_total": self.failed_total,
            "sos_pending": self.sos_pending,
            "server_time_skew_s": round(self.server_time_skew_s, 1),
            "consecutive_failures": self.consecutive_failures,
        }


class SyncDaemon:
    """The background worker. Start it with :meth:`start`, stop with :meth:`stop`."""

    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        client: DeviceClient,
        clock: Optional[Clock] = None,
        bus: Optional[Bus] = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.client = client
        self.clock = clock or Clock()
        self.bus = bus
        self.outbox = client.outbox or Outbox(storage, bus=bus)
        client.outbox = self.outbox
        self.status = SyncStatus()
        self._task: Optional[asyncio.Task[Any]] = None
        self._running = False
        self._last_heartbeat = 0.0
        self._last_health = 0.0
        self._unsubscribers: List[Any] = []
        self.on_trip_drained: Optional[Any] = None

    # ------------------------------------------------------------------ #
    # wiring to the bus
    # ------------------------------------------------------------------ #
    def attach_to_bus(self) -> None:
        """Queue writes and reactions requested by the engine/UI."""
        if not self.bus:
            return
        self._unsubscribers.append(self.bus.subscribe("ui.command", self._on_ui_command))
        self._unsubscribers.append(self.bus.subscribe("sos.triggered", self._on_sos))
        self._unsubscribers.append(self.bus.subscribe("sync.status", self._on_sync_request))

    def detach(self) -> None:
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:
                pass
        self._unsubscribers.clear()

    def _on_ui_command(self, message: Any) -> None:
        payload = message.payload or {}
        action = payload.get("action")
        trip_id = payload.get("trip_id")
        if action == "queue_points" and trip_id and payload.get("points"):
            from ..core.models import GPSPointItem

            points = [GPSPointItem.model_validate(item) for item in payload["points"]]
            self.client.queue_points(trip_id, points)
            self._refresh_queue()
        elif action == "queue_plan" and payload.get("plan"):
            from ..core.models import Plan

            plan = Plan.model_validate(payload["plan"])
            self.client.queue_trip(plan)
            self._refresh_queue()
        elif action == "queue_arrive" and trip_id:
            self.client.queue_arrive(trip_id, float(payload.get("lat", 0.0)), float(payload.get("lng", 0.0)))
            self._refresh_queue()
        elif action == "queue_finish" and trip_id:
            self.client.queue_finish(trip_id, float(payload.get("distance_m", 0.0)), payload.get("summary", ""))
            self._refresh_queue()
        elif action == "queue_reveal" and trip_id:
            self.client.queue_waypoint_reveal(trip_id, int(payload.get("idx", 0)))
            self._refresh_queue()

    def _on_sos(self, message: Any) -> None:
        payload = message.payload or {}
        self.outbox.queue_sos(
            {
                "lat": payload.get("lat"),
                "lng": payload.get("lng"),
                "trip_id": payload.get("trip_id"),
                "at": iso_z(utcnow()),
            }
        )
        self._refresh_queue()

    def _on_sync_request(self, message: Any) -> None:
        if (message.payload or {}).get("force"):
            self.outbox.reset_backoff()

    # ------------------------------------------------------------------ #
    # one pass
    # ------------------------------------------------------------------ #
    def flush_once(self) -> bool:
        """One outbox send. Returns True when something was sent successfully."""
        row = self.outbox.head()
        if row is None:
            return False

        # SOS rows retry on a fixed short interval, never the exponential ladder.
        if row["kind"] == "sos":
            result = self.client.send_outbox_row(row)
            if result.ok:
                self.storage.outbox_done(row["id"])
                self.status.sent_total += 1
                self._refresh_queue()
                return True
            if result.outcome == "retry":
                self.storage.outbox_retry_later(
                    row["id"], int(row["attempts"] or 0), status=result.status,
                    error=result.message, delay=SOS_RETRY_SECONDS,
                )
            else:
                self.storage.outbox_done(row["id"])
            self.status.failed_total += 1
            self._refresh_queue()
            return False

        result = self.outbox.flush_once(self.client.send_outbox_row)
        if result is None:
            return False
        if result.ok:
            self.status.sent_total += 1
            self.status.online = True
            self.status.consecutive_failures = 0
            self.status.last_success_at = iso_z(utcnow())
            self.status.last_error = ""
            return True
        if result.outcome == "unauthorised":
            self.status.last_error = "the server rejected our token; re-pairing is required"
            self.status.online = False
            return False
        if result.outcome == "retry":
            self.status.failed_total += 1
            self.status.consecutive_failures += 1
            self.status.last_error = result.message
            if result.status is None:
                self.status.online = False
        return False

    def check_connection(self) -> bool:
        """Health check + clock alignment. Cheap enough to run every few seconds."""
        health = self.client.health()
        if health is None:
            self.status.online = False
            self.status.last_error = f"{self.settings.server_url} did not answer /api/health"
            self.status.consecutive_failures += 1
            return False

        self.status.online = True
        self.status.last_error = ""
        self.status.consecutive_failures = 0
        self.status.server_time_skew_s = self.clock.apply_server_time(health.server_time)
        self._last_health = time.monotonic()
        return True

    def heartbeat_if_due(self, battery_pct: int = 100) -> bool:
        now = time.monotonic()
        if now - self._last_heartbeat < self.settings.heartbeat_interval_s:
            return False
        self._last_heartbeat = now
        try:
            self.client.heartbeat(battery_pct)
            return True
        except NotPaired:
            logger.warning("heartbeat rejected: the token is invalid, re-pairing is needed")
            self.status.last_error = "invalid device token"
            if self.bus:
                self.bus.publish("connection.status", {"paired": False, "reason": "401"}, source="syncd")
            return False
        except ServerUnreachable:
            self.status.online = False
            return False

    def _refresh_queue(self) -> None:
        self.status.queued = self.outbox.pending()
        sos_rows = [row for row in self.storage.outbox_peek(50) if row["kind"] == "sos"]
        self.status.sos_pending = len(sos_rows)
        if self.bus:
            self.bus.publish("sync.status", self.status.to_payload(), source="syncd")

    def check_trip_drained(self, trip_id: str) -> bool:
        """True when a trip has nothing left in the queue. Used to free local photos."""
        rows = [row for row in self.storage.outbox_peek(200) if trip_id and trip_id in (row["path"] or "")]
        drained = not rows
        if drained and self.on_trip_drained:
            try:
                self.on_trip_drained(trip_id)
            except Exception:
                logger.exception("on_trip_drained handler failed")
        return drained

    # ------------------------------------------------------------------ #
    # the loop
    # ------------------------------------------------------------------ #
    async def run(self) -> None:
        self._running = True
        logger.info("syncd started against %s", self.settings.server_url)
        while self._running:
            did_work = False
            try:
                if time.monotonic() - self._last_health > 15.0:
                    self.check_connection()
                self.flush_once()
                did_work = True
                self.heartbeat_if_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("syncd pass failed")
                self.status.consecutive_failures += 1

            self._refresh_queue()
            interval = ONLINE_INTERVAL_S if self.status.online else OFFLINE_INTERVAL_S
            if not did_work and self.outbox.pending() == 0:
                interval = max(interval, 4.0)
            await asyncio.sleep(interval)

    def start(self) -> asyncio.Task[Any]:
        self.attach_to_bus()
        self._task = asyncio.ensure_future(self.run())
        return self._task

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.detach()

    @property
    def running(self) -> bool:
        return self._running

    def status_dict(self) -> Dict[str, Any]:
        return {
            **self.status.to_payload(),
            "outbox": self.outbox.status(),
            "clock": self.clock.describe(),
            "paired": self.client.paired,
        }


async def run_syncd(
    settings: Settings,
    storage: Storage,
    client: DeviceClient,
    clock: Optional[Clock] = None,
    bus: Optional[Bus] = None,
) -> None:
    """Process entry point for the ``syncd`` process (systemd unit in ``systemd/``)."""
    daemon = SyncDaemon(settings, storage, client, clock=clock, bus=bus)
    task = daemon.start()
    try:
        await task
    except asyncio.CancelledError:
        daemon.stop()
        raise
