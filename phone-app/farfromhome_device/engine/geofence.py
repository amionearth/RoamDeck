"""Geofences and the off-route detector.

Two jobs that both use distance, and neither of which may leak the destination:

**Geofences** — circle around a waypoint or the destination. Emits
``geofence.entered`` when the user crosses in, with hysteresis on the way out so a
user standing on the boundary does not produce a stream of enter/exit events.

**Off-route** — brief §5.2 step 5: *"the user chooses their own way, so only warn
when they move away beyond a tolerance."* So this is a **distance-from-path**
check with a *dwell* requirement: we only raise ``off_route`` after the user has
been beyond the tolerance continuously for N seconds (default 45, configurable).
Walking a side street for 20 seconds to look at something must never nag them.

The zone that matters for the product is the destination, because arriving is what
reveals the secret. Everything else is encouragement.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.models import Plan, Position
from .geo import distance_to_path_m, haversine_m

logger = logging.getLogger("ffh.engine.geofence")


@dataclass
class Geofence:
    """A circle to watch."""

    key: str
    lat: float
    lng: float
    radius_m: float
    kind: str = "waypoint"  # "waypoint" | "destination" | "photo_spot" | "meetup"
    label: str = ""
    waypoint_index: Optional[int] = None

    def contains(self, position: Position) -> bool:
        return haversine_m(position.lat, position.lng, self.lat, self.lng) <= self.radius_m

    def distance_from(self, position: Position) -> float:
        return haversine_m(position.lat, position.lng, self.lat, self.lng)


@dataclass
class OffRouteState:
    """How far off the planned route the user currently is."""

    off_route: bool = False
    distance_m: float = 0.0
    seconds_off: float = 0.0
    warned_at: Optional[float] = None
    warnings: int = 0

    def to_payload(self) -> Dict[str, Any]:
        return {
            "off_route": self.off_route,
            "distance_m": round(self.distance_m, 1),
            "seconds_off": round(self.seconds_off, 1),
            "warnings": self.warnings,
        }


class GeofenceEngine:
    """Watches waypoint and destination geofences with hysteresis."""

    #: Exit radius as a multiple of the enter radius. Prevents flapping.
    HYSTERESIS = 1.25

    def __init__(self, plan: Plan) -> None:
        self.plan = plan
        self.zones: List[Geofence] = []
        self._inside: Dict[str, bool] = {}
        self._entered: set[str] = set()
        self.build_zones()

    def build_zones(self) -> None:
        zones: List[Geofence] = []
        for waypoint in sorted(self.plan.waypoints, key=lambda item: item.idx):
            zones.append(
                Geofence(
                    key=f"wp:{waypoint.idx}",
                    lat=waypoint.lat,
                    lng=waypoint.lng,
                    radius_m=waypoint.radius_m,
                    kind=waypoint.kind.value,
                    label=f"waypoint {waypoint.idx}",
                    waypoint_index=waypoint.idx,
                )
            )
        destination = self.plan.destination
        zones.append(
            Geofence(
                key="destination",
                lat=destination.lat,
                lng=destination.lng,
                radius_m=destination.radius_m,
                kind="destination",
                label="destination",
            )
        )
        self.zones = zones

    def add_zone(self, zone: Geofence) -> None:
        self.zones.append(zone)

    # ------------------------------------------------------------------ #
    def update(self, position: Position) -> List[Geofence]:
        """Return zones entered since the last call, in plan order."""
        entered: List[Geofence] = []
        for zone in self.zones:
            distance = zone.distance_from(position)
            radius = zone.radius_m
            was_inside = self._inside.get(zone.key, False)
            if was_inside:
                # Stay "inside" until we are clearly outside.
                inside = distance <= radius * self.HYSTERESIS
            else:
                inside = distance <= radius
            self._inside[zone.key] = inside

            if inside and not was_inside:
                self._entered.add(zone.key)
                entered.append(zone)
                logger.info("geofence entered: %s (%.1f m)", zone.key, distance)
        return entered

    def has_entered(self, key: str) -> bool:
        return key in self._entered

    def inside_keys(self) -> List[str]:
        return [key for key, inside in self._inside.items() if inside]

    def status(self) -> Dict[str, Any]:
        return {
            "zones": len(self.zones),
            "inside": self.inside_keys(),
            "entered": sorted(self._entered),
            "destination_entered": "destination" in self._entered,
        }


class OffRouteDetector:
    """Distance-from-route with a dwell timer.

    ``path`` is the polyline the engine assembled from the plan's waypoints plus
    the destination. If the plan has no route at all, the detector reports nothing
    — a trip with no planned path cannot be off it.
    """

    def __init__(
        self,
        path: Sequence[Tuple[float, float]],
        tolerance_m: float = 120.0,
        dwell_seconds: float = 45.0,
        re_warn_seconds: float = 120.0,
    ) -> None:
        self.path = list(path)
        self.tolerance_m = tolerance_m
        self.dwell_seconds = dwell_seconds
        self.re_warn_seconds = re_warn_seconds
        self.state = OffRouteState()
        self._off_since: Optional[float] = None
        self._last_warn: Optional[float] = None

    @property
    def enabled(self) -> bool:
        return len(self.path) >= 2

    def update(self, position: Position, now: Optional[float] = None) -> OffRouteState:
        """Update and return the state. ``off_route`` flips True only after dwell."""
        moment = time.monotonic() if now is None else now
        if not self.enabled:
            return self.state

        distance = distance_to_path_m(position.lat, position.lng, self.path)
        self.state.distance_m = distance

        if distance > self.tolerance_m:
            if self._off_since is None:
                self._off_since = moment
            self.state.seconds_off = moment - self._off_since
            if self.state.seconds_off >= self.dwell_seconds:
                if not self.state.off_route:
                    self.state.off_route = True
                    self.state.warnings += 1
                    self._last_warn = moment
                    logger.info(
                        "off route: %.0f m from path for %.0f s",
                        distance,
                        self.state.seconds_off,
                    )
                elif self._last_warn is not None and moment - self._last_warn >= self.re_warn_seconds:
                    # A steady, gentle reminder rather than a nag.
                    self.state.warnings += 1
                    self._last_warn = moment
        else:
            self._off_since = None
            self.state.seconds_off = 0.0
            self.state.off_route = False

        return self.state

    def back_on_route(self) -> bool:
        return self.enabled and not self.state.off_route and self._off_since is None

    def status(self) -> Dict[str, Any]:
        payload = self.state.to_payload()
        payload.update({"enabled": self.enabled, "tolerance_m": self.tolerance_m,
                        "dwell_seconds": self.dwell_seconds})
        return payload


def build_route_path(plan: Plan) -> List[Tuple[float, float]]:
    """The planned polyline: waypoints in order, then the destination.

    Waypoints whose reveal is ``never`` are route hints, so they belong on the
    path. The destination is always the last point — it is the shape of the route,
    not a label, so this leaks nothing.
    """
    path = [(waypoint.lat, waypoint.lng) for waypoint in sorted(plan.waypoints, key=lambda item: item.idx)]
    destination = plan.destination
    path.append((destination.lat, destination.lng))
    return path


def nearby_points(
    position: Position,
    plan: Plan,
    radius_m: float = 120.0,
    revealed_keys: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """Waypoints the user is near, for the "something is close" alert.

    The hint is **only** included once its reveal has fired, so this function
    cannot be the hole through which a hint leaks early (brief §1 "Mystery first").
    Callers that have no reveal state pass ``revealed_keys`` as ``None`` and get
    counts and distances with no text at all.
    """
    revealed = revealed_keys or set()
    found = []
    for waypoint in sorted(plan.waypoints, key=lambda item: item.idx):
        distance = haversine_m(position.lat, position.lng, waypoint.lat, waypoint.lng)
        if distance <= max(radius_m, waypoint.radius_m):
            key = f"wp:{waypoint.idx}"
            found.append(
                {
                    "idx": waypoint.idx,
                    "kind": waypoint.kind.value,
                    "distance_m": round(distance, 1),
                    "hint": waypoint.hint if key in revealed else "",
                    "revealed": key in revealed,
                }
            )
    return found
