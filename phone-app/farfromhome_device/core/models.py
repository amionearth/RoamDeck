"""Every message and JSON document the handheld exchanges, as pydantic models.

The brief requires "pydantic for every message and JSON schema" (brief §5.0).
Two families live here:

* **Wire models** — exactly what the server expects (see ``docs/SERVER_CONTRACT.md``).
  These were derived by reading the server source at
  ``layover/backend/app/routers/device.py``, not from prose, so field names are
  the ones the server actually validates.
* **Handheld models** — the ``TripSpec`` / ``Plan`` documents the planner
  produces and the engine consumes.

Convention: ``extra="allow"`` on wire models (the server stores extra fields),
``extra="forbid"`` on the planner's LLM-facing schema so a hallucinated field is
caught at validation time instead of silently ignored.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #
def new_id() -> str:
    """Client-generated UUID. The brief requires the client to make these so
    every write is safe to repeat (idempotency)."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: Optional[datetime] = None) -> str:
    """ISO 8601 in UTC with a trailing ``Z`` — exactly the brief's format."""
    moment = value or utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


_ISO_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})[T ](?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})"
    r"(?P<frac>\.\d+)?(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse the ISO forms the server emits. Returns ``None`` on anything odd."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Transport(str, Enum):
    WALK = "walk"
    BIKE = "bike"
    CAR = "car"
    TRANSIT = "transit"


class RevealRule(str, Enum):
    NEVER = "never"
    ON_PROXIMITY = "on_proximity"
    ON_ARRIVAL = "on_arrival"
    AT_TIME = "at_time"


class WaypointKind(str, Enum):
    ROUTE = "route"
    PHOTO_SPOT = "photo_spot"
    SHOP = "shop"
    FOOD = "food"
    VIEWPOINT = "viewpoint"
    REST = "rest"


class TripState(str, Enum):
    IDLE = "idle"
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    ARRIVED = "arrived"
    WRAPPED_UP = "wrapped_up"


class GameType(str, Enum):
    PHOTO_HUNT = "photo_hunt"
    BINGO = "bingo"
    COLOR_HUNT = "color_hunt"
    FUNNY_TASKS = "funny_tasks"
    SECRET_MISSIONS = "secret_missions"
    IMPOSTER = "imposter"


class GameHostState(str, Enum):
    """The state machine from brief 02 §B2."""

    IDLE = "IDLE"
    CONFIGURING = "CONFIGURING"
    GENERATING = "GENERATING"
    CREATING_SESSION = "CREATING_SESSION"
    LOBBY = "LOBBY"
    RUNNING = "RUNNING"
    ENDING = "ENDING"
    RESULTS = "RESULTS"
    CLOSED = "CLOSED"
    OFFLINE_FALLBACK = "OFFLINE_FALLBACK"


class ApprovalMode(str, Enum):
    PEER_VOTE = "peer_vote"
    AUTO = "auto"
    HOST = "host"


class WinCondition(str, Enum):
    MOST_POINTS = "most_points"
    FIRST_N_IN_ROW = "first_n_in_row"


class DestinationCategory(str, Enum):
    BEACH = "beach"
    PARK = "park"
    CAFE = "cafe"
    MARKET = "market"
    TEMPLE = "temple"
    HERITAGE = "heritage"
    VIEWPOINT = "viewpoint"
    OTHER = "other"


class AssistantMood(str, Enum):
    CALM = "calm"
    CHILL = "chill"
    PLAYFUL = "playful"
    ADVENTURE = "adventure"
    ROMANTIC = "romantic"
    FOODIE = "foodie"


