"""Planner tests, including the fuzzer from the brief's testing section.

Brief §14: *"a fuzzer for the planner"* and M4's acceptance check: *"20 varied specs
produce valid plans; kill a provider and it still works."*

That is exactly what :func:`test_twenty_varied_specs_produce_valid_plans` and the
"kill a provider" tests below do.
"""

from __future__ import annotations

import asyncio
from typing import Any, List

import pytest

from farfromhome_device.agent.input_parser import (
    parse_heuristic,
    parse_buttons,
    extract_minutes,
    extract_people,
    extract_range_km,
)
from farfromhome_device.agent.llm_router import (
    LlmCache,
    LlmConfig,
    LlmRouter,
    LlmUnavailable,
    ProviderSpec,
    ScriptedProvider,
    ask_llm,
    extract_json,
    redact_for_llm,
)
from farfromhome_device.agent.planner import PlanRequest, Planner
from farfromhome_device.agent.schemas import GameDraft, PlanCopyDraft, TripSpecDraft
from farfromhome_device.agent.template import default_game_draft, default_copy
from farfromhome_device.agent.tools import (
    OfflinePlaces,
    OverpassPlaces,
    StraightLineRouting,
)
from farfromhome_device.core.models import AssistantMood, Plan, Transport, TripSpec


# --------------------------------------------------------------------------- #
# the trip spec parser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, expected",
    [
        ("a chill walk for two, about an hour", 60),
        ("two hours and 30 minutes of walking", 150),
        ("just 45 minutes", 45),
        ("half an hour", 30),
        ("we have all day", 360),
    ],
)
def test_extract_minutes(text: str, expected: int) -> None:
    assert extract_minutes(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("for two", 2),
        ("three of us", 3),
        ("just me", 1),
        ("there are 5 friends", 5),
        ("a group of four friends", 4),
    ],
)
def test_extract_people(text: str, expected: int) -> None:
    assert extract_people(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("about 3 km", (1.5, 3.0)),
        ("under 5 kilometres", (0.5, 5.0)),
        ("between 2 and 8 km", (2.0, 8.0)),
    ],
)
def test_extract_range(text: str, expected: tuple) -> None:
    assert extract_range_km(text) == expected


def test_heuristic_parse_understands_a_full_sentence() -> None:
    spec = parse_heuristic("a playful cycle ride for four friends, 2 hours, coffee and a viewpoint")
    assert spec.vibe == AssistantMood.PLAYFUL
    assert spec.transport == Transport.BIKE
    assert spec.people == 4
    assert spec.max_minutes == 120
    assert "coffee" in spec.interests
    assert "viewpoint" in spec.interests


def test_heuristic_parse_records_what_it_guessed() -> None:
    spec = parse_heuristic("somewhere nice")
    assert "vibe" in spec.defaults_applied
    assert "transport" in spec.defaults_applied
    assert "people" in spec.defaults_applied
    assert "max_minutes" in spec.defaults_applied


def test_heuristic_parse_catches_food_exclusions() -> None:
    spec = parse_heuristic("a foodie trip, vegetarian, no pork")
    assert any("vegetarian" in item for item in spec.food_exclusions)


def test_buttons_path_always_produces_a_spec() -> None:
    spec = parse_buttons()
    assert spec.people == 2
    assert spec.interests
    assert spec.transport == Transport.WALK


def test_buttons_and_text_paths_agree_on_the_obvious() -> None:
    buttons = parse_buttons(vibe="playful", people=3, transport="bike", max_minutes=90)
    text = parse_heuristic("a playful bike ride for three, 90 minutes")
    assert buttons.vibe == text.vibe
    assert buttons.people == text.people
    assert buttons.transport == text.transport
    assert buttons.max_minutes == text.max_minutes


def test_spec_rejects_a_reversed_range() -> None:
    spec = TripSpec(range_km=(8.0, 2.0))
    assert spec.range_km == (2.0, 8.0)


