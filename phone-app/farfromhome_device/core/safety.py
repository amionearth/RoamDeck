"""Safety rules, in code. Brief §11 is a requirement, not a suggestion.

Four checks:

1. **Public place only** — the destination must be a place the map data says is
   public (park, beach, cafe, market, temple, viewpoint...). Anything that looks
   like private property, water, a construction site or a restricted area is
   rejected in :func:`is_public_place`.
2. **Daylight** — warn when arrival is projected after dark. The projection is
   naive (straight line at the planned speed) because that is honest: we do not
   know the user's real pace before they start.
3. **Distance from the emergency contact** — if a contact location is configured,
   flag destinations beyond a configured radius (default 15 km) so the user sees a
   warning before they set off.
4. **Emergency contact recorded** — the plan must carry the flag the brief's
   example `Plan` shows, and the UI refuses to start a stranger-facing trip
   without one.

Everything returns a :class:`SafetyReport`; nothing raises, so a warning can
always be shown rather than an exception swallowed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..core.models import Plan, PlanSafety, TripSpec, iso_z, utcnow
from ..engine.geo import daylight_window, haversine_m

logger = logging.getLogger("ffh.safety")

#: Categories OSM/our provider marks as public and safe to send a stranger to.
PUBLIC_CATEGORIES = {"beach", "park", "cafe", "market", "temple", "heritage", "viewpoint", "other"}

#: Substrings that disqualify a place outright. Deliberately blunt: a false
#: positive costs nothing but a different destination, a false negative is a
#: safety incident.
DISALLOWED_TOKENS = (
    "private", "residence", "residential", "apartment", "flat", "villa",
    "construction", "quarry", "landfill", "sewage", "dump", "military",
    "restricted", "no access", "noentry", "trespass", "danger", "cliff edge",
    "swimming", "river", "lake", "reservoir", "canal", "waterfall",
    "power station", "substation", "factory", "warehouse", "prison", "police",
    "hospital", "clinic", "school", "kindergarten", "cemetery", "crematorium",
    "railway", "track", "runway", "airfield",
)

#: Tags that positively confirm public access in OSM data.
PUBLIC_ACCESS_VALUES = {"yes", "public", "permissive", "designated"}

DEFAULT_CONTACT_RADIUS_KM = 15.0
DEFAULT_SUNSET_MARGIN_MINUTES = 45.0


@dataclass
class SafetyIssue:
    code: str
    message: str
    severity: str = "warning"  # "warning" | "error"
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "severity": self.severity, "detail": self.detail}


@dataclass
class SafetyReport:
    ok: bool = True
    issues: List[SafetyIssue] = field(default_factory=list)
    public_place: bool = True
    arrival_before_sunset: bool = True
    emergency_contact_required: bool = True
    contact_distance_km: Optional[float] = None
    sunrise_hour: Optional[float] = None
    sunset_hour: Optional[float] = None

    def add(self, issue: SafetyIssue) -> None:
        self.issues.append(issue)
        if issue.severity == "error":
            self.ok = False

    @property
    def warnings(self) -> List[SafetyIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def errors(self) -> List[SafetyIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    def summary_line(self) -> str:
        if self.ok and not self.issues:
            return "Safety checks passed."
        parts = [issue.message for issue in self.issues[:3]]
        return " ".join(parts)

    def to_plan_safety(self, contact_required: bool = True) -> PlanSafety:
        return PlanSafety(
            public_place=self.public_place,
            arrival_before_sunset=self.arrival_before_sunset,
            emergency_contact_required=contact_required,
            daylight_warning="Arrival is projected after dark; the trip may end in low light."
            if not self.arrival_before_sunset
            else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "public_place": self.public_place,
            "arrival_before_sunset": self.arrival_before_sunset,
            "emergency_contact_required": self.emergency_contact_required,
            "contact_distance_km": self.contact_distance_km,
            "sunrise_hour": self.sunrise_hour,
            "sunset_hour": self.sunset_hour,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def is_public_place(category: str, tags: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Decide whether a destination is somewhere it is safe to send people.

    Returns ``(allowed, reason)``. Unknown categories are allowed as ``other``
    because the planner already maps them, but *known bad* signals always win.
    """
    tags = tags or {}
    haystack_parts = [str(category).lower()]
    for key in ("name", "description", "kind", "type", "landuse", "amenity", "leisure", "tourism"):
        if key in tags and tags[key]:
            haystack_parts.append(str(tags[key]).lower())
    haystack = " ".join(haystack_parts)

    for token in DISALLOWED_TOKENS:
        if token in haystack:
            return False, f"Place looks like '{token}', which is not a safe public destination."

    if category.lower() not in PUBLIC_CATEGORIES:
        return False, f"Unknown destination category '{category}'."

    access = str(tags.get("access", "")).lower()
    if access and access not in PUBLIC_ACCESS_VALUES:
        return False, f"Map data marks access as '{access}'."

    return True, "Public place."


