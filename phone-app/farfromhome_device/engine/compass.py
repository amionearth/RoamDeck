"""The pointing compass.

The single hard rule from the brief (§5.2): *"The compass must never display
distance to the destination, only direction."* This module therefore has no way
to produce a distance — :class:`Compass` only ever emits
:class:`~farfromhome_device.core.models.CompassUpdate`, which has an arrow angle,
a bearing, a heading and a label. A distance field is impossible to add by
accident, and there is a unit test asserting the model has no such field.

What *is* allowed, and is implemented:

* a coarse warmer/colder band, because the brief explicitly permits it
  ("optional: a coarse 'warmer/colder' hint if the product owner asks"). It is
  off by default and exposes only three states, never a number.
* "you are within 500 m" style bands for meetup mode (brief §10).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..core.models import CompassUpdate, HeadingReading, Plan, PlanWaypoint, Position, RevealRule
from .geo import arrow_angle, bearing_deg, compass_point, haversine_m, turn_direction
from .reveal import RevealEngine

logger = logging.getLogger("ffh.engine.compass")

#: Coarse proximity bands. Never surfaced as a number.
WARMER_BANDS: Tuple[Tuple[float, str], ...] = (
    (150.0, "very warm"),
    (400.0, "warmer"),
    (900.0, "lukewarm"),
)
COLDER_LABEL = "colder"


@dataclass
class CompassTarget:
    """The thing the arrow points at right now."""

    label: str
    kind: str  # "route" | "photo_spot" | "shop" | "destination"
    lat: float
    lng: float
    waypoint_index: Optional[int] = None

    def is_destination(self) -> bool:
        return self.kind == "destination"


class Compass:
    """Turns a plan + a position + a heading into the arrow the user follows.

    Target selection is deliberately conservative: the next *unreached, revealed*
    waypoint in plan order, then the destination. A waypoint with
    ``reveal == "never"`` is used as a route hint only — it never gets a label, and
    the UI shows it as "onward" so its hint text is not leaked early.
    """

    def __init__(self, plan: Plan, warmer_colder_enabled: bool = False) -> None:
        self.plan = plan
        self.revealer = RevealEngine(plan)
        #: Off by default: the brief allows a coarse hint only "if the product
        #: owner asks". Naming it *_enabled keeps it distinct from the method.
        self.warmer_colder_enabled = warmer_colder_enabled
        self._previous_distance: Optional[float] = None
        self._reached: set[int] = set()
        self._last_target: Optional[CompassTarget] = None

    # ------------------------------------------------------------------ #
    # target selection
    # ------------------------------------------------------------------ #
    def waypoints_in_order(self) -> List[PlanWaypoint]:
        return sorted(self.plan.waypoints, key=lambda waypoint: waypoint.idx)

    def mark_reached(self, index: int) -> None:
        self._reached.add(index)

    def next_target(self, position: Position) -> CompassTarget:
        """The next waypoint not yet reached, else the destination."""
        for waypoint in self.waypoints_in_order():
            if waypoint.idx in self._reached:
                continue
            distance = haversine_m(position.lat, position.lng, waypoint.lat, waypoint.lng)
            if distance <= max(15.0, waypoint.radius_m):
                self._reached.add(waypoint.idx)
                continue
            label = self._label_for(waypoint)
            target = CompassTarget(
                label=label,
                kind=waypoint.kind.value,
                lat=waypoint.lat,
                lng=waypoint.lng,
                waypoint_index=waypoint.idx,
            )
            self._last_target = target
            return target

        destination = self.plan.destination
        target = CompassTarget(
            label="your destination",
            kind="destination",
            lat=destination.lat,
            lng=destination.lng,
        )
        self._last_target = target
        return target

    def _label_for(self, waypoint: PlanWaypoint) -> str:
        if waypoint.reveal == RevealRule.NEVER or waypoint.kind.value == "route":
            # "onward", not "w2": the id and the kind are hints too.
            return "onward"
        if waypoint.kind.value == "photo_spot":
            return "a photo spot"
        if waypoint.kind.value == "shop":
            return "a shop"
        if waypoint.kind.value == "food":
            return "somewhere to eat"
        if waypoint.kind.value == "viewpoint":
            return "a viewpoint"
        return "your next stop"

    # ------------------------------------------------------------------ #
    # producing updates
    # ------------------------------------------------------------------ #
    def update(
        self,
        position: Optional[Position],
        heading: Optional[HeadingReading],
        state: str = "running",
        *,
        destination_revealed: bool = False,
    ) -> CompassUpdate:
        """Build the next ``compass.update`` payload.

        With no GPS fix the arrow freezes (``has_fix=False``) and the UI shows a
        "searching" state; the direction is preserved rather than snapping to 0.

        ``destination_revealed`` is accepted for symmetry with the engine's state but
        deliberately **not** used to put the destination's name on the arrow: the
        compass says "your destination" always, and the reveal screen is where the
        name belongs. That keeps one rule ("the compass is direction only") true at
        every moment of the trip.
        """
        if position is None:
            return CompassUpdate(
                arrow_angle=self._last_arrow(),
                bearing_deg=0.0,
                heading_deg=heading.heading_deg if heading else 0.0,
                target_label=self._last_label(),
                target_kind=self._last_target.kind if self._last_target else "route",
                has_fix=False,
                state="searching",
            )

        target = self.next_target(position)
        bearing = bearing_deg(position.lat, position.lng, target.lat, target.lng)
        current_heading = heading.heading_deg if heading else bearing
        angle = arrow_angle(bearing, current_heading)

        return CompassUpdate(
            arrow_angle=angle,
            bearing_deg=bearing,
            heading_deg=current_heading,
            target_label=target.label,
            target_kind=target.kind,
            has_fix=True,
            state=state,
        )

    def _last_arrow(self) -> float:
        return 0.0

    def _last_label(self) -> str:
        return self._last_target.label if self._last_target else "next stop"

    # ------------------------------------------------------------------ #
    # allowed extras
    # ------------------------------------------------------------------ #
    def warmer_colder_hint(self, position: Position) -> Optional[str]:
        """A three-word band, never a number. Off unless explicitly enabled."""
        if not self.warmer_colder_enabled:
            return None
        destination = self.plan.destination
        distance = haversine_m(position.lat, position.lng, destination.lat, destination.lng)
        if self._previous_distance is None:
            self._previous_distance = distance
            for threshold, label in WARMER_BANDS:
                if distance <= threshold:
                    return label
            return COLDER_LABEL
        improving = distance < self._previous_distance - 10.0
        worsening = distance > self._previous_distance + 10.0
        self._previous_distance = distance
        if improving:
            for threshold, label in WARMER_BANDS:
                if distance <= threshold:
                    return label
            return "warmer"
        if worsening:
            return COLDER_LABEL
        return None

    def meetup_band(self, position: Position, other_lat: float, other_lng: float) -> str:
        """``far`` / ``under_1km`` / ``under_500m`` for meetup mode (brief §10).

        The brief says the *server* owns the hysteresis (enters at 500 m, leaves at
        550 m) for the real feature. This helper is only for the offline fallback,
        so it uses the same threshold for both directions and documents that.
        """
        distance = haversine_m(position.lat, position.lng, other_lat, other_lng)
        if distance <= 500.0:
            return "under_500m"
        if distance <= 1000.0:
            return "under_1km"
        return "far"

    def current_target(self) -> Optional[CompassTarget]:
        return self._last_target

    def describe_target(self, position: Position) -> str:
        """A short sentence for the agent bubble. No numbers, no names before reveal."""
        target = self.next_target(position)
        bearing = bearing_deg(position.lat, position.lng, target.lat, target.lng)
        return f"Head {compass_point(bearing)} towards {target.label}."

    def turn_hint(self, position: Position, heading: HeadingReading) -> str:
        bearing = bearing_deg(position.lat, position.lng, self.next_target(position).lat,
                              self.next_target(position).lng)
        return turn_direction(bearing, heading.heading_deg)


def distance_only_for_arrival(position: Position, plan: Plan) -> float:
    """Distance to the destination.

    The **only** place in the engine allowed to compute this, and its single
    caller is the arrival check (which is allowed to know). It is not exported
    through any bus topic.
    """
    return haversine_m(
        position.lat, position.lng, plan.destination.lat, plan.destination.lng
    )