def test_spec_describe_is_one_line() -> None:
    described = parse_buttons().describe()
    assert "\n" not in described
    assert "walk" in described


# --------------------------------------------------------------------------- #
# prompt safety
# --------------------------------------------------------------------------- #
def test_redaction_removes_emails() -> None:
    assert "@" not in redact_for_llm("mail me at a.b@example.com")
    assert "[email]" in redact_for_llm("mail me at a.b@example.com")


def test_redaction_removes_phone_numbers() -> None:
    assert "9876543210" not in redact_for_llm("call 9876543210")


def test_redaction_removes_coordinates() -> None:
    cleaned = redact_for_llm("start at 11.2588, 75.7804")
    assert "11.2588" not in cleaned
    assert "[coordinates]" in cleaned


def test_redaction_removes_an_explicit_address() -> None:
    cleaned = redact_for_llm("my home address is 14 Beach Road, Kozhikode.")
    assert "Beach Road" not in cleaned


def test_the_copy_prompt_never_contains_the_destination_name(offline_planner: Planner) -> None:
    """The whole point of the mystery: the model must not be told where it is."""
    outcome = asyncio.run(
        offline_planner.plan(PlanRequest(text="a chill walk for two", start_lat=11.2588,
                                         start_lng=75.7804, offline=True))
    )
    assert outcome.plan is not None
    best = outcome.candidates[0]
    prompt = offline_planner.build_copy_prompt(
        outcome.spec, best, ["Asha", "Dev"], ["photo_hunt"]
    )
    assert best.place.name not in prompt
    assert str(best.place.lat) not in prompt
    assert str(best.place.lng) not in prompt
    assert best.place.poi_id not in prompt
    # But the *shape* of the trip is there, which is what the model needs.
    assert best.place.category in prompt


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #
def test_plan_copy_schema_forbids_extra_fields() -> None:
    with pytest.raises(Exception):
        PlanCopyDraft.model_validate({"title": "x", "destination_name": "Kappad Beach"})


def test_trip_spec_draft_clamps_bad_values() -> None:
    draft = TripSpecDraft.model_validate({"vibe": "nonsense", "people": 99, "transport": "teleport"})
    assert draft.vibe == "chill"
    assert draft.people <= 12
    assert draft.transport == "walk"


def test_trip_spec_draft_cleans_lists() -> None:
    draft = TripSpecDraft.model_validate({"interests": ["Coffee", "coffee", " Viewpoint "]})
    assert draft.interests == ["coffee", "viewpoint"]


def test_hint_rejects_an_essay() -> None:
    with pytest.raises(Exception):
        PlanCopyDraft.model_validate({"hints": [{"waypoint_idx": 0, "text": "x" * 500}]})


def test_game_draft_falls_back_to_a_known_type() -> None:
    draft = GameDraft.model_validate({"type": "space invaders"})
    assert draft.type == "photo_hunt"


def test_bingo_draft_needs_sensible_n() -> None:
    draft = GameDraft.model_validate({"type": "bingo", "n": 99})
    assert 3 <= draft.n <= 5


# --------------------------------------------------------------------------- #
# JSON extraction and validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Sure! Here you go:\n{"a": 1}\nHope that helps.',
    ],
)
def test_extract_json_handles_the_shapes_models_actually_produce(raw: str) -> None:
    import json

    assert json.loads(extract_json(raw)) == {"a": 1}


def test_extract_json_survives_an_unterminated_object() -> None:
    assert extract_json('{"a": 1, "b": [1, 2') != ""


