"""Template content and the template-based plan.

Two jobs:

1. **Built-in default items** for every game, used when LLM generation fails
   (brief 02 §B2 step 2: *"If generation fails, use the template's built-in
   default items"*).
2. **The template-based plan** the brief requires as the last resort
   (§5.1: *"On failure, retry once, then fall back to a template-based plan"*).

A template plan is a real, walkable plan: it picks a candidate the code already
scored, builds a sensible route of waypoints, schedules a game, and fills the
copy with fixed text. It is marked ``fallback.used = True`` with a reason so the
UI can say so honestly instead of pretending the LLM wrote it.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple

from ..core.models import (
    DestinationCategory,
    GameType,
    Plan,
    PlanDestination,
    PlanFallback,
    PlanGame,
    PlanSafety,
    PlanWaypoint,
    RevealRule,
    ScheduleEvent,
    ScheduleEventKind,
    TripSpec,
    WaypointKind,
)
from ..engine.geo import bearing_deg, haversine_m, interpolate, move_towards
from .schemas import GameDraft, HintDraft, PlanCopyDraft, TaskDraft
from .tools import Place, RouteEstimate, ScoredCandidate

logger = logging.getLogger("ffh.agent.template")


# --------------------------------------------------------------------------- #
# Built-in game content
# --------------------------------------------------------------------------- #
PHOTO_HUNT_ITEMS: List[Tuple[str, str, int]] = [
    ("something older than you", "old", 15),
    ("a reflection that is not a mirror", "reflection", 20),
    ("a door that looks like it has a story", "door", 15),
    ("a colour you cannot name quickly", "colour", 25),
    ("something that is exactly one hand wide", "hand", 15),
    ("a sign with a spelling mistake", "sign", 20),
    ("something that moves in the wind", "wind", 10),
    ("a pattern that repeats three times", "pattern", 20),
    ("something that would confuse a tourist", "confusing", 25),
    ("the smallest living thing you can photograph", "small", 20),
]

BINGO_TILES: List[str] = [
    "a red door", "a cat", "a bicycle", "a hand-painted sign", "a flower box",
    "a bench with a view", "a bird in flight", "something blue", "a cup of coffee",
    "a poster older than a year", "a potted plant", "a reflection", "a bridge",
    "a person reading", "a wall of graffiti", "a paper lantern", "a spice",
    "a boat", "a statue", "a staircase", "a hanging cloth", "a mango",
    "a street dog sleeping", "a coconut", "a window with curtains",
]

COLOR_HUNT_COLORS: List[Tuple[str, str, str, int]] = [
    ("red", "#D32F2F", "something red", 10),
    ("blue", "#1565C0", "something blue", 10),
    ("yellow", "#F9A825", "something yellow", 10),
    ("green", "#2E7D32", "something green", 10),
    ("orange", "#EF6C00", "something orange", 15),
    ("purple", "#6A1B9A", "something purple", 20),
]

FUNNY_TASKS: List[str] = [
    "Photograph your best 'I meant to do that' face at the next corner.",
    "Find the most dramatic shadow you can and pose in it.",
    "Ask someone for directions to somewhere you are not going, and photograph their hands.",
    "Take a photo that looks like an album cover.",
    "Find something that is the same colour as your shoes.",
    "Photograph a view that would look good on a postcard from 1974.",
]

SECRET_MISSIONS: List[str] = [
    "Make the group laugh without saying a word. Photo as proof.",
    "Get a stranger to wave at the camera (politely).",
    "Find a spot where you cannot hear traffic, and photograph the silence.",
    "Photograph the group from below, all looking up.",
]

DEFAULT_HINTS: List[str] = [
    "Where the road starts to smell of the sea.",
    "Past the shop with the blue shutters.",
    "Where the paving stops and the ground goes soft.",
    "Follow the noise that is not traffic.",
    "Look for the wall with the old paint.",
    "Where two paths meet under a tree.",
]

DEFAULT_TITLES: Dict[str, List[str]] = {
    "calm": ["A quiet line", "Slow miles", "The long way round"],
    "chill": ["Salt and shade", "Nothing urgent", "Half a plan"],
    "playful": ["Silly by design", "Wrong turns welcome", "The detour game"],
    "adventure": ["Past the last lamp", "The unknown bit", "Further than usual"],
    "romantic": ["Two and a road", "Late light", "Somewhere with a view"],
    "foodie": ["Following the smell", "Lunch is the destination", "Snack navigation"],
}

DEFAULT_ARRIVAL_LINES = [
    "You found it. Take a minute.",
    "This is the place. Look around properly.",
    "Made it. The view is yours.",
]

DEFAULT_VIBE_LINES = [
    "Keep it slow. The arrow knows.",
    "No rush. The mystery is the point.",
    "Follow the needle and see what happens.",
]


def default_game_draft(game_type: str, players: Sequence[str], spec: TripSpec) -> GameDraft:
    """Built-in items for one game type, with per-player assignment."""
    game_type = (game_type or "photo_hunt").lower()
    people = list(players) or ["Explorer 1", "Explorer 2"]

    if game_type == "bingo":
        tasks = [
            TaskDraft(text=text, item_key=f"r{index // 5}c{index % 5}", points=10)
            for index, text in enumerate(BINGO_TILES[:25])
        ]
        return GameDraft(
            type="bingo",
            title="Bingo walk",
            duration_min=max(20, min(90, spec.max_minutes // 2)),
            approval="peer_vote",
            win_condition="first_n_in_row",
            n=5,
            tasks=tasks,
        )

    if game_type == "color_hunt":
        tasks = []
        for index, (key, _hex, text, points) in enumerate(COLOR_HUNT_COLORS):
            member = people[index % len(people)]
            tasks.append(
                TaskDraft(player_name=member, text=text, item_key=key, points=points, time_limit_s=300)
            )
        return GameDraft(
            type="color_hunt",
            title="Colour hunt",
            duration_min=max(10, min(45, spec.max_minutes // 3)),
            approval="auto",
            win_condition="most_points",
            tasks=tasks,
        )

    if game_type == "funny_tasks":
        tasks = [
            TaskDraft(player_name=people[index % len(people)], text=text, points=15)
            for index, text in enumerate(FUNNY_TASKS)
        ]
        return GameDraft(type="funny_tasks", title="Silly tasks", duration_min=30, tasks=tasks)

    if game_type == "secret_missions":
        tasks = [
            TaskDraft(player_name=people[index % len(people)], text=text, points=25)
            for index, text in enumerate(SECRET_MISSIONS)
        ]
        return GameDraft(type="secret_missions", title="Secret missions", duration_min=40, tasks=tasks)

    # photo_hunt (default)
    tasks = []
    for index, (text, key, points) in enumerate(PHOTO_HUNT_ITEMS):
        tasks.append(
            TaskDraft(
                player_name=people[index % len(people)] if len(people) > 1 else None,
                text=f"Photograph {text}.",
                item_key=key,
                points=points,
                time_limit_s=300,
            )
        )
    return GameDraft(
        type="photo_hunt",
        title="Photo hunt",
        duration_min=max(15, min(60, spec.max_minutes // 2)),
        approval="peer_vote",
        win_condition="most_points",
        tasks=tasks,
    )


def default_tasks_per_member(players: Sequence[str], spec: TripSpec) -> Dict[str, List[str]]:
    """Different silly task per member, from the brief's game list item 2."""
    out: Dict[str, List[str]] = {}
    for index, member in enumerate(players or ["Explorer 1"]):
        primary = FUNNY_TASKS[index % len(FUNNY_TASKS)]
        secondary = FUNNY_TASKS[(index + 3) % len(FUNNY_TASKS)]
        out[member] = [primary, secondary]
    return out