# --------------------------------------------------------------------------- #
# Planner input: TripSpec
# --------------------------------------------------------------------------- #
class TripSpec(BaseModel):
    """What the user asked for, after parsing voice/buttons and filling defaults."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    vibe: AssistantMood = AssistantMood.CHILL
    people: int = Field(default=2, ge=1, le=12)
    transport: Transport = Transport.WALK
    max_minutes: int = Field(default=120, ge=20, le=720)
    range_km: tuple[float, float] = (1.0, 4.0)
    interests: List[str] = Field(default_factory=lambda: ["coffee", "viewpoint"])
    food_exclusions: List[str] = Field(default_factory=list)
    start_lat: Optional[float] = None
    start_lng: Optional[float] = None
    scope: str = "local"  # local | international   (international is "later")
    language: str = "en"
    raw_text: str = ""
    defaults_applied: List[str] = Field(default_factory=list)

    @field_validator("range_km", mode="before")
    @classmethod
    def _range(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            low, high = float(value[0]), float(value[1])
            if low > high:
                low, high = high, low
            return (max(0.1, low), max(0.2, high))
        return value

    @model_validator(mode="after")
    def _sanity(self) -> "TripSpec":
        if self.range_km[1] > 60:
            object.__setattr__(self, "range_km", (self.range_km[0], 60.0))
        if not self.interests:
            object.__setattr__(self, "interests", ["coffee", "viewpoint"])
        return self

    def describe(self) -> str:
        """One line for the planner prompt and the UI."""
        bits = [
            f"{self.vibe.value} trip for {self.people}",
            f"by {self.transport.value}",
            f"up to {self.max_minutes} min",
            f"{self.range_km[0]:.1f}-{self.range_km[1]:.1f} km",
            "interests: " + ", ".join(self.interests[:5]),
        ]
        if self.food_exclusions:
            bits.append("avoid: " + ", ".join(self.food_exclusions[:4]))
        return "; ".join(bits)


# --------------------------------------------------------------------------- #
# Planner output: Plan
# --------------------------------------------------------------------------- #
class PlanDestination(BaseModel):
    """The secret. ``name`` is what the handheld keeps hidden from its own UI
    until the reveal rule fires (brief 03 §6.1, last bullet)."""

    model_config = ConfigDict(extra="forbid")

    poi_id: str = ""
    name: str = "HIDDEN"
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    category: DestinationCategory = DestinationCategory.OTHER
    radius_m: float = Field(default=60.0, ge=10, le=500)
    reveal: RevealRule = RevealRule.ON_ARRIVAL
    secret: bool = True


class PlanWaypoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: "w" + uuid.uuid4().hex[:6])
    idx: int = 0
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    kind: WaypointKind = WaypointKind.ROUTE
    hint: str = ""
    reveal: RevealRule = RevealRule.ON_PROXIMITY
    radius_m: float = Field(default=40.0, ge=5, le=500)


class ScheduleEventKind(str, Enum):
    GAME_START = "game_start"
    HINT = "hint"
    TASK = "task"
    BREAK = "break"
    REVEAL = "reveal"
    WRAP_UP = "wrap_up"


class ScheduleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at_minutes: float = Field(ge=0)
    event: ScheduleEventKind
    game: Optional[GameType] = None
    text: str = ""


class PlanGame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: GameType
    tasks: List[str] = Field(default_factory=list)
    duration_min: int = Field(default=30, ge=2, le=240)
    approval: ApprovalMode = ApprovalMode.PEER_VOTE
    win_condition: WinCondition = WinCondition.MOST_POINTS
    n: int = 5


class PlanSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_place: bool = True
    arrival_before_sunset: bool = True
    emergency_contact_required: bool = True
    daylight_warning: Optional[str] = None


class PlanFallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "template"
    used: bool = False
    reason: str = ""


class Plan(BaseModel):
    """Validated, versioned plan. This is the contract between agent and engine."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    trip_id: str = Field(default_factory=new_id)
    created_at: str = Field(default_factory=iso_z)
    title: str = "Blind Exploration"
    description: str = ""
    spec: TripSpec
    destination: PlanDestination
    waypoints: List[PlanWaypoint] = Field(default_factory=list)
    schedule: List[ScheduleEvent] = Field(default_factory=list)
    games: List[PlanGame] = Field(default_factory=list)
    tasks_per_member: Dict[str, List[str]] = Field(default_factory=dict)
    safety: PlanSafety = Field(default_factory=PlanSafety)
    fallback: PlanFallback = Field(default_factory=PlanFallback)
    route_km: float = 0.0
    plannable: bool = True

    @field_validator("waypoints")
    @classmethod
    def _index_waypoints(cls, value: List[PlanWaypoint]) -> List[PlanWaypoint]:
        for position, waypoint in enumerate(value):
            waypoint.idx = position
        return value

    @model_validator(mode="after")
    def _sort_schedule(self) -> "Plan":
        self.schedule.sort(key=lambda event: event.at_minutes)
        return self

    # --- helpers the engine and UI need -------------------------------------
    def destination_ordinal(self) -> str:
        """A word the UI can show without leaking the place."""
        return "your destination"

    def public_summary(self) -> Dict[str, Any]:
        """Safe to show on screen before arrival: **no** name, no coordinates."""
        return {
            "trip_id": self.trip_id,
            "title": self.title,
            "transport": self.spec.transport.value,
            "people": self.spec.people,
            "waypoint_count": len(self.waypoints),
            "route_km": round(self.route_km, 2),
            "games": [game.type.value for game in self.games],
        }

    def to_server_plan(self) -> Dict[str, Any]:
        """The body for ``POST /api/device/trips`` (see docs/SERVER_CONTRACT.md).

        The server's ``create_device_trip`` reads ``destination.{name,lat,lng,
        category}``, ``waypoints[].{idx,lat,lng,radius_m,hint}``, ``members``,
        ``calendar_at``, ``mood``, ``title`` and ``description``, and stashes the
        whole body in ``plan_json``. We therefore send everything; extra keys are
        preserved.
        """
        return {
            "trip_id": self.trip_id,
            "title": self.title,
            "description": self.description,
            "mood": self.spec.vibe.value,
            "calendar_at": self.created_at,
            "members": [
                {"name": f"Explorer {i + 1}", "color": member_color(i)}
                for i in range(self.spec.people)
            ],
            "destination": {
                "name": self.destination.name,
                "lat": self.destination.lat,
                "lng": self.destination.lng,
                "category": self.destination.category.value,
                "radius_m": self.destination.radius_m,
                "secret": True,
            },
            "waypoints": [
                {
                    "idx": waypoint.idx,
                    "lat": waypoint.lat,
                    "lng": waypoint.lng,
                    "radius_m": waypoint.radius_m,
                    "hint": waypoint.hint,
                    "kind": waypoint.kind.value,
                    "reveal": waypoint.reveal.value,
                }
                for waypoint in self.waypoints
            ],
            "photo_spots": [
                {
                    "lat": waypoint.lat,
                    "lng": waypoint.lng,
                    "radius_m": waypoint.radius_m,
                    "tip": waypoint.hint,
                }
                for waypoint in self.waypoints
                if waypoint.kind == WaypointKind.PHOTO_SPOT
            ],
            "games": [game.model_dump(mode="json") for game in self.games],
            "schedule": [event.model_dump(mode="json") for event in self.schedule],
            "safety": self.safety.model_dump(mode="json"),
            "spec": self.spec.model_dump(mode="json"),
            "schema_version": self.schema_version,
        }


