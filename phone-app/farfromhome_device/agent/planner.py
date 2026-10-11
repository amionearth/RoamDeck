"""The planner: tools → code scoring → LLM text → validated ``Plan``.

The loop, in the brief's words (§5.1): *"parse input to ``TripSpec``, call tools,
score candidates in code, ask the LLM to write the creative parts, validate, output
``Plan``."*

Sequence implemented here:

1. **Resolve the start point** (GPS, or the last known fix, or the config default).
2. **Places** — ``PlacesProvider.find`` over a radius derived from the spec.
3. **Filter** — the safety checker removes anything not a public place, before any
   scoring, so a disqualified place can never win on charm.
4. **Route** — ``RoutingProvider.route`` for each surviving candidate, in parallel.
5. **Score** — :func:`~.tools.score_candidate`, in code.
6. **Pick** — highest score wins; ties break on the shorter route.
7. **Creative text** — one LLM call, schema-constrained to
   :class:`~.schemas.PlanCopyDraft`. On failure the template content is used and
   ``fallback.used`` is set.
8. **Assemble and validate** — the ``Plan`` model is the contract; the code owns
   destination, coordinates, waypoints, schedule and safety flags.

Progress is published on ``planning.progress`` so the UI can show what is happening
instead of a spinner, and ``plan.ready`` / ``plan.failed`` at the end.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from ..core.bus import Bus
from ..core.config import Settings
from ..core.models import (
    GameType,
    Plan,
    PlanDestination,
    PlanFallback,
    PlanGame,
    PlanSafety,
    RevealRule,
    ScheduleEvent,
    ScheduleEventKind,
    TripSpec,
    iso_z,
    utcnow,
)
from . import template as templates
from .input_parser import parse_heuristic
from .llm_router import LlmRouter, ask_llm
from .schemas import GameDraft, PlanCopyDraft, copy_schema
from .tools import (
    NullReviews,
    OfflinePlaces,
    OsrmRouting,
    OverpassPlaces,
    Place,
    PlacesProvider,
    ReviewsProvider,
    RoutingProvider,
    SafetyChecker,
    ScoredCandidate,
    SocialSignals,
    StraightLineRouting,
    score_candidate,
)

logger = logging.getLogger("ffh.agent.planner")

#: How many candidates get a routing call. OSRM is a public service; asking about
#: 30 places would be rude and slow. The top N by straight-line distance is enough.
MAX_ROUTED_CANDIDATES = 8

#: Hard ceiling on the places search radius, whatever the spec says.
MAX_SEARCH_RADIUS_M = 25_000.0


@dataclass
class PlanRequest:
    """Everything the planner needs. Either ``text`` or a ready ``spec``."""

    text: str = ""
    spec: Optional[TripSpec] = None
    start_lat: Optional[float] = None
    start_lng: Optional[float] = None
    players: List[str] = field(default_factory=list)
    game_types: List[str] = field(default_factory=lambda: ["photo_hunt"])
    offline: bool = False


@dataclass
class PlanOutcome:
    plan: Optional[Plan]
    spec: TripSpec
    candidates: List[ScoredCandidate] = field(default_factory=list)
    provider: str = ""
    used_template: bool = False
    reason: str = ""
    duration_ms: int = 0
    progress: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.plan is not None

    def summary(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "trip_id": self.plan.trip_id if self.plan else None,
            "title": self.plan.title if self.plan else None,
            "destination_hidden": not (self.plan and self.plan.fallback.used and False),
            "used_template": self.used_template,
            "reason": self.reason,
            "provider": self.provider,
            "candidates": len(self.candidates),
            "route_km": round(self.plan.route_km, 2) if self.plan else 0.0,
            "duration_ms": self.duration_ms,
        }


class Planner:
    """The thin custom agent. One instance per process is fine."""

    def __init__(
        self,
        settings: Settings,
        bus: Optional[Bus] = None,
        router: Optional[LlmRouter] = None,
        *,
        places: Optional[PlacesProvider] = None,
        routing: Optional[RoutingProvider] = None,
        reviews: Optional[ReviewsProvider] = None,
        safety: Optional[SafetyChecker] = None,
        social: Optional[SocialSignals] = None,
    ) -> None:
        self.settings = settings
        self.bus = bus
        self.router = router
        self.places = places or OverpassPlaces()
        self.routing = routing or OsrmRouting()
        self.reviews = reviews or NullReviews()
        self.safety = safety or SafetyChecker(
            contact_name=settings.emergency_contact_name,
        )
        self.social = social or SocialSignals(enabled=False)
        self._offline_places = OfflinePlaces()
        self._straight = StraightLineRouting()
        self.last_candidates: List[ScoredCandidate] = []

    # ------------------------------------------------------------------ #
    # progress
    # ------------------------------------------------------------------ #
    def _progress(self, step: str, detail: str = "", fraction: float = 0.0) -> None:
        logger.info("planning: %s (%s)", step, detail)
        if self.bus:
            self.bus.publish(
                "planning.progress",
                {"step": step, "detail": detail, "fraction": round(fraction, 2), "at": iso_z(utcnow())},
                source="planner",
            )

    # ------------------------------------------------------------------ #
    # main entry
    # ------------------------------------------------------------------ #
    async def plan(self, request: PlanRequest) -> PlanOutcome:
        """Produce a validated plan. **Always** returns a plan, even offline."""
        started = time.monotonic()
        progress: List[str] = []

        def note(step: str, detail: str = "", fraction: float = 0.0) -> None:
            progress.append(step)
            self._progress(step, detail, fraction)

        # 1. spec --------------------------------------------------------
        note("reading", "working out what you asked for", 0.05)
        if request.spec is not None:
            spec = request.spec
        else:
            spec = await self._spec_from_text(request)

        start_lat = request.start_lat if request.start_lat is not None else spec.start_lat
        start_lng = request.start_lng if request.start_lng is not None else spec.start_lng
        if start_lat is None or start_lng is None:
            start_lat, start_lng = 11.2588, 75.7804
            logger.warning("no start position known; using the configured demo coordinates")
        spec.start_lat, spec.start_lng = start_lat, start_lng

        # 2. places ------------------------------------------------------
        radius = self._search_radius(spec)
        note("searching", f"looking for places within {radius / 1000:.1f} km", 0.2)
        places = await self._find_places(spec, start_lat, start_lng, radius, offline=request.offline)

        # 3. filter ------------------------------------------------------
        note("filtering", f"{len(places)} candidates", 0.4)
        allowed: List[Place] = []
        rejected: List[Tuple[Place, str]] = []
        for place in places:
            reason = self.safety.reject_reason(place)
            if reason:
                rejected.append((place, reason))
            else:
                allowed.append(place)
        if rejected:
            logger.info("safety filter removed %d candidates", len(rejected))
        if not allowed:
            # Fall back to the offline place set rather than failing the trip.
            note("searching", "no safe candidates from the map; using the built-in set", 0.45)
            allowed = await self._offline_for(spec).find(
                start_lat, start_lng, radius, spec.interests, limit=8
            )
            allowed = [place for place in allowed if not self.safety.reject_reason(place)]

        # 4 + 5. route and score ----------------------------------------
        note("scoring", "measuring routes", 0.6)
        candidates = await self._score(spec, allowed, start_lat, start_lng, offline=request.offline)
        self.last_candidates = candidates
        if not candidates:
            outcome = self._failure(spec, "no candidate places could be scored", started, progress)
            return outcome

        best = candidates[0]
        note("choosing", "settled on a destination", 0.75)

        # 6. creative text ----------------------------------------------
        players = request.players or [f"Explorer {index + 1}" for index in range(spec.people)]
        copy, provider, used_template, reason = await self._write_copy(
            spec, best, players, request.game_types, offline=request.offline
        )
        note("writing", "writing the hints and tasks", 0.9)

        # 7. assemble ---------------------------------------------------
        plan = self._assemble(spec, best, copy, players, request.game_types, used_template, reason)
        outcome = PlanOutcome(
            plan=plan,
            spec=spec,
            candidates=candidates,
            provider=provider,
            used_template=used_template,
            reason=reason,
            duration_ms=int((time.monotonic() - started) * 1000),
            progress=progress,
        )
        note("ready", plan.title, 1.0)

        if self.bus and plan is not None:
            self.bus.publish("plan.ready", plan.public_summary(), source="planner")
        return outcome

    def _failure(self, spec: TripSpec, reason: str, started: float, progress: List[str]) -> PlanOutcome:
        logger.error("planning failed: %s", reason)
        if self.bus:
            self.bus.publish("plan.failed", {"reason": reason, "spec": spec.model_dump(mode="json")},
                             source="planner")
        return PlanOutcome(
            plan=None,
            spec=spec,
            used_template=True,
            reason=reason,
            duration_ms=int((time.monotonic() - started) * 1000),
            progress=progress,
        )

    # ------------------------------------------------------------------ #
    # steps
    # ------------------------------------------------------------------ #
    async def _spec_from_text(self, request: PlanRequest) -> TripSpec:
        if self.router is not None and not request.offline:
            from .input_parser import parse_with_llm

            return await parse_with_llm(
                self.router, request.text, request.start_lat, request.start_lng
            )
        return parse_heuristic(request.text, request.start_lat, request.start_lng)

    @staticmethod
    def _search_radius(spec: TripSpec) -> float:
        """Search a bit wider than the wanted range, so there is something to choose."""
        return min(MAX_SEARCH_RADIUS_M, max(600.0, spec.range_km[1] * 1000.0 * 1.35))

    async def _find_places(
        self,
        spec: TripSpec,
        lat: float,
        lng: float,
        radius_m: float,
        offline: bool = False,
        timeout_s: float = 12.0,
    ) -> List[Place]:
        if not offline:
            try:
                found = await self.places.find(lat, lng, radius_m, spec.interests, limit=40,
                                               timeout_s=timeout_s)
                if found:
                    logger.info("found %d places via %s", len(found), getattr(self.places, "name", "?"))
                    return found
                logger.info("places provider returned nothing; using the built-in set")
            except Exception as exc:
                logger.warning("places provider failed (%s); using the built-in set", exc)
        return await self._offline_for(spec).find(lat, lng, radius_m, spec.interests, limit=12)

    @staticmethod
    def _offline_for(spec: TripSpec) -> OfflinePlaces:
        """Offline places spread across the distance the user actually asked for."""
        return OfflinePlaces(wanted_max_m=spec.range_km[1] * 1000.0)

    async def _score(
        self,
        spec: TripSpec,
        places: Sequence[Place],
        start_lat: float,
        start_lng: float,
        offline: bool = False,
    ) -> List[ScoredCandidate]:
        """Route the nearest candidates and score them all in code."""
        nearby = sorted(places, key=lambda place: place.distance_m)[:MAX_ROUTED_CANDIDATES]
        reviews = await asyncio.gather(
            *(self.reviews.reviews_for(place) for place in nearby), return_exceptions=True
        )
        for place, result in zip(nearby, reviews):
            if isinstance(result, Place):
                place.rating = result.rating
                place.review_count = result.review_count

        signals = await self.social.signals_for(nearby)

        routing: RoutingProvider = self._straight if offline else self.routing
        routes = await asyncio.gather(
            *(
                routing.route([(start_lat, start_lng), (place.lat, place.lng)], spec.transport.value)
                for place in nearby
            ),
            return_exceptions=True,
        )

        candidates: List[ScoredCandidate] = []
        for place, route in zip(nearby, routes):
            if isinstance(route, BaseException) or route is None:
                route = await self._straight.route(
                    [(start_lat, start_lng), (place.lat, place.lng)], spec.transport.value
                )
            safety = self.safety.check(spec, place)
            scored = score_candidate(place, spec, route, safety, signals.get(place.poi_id))
            candidates.append(scored)

        # Range filter: drop anything beyond the asked-for distance. The brief's
        # "pick the simplest option" rule (and the user's own words) outweigh a
        # charming place 8 km away.
        low_km, high_km = spec.range_km
        ceiling_km = min(60.0, max(high_km, low_km) * 1.25)
        within = [
            candidate
            for candidate in candidates
            if (candidate.route.distance_m / 1000.0) <= ceiling_km
        ]
        if not within:
            logger.info(
                "no candidate inside %.1f km; relaxing the range filter (nearest is %.1f km)",
                ceiling_km,
                min((candidate.route.distance_m / 1000.0) for candidate in candidates)
                if candidates
                else 0.0,
            )
            within = candidates

        # Within the range, the score decides; ties break towards the shorter walk.
        within.sort(
            key=lambda candidate: (
                -candidate.score,
                candidate.route.distance_m if candidate.route else 0.0,
            )
        )
        return within

    async def _write_copy(
        self,
        spec: TripSpec,
        best: ScoredCandidate,
        players: Sequence[str],
        game_types: Sequence[str],
        offline: bool = False,
    ) -> Tuple[PlanCopyDraft, str, bool, str]:
        """Ask the LLM for the words. Fall back to the template on any failure."""
        fallback_copy = templates.default_copy(spec, players, game_types)

        if offline or self.router is None:
            return fallback_copy, "", True, "running offline"

        prompt = self.build_copy_prompt(spec, best, players, game_types)
        result = await ask_llm(
            self.router,
            task="plan",
            prompt=prompt,
            schema=copy_schema(),
            model_cls=PlanCopyDraft,
            temperature=0.8,
            max_tokens=1600,
        )
        if result is None:
            return fallback_copy, "", True, "no LLM provider was reachable"

        try:
            draft = PlanCopyDraft.model_validate_json(result.text)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            return fallback_copy, result.provider, True, f"the model's JSON did not match the schema ({first.get('msg', 'invalid')})"

        merged = self._merge_copy(fallback_copy, draft)
        return merged, result.provider, False, ""

    @staticmethod
    def _merge_copy(fallback: PlanCopyDraft, draft: PlanCopyDraft) -> PlanCopyDraft:
        """Keep whatever the model actually produced; fill the gaps from the template.

        A model that returns a title and nothing else should not cost us the hints
        and the game items.
        """
        return PlanCopyDraft(
            title=draft.title or fallback.title,
            description=draft.description or fallback.description,
            vibe_line=draft.vibe_line or fallback.vibe_line,
            arrival_line=draft.arrival_line or fallback.arrival_line,
            hints=draft.hints or fallback.hints,
            schedule_hints=draft.schedule_hints or fallback.schedule_hints,
            games=draft.games or fallback.games,
            tasks_per_member=draft.tasks_per_member or fallback.tasks_per_member,
            agent_greeting=draft.agent_greeting or fallback.agent_greeting,
        )

    def build_copy_prompt(
        self,
        spec: TripSpec,
        best: ScoredCandidate,
        players: Sequence[str],
        game_types: Sequence[str],
    ) -> str:
        """The creative prompt.

        **What is deliberately absent:** the destination's name, its coordinates and
        its ``poi_id``. The model is told the *category* and the *shape of the trip*
        ("a 2.1 km walk ending at a beach, about 35 minutes") and nothing that could
        identify the place. That is what keeps the mystery intact even though a
        free-tier model may use the prompt for training (brief §5.1).
        """
        route = best.route
        distance_km = (route.distance_m if route else best.place.distance_m) / 1000.0
        duration_min = route.duration_min if route else distance_km / 1.35
        player_list = ", ".join(players) or "2 explorers"
        game_list = ", ".join(game_types) or "photo_hunt"

        return (
            "Write the words for a surprise walking trip. The destination is SECRET.\n"
            "\n"
            "Facts you may use:\n"
            f"- mood: {spec.vibe.value}\n"
            f"- group: {spec.people} people ({player_list})\n"
            f"- how they travel: {spec.transport.value}\n"
            f"- time budget: {spec.max_minutes} minutes\n"
            f"- route length: {distance_km:.1f} km, roughly {duration_min:.0f} minutes of travel\n"
            f"- the destination is a {best.place.category}\n"
            f"- things they like: {', '.join(spec.interests)}\n"
            f"- games to include: {game_list}\n"
            + (f"- food to avoid: {', '.join(spec.food_exclusions)}\n" if spec.food_exclusions else "")
            + "\n"
            "Rules, all of them important:\n"
            "1. NEVER name a place, a street, a landmark or a business. Not in any field.\n"
            "2. Hints must be things a person can see, smell or hear on the way. They must\n"
            "   be useful without giving the destination away.\n"
            '3. "hints" is one entry per waypoint, with waypoint_idx counting from 0.\n'
            "4. Game tasks are short, concrete and fun to photograph. Give each one an\n"
            '   "item_key": one lowercase word.\n'
            "5. Keep every field inside its length limit.\n"
            "6. The agent_greeting is one friendly sentence, spoken aloud.\n"
            "7. Answer with JSON only."
        )

    def _assemble(
        self,
        spec: TripSpec,
        best: ScoredCandidate,
        copy: PlanCopyDraft,
        players: Sequence[str],
        game_types: Sequence[str],
        used_template: bool,
        reason: str,
    ) -> Plan:
        """Build the ``Plan``. The code owns every factual field."""
        place = best.place
        start_lat = spec.start_lat or place.lat
        start_lng = spec.start_lng or place.lng

        hints = [hint.text for hint in copy.hints] or templates.DEFAULT_HINTS
        waypoints = templates.build_waypoints(spec, start_lat, start_lng, place, hints)
        for hint, waypoint in zip(copy.hints, waypoints):
            waypoint.hint = hint.text

        games: List[PlanGame] = []
        drafts: List[GameDraft] = list(copy.games)
        if not drafts:
            drafts = [templates.default_game_draft(game_types[0], players, spec)]
        for draft in drafts[:3]:
            games.append(
                PlanGame(
                    type=GameType(draft.type),
                    tasks=[task.text for task in draft.tasks],
                    duration_min=draft.duration_min,
                    approval=draft.approval,  # type: ignore[arg-type]
                    win_condition=draft.win_condition,  # type: ignore[arg-type]
                    n=draft.n,
                )
            )

        schedule = templates.build_schedule(spec, drafts, hints)
        for extra in copy.schedule_hints:
            schedule.append(
                ScheduleEvent(
                    at_minutes=extra.at_minutes,
                    event=ScheduleEventKind.HINT,
                    text=extra.text,
                )
            )

        safety_report = best.safety
        distance_km = (best.route.distance_m if best.route else place.distance_m) / 1000.0

        plan = Plan(
            spec=spec,
            title=copy.title,
            description=copy.description,
            destination=PlanDestination(
                poi_id=place.poi_id,
                name=place.name,
                lat=place.lat,
                lng=place.lng,
                category=templates._category(place.category),
                radius_m=60.0,
                reveal=RevealRule.ON_ARRIVAL,
                secret=True,
            ),
            waypoints=waypoints,
            schedule=schedule,
            games=games,
            tasks_per_member=copy.tasks_per_member or templates.default_tasks_per_member(players, spec),
            safety=PlanSafety(
                public_place=bool(safety_report.public_place) if safety_report else True,
                arrival_before_sunset=bool(safety_report.arrival_before_sunset) if safety_report else True,
                emergency_contact_required=True,
                daylight_warning=None
                if not safety_report or safety_report.arrival_before_sunset
                else "Arrival is projected after dark; consider starting earlier.",
            ),
            fallback=PlanFallback(
                type="llm" if not used_template else "template",
                used=used_template,
                reason=reason,
            ),
            route_km=distance_km,
            plannable=True,
        )
        # Attach the parts the pydantic Plan has no field for, so the UI can show
        # them without a second model.
        plan_copy_note = copy.vibe_line
        logger.info(
            "plan built: %s (%.2f km, %d waypoints, %d games, template=%s) - %s",
            plan.title, plan.route_km, len(plan.waypoints), len(plan.games), used_template,
            plan_copy_note,
        )
        return plan

    # ------------------------------------------------------------------ #
    # convenience
    # ------------------------------------------------------------------ #
    async def plan_from_text(self, text: str, **kwargs: Any) -> PlanOutcome:
        return await self.plan(PlanRequest(text=text, **kwargs))

    async def plan_from_spec(self, spec: TripSpec, **kwargs: Any) -> PlanOutcome:
        return await self.plan(PlanRequest(spec=spec, **kwargs))

    def status(self) -> Dict[str, Any]:
        return {
            "places_provider": getattr(self.places, "name", "?"),
            "routing_provider": getattr(self.routing, "name", "?"),
            "reviews_provider": getattr(self.reviews, "name", "?"),
            "social_enabled": self.social.enabled,
            "router": self.router.status() if self.router else None,
            "last_candidates": [candidate.to_dict() for candidate in self.last_candidates[:3]],
        }