def default_copy(spec: TripSpec, players: Sequence[str], game_types: Sequence[str]) -> PlanCopyDraft:
    """Fixed text for the template plan. Same shape as the LLM's output."""
    import random

    rng = random.Random(hash((spec.vibe.value, spec.people, spec.transport.value)) & 0xFFFF)
    titles = DEFAULT_TITLES.get(spec.vibe.value, DEFAULT_TITLES["chill"])
    hints = [
        HintDraft(waypoint_idx=index, text=DEFAULT_HINTS[index % len(DEFAULT_HINTS)])
        for index in range(min(6, max(2, spec.max_minutes // 30)))
    ]
    games = [default_game_draft(game_type, players, spec) for game_type in game_types[:2]]
    return PlanCopyDraft(
        title=rng.choice(titles),
        description=f"A {spec.vibe.value} {spec.transport.value} for {spec.people}. The destination stays secret.",
        vibe_line=rng.choice(DEFAULT_VIBE_LINES),
        arrival_line=rng.choice(DEFAULT_ARRIVAL_LINES),
        hints=hints,
        schedule_hints=[],
        games=games,
        tasks_per_member=default_tasks_per_member(players, spec),
        agent_greeting="Give me a moment to pick somewhere good.",
    )


# --------------------------------------------------------------------------- #
# Waypoint construction (code, never the LLM)
# --------------------------------------------------------------------------- #
def build_waypoints(
    spec: TripSpec,
    start_lat: float,
    start_lng: float,
    destination: Place,
    hints: Sequence[str],
    count: Optional[int] = None,
) -> List[PlanWaypoint]:
    """Lay out ``n`` waypoints along the straight line to the destination.

    Deliberately simple: evenly spaced points, each nudged sideways so the route
    is not a ruler line, with alternating kinds so the trip has texture (a photo
    spot, a shop, a route hint). The router then measures the real distance. This
    is the kind of decision the brief says belongs in code, and the LLM is only
    asked for the words on top.
    """
    total = haversine_m(start_lat, start_lng, destination.lat, destination.lng)
    if count is None:
        count = max(2, min(6, int(total / 700.0) + 2))
    if total < 250:
        return []

    kinds = [WaypointKind.PHOTO_SPOT, WaypointKind.ROUTE, WaypointKind.SHOP,
             WaypointKind.VIEWPOINT, WaypointKind.ROUTE, WaypointKind.FOOD]
    waypoints: List[PlanWaypoint] = []
    route_bearing = bearing_deg(start_lat, start_lng, destination.lat, destination.lng)

    for index in range(count):
        fraction = (index + 1) / (count + 1)
        lat, lng = interpolate(start_lat, start_lng, destination.lat, destination.lng, fraction)
        # Nudge sideways so the path bends like a real walk, alternating sides.
        side = 90.0 if index % 2 == 0 else -90.0
        offset = min(90.0, total * 0.04) * (1.0 + 0.3 * index)
        lat, lng = move_towards(lat, lng, route_bearing + side, offset)

        kind = kinds[index % len(kinds)]
        reveal = RevealRule.ON_PROXIMITY if kind != WaypointKind.ROUTE else RevealRule.ON_PROXIMITY
        waypoints.append(
            PlanWaypoint(
                idx=index,
                lat=lat,
                lng=lng,
                kind=kind,
                hint=hints[index % len(hints)] if hints else "",
                reveal=reveal,
                radius_m=40.0 if kind == WaypointKind.PHOTO_SPOT else 60.0,
            )
        )
    return waypoints


def build_schedule(spec: TripSpec, games: Sequence[GameDraft], hints: Sequence[str]) -> List[ScheduleEvent]:
    """Game at the start, a hint about halfway, wrap-up at the end."""
    schedule: List[ScheduleEvent] = []
    if games:
        schedule.append(
            ScheduleEvent(at_minutes=5, event=ScheduleEventKind.GAME_START,
                          game=GameType(games[0].type), text=games[0].title)
        )
    if hints:
        schedule.append(
            ScheduleEvent(at_minutes=spec.max_minutes * 0.5, event=ScheduleEventKind.HINT, text=hints[0])
        )
    schedule.append(
        ScheduleEvent(at_minutes=float(spec.max_minutes), event=ScheduleEventKind.WRAP_UP,
                      text="Time for the collage.")
    )
    return schedule


def template_plan(
    spec: TripSpec,
    candidate: ScoredCandidate,
    start_lat: float,
    start_lng: float,
    route: Optional[RouteEstimate] = None,
    reason: str = "the LLM was unavailable",
    players: Optional[Sequence[str]] = None,
    game_types: Sequence[str] = ("photo_hunt",),
) -> Plan:
    """Build a complete, valid plan with no LLM involved at all."""
    players = list(players or [f"Explorer {index + 1}" for index in range(spec.people)])
    copy = default_copy(spec, players, game_types)
    hints = [hint.text for hint in copy.hints]

    waypoints = build_waypoints(spec, start_lat, start_lng, candidate.place, hints)
    for hint, waypoint in zip(copy.hints, waypoints):
        waypoint.hint = hint.text

    games = [
        PlanGame(
            type=GameType(draft.type),
            tasks=[task.text for task in draft.tasks],
            duration_min=draft.duration_min,
            approval=draft.approval,  # type: ignore[arg-type]
            win_condition=draft.win_condition,  # type: ignore[arg-type]
            n=draft.n,
        )
        for draft in copy.games
    ]

    distance_km = (route.distance_m if route else candidate.place.distance_m) / 1000.0
    return Plan(
        spec=spec,
        title=copy.title,
        description=copy.description,
        destination=PlanDestination(
            poi_id=candidate.place.poi_id,
            name=candidate.place.name,
            lat=candidate.place.lat,
            lng=candidate.place.lng,
            category=_category(candidate.place.category),
            radius_m=60.0,
            reveal=RevealRule.ON_ARRIVAL,
            secret=True,
        ),
        waypoints=waypoints,
        schedule=build_schedule(spec, copy.games, hints),
        games=games,
        tasks_per_member=copy.tasks_per_member,
        safety=PlanSafety(
            public_place=bool(candidate.safety.public_place) if candidate.safety else True,
            arrival_before_sunset=bool(candidate.safety.arrival_before_sunset) if candidate.safety else True,
            emergency_contact_required=True,
            daylight_warning=None
            if not candidate.safety or candidate.safety.arrival_before_sunset
            else "Arrival is projected after dark.",
        ),
        fallback=PlanFallback(type="template", used=True, reason=reason),
        route_km=distance_km,
        plannable=True,
    )


def _category(raw: str) -> DestinationCategory:
    try:
        return DestinationCategory(raw)
    except ValueError:
        return DestinationCategory.OTHER