MEMBER_COLORS = ["#1E7F85", "#F2A33A", "#1F4B8F", "#7B4FC2", "#2E7D32", "#C2185B"]


def member_color(index: int) -> str:
    return MEMBER_COLORS[index % len(MEMBER_COLORS)]


# --------------------------------------------------------------------------- #
# Sensor readings
# --------------------------------------------------------------------------- #
class Position(BaseModel):
    """A GPS fix."""

    lat: float
    lng: float
    accuracy_m: float = 5.0
    altitude_m: Optional[float] = None
    speed_mps: Optional[float] = None
    course_deg: Optional[float] = None
    satellites: Optional[int] = None
    t: datetime = Field(default_factory=utcnow)

    @property
    def valid(self) -> bool:
        return -90 <= self.lat <= 90 and -180 <= self.lng <= 180 and self.accuracy_m > 0

    def to_wire(self) -> Dict[str, Any]:
        """One entry of the ``points`` array for ``POST /device/trips/{id}/points``.

        The server's ``GPSPointItem`` is ``{lat, lng, t, acc}``.
        """
        return {
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
            "t": iso_z(self.t),
            "acc": round(self.accuracy_m, 2),
        }


class HeadingReading(BaseModel):
    """Fused heading. ``heading_deg`` is clockwise from true north."""

    heading_deg: float = 0.0
    source: str = "fused"  # magnetometer | gyro | gps_course | fused
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    magnetometer_ok: bool = True
    needs_calibration: bool = False
    t: datetime = Field(default_factory=utcnow)