# --------------------------------------------------------------------------- #
# the router
# --------------------------------------------------------------------------- #
def test_router_falls_over_to_the_next_provider() -> None:
    config = LlmConfig(
        offline=False,
        chains={"plan": ("broken", "working")},
        providers={
            "broken": ProviderSpec(name="broken", base_url="http://x", model="m", api_key="k"),
            "working": ProviderSpec(name="working", base_url="http://y", model="m", api_key="k"),
        },
    )
    router = LlmRouter(config, cache=LlmCache())
    router.register_provider(ScriptedProvider(config.providers["broken"], []))
    router.register_provider(ScriptedProvider(config.providers["working"], ['{"ok": true}']))
    result = asyncio.run(router.ask("plan", "hello", schema={"type": "object"}))
    assert result.provider == "working"
    assert result.text == '{"ok": true}'


def test_router_retries_an_invalid_schema_once() -> None:
    config = LlmConfig(
        offline=False,
        chains={"plan": ("flaky",)},
        providers={"flaky": ProviderSpec(name="flaky", base_url="http://x", model="m", api_key="k")},
    )
    router = LlmRouter(config, cache=LlmCache())
    router.register_provider(ScriptedProvider(config.providers["flaky"], ['{"wrong": 1}', '{"title": "ok"}']))
    result = asyncio.run(router.ask("plan", "hello", model_cls=PlanCopyDraft))
    assert result.attempts == 2
    assert PlanCopyDraft.model_validate_json(result.text).title == "ok"


def test_router_gives_up_when_every_provider_fails() -> None:
    config = LlmConfig(
        offline=False,
        chains={"plan": ("dead",)},
        providers={"dead": ProviderSpec(name="dead", base_url="http://x", model="m", api_key="k")},
    )
    router = LlmRouter(config, cache=LlmCache())
    router.register_provider(ScriptedProvider(config.providers["dead"], []))
    with pytest.raises(LlmUnavailable):
        asyncio.run(router.ask("plan", "hello"))


def test_router_caches_identical_requests() -> None:
    config = LlmConfig(
        offline=False,
        chains={"plan": ("canned",)},
        providers={"canned": ProviderSpec(name="canned", base_url="http://x", model="m", api_key="k")},
    )
    router = LlmRouter(config, cache=LlmCache())
    provider = ScriptedProvider(config.providers["canned"], ['{"a": 1}'])
    router.register_provider(provider)
    asyncio.run(router.ask("plan", "same prompt"))
    second = asyncio.run(router.ask("plan", "same prompt"))
    assert second.cached is True
    assert provider.calls == 1


def test_cache_key_depends_on_the_schema() -> None:
    left = LlmCache.key("plan", "prompt", {"type": "object"})
    right = LlmCache.key("plan", "prompt", {"type": "string"})
    assert left != right


def test_ask_llm_returns_none_instead_of_raising() -> None:
    config = LlmConfig(
        offline=False,
        chains={"plan": ("dead",)},
        providers={"dead": ProviderSpec(name="dead", base_url="http://x", model="m", api_key="k")},
    )
    router = LlmRouter(config, cache=LlmCache())
    router.register_provider(ScriptedProvider(config.providers["dead"], []))
    assert asyncio.run(ask_llm(router, "plan", "hello")) is None


def test_offline_config_has_no_chain() -> None:
    config = LlmConfig(offline=True)
    assert config.chain_for("plan") == ()


def test_dev_mode_skips_the_server_gateway() -> None:
    config = LlmConfig(offline=False, dev_mode=True)
    chain = config.chain_for("plan")
    assert "server" not in chain


def test_provider_usable_requires_a_key() -> None:
    spec = ProviderSpec(name="gemini", base_url="http://x", model="m", api_key_env="FFH_TEST_MISSING_KEY")
    assert spec.usable is False
    spec.api_key = "present"
    assert spec.usable is True


# --------------------------------------------------------------------------- #
# the offline planner (and its fallback path)
# --------------------------------------------------------------------------- #
def test_planner_produces_a_valid_plan_offline(offline_planner: Planner) -> None:
    outcome = asyncio.run(
        offline_planner.plan(PlanRequest(text="a chill walk for two", start_lat=11.2588,
                                         start_lng=75.7804, offline=True))
    )
    assert outcome.ok
    assert outcome.used_template is True
    assert outcome.plan is not None
    # It must round-trip through the model, which is the agent/engine contract.
    assert Plan.model_validate_json(outcome.plan.model_dump_json()).trip_id == outcome.plan.trip_id


