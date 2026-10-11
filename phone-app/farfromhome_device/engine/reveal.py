"""Reveal rules: ``never``, ``on_proximity``, ``on_arrival``, ``at_time``.

"Mystery first" (brief §1) is a *rule*, not a UI habit, so it lives in code that
can be tested. :class:`RevealEngine` decides what may be shown, and everything the
UI displays passes through it (or through a `Plan.public_summary()`, which is
written to be leak-free by construction).

Reveal semantics as implemented:

======================  ==========================================================
rule                    fires when
======================  ==========================================================
``never``               never. The waypoint is a route hint only.
``on_proximity``        the user is inside ``radius_m`` of the waypoint.
``on_arrival``          the user is inside the destination radius.
``at_time``             ``trip_started_at + at_offset_minutes`` has passed; the
                        offset is taken from the matching plan schedule entry, or
                        from ``default_at_minutes``.
======================  ==========================================================

Every reveal fires **once**: the engine keeps a set of fired keys, so a GPS
wobble in and out of a radius cannot re-trigger an animation or re-send an
``arrive`` to the server (brief 03 §6.5 "Send it once").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.models import (
    Plan,
    PlanWaypoint,
    Position,
    RevealRule,
    iso_z,
    utcnow,
)
from .geo import haversine_m

logger = logging.getLogger("ffh.engine.reveal")


@dataclass
class Reveal:
    """One thing that became visible."""

    kind: str  # "waypoint" | "destination" | "hint"
    key: str
    label: str
    text: str = ""
    waypoint_index: Optional[int] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    at: str = field(default_factory=iso_z)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "label": self.label,
            "text": self.text,
            "waypoint_index": self.waypoint_index,
            "lat": self.lat,
            "lng": self.lng,
            "at": self.at,
        }


class RevealEngine:
    """Tracks which reveals have fired for one trip."""

    def __init__(
        self,
        plan: Plan,
        started_at: Optional[datetime] = None,
        default_at_minutes: float = 30.0,
    ) -> None:
        self.plan = plan
        self.started_at = started_at
        self.default_at_minutes = default_at_minutes
        self.fired: set[str] = set()
        self.history: List[Reveal] = []
        #: Waypoints the *user* has been inside the radius of, whether or not the
        #: rule allowed a reveal. The engine still reports "reached" for scoring.
        self.reached_waypoints: set[int] = set()

    # ------------------------------------------------------------------ #
    # time offsets
    # ------------------------------------------------------------------ #
    def set_started_at(self, moment: Optional[datetime]) -> None:
        self.started_at = moment

    def _offset_for(self, waypoint: Optional[PlanWaypoint]) -> float:
        """Minutes after trip start at which an ``at_time`` reveal is due.

        The plan's schedule carries explicit hints in minutes; a waypoint marked
        ``at_time`` does not, so we space waypoints evenly across the trip's
        estimated duration and fall back to ``default_at_minutes``. Spacing them
        evenly is the simplest rule that produces a sensible drip of hints, and it
        is recorded in DECISIONS.md D-09.
        """
        if waypoint is None or not self.plan.waypoints:
            return self.default_at_minutes
        total = max(1, len(self.plan.waypoints))
        budget = max(self.default_at_minutes, self.plan.spec.max_minutes * 0.8)
        position = max(1, waypoint.idx + 1)
        return min(budget, budget * position / total)

    def _elapsed_minutes(self, now: Optional[datetime] = None) -> Optional[float]:
        if self.started_at is None:
            return None
        moment = now or utcnow()
        return (moment - self.started_at).total_seconds() / 60.0

    # ------------------------------------------------------------------ #
    # evaluation
    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        position: Optional[Position],
        now: Optional[datetime] = None,
    ) -> List[Reveal]:
        """Return every reveal that should fire for this position, in plan order."""
        new_reveals: List[Reveal] = []

        for waypoint in sorted(self.plan.waypoints, key=lambda item: item.idx):
            if position is not None:
                distance = haversine_m(position.lat, position.lng, waypoint.lat, waypoint.lng)
                if distance <= max(5.0, waypoint.radius_m):
                    self.reached_waypoints.add(waypoint.idx)

        for waypoint in sorted(self.plan.waypoints, key=lambda item: item.idx):
            key = f"wp:{waypoint.idx}"
            if key in self.fired:
                continue
            if waypoint.reveal == RevealRule.NEVER:
                continue

            if waypoint.reveal == RevealRule.ON_PROXIMITY:
                if waypoint.idx in self.reached_waypoints:
                    new_reveals.append(self._reveal_waypoint(waypoint))

            elif waypoint.reveal == RevealRule.AT_TIME:
                if self._at_time_reached(waypoint, now):
                    new_reveals.append(self._reveal_waypoint(waypoint))

            elif waypoint.reveal == RevealRule.ON_ARRIVAL:
                # A waypoint explicitly marked on_arrival reveals with the arrival.
                continue

        # Destination
        destination_key = "destination"
        if (
            destination_key not in self.fired
            and self.plan.destination.reveal == RevealRule.ON_ARRIVAL
            and position is not None
            and haversine_m(
                position.lat,
                position.lng,
                self.plan.destination.lat,
                self.plan.destination.lng,
            )
            <= self.plan.destination.radius_m
        ):
            new_reveals.append(self._reveal_destination())

        elif (
            destination_key not in self.fired
            and self.plan.destination.reveal == RevealRule.AT_TIME
            and self._at_time_reached(None, now)
        ):
            new_reveals.append(self._reveal_destination())

        for reveal in new_reveals:
            self.fired.add(reveal.key)
            self.history.append(reveal)
            logger.info("reveal fired: %s (%s)", reveal.key, reveal.kind)
        return new_reveals

    def _at_time_reached(self, waypoint: Optional[PlanWaypoint], now: Optional[datetime]) -> bool:
        elapsed = self._elapsed_minutes(now)
        if elapsed is None:
            return False
        return elapsed >= self._offset_for(waypoint)

    def _reveal_waypoint(self, waypoint: PlanWaypoint) -> Reveal:
        return Reveal(
            kind="waypoint",
            key=f"wp:{waypoint.idx}",
            label=self.label_for(waypoint),
            text=waypoint.hint,
            waypoint_index=waypoint.idx,
            lat=waypoint.lat,
            lng=waypoint.lng,
        )

    def _reveal_destination(self) -> Reveal:
        destination = self.plan.destination
        return Reveal(
            kind="destination",
            key="destination",
            label=destination.name,
            text=self.plan.title,
            lat=destination.lat,
            lng=destination.lng,
        )

    # ------------------------------------------------------------------ #
    # what the UI is allowed to show
    # ------------------------------------------------------------------ #
    @staticmethod
    def label_for(waypoint: PlanWaypoint) -> str:
        """A safe label. Never the waypoint id, never an exact place name."""
        if waypoint.reveal == RevealRule.NEVER:
            return "onward"
        kind_labels = {
            "route": "onward",
            "photo_spot": "a photo spot",
            "shop": "a shop",
            "food": "somewhere to eat",
            "viewpoint": "a viewpoint",
            "rest": "a place to rest",
        }
        return kind_labels.get(waypoint.kind.value, "your next stop")

    def is_destination_revealed(self) -> bool:
        return "destination" in self.fired

    def visible_waypoints(self) -> List[PlanWaypoint]:
        """Waypoints whose hints may currently be shown."""
        out = []
        for waypoint in sorted(self.plan.waypoints, key=lambda item: item.idx):
            key = f"wp:{waypoint.idx}"
            if key in self.fired:
                out.append(waypoint)
        return out

    def pending_hints(self) -> List[str]:
        return [waypoint.hint for waypoint in self.visible_waypoints() if waypoint.hint]

    def safe_plan_view(self) -> Dict[str, Any]:
        """Everything the UI is allowed to draw. Coordinates are included **only**
        for revealed waypoints; the destination stays redacted until arrival."""
        view = self.plan.public_summary()
        view["waypoints"] = [
            {
                "idx": waypoint.idx,
                "label": self.label_for(waypoint),
                "hint": waypoint.hint if f"wp:{waypoint.idx}" in self.fired else "",
                "revealed": f"wp:{waypoint.idx}" in self.fired,
                "lat": waypoint.lat if f"wp:{waypoint.idx}" in self.fired else None,
                "lng": waypoint.lng if f"wp:{waypoint.idx}" in self.fired else None,
            }
            for waypoint in sorted(self.plan.waypoints, key=lambda item: item.idx)
        ]
        if self.is_destination_revealed():
            view["destination"] = {
                "name": self.plan.destination.name,
                "lat": self.plan.destination.lat,
                "lng": self.plan.destination.lng,
                "category": self.plan.destination.category.value,
            }
        else:
            view["destination"] = None
        return view