class ImuSample(BaseModel):
    """One raw IMU sample from the HAL."""

    ax: float = 0.0
    ay: float = 0.0
    az: float = 9.81
    gx: float = 0.0  # deg/s
    gy: float = 0.0
    gz: float = 0.0
    mx: float = 0.0  # microtesla
    my: float = 0.0
    mz: float = 0.0
    t: datetime = Field(default_factory=utcnow)


class CompassUpdate(BaseModel):
    """Published on ``compass.update`` — **direction only, never distance**."""

    arrow_angle: float = 0.0  # (bearing - heading) mod 360
    bearing_deg: float = 0.0
    heading_deg: float = 0.0
    target_label: str = "next stop"
    target_kind: str = "route"
    has_fix: bool = True
    state: str = "running"
    updated_at: str = Field(default_factory=iso_z)

    @model_validator(mode="after")
    def _no_leak(self) -> "CompassUpdate":
        # A guard rail for the "mystery first" principle: this message type must
        # never grow a distance-to-destination field.
        return self


# --------------------------------------------------------------------------- #
# Wire models: pairing
# --------------------------------------------------------------------------- #
class RegisterDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str


class RegisterDeviceResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    device_id: str
    pairing_code: str
    poll_secret: str


class PairingStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str  # pending | paired | expired
    token: Optional[str] = None
    owner_user_id: Optional[str] = None


class HeartbeatBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    battery_pct: int = 100


# --------------------------------------------------------------------------- #
# Wire models: trips
# --------------------------------------------------------------------------- #
class GPSPointItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    lat: float
    lng: float
    t: Optional[str] = None
    acc: Optional[float] = 5.0


class PointsBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    points: List[GPSPointItem]


class RevealBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    at: Optional[str] = None


class ArriveBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    lat: float
    lng: float
    at: Optional[str] = None


class FinishBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    distance_m: float = 0.0
    summary: str = ""