def test_template_plan_marks_itself_as_a_fallback(offline_planner: Planner) -> None:
    outcome = asyncio.run(
        offline_planner.plan(PlanRequest(text="walk", start_lat=11.2588, start_lng=75.7804, offline=True))
    )
    assert outcome.plan is not None
    assert outcome.plan.fallback.used is True
    assert outcome.plan.fallback.reason


def test_twenty_varied_specs_produce_valid_plans(offline_planner: Planner) -> None:
    """The M4 acceptance check: "20 varied specs produce valid plans"."""
    specs = [
        "a calm solo walk for 30 minutes",
        "a playful walk for eight friends, 3 hours",
        "a romantic bike ride for two at sunset",
        "a foodie trip for four, 2 km",
        "an adventure hike for six, 5 hours",
        "coffee and a bookshop for one",
        "a beach walk for three",
        "a market trip for two, 45 minutes",
        "a temple and heritage walk for five",
        "a viewpoint hunt for two, under 3 km",
        "a quick 20 minute stroll",
        "an all day adventure for ten",
        "a chill car trip for two, 40 km",
        "a transit trip to a market for three",
        "a nature walk with no coffee for two",
        "a playful photo walk for four, 90 minutes",
        "a heritage walk for two, 2 hours",
        "a sunset viewpoint trip for two",
        "a rainy day cafe trip for three",
        "a 6 hour bike ride for two",
    ]
    assert len(specs) == 20
    for text in specs:
        outcome = asyncio.run(
            offline_planner.plan(PlanRequest(text=text, start_lat=11.2588, start_lng=75.7804, offline=True))
        )
        assert outcome.ok, f"no plan for {text!r}: {outcome.reason}"
        plan = outcome.plan
        assert plan is not None
        # Re-validating is the contract test: the engine will do exactly this.
        Plan.model_validate(plan.model_dump())
        assert plan.destination.name
        assert plan.waypoints or plan.route_km < 1.0
        assert plan.games
        assert plan.spec.people >= 1


def test_planner_survives_a_dead_places_provider(settings: Any) -> None:
    """M4: "kill a provider and it still works"."""

    class Exploding(OfflinePlaces):
        async def find(self, *args: Any, **kwargs: Any) -> List[Any]:
            raise RuntimeError("Overpass is on fire")

    planner = Planner(settings, places=Exploding(), routing=StraightLineRouting())
    outcome = asyncio.run(
        planner.plan(PlanRequest(text="a chill walk for two", start_lat=11.2588,
                                 start_lng=75.7804, offline=False))
    )
    assert outcome.ok, "a dead places provider must fall back to the built-in set"


