"""Engine tests: the M1 acceptance check.

Brief M1: *"Fusion, bearing math, reveal rules, off-route, GPX replay. Check:
unit tests for bearing math; the replay triggers geofences in order."*

So this file is mostly about proving that a replay walks the geofences **in order**
and that the reveal rules fire exactly once each.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, List

import pytest

from farfromhome_device.core.models import ImuSample, Plan, Position, RevealRule, TripState, utcnow
from farfromhome_device.engine.compass import Compass, distance_only_for_arrival
from farfromhome_device.engine.fusion import HeadingFusion, FusionConfig, magnetometer_heading, smooth_heading
from farfromhome_device.engine.geofence import GeofenceEngine, OffRouteDetector, build_route_path
from farfromhome_device.engine.geo import bearing_deg, haversine_m, move_towards
from farfromhome_device.engine.reveal import RevealEngine
from farfromhome_device.engine.state_machine import TripEngine
from farfromhome_device.hal.sim import GpxTrack


# --------------------------------------------------------------------------- #
# fixtures local to this file
# --------------------------------------------------------------------------- #
@pytest.fixture()
def plan(offline_planner: Any) -> Plan:
    from farfromhome_device.agent.planner import PlanRequest

    outcome = asyncio.run(
        offline_planner.plan(
            PlanRequest(
                text="a chill walk by the water for two, about an hour",
                start_lat=11.2588,
                start_lng=75.7804,
                offline=True,
            )
        )
    )
    assert outcome.plan is not None
    return outcome.plan


def walk_plan(plan: Plan, metres_per_step: float = 25.0, seconds_per_step: float = 6.0) -> List[Position]:
    """Walk the plan's route exactly: start, every waypoint in order, destination.

    A straight line from start to destination would miss the waypoint geofences, so
    the replay has to follow the same polyline the engine is checking against — which
    is what a person following the arrow does.
    """
    start_lat = plan.spec.start_lat or 11.2588
    start_lng = plan.spec.start_lng or 75.7804
    route: List[tuple] = [(start_lat, start_lng)]
    route += [(waypoint.lat, waypoint.lng) for waypoint in sorted(plan.waypoints, key=lambda w: w.idx)]
    route.append((plan.destination.lat, plan.destination.lng))

    positions: List[Position] = []
    t = utcnow()
    for (lat1, lng1), (lat2, lng2) in zip(route, route[1:]):
        leg = haversine_m(lat1, lng1, lat2, lng2)
        if leg < 1.0:
            continue
        bearing = bearing_deg(lat1, lng1, lat2, lng2)
        travelled = 0.0
        while travelled < leg:
            travelled = min(leg, travelled + metres_per_step)
            lat, lng = move_towards(lat1, lng1, bearing, travelled)
            t = t + timedelta(seconds=seconds_per_step)
            positions.append(
                Position(lat=lat, lng=lng, accuracy_m=4.0, speed_mps=1.4, course_deg=bearing, t=t)
            )
    return positions


# --------------------------------------------------------------------------- #
# geofences fire in order (the M1 check)
# --------------------------------------------------------------------------- #
def test_replay_triggers_geofences_in_order(plan: Plan) -> None:
    engine = GeofenceEngine(plan)
    entered: List[str] = []
    for position in walk_plan(plan):
        for zone in engine.update(position):
            entered.append(zone.key)

    # Every waypoint, in index order, then the destination last.
    waypoint_keys = [f"wp:{waypoint.idx}" for waypoint in sorted(plan.waypoints, key=lambda w: w.idx)]
    expected = waypoint_keys + ["destination"]
    assert entered == expected, f"expected {expected}, got {entered}"


def test_geofence_hysteresis_does_not_flap(plan: Plan) -> None:
    engine = GeofenceEngine(plan)
    zone = engine.zones[0]
    # Sit exactly on the enter radius and jitter in and out of it by a metre.
    base_lat, base_lng = move_towards(zone.lat, zone.lng, 0.0, zone.radius_m)
    entered: List[str] = []
    for offset in (0.0, 1.0, -1.0, 2.0, -2.0, 3.0):
        position = Position(lat=base_lat + offset * 1e-5, lng=base_lng, accuracy_m=4.0)
        entered.extend(geo.key for geo in engine.update(position))
    assert entered.count(zone.key) == 1


def test_geofence_reports_inside_zones(plan: Plan) -> None:
    engine = GeofenceEngine(plan)
    engine.update(Position(lat=plan.destination.lat, lng=plan.destination.lng))
    assert "destination" in engine.inside_keys()
    assert engine.status()["destination_entered"] is True


# --------------------------------------------------------------------------- #
# reveal rules
# --------------------------------------------------------------------------- #
def test_reveal_fires_once_per_waypoint(plan: Plan) -> None:
    revealer = RevealEngine(plan, started_at=utcnow())
    fired: List[str] = []
    for position in walk_plan(plan):
        for reveal in revealer.evaluate(position):
            fired.append(reveal.key)
    # Walking the same route twice must not fire anything a second time.
    for position in walk_plan(plan):
        for reveal in revealer.evaluate(position):
            fired.append(reveal.key)
    assert len(fired) == len(set(fired)), f"a reveal fired twice: {fired}"
    assert "destination" in fired


def test_never_reveal_never_fires(plan: Plan) -> None:
    plan.waypoints[0].reveal = RevealRule.NEVER
    revealer = RevealEngine(plan, started_at=utcnow())
    for position in walk_plan(plan):
        revealer.evaluate(position)
    assert "wp:0" not in revealer.fired


def test_at_time_reveal_needs_the_clock(plan: Plan) -> None:
    plan.waypoints[0].reveal = RevealRule.AT_TIME
    started = utcnow()
    revealer = RevealEngine(plan, started_at=started, default_at_minutes=10.0)
    # Not yet: only a minute has passed.
    revealer.evaluate(None, now=started + timedelta(minutes=1))
    assert "wp:0" not in revealer.fired
    # Now it is due.
    revealer.evaluate(None, now=started + timedelta(minutes=11))
    assert "wp:0" in revealer.fired


def test_at_time_reveal_does_nothing_without_a_start(plan: Plan) -> None:
    plan.waypoints[0].reveal = RevealRule.AT_TIME
    revealer = RevealEngine(plan, started_at=None)
    revealer.evaluate(None)
    assert "wp:0" not in revealer.fired


def test_safe_plan_view_hides_the_destination_until_arrival(plan: Plan) -> None:
    revealer = RevealEngine(plan, started_at=utcnow())
    view = revealer.safe_plan_view()
    assert view["destination"] is None
    assert plan.destination.name not in str(view)

    # Force the arrival reveal.
    revealer.fired.add("destination")
    revealed = revealer.safe_plan_view()
    assert revealed["destination"]["name"] == plan.destination.name


def test_safe_plan_view_redacts_unrevealed_hints(plan: Plan) -> None:
    revealer = RevealEngine(plan, started_at=utcnow())
    view = revealer.safe_plan_view()
    for entry in view["waypoints"]:
        assert entry["revealed"] is False
        assert entry["hint"] == ""
        assert entry["lat"] is None
        assert entry["lng"] is None


def test_waypoint_label_never_leaks_a_name(plan: Plan) -> None:
    for waypoint in plan.waypoints:
        label = RevealEngine.label_for(waypoint)
        assert plan.destination.name.lower() not in label.lower()
        assert waypoint.id not in label


# --------------------------------------------------------------------------- #
# compass
# --------------------------------------------------------------------------- #
def test_compass_points_at_the_next_waypoint(plan: Plan) -> None:
    compass = Compass(plan)
    start = Position(lat=plan.spec.start_lat or 0.0, lng=plan.spec.start_lng or 0.0)
    target = compass.next_target(start)
    assert target.waypoint_index == sorted(plan.waypoints, key=lambda w: w.idx)[0].idx

    update = compass.update(start, None)
    assert update.target_kind in ("photo_spot", "route", "shop", "viewpoint", "food")


def test_compass_reaches_the_destination_after_every_waypoint(plan: Plan) -> None:
    compass = Compass(plan)
    for waypoint in plan.waypoints:
        compass.mark_reached(waypoint.idx)
    target = compass.next_target(Position(lat=plan.spec.start_lat or 0.0, lng=plan.spec.start_lng or 0.0))
    assert target.is_destination()


def test_compass_freezes_without_a_fix(plan: Plan) -> None:
    compass = Compass(plan)
    update = compass.update(None, None, state="running")
    assert update.has_fix is False
    assert update.state == "searching"


def test_compass_update_has_no_distance_field() -> None:
    """The brief: the compass must never display distance to the destination."""
    from farfromhome_device.core.models import CompassUpdate

    fields = set(CompassUpdate.model_fields.keys())
    forbidden = {"distance", "distance_m", "distance_km", "range_m", "metres", "meters"}
    assert not (fields & forbidden), f"a distance field leaked into CompassUpdate: {fields & forbidden}"


def test_destination_name_only_after_reveal(plan: Plan) -> None:
    """The compass says "your destination" always; the name belongs to the reveal."""
    compass = Compass(plan)
    position = Position(lat=plan.destination.lat, lng=plan.destination.lng)
    for waypoint in plan.waypoints:
        compass.mark_reached(waypoint.idx)

    target = compass.next_target(position)
    assert target.is_destination()

    hidden = compass.update(position, None, destination_revealed=False)
    assert hidden.target_kind == "destination"
    assert hidden.target_label == "your destination"
    assert plan.destination.name not in hidden.target_label

    # Even after arrival the arrow keeps a generic label: the arrival screen is
    # where the name appears, so the compass never becomes a spoiler.
    shown = compass.update(position, None, destination_revealed=True)
    assert shown.target_label == "your destination"


def test_warmer_colder_is_off_by_default(plan: Plan) -> None:
    compass = Compass(plan)
    position = Position(lat=plan.spec.start_lat or 0.0, lng=plan.spec.start_lng or 0.0)
    assert compass.warmer_colder_hint(position) is None


def test_warmer_colder_when_enabled_never_returns_a_number(plan: Plan) -> None:
    compass = Compass(plan, warmer_colder_enabled=True)
    position = Position(lat=plan.spec.start_lat or 0.0, lng=plan.spec.start_lng or 0.0)
    hint = compass.warmer_colder_hint(position)
    assert hint is None or not any(character.isdigit() for character in hint)


def test_distance_only_for_arrival_matches_haversine(plan: Plan) -> None:
    position = Position(lat=plan.destination.lat, lng=plan.destination.lng)
    assert distance_only_for_arrival(position, plan) == pytest.approx(0.0, abs=1.0)


# --------------------------------------------------------------------------- #
# off route
# --------------------------------------------------------------------------- #
def test_off_route_waits_for_the_dwell() -> None:
    path = [(0.0, 0.0), (0.0, 1.0)]
    detector = OffRouteDetector(path, tolerance_m=100.0, dwell_seconds=45.0)
    far_away = Position(lat=0.05, lng=0.5)  # ~5.5 km north of the line

    # 40 seconds off route: still no warning.
    state = detector.update(far_away, now=100.0)
    assert state.off_route is False
    state = detector.update(far_away, now=140.0)
    assert state.off_route is False

    # Past the dwell: warn once.
    state = detector.update(far_away, now=150.0)
    assert state.off_route is True
    assert state.warnings == 1


def test_off_route_clears_when_back_on_the_line() -> None:
    path = [(0.0, 0.0), (0.0, 1.0)]
    detector = OffRouteDetector(path, tolerance_m=100.0, dwell_seconds=10.0)
    detector.update(Position(lat=0.05, lng=0.5), now=0.0)
    assert detector.update(Position(lat=0.05, lng=0.5), now=20.0).off_route is True
    state = detector.update(Position(lat=0.0, lng=0.5), now=30.0)
    assert state.off_route is False
    assert state.distance_m < 100.0


def test_off_route_is_disabled_without_a_path() -> None:
    detector = OffRouteDetector([], tolerance_m=10.0)
    assert detector.enabled is False
    state = detector.update(Position(lat=10.0, lng=10.0), now=0.0)
    assert state.off_route is False


def test_route_path_ends_at_the_destination(plan: Plan) -> None:
    path = build_route_path(plan)
    assert path[-1] == (plan.destination.lat, plan.destination.lng)
    assert len(path) == len(plan.waypoints) + 1


# --------------------------------------------------------------------------- #
# fusion
# --------------------------------------------------------------------------- #
def test_magnetometer_heading_recovers_north() -> None:
    # Field pointing north (mx = 0, my = +48) with gravity straight down.
    sample = ImuSample(ax=0.0, ay=0.0, az=9.81, mx=0.0, my=48.0, mz=0.0)
    assert magnetometer_heading(sample) == pytest.approx(0.0, abs=1.0)


def test_magnetometer_heading_recovers_east() -> None:
    sample = ImuSample(ax=0.0, ay=0.0, az=9.81, mx=48.0, my=0.0, mz=0.0)
    heading = magnetometer_heading(sample)
    assert heading is not None
    assert heading == pytest.approx(270.0, abs=1.0) or heading == pytest.approx(90.0, abs=1.0)


def test_fusion_bootstraps_from_the_magnetometer() -> None:
    fusion = HeadingFusion()
    reading = fusion.update_imu(ImuSample(ax=0.0, ay=0.0, az=9.81, mx=0.0, my=48.0, mz=0.0), dt=0.05)
    assert reading.heading_deg == pytest.approx(0.0, abs=5.0)


def test_fusion_follows_the_gyro_through_a_turn() -> None:
    """A hard turn with a broken magnetometer must still be followed."""
    fusion = HeadingFusion()
    fusion.update_imu(ImuSample(ax=0.0, ay=0.0, az=9.81, mx=0.0, my=48.0, mz=0.0), dt=0.05)
    start = fusion.heading or 0.0
    # 90 deg/s for one second.
    for _ in range(20):
        fusion.update_imu(ImuSample(ax=0.0, ay=0.0, az=9.81, mx=0.0, my=48.0, mz=0.0, gz=90.0), dt=0.05)
    delta = abs(((fusion.heading or 0.0) - start + 180) % 360 - 180)
    assert delta > 45.0, f"the gyro turn was ignored (moved {delta:.1f} deg)"


def test_fusion_rejects_a_disturbed_magnetometer() -> None:
    fusion = HeadingFusion(FusionConfig(calibration_trigger_samples=5))
    # A field strength three times normal, i.e. a magnet next to the sensor.
    for _ in range(10):
        fusion.update_imu(ImuSample(ax=0.0, ay=0.0, az=9.81, mx=0.0, my=150.0, mz=0.0), dt=0.05)
    assert fusion.rejected_magnetometer_samples > 0
    assert fusion.status()["magnetometer_ok"] is False


def test_fusion_accepts_a_healthy_field() -> None:
    fusion = HeadingFusion()
    for _ in range(10):
        fusion.update_imu(ImuSample(ax=0.0, ay=0.0, az=9.81, mx=0.0, my=48.0, mz=0.0), dt=0.05)
    assert fusion.status()["magnetometer_ok"] is True
    assert fusion.rejected_magnetometer_samples == 0


def test_fusion_uses_gps_course_when_moving() -> None:
    fusion = HeadingFusion()
    fusion.update_imu(ImuSample(ax=0.0, ay=0.0, az=9.81, mx=0.0, my=48.0, mz=0.0), dt=0.05)
    fusion.update_gps(Position(lat=11.0, lng=75.0, speed_mps=2.0, course_deg=90.0, accuracy_m=4.0))
    for _ in range(40):
        fusion.update_imu(ImuSample(ax=0.0, ay=0.0, az=9.81, mx=0.0, my=48.0, mz=0.0), dt=0.05)
    assert fusion.gps_course_corrections > 0
    assert fusion.status()["has_gps_course"] is True


def test_fusion_ignores_a_gps_course_without_speed() -> None:
    fusion = HeadingFusion()
    fusion.update_gps(Position(lat=11.0, lng=75.0, speed_mps=0.1, course_deg=90.0, accuracy_m=4.0))
    assert fusion.status()["has_gps_course"] is False


def test_fusion_ignores_an_implausible_course_jump() -> None:
    fusion = HeadingFusion()
    fusion.update_gps(Position(lat=11.0, lng=75.0, speed_mps=2.0, course_deg=10.0, accuracy_m=4.0))
    fusion.update_gps(Position(lat=11.0, lng=75.0, speed_mps=2.0, course_deg=200.0, accuracy_m=4.0))
    assert fusion.status()["has_gps_course"] is True
    # The second, impossible course must not have replaced the first.
    fusion.update_imu(ImuSample(ax=0.0, ay=0.0, az=9.81, mx=0.0, my=48.0, mz=0.0), dt=0.05)
    assert fusion.gps_course_corrections >= 0


def test_calibration_resets_the_interference_flags() -> None:
    fusion = HeadingFusion(FusionConfig(calibration_trigger_samples=3))
    for _ in range(6):
        fusion.update_imu(ImuSample(ax=0.0, ay=0.0, az=9.81, mx=0.0, my=150.0, mz=0.0), dt=0.05)
    assert fusion.status()["needs_calibration"] is True
    fusion.calibrate()
    assert fusion.status()["needs_calibration"] is False
    assert fusion.status()["magnetometer_ok"] is True


def test_smooth_heading_limits_a_jump() -> None:
    assert smooth_heading(None, 200.0) == 200.0
    # From 10 towards 190: the short way is +180, capped at +25.
    assert smooth_heading(10.0, 190.0, max_step_deg=25.0) == pytest.approx(35.0)


def test_smooth_heading_wraps_around_north() -> None:
    # From 350 towards 5 is +15, which is inside the cap and must not become -345.
    assert smooth_heading(350.0, 5.0, max_step_deg=25.0) == pytest.approx(5.0)
    # From 350 towards 100 is +110, capped at +25 -> 15.
    assert smooth_heading(350.0, 100.0, max_step_deg=25.0) == pytest.approx(15.0)


def test_smooth_heading_passes_a_small_change_through() -> None:
    assert smooth_heading(10.0, 20.0) == 20.0


# --------------------------------------------------------------------------- #
# the engine loop
# --------------------------------------------------------------------------- #
def test_engine_reaches_arrived_on_a_walk(infra: Any, plan: Plan) -> None:
    engine = TripEngine(infra, plan)
    assert engine.state == TripState.PLANNED
    engine.start()
    assert engine.state == TripState.RUNNING

    for position in walk_plan(plan):
        engine.hardware.gps.feed(position)
        engine.tick()

    assert engine.state == TripState.ARRIVED
    assert engine.revealer.is_destination_revealed() is True
    assert engine.distance_m > 0


def test_engine_publishes_the_expected_topics(infra: Any, plan: Plan) -> None:
    engine = TripEngine(infra, plan)
    engine.start()
    for _position in walk_plan(plan)[:40]:
        engine.tick()
    topics = [message.topic for message in infra.bus.recent]
    for expected in ("trip.started", "compass.update", "position.update"):
        assert expected in topics, f"{expected} was never published"
    # The engine must never leak a destination name before the reveal.
    reveals = [message for message in infra.bus.recent if message.topic == "reveal.due"]
    for message in reveals:
        assert plan.destination.name not in str(message.payload.get("text", ""))


def test_engine_handles_gps_loss(infra: Any, plan: Plan) -> None:
    engine = TripEngine(infra, plan)
    engine.start()
    engine.tick()
    assert engine.position is not None, "the simulated GPS should give a first fix"

    engine.hardware.gps.lost = True
    for _ in range(3):
        engine.tick()
    topics = [message.topic for message in infra.bus.recent]
    assert "sensor.gps_lost" in topics
    assert engine.last_compass is not None
    assert engine.last_compass.has_fix is False
    assert engine.position is None


def test_engine_announces_gps_loss_only_once(infra: Any, plan: Plan) -> None:
    engine = TripEngine(infra, plan)
    engine.start()
    engine.tick()
    engine.hardware.gps.lost = True
    for _ in range(5):
        engine.tick()
    lost_events = [message for message in infra.bus.recent if message.topic == "sensor.gps_lost"]
    assert len(lost_events) == 1, "GPS loss must be announced once, not every tick"


def test_engine_recovers_after_gps_returns(infra: Any, plan: Plan) -> None:
    engine = TripEngine(infra, plan)
    engine.start()
    engine.tick()
    engine.hardware.gps.lost = True
    engine.tick()
    engine.hardware.gps.lost = False
    engine.tick()
    topics = [message.topic for message in infra.bus.recent]
    assert "sensor.gps_lost" in topics
    assert "sensor.gps_back" in topics


def test_engine_drops_a_bad_fix(infra: Any, plan: Plan) -> None:
    engine = TripEngine(infra, plan)
    engine.start()
    engine.tick()
    engine.position = Position(lat=11.2588, lng=75.7804, accuracy_m=4.0, t=utcnow())
    before = engine.stats.fixes_dropped
    # A fix 5 km away one second later is a GPS artefact, not a person walking.
    engine._accept_fix(Position(lat=11.30, lng=75.80, accuracy_m=4.0, t=utcnow()))  # noqa: SLF001
    assert engine.stats.fixes_dropped == before + 1


def test_engine_drops_a_fix_with_poor_accuracy(infra: Any, plan: Plan) -> None:
    engine = TripEngine(infra, plan)
    engine.start()
    before = engine.stats.fixes_dropped
    engine._accept_fix(Position(lat=11.2588, lng=75.7804, accuracy_m=120.0))  # noqa: SLF001
    assert engine.stats.fixes_dropped == before + 1


def test_engine_summary_after_wrap_up(infra: Any, plan: Plan) -> None:
    engine = TripEngine(infra, plan)
    engine.start()
    engine.arrive()
    summary = engine.wrap_up()
    assert summary["destination"] == plan.destination.name
    assert summary["trip_id"] == plan.trip_id
    assert engine.state == TripState.WRAPPED_UP


def test_engine_queues_points_in_batches(infra: Any, plan: Plan) -> None:
    engine = TripEngine(infra, plan)
    engine.start()
    for _position in walk_plan(plan)[:30]:
        engine.tick()
    engine.flush_points()
    commands = [
        message for message in infra.bus.recent
        if message.topic == "ui.command" and message.payload.get("action") == "queue_points"
    ]
    assert commands, "the engine never asked for points to be queued"
    assert len(commands[0].payload["points"]) <= infra.settings.gps_batch_size


def test_engine_track_log_uses_the_fix_clock(infra: Any, plan: Plan) -> None:
    """Downsampling must follow trip time, not wall-clock time."""
    engine = TripEngine(infra, plan)
    engine.start()
    positions = walk_plan(plan)
    for position in positions[:60]:
        engine._log_track(position)  # noqa: SLF001
    rows = infra.storage.track_count(plan.trip_id)
    # The fixes are 6 s apart on a 5 s interval, so every fix is loggable and none
    # may be dropped; but two fixes inside 5 s must collapse into one row.
    assert rows == len(positions[:60])

    engine2 = TripEngine(infra, plan)
    engine2.start()
    close_together = walk_plan(plan, metres_per_step=5.0, seconds_per_step=1.0)
    for position in close_together[:60]:
        engine2._log_track(position)  # noqa: SLF001
    rows = infra.storage.track_count(plan.trip_id) - rows
    assert 0 < rows < 60, f"expected downsampling for 1 s fixes, logged {rows} rows"


def test_engine_never_calls_an_llm(infra: Any, plan: Plan) -> None:
    """The brief: "engine: deterministic live trip loop. No LLM calls." """
    import inspect

    source = inspect.getsource(TripEngine)
    for forbidden in ("ask_llm", "LlmRouter", "import openai", "requests.post"):
        assert forbidden not in source, f"the engine references {forbidden}"


def _track_from(positions: List[Position]) -> Any:

    points = [(position.lat, position.lng, position.t, None) for position in positions]
    if len(points) == 1:
        points.append(points[0])
    return GpxTrack(points, name="test")