class PhotoMeta(BaseModel):
    """Non-file fields for the multipart photo upload."""

    model_config = ConfigDict(extra="allow")
    photo_id: str = Field(default_factory=new_id)
    taken_at: str = Field(default_factory=iso_z)
    lat: Optional[float] = None
    lng: Optional[float] = None
    member_name: Optional[str] = None
    task_id: Optional[str] = None
    game_id: Optional[str] = None
    category: str = "waypoint"  # waypoint | destination | task | group | food
    caption: str = ""

    def to_form(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for key, value in self.model_dump().items():
            if value is None or value == "":
                continue
            out[key] = str(value)
        return out


# --------------------------------------------------------------------------- #
# Wire models: games
# --------------------------------------------------------------------------- #
class GamePlayerSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    color: str = "#1E7F85"
    team: Optional[str] = None


class GameTaskSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    player_name: Optional[str] = None
    text: str
    item_key: Optional[str] = None
    points: int = 10
    time_limit_s: int = 300


class CreateGameRequest(BaseModel):
    """Body for ``POST /api/device/games``.

    ``game_id`` is honoured by the server: sending our own id makes the create
    idempotent, so an outbox retry cannot mint a second session (and a second join
    code). Omitting it lets the server generate one, which is what an older client
    still gets. See docs/SERVER_CONTRACT.md G-01.
    """

    model_config = ConfigDict(extra="allow")
    game_id: Optional[str] = None
    trip_id: Optional[str] = None
    type: str = "photo_hunt"
    duration_s: int = 600
    params: Dict[str, Any] = Field(default_factory=dict)
    players: List[GamePlayerSpec] = Field(default_factory=list)
    tasks: List[GameTaskSpec] = Field(default_factory=list)


class CreateGameResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    game_id: str
    code: str
    join_url: str
    qr_png_url: str = ""
    status: str = "open"
    ends_at: Optional[str] = None


class GameStatePlayer(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = ""
    display_name: str = ""
    joined: bool = False
    score: int = 0
    submissions: int = 0
    color: str = "#1E7F85"

    @property
    def label(self) -> str:
        return self.display_name or self.name or "Player"


class GameStateResponse(BaseModel):
    """Best-effort view of ``GET /api/device/games/{id}/state``.

    The running server returns ``{status, ends_at, scores: {player_id: points},
    submissions_count}``. Brief 03 §7.3 asks for a ``players`` array with
    ``joined``/``submissions``, ``pending_votes`` and ``winner``. We model both
    shapes and normalise in :meth:`normalised`.
    """

    model_config = ConfigDict(extra="allow")
    status: str = "open"
    ends_at: Optional[str] = None
    scores: Dict[str, int] = Field(default_factory=dict)
    submissions_count: int = 0
    players: List[Dict[str, Any]] = Field(default_factory=list)
    pending_votes: int = 0
    winner: Optional[str] = None

    def normalised(
        self,
        roster: Optional[List[GamePlayerSpec]] = None,
        player_ids: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Return a uniform ``[{name, joined, score, submissions, color}]``."""
        if self.players:
            out = []
            for entry in self.players:
                out.append(
                    {
                        "name": entry.get("display_name") or entry.get("name") or "Player",
                        "joined": bool(entry.get("joined", True)),
                        "score": int(entry.get("score", 0) or 0),
                        "submissions": int(entry.get("submissions", 0) or 0),
                        "color": entry.get("color", "#1E7F85"),
                    }
                )
            out.sort(key=lambda item: item["score"], reverse=True)
            return out

        out = []
        for spec in roster or []:
            # The roster may be pydantic specs or the plain dicts the host carries.
            if isinstance(spec, dict):
                name = str(spec.get("name") or spec.get("display_name") or "Player")
                colour = str(spec.get("color") or "#1E7F85")
            else:
                name = str(getattr(spec, "name", "") or "Player")
                colour = str(getattr(spec, "color", "#1E7F85"))
            player_id = (player_ids or {}).get(name)
            out.append(
                {
                    "name": name,
                    "joined": False,
                    "score": int(self.scores.get(player_id or "", 0)),
                    "submissions": 0,
                    "color": colour,
                }
            )
        out.sort(key=lambda item: item["score"], reverse=True)
        return out


class GameSubmissionEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    photo_id: str = ""
    player_id: str = ""
    player_name: str = ""
    task_id: str = ""
    thumb_url: str = ""
    status: str = "pending"


# --------------------------------------------------------------------------- #
# Wire models: SOS / heartbeat / network info
# --------------------------------------------------------------------------- #
class SOSBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    lat: float
    lng: float
    trip_id: Optional[str] = None
    at: Optional[str] = None


class NetworkInfo(BaseModel):
    """``GET /api/network-info`` (no auth) — brief 02 §C2."""

    model_config = ConfigDict(extra="allow")
    lan_ip: str = ""
    port: int = 8000
    base_url: str = ""
    candidates: List[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    ok: bool = False
    app: str = ""
    server_time: Optional[str] = None


# --------------------------------------------------------------------------- #
# Bus envelope
# --------------------------------------------------------------------------- #
class BusMessage(BaseModel):
    """One message on the local bus. Topics are the brief's list in §5.0."""

    model_config = ConfigDict(extra="allow")
    topic: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "unknown"
    t: str = Field(default_factory=iso_z)


BUS_TOPICS: tuple[str, ...] = (
    "trip.requested",
    "plan.ready",
    "trip.started",
    "position.update",
    "heading.update",
    "compass.update",
    "reveal.due",
    "geofence.entered",
    "off_route",
    "game.started",
    "game.event",
    "photo.captured",
    "agent.say",
    "sync.status",
    "trip.ended",
    "sos.triggered",
)

#: Extra topics the handheld uses internally (documented in ARCHITECTURE.md).
EXTRA_BUS_TOPICS: tuple[str, ...] = (
    "planning.progress",
    "plan.failed",
    "task.update",
    "reveal.shown",
    "trip.arrived",
    "trip.wrapped_up",
    "game.phase",
    "ui.command",
    "sensor.gps_lost",
    "sensor.gps_back",
    "outbox.changed",
    "connection.status",
)


def all_topics() -> tuple[str, ...]:
    return BUS_TOPICS + EXTRA_BUS_TOPICS


def validate_topic(topic: str) -> str:
    """Typo guard: topics must be one the brief or ARCHITECTURE.md declares."""
    if topic in all_topics():
        return topic
    raise ValueError(f"Unknown bus topic: {topic!r}. Add it to EXTRA_BUS_TOPICS if intended.")
