"""The engine: a deterministic state machine that runs a plan. **No LLM calls.**

    idle → planned → running → paused → arrived → wrapped_up

Loop (default 8 Hz, brief §5.2 "about 5-10 Hz, plus event-driven triggers"):

1. Read the GPS fix and the IMU heading.
2. Fuse the heading (tilt-compensated magnetometer + gyro + GPS course).
3. Compute the bearing to the next target and ``arrow_angle``; publish
   ``compass.update``.
4. Check geofences, reveal rules and the schedule; publish ``reveal.due`` or
   ``geofence.entered``.
5. Detect ``off_route`` after the dwell window and warn gently.
6. Log the (downsampled) track to SQLite for the wrap-up.

Everything the engine knows is local, so it runs offline. It never blocks on the
network: writes go to storage and to the bus, and ``syncd`` picks them up.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core.bus import Bus
from ..core.clock import Clock, format_duration
from ..core.config import Settings
from ..core.db import Storage
from ..core.models import (
    CompassUpdate,
    HeadingReading,
    Plan,
    Position,
    TripState,
    iso_z,
)
from ..hal.base import HardwareBundle
from .compass import Compass
from .fusion import HeadingFusion, smooth_heading
from .geofence import GeofenceEngine, OffRouteDetector, build_route_path
from .geo import compass_point, haversine_m, simplify_path
from .reveal import Reveal, RevealEngine

logger = logging.getLogger("ffh.engine")


@dataclass
class EngineStats:
    ticks: int = 0
    fixes: int = 0
    fixes_dropped: int = 0
    gps_loss_events: int = 0
    points_logged: int = 0
    points_queued: int = 0
    reveals: int = 0
    compass_updates: int = 0
    off_route_warnings: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)

    @property
    def runtime_s(self) -> float:
        return time.monotonic() - self.started_monotonic

    def to_payload(self) -> Dict[str, Any]:
        return {
            "ticks": self.ticks,
            "fixes": self.fixes,
            "fixes_dropped": self.fixes_dropped,
            "gps_loss_events": self.gps_loss_events,
            "points_logged": self.points_logged,
            "points_queued": self.points_queued,
            "reveals": self.reveals,
            "compass_updates": self.compass_updates,
            "off_route_warnings": self.off_route_warnings,
            "runtime_s": round(self.runtime_s, 1),
        }


class TripEngine:
    """Runs one trip. Construct, :meth:`start`, then :meth:`tick` (or ``run``)."""

    def __init__(
        self,
        infrastructure: Any,
        plan: Plan,
        clock: Optional[Clock] = None,
        *,
        auto_start: bool = True,
    ) -> None:
        self.infra = infrastructure
        self.settings: Settings = infrastructure.settings
        self.bus: Bus = infrastructure.bus
        self.storage: Storage = infrastructure.storage
        self.hardware: HardwareBundle = infrastructure.hardware
        self.clock = clock or infrastructure.clock
        self.plan = plan

        # --- state ----------------------------------------------------------
        self.state = TripState.IDLE
        self.stats = EngineStats()
        self.started_at: Optional[datetime] = None
        self.arrived_at: Optional[datetime] = None
        self.position: Optional[Position] = None
        #: The fix we had when GPS was lost. Kept for diagnostics and for a future
        #: "you were last seen here" screen; it is never used to draw the arrow.
        self._last_position_when_lost: Optional[Position] = None
        self.heading: Optional[HeadingReading] = None
        self.last_compass: Optional[CompassUpdate] = None
        self.track: List[Position] = []
        self.distance_m = 0.0
        # Starts False so the very first tick with no fix reports GPS loss. Starting
        # True would mean a trip that never gets a fix never says why.
        self._last_gps_ok = False
        # Downsampling uses the *fix* timestamps, not the wall clock, so a GPX
        # replay or an accelerated demo behaves exactly like a real walk.
        self._last_logged_at: Optional[float] = None
        self._last_queued_at: Optional[float] = None
        self._pending_points: List[Position] = []
        self._reveal_payloads: List[Reveal] = []
        self._running = False
        self._task: Optional[asyncio.Task[Any]] = None

        # --- collaborators --------------------------------------------------
        self.fusion = HeadingFusion()
        self.compass = Compass(plan, warmer_colder_enabled=False)
        self.revealer = RevealEngine(plan)
        self.geofences = GeofenceEngine(plan)
        self.off_route = OffRouteDetector(
            path=build_route_path(plan),
            tolerance_m=self.settings.off_route_tolerance_m,
            dwell_seconds=self.settings.off_route_seconds,
        )

        if auto_start:
            self.prepare()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def prepare(self) -> None:
        """idle → planned: persist the plan and get the hardware listening."""
        self.storage.save_plan(self.plan)
        self.storage.set_trip_state(self.plan.trip_id, TripState.PLANNED.value)
        self.state = TripState.PLANNED
        for device in self.hardware.devices:
            if not device.is_open:
                device.open()
        self.bus.publish(
            "plan.ready",
            self.plan.public_summary(),
            source="engine",
        )
        logger.info(
            "engine prepared: trip %s, %d waypoints, %.2f km planned",
            self.plan.trip_id,
            len(self.plan.waypoints),
            self.plan.route_km,
        )

    def start(self) -> None:
        """planned → running."""
        if self.state in (TripState.RUNNING, TripState.ARRIVED, TripState.WRAPPED_UP):
            return
        self.started_at = self.clock.now()
        self.revealer.set_started_at(self.started_at)
        self.state = TripState.RUNNING
        self.storage.set_trip_state(
            self.plan.trip_id, TripState.RUNNING.value, started_at=iso_z(self.started_at)
        )
        self.bus.publish(
            "trip.started",
            {
                "trip_id": self.plan.trip_id,
                "title": self.plan.title,
                "started_at": iso_z(self.started_at),
                "waypoint_count": len(self.plan.waypoints),
            },
            source="engine",
        )
        logger.info("trip %s started", self.plan.trip_id)

    def pause(self) -> None:
        if self.state == TripState.RUNNING:
            self.state = TripState.PAUSED
            self.storage.set_trip_state(self.plan.trip_id, TripState.PAUSED.value)
            self.bus.publish(
                "game.phase",
                {"phase": "paused", "text": "Paused. The trip is still here when you are."},
                source="engine",
            )

    def resume(self) -> None:
        if self.state == TripState.PAUSED:
            self.state = TripState.RUNNING
            self.storage.set_trip_state(self.plan.trip_id, TripState.RUNNING.value)

    def arrive(self) -> None:
        """running → arrived. Fires the destination reveal and the arrival photo task."""
        if self.state == TripState.ARRIVED:
            return
        self.arrived_at = self.clock.now()
        self.state = TripState.ARRIVED
        self.storage.set_trip_state(
            self.plan.trip_id, TripState.ARRIVED.value, arrived_at=iso_z(self.arrived_at)
        )
        # Force the destination reveal even if the geofence did not fire first
        # (e.g. the trip was arrived manually from the UI).
        if "destination" not in self.revealer.fired:
            self.revealer.fired.add("destination")
        self.bus.publish(
            "trip.arrived",
            {
                "trip_id": self.plan.trip_id,
                "name": self.plan.destination.name,
                "lat": self.plan.destination.lat,
                "lng": self.plan.destination.lng,
                "category": self.plan.destination.category.value,
                "at": iso_z(self.arrived_at),
            },
            source="engine",
        )
        logger.info("trip %s arrived at %s", self.plan.trip_id, self.plan.destination.name)

    def wrap_up(self) -> Dict[str, Any]:
        """arrived → wrapped_up. Emits the summary the wrap-up screen renders."""
        self.state = TripState.WRAPPED_UP
        self.storage.set_trip_state(
            self.plan.trip_id,
            TripState.WRAPPED_UP.value,
            finished_at=iso_z(self.clock.now()),
            distance_m=round(self.distance_m, 1),
        )
        summary = self.summary()
        self.bus.publish("trip.ended", summary, source="engine")
        logger.info("trip %s wrapped up: %.0f m", self.plan.trip_id, self.distance_m)
        return summary

    def summary(self) -> Dict[str, Any]:
        return {
            "trip_id": self.plan.trip_id,
            "title": self.plan.title,
            "destination": self.plan.destination.name,
            "category": self.plan.destination.category.value,
            "distance_m": round(self.distance_m, 1),
            "duration_s": int((self.clock.now() - self.started_at).total_seconds()) if self.started_at else 0,
            "points": len(self.track),
            "waypoints_reached": len(self.revealer.reached_waypoints),
            "waypoints_total": len(self.plan.waypoints),
            "photos": self.storage.photo_count(self.plan.trip_id),
            "stats": self.stats.to_payload(),
        }

    # ------------------------------------------------------------------ #
    # one tick
    # ------------------------------------------------------------------ #
    def tick(self, dt: Optional[float] = None) -> Optional[CompassUpdate]:
        """Advance the engine one step. Safe to call directly from tests."""
        if self.state not in (TripState.RUNNING, TripState.PAUSED):
            return None
        self.stats.ticks += 1

        # 1 + 2: sensors and fusion -------------------------------------
        self._read_heading(dt)
        fix = self._read_position()
        if fix is not None:
            self._accept_fix(fix)
        else:
            self._check_gps_loss()

        position = self.position
        if position is None:
            update = self.compass.update(None, self.heading, state=self.state.value)
            self.last_compass = update
            self.bus.publish("compass.update", update.model_dump(mode="json"), source="engine")
            self.stats.compass_updates += 1
            return update

        # 3: compass ------------------------------------------------------
        destination_revealed = self.revealer.is_destination_revealed()
        update = self.compass.update(
            position,
            self.heading,
            state=self.state.value,
            destination_revealed=destination_revealed,
        )
        self.last_compass = update
        self.bus.publish("compass.update", update.model_dump(mode="json"), source="engine")
        self.stats.compass_updates += 1

        # 4: geofences, reveals, schedule ---------------------------------
        self._evaluate_geofences(position)
        self._evaluate_reveals(position)

        # 5: off route -----------------------------------------------------
        self._evaluate_off_route(position)

        # 6: track log -----------------------------------------------------
        self._log_track(position)
        if self.state == TripState.RUNNING:
            self._maybe_queue_points()
        return update

    async def run(self, hz: Optional[float] = None) -> None:
        """Run the loop until :meth:`stop`. Cancellable."""
        rate = hz or self.settings.engine_hz
        period = 1.0 / max(0.5, rate)
        self._running = True
        logger.info("engine loop starting at %.1f Hz", rate)
        while self._running:
            begin = time.monotonic()
            try:
                self.tick()
            except Exception:
                logger.exception("engine tick failed")
            elapsed = time.monotonic() - begin
            await asyncio.sleep(max(0.0, period - elapsed))

    def start_background(self, hz: Optional[float] = None) -> asyncio.Task[Any]:
        self._task = asyncio.ensure_future(self.run(hz))
        return self._task

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    # ------------------------------------------------------------------ #
    # sensor handling
    # ------------------------------------------------------------------ #
    def _read_heading(self, dt: Optional[float]) -> None:
        sample = None
        imu = self.hardware.imu
        if imu.is_open:
            sample = imu.read()
        if sample is None:
            return
        reading = self.fusion.update_imu(sample, dt)
        self.heading = HeadingReading(
            heading_deg=smooth_heading(
                self.heading.heading_deg if self.heading else None,
                reading.heading_deg,
            ),
            source=reading.source,
            confidence=reading.confidence,
            magnetometer_ok=reading.magnetometer_ok,
            needs_calibration=reading.needs_calibration,
        )
        self.bus.publish(
            "heading.update",
            {
                "heading_deg": round(self.heading.heading_deg, 1),
                "source": self.heading.source,
                "confidence": self.heading.confidence,
                "magnetometer_ok": self.heading.magnetometer_ok,
                "needs_calibration": self.heading.needs_calibration,
            },
            source="engine",
        )

    def _read_position(self) -> Optional[Position]:
        gps = self.hardware.gps
        if not gps.is_open:
            return None
        return gps.read()

    def _accept_fix(self, fix: Position) -> None:
        """Smooth and filter. A bad fix is dropped, not used."""
        if not fix.valid or fix.accuracy_m > self.settings.gps_max_accuracy_m:
            self.stats.fixes_dropped += 1
            return

        if self.position is not None:
            # Reject a physically impossible jump (> 12 m/s between fixes).
            jump = haversine_m(self.position.lat, self.position.lng, fix.lat, fix.lng)
            delta_t = max(0.1, (fix.t - self.position.t).total_seconds())
            if jump / delta_t > 12.0:
                logger.debug("dropping implausible GPS jump: %.0f m in %.1f s", jump, delta_t)
                self.stats.fixes_dropped += 1
                return

        previous = self.position
        self.position = fix
        self.stats.fixes += 1
        self.fusion.update_gps(fix)

        if previous is not None:
            step = haversine_m(previous.lat, previous.lng, fix.lat, fix.lng)
            # GPS jitter of a couple of metres must not be added to the trip
            # distance: standing still would otherwise show a walk. Real movement
            # is well above this floor, so the trip total stays honest.
            noise_floor = max(2.0, 0.5 * min(previous.accuracy_m, fix.accuracy_m))
            if step >= noise_floor:
                self.distance_m += step

        if not self._last_gps_ok:
            self._last_gps_ok = True
            self.bus.publish(
                "sensor.gps_back",
                {"lat": fix.lat, "lng": fix.lng, "accuracy_m": fix.accuracy_m},
                source="engine",
            )

        self.bus.publish(
            "position.update",
            {
                "lat": fix.lat,
                "lng": fix.lng,
                "accuracy_m": round(fix.accuracy_m, 1),
                "speed_mps": fix.speed_mps,
                "course_deg": fix.course_deg,
                "distance_m": round(self.distance_m, 1),
            },
            source="engine",
        )

    def _check_gps_loss(self) -> None:
        """Announce a lost fix once, on the transition, not on every tick.

        The last known position is kept for the arrival check but is marked stale so
        the compass shows its "searching" state rather than a confident arrow based
        on a fix from ten minutes ago.
        """
        if not self._last_gps_ok:
            return
        self._last_gps_ok = False
        self._last_position_when_lost = self.position
        self.position = None
        self.stats.gps_loss_events += 1
        self.bus.publish("sensor.gps_lost", {}, source="engine")

    # ------------------------------------------------------------------ #
    # rules
    # ------------------------------------------------------------------ #
    def _evaluate_geofences(self, position: Position) -> None:
        entered = self.geofences.update(position)
        for zone in entered:
            self.bus.publish(
                "geofence.entered",
                {
                    "key": zone.key,
                    "kind": zone.kind,
                    "waypoint_index": zone.waypoint_index,
                    "label": zone.label,
                },
                source="engine",
            )
            if zone.kind == "destination" and self.state == TripState.RUNNING:
                self.arrive()
                self._evaluate_reveals(position)

    def _evaluate_reveals(self, position: Position) -> None:
        for reveal in self.revealer.evaluate(position, now=self.clock.now()):
            self.stats.reveals += 1
            payload = reveal.to_payload()
            # The destination reveal is the arrival; everything else is a hint.
            self.bus.publish(
                "reveal.due" if reveal.kind != "destination" else "reveal.shown",
                payload,
                source="engine",
            )
            if reveal.kind == "waypoint":
                self.bus.publish(
                    "agent.say",
                    {"text": self._hint_sentence(reveal), "kind": "hint"},
                    source="engine",
                )

    def _hint_sentence(self, reveal: Reveal) -> str:
        if reveal.text:
            return reveal.text
        return f"You are close to {reveal.label}."

    def _evaluate_off_route(self, position: Position) -> None:
        previous_warnings = self.off_route.state.warnings
        state = self.off_route.update(position)
        if state.warnings != previous_warnings:
            self.stats.off_route_warnings = state.warnings
            self.bus.publish(
                "off_route",
                {
                    **state.to_payload(),
                    "text": "You have wandered a little. No rush - the arrow will still point the way.",
                },
                source="engine",
            )

    # ------------------------------------------------------------------ #
    # logging and upload queueing
    # ------------------------------------------------------------------ #
    def _log_track(self, position: Position) -> None:
        # Downsample: one row every ~5 s of *trip* time (brief §5.2 step 6). Using
        # the fix timestamp means a fast replay logs the same track as real time.
        fix_time = position.t.timestamp()
        if (
            self._last_logged_at is not None
            and fix_time - self._last_logged_at < self.settings.gps_point_interval_s
        ):
            return
        self._last_logged_at = fix_time
        self.track.append(position)
        self.storage.add_track_point(
            trip_id=self.plan.trip_id,
            lat=position.lat,
            lng=position.lng,
            accuracy_m=position.accuracy_m,
            speed_mps=position.speed_mps,
            course_deg=position.course_deg,
            heading_deg=self.heading.heading_deg if self.heading else None,
            recorded_at=iso_z(position.t),
        )
        self.stats.points_logged += 1

    def _maybe_queue_points(self) -> None:
        """Buffer fixes and hand a batch to the sync layer every N seconds of trip time.

        The engine never performs network I/O. It publishes ``ui.command`` with a
        ``queue_points`` action; :mod:`farfromhome_device.net.syncd` (or the runtime
        in single-process demo mode) turns that into outbox rows.
        """
        if self.position is not None:
            self._pending_points.append(self.position)
        if self._last_queued_at is None:
            self._last_queued_at = self.position.t.timestamp() if self.position else 0.0
            return
        fix_time = self.position.t.timestamp() if self.position else self._last_queued_at
        if fix_time - self._last_queued_at < self.settings.gps_batch_interval_s:
            return
        self._last_queued_at = fix_time
        self.flush_points()

    def flush_points(self, force: bool = False) -> List[Position]:
        """Emit the buffered fixes for upload. Returns the batch that was sent."""
        if not self._pending_points:
            return []
        batch = self._pending_points[: self.settings.gps_batch_size]
        self._pending_points = self._pending_points[len(batch):]
        if not batch and not force:
            return []
        self.stats.points_queued += len(batch)
        self.bus.publish(
            "ui.command",
            {
                "action": "queue_points",
                "trip_id": self.plan.trip_id,
                "points": [point.to_wire() for point in batch],
            },
            source="engine",
        )
        return batch

    # ------------------------------------------------------------------ #
    # views for the UI
    # ------------------------------------------------------------------ #
    def track_polyline(self, tolerance_m: float = 8.0) -> List[Tuple[float, float]]:
        return simplify_path([(point.lat, point.lng) for point in self.track], tolerance_m)

    def live_status(self) -> Dict[str, Any]:
        remaining: Optional[float] = None
        if self.started_at:
            elapsed = (self.clock.now() - self.started_at).total_seconds() / 60.0
            remaining = max(0.0, self.plan.spec.max_minutes - elapsed)
        return {
            "state": self.state.value,
            "trip_id": self.plan.trip_id,
            "title": self.plan.title,
            "distance_m": round(self.distance_m, 1),
            "track_points": len(self.track),
            "minutes_remaining": round(remaining, 1) if remaining is not None else None,
            "minutes_remaining_label": format_duration(remaining * 60) if remaining is not None else "--:--",
            "heading": self.heading.model_dump(mode="json") if self.heading else None,
            "compass": self.last_compass.model_dump(mode="json") if self.last_compass else None,
            "geofences": self.geofences.status(),
            "off_route": self.off_route.status(),
            "reveals": [reveal.to_payload() for reveal in self.revealer.history],
            "revealed_waypoints": sorted(self.revealer.reached_waypoints),
            "destination_revealed": self.revealer.is_destination_revealed(),
            "stats": self.stats.to_payload(),
        }

    def current_bearing_label(self) -> str:
        if self.last_compass is None:
            return "--"
        return compass_point(self.last_compass.bearing_deg)