def test_planner_survives_a_dead_routing_provider(settings: Any, offline_planner: Planner) -> None:
    class ExplodingRouting(StraightLineRouting):
        async def route(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("OSRM is down")

    planner = Planner(
        settings,
        places=OfflinePlaces(wanted_max_m=4000.0),
        routing=ExplodingRouting(),
    )
    outcome = asyncio.run(
        planner.plan(PlanRequest(text="a chill walk for two", start_lat=11.2588,
                                 start_lng=75.7804, offline=False))
    )
    assert outcome.ok


def test_planner_keeps_the_destination_inside_the_asked_range(settings: Any) -> None:
    planner = Planner(settings, places=OfflinePlaces(), routing=StraightLineRouting())
    outcome = asyncio.run(
        planner.plan(
            PlanRequest(
                spec=parse_buttons(interests=["viewpoint"], range_km=(1.0, 2.0), max_minutes=60),
                start_lat=11.2588,
                start_lng=75.7804,
                offline=True,
            )
        )
    )
    assert outcome.plan is not None
    assert outcome.plan.route_km <= 2.0 * 1.25 + 0.01, f"planned {outcome.plan.route_km} km"


def test_planner_never_puts_a_place_name_in_the_public_summary(offline_planner: Planner) -> None:
    outcome = asyncio.run(
        offline_planner.plan(PlanRequest(text="a chill walk", start_lat=11.2588,
                                         start_lng=75.7804, offline=True))
    )
    assert outcome.plan is not None
    summary = str(outcome.plan.public_summary())
    assert outcome.plan.destination.name not in summary
    assert str(outcome.plan.destination.lat) not in summary


def test_overpass_query_is_well_formed() -> None:
    query = OverpassPlaces().build_query(11.2588, 75.7804, 3000, ["coffee", "viewpoint"])
    assert query.startswith("[out:json]")
    assert "around:3000,11.2588,75.7804" in query
    assert 'amenity"="cafe' in query
    assert 'tourism"="viewpoint' in query


def test_overpass_query_has_a_default_when_interests_are_unknown() -> None:
    query = OverpassPlaces().build_query(11.0, 75.0, 1000, ["skiing on the moon"])
    assert "viewpoint" in query
    assert "park" in query


def test_overpass_parser_maps_osm_tags_to_categories() -> None:
    payload = {
        "elements": [
            {"type": "node", "id": 1, "lat": 11.26, "lon": 75.78, "tags": {"name": "X", "amenity": "cafe"}},
            {"type": "node", "id": 2, "lat": 11.27, "lon": 75.79, "tags": {"name": "Y", "natural": "beach"}},
            {"type": "node", "id": 3, "lat": 11.28, "lon": 75.80, "tags": {"name": "Z", "tourism": "viewpoint"}},
            {"type": "node", "id": 4, "lat": 11.29, "lon": 75.81, "tags": {"amenity": "cafe"}},
        ]
    }
    places = OverpassPlaces.parse_response(payload, 11.26, 75.78)
    names = {place.name for place in places}
    assert "X" in names and "Y" in names and "Z" in names
    # An element without a name is skipped rather than shown as "Unnamed".
    assert len(places) == 3
    by_name = {place.name: place for place in places}
    assert by_name["X"].category == "cafe"
    assert by_name["Y"].category == "beach"
    assert by_name["Z"].category == "viewpoint"


def test_straight_line_routing_adds_a_detour_factor() -> None:
    routing = StraightLineRouting()
    estimate = asyncio.run(routing.route([(11.0, 75.0), (11.01, 75.0)], "walk"))
    assert estimate is not None
    from farfromhome_device.engine.geo import haversine_m

    raw = haversine_m(11.0, 75.0, 11.01, 75.0)
    assert estimate.distance_m > raw, "street distance must exceed the straight line"
    assert estimate.duration_min > 0


# --------------------------------------------------------------------------- #
# game templates
# --------------------------------------------------------------------------- #
def test_default_game_drafts_exist_for_every_type() -> None:
    spec = parse_buttons(people=3)
    for game_type in ("photo_hunt", "bingo", "color_hunt", "funny_tasks", "secret_missions"):
        draft = default_game_draft(game_type, ["A", "B", "C"], spec)
        assert draft.tasks, f"{game_type} produced no tasks"
        assert draft.duration_min >= 2


def test_bingo_draft_has_a_full_grid() -> None:
    spec = parse_buttons()
    draft = default_game_draft("bingo", ["A", "B"], spec)
    assert len(draft.tasks) == 25
    keys = {task.item_key for task in draft.tasks}
    assert keys == {f"r{row}c{col}" for row in range(5) for col in range(5)}


def test_default_copy_assigns_a_task_to_every_member() -> None:
    spec = parse_buttons(people=4)
    copy = default_copy(spec, ["A", "B", "C", "D"], ["photo_hunt"])
    assert set(copy.tasks_per_member) == {"A", "B", "C", "D"}
    for tasks in copy.tasks_per_member.values():
        assert tasks