def daylight_report(
    lat: float,
    lng: float,
    eta: Optional[datetime] = None,
    margin_minutes: float = DEFAULT_SUNSET_MARGIN_MINUTES,
) -> Tuple[bool, Optional[float], Optional[float]]:
    """``(arrives_before_dark, sunrise_hour, sunset_hour)``."""
    sunrise, sunset = daylight_window(lat, lng, eta.date() if eta else None)
    if eta is None:
        return True, sunrise, sunset
    local_hour = eta.hour + eta.minute / 60.0 + eta.second / 3600.0
    safe = sunrise + margin_minutes / 60.0 <= local_hour <= sunset - margin_minutes / 60.0
    return safe, sunrise, sunset


def projected_arrival(spec: TripSpec, start: Optional[datetime] = None) -> datetime:
    moment = start or datetime.now(timezone.utc)
    return moment + timedelta(minutes=spec.max_minutes)


def check_trip_safety(
    spec: TripSpec,
    destination_lat: float,
    destination_lng: float,
    destination_category: str,
    destination_tags: Optional[Dict[str, Any]] = None,
    contact_lat: Optional[float] = None,
    contact_lng: Optional[float] = None,
    contact_name: str = "",
    contact_radius_km: float = DEFAULT_CONTACT_RADIUS_KM,
    eta: Optional[datetime] = None,
    emergency_contact_required: bool = True,
) -> SafetyReport:
    """Run every safety rule against a candidate destination."""
    report = SafetyReport(emergency_contact_required=emergency_contact_required)

    allowed, reason = is_public_place(destination_category, destination_tags)
    report.public_place = allowed
    if not allowed:
        report.add(SafetyIssue("not_public", reason, severity="error"))

    arrives_before_dark, sunrise, sunset = daylight_report(
        destination_lat, destination_lng, eta or projected_arrival(spec)
    )
    report.arrival_before_sunset = arrives_before_dark
    report.sunrise_hour = sunrise
    report.sunset_hour = sunset
    if not arrives_before_dark:
        report.add(
            SafetyIssue(
                "after_dark",
                "This trip would finish after dark. It is safer to start earlier.",
                severity="warning",
                detail={"sunrise_hour": sunrise, "sunset_hour": sunset},
            )
        )

    if emergency_contact_required and not contact_name:
        report.add(
            SafetyIssue(
                "no_emergency_contact",
                "Add an emergency contact in Settings before you set off.",
                severity="warning",
            )
        )

    if contact_lat is not None and contact_lng is not None:
        distance_km = haversine_m(contact_lat, contact_lng, destination_lat, destination_lng) / 1000.0
        report.contact_distance_km = round(distance_km, 2)
        if distance_km > contact_radius_km:
            report.add(
                SafetyIssue(
                    "far_from_contact",
                    f"That is {distance_km:.0f} km from your emergency contact.",
                    severity="warning",
                    detail={"distance_km": round(distance_km, 2), "limit_km": contact_radius_km},
                )
            )

    return report


def check_plan_safety(plan: Plan) -> SafetyReport:
    """Re-check a finished plan, for the pre-trip safety screen."""
    return check_trip_safety(
        spec=plan.spec,
        destination_lat=plan.destination.lat,
        destination_lng=plan.destination.lng,
        destination_category=plan.destination.category.value,
        emergency_contact_required=plan.safety.emergency_contact_required,
        contact_name="(configured)" if not plan.safety.emergency_contact_required else "",
    )


def strangers_allowed_default() -> bool:
    """Stranger meetup is off, and stays off until the age policy question is answered.

    Brief §15 question 5 and §11: *"[ASK] the product owner for the age policy
    before shipping any stranger feature."* Until that answer exists, this is the
    single switch the meetup feature checks, and it returns False.
    """
    return False


def sos_payload(lat: float, lng: float, trip_id: Optional[str] = None) -> Dict[str, Any]:
    """The exact body ``POST /api/device/sos`` expects, per the server source."""
    return {"lat": lat, "lng": lng, "trip_id": trip_id, "at": iso_z(utcnow())}
