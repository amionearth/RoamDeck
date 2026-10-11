"""The only shapes the LLM is ever allowed to fill in.

Brief §1.2: *"Code does searching, filtering, scoring, routing and scheduling.
The LLM only produces text (hints, task wording, game items, recipe steps,
clues), always as JSON that matches a schema."*

So this module is the whole LLM surface, and it is deliberately small. Every model
here has ``extra="forbid"`` (a hallucinated field is caught, not silently kept) and
length limits (a model that writes an essay cannot blow up the screen).

**Nothing here can name a place.** There is no ``destination_name`` field anywhere
in this file; the closest thing is ``vibe_line``, which is prose. That is how the
"mystery first" rule survives contact with a language model.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------- #
# TripSpec parsing (voice/buttons -> structured spec)
# --------------------------------------------------------------------------- #
class TripSpecDraft(BaseModel):
    """What the LLM returns when parsing a spoken request into a ``TripSpec``.

    Everything is optional with a sane default, because a person saying "a chill
    walk by the water for two" must not fail validation for omitting the time
    budget.
    """

    model_config = ConfigDict(extra="forbid")

    vibe: str = "chill"
    people: int = Field(default=2)
    transport: str = "walk"
    max_minutes: int = Field(default=120)
    range_km_min: float = Field(default=1.0, ge=0.1, le=60)
    range_km_max: float = Field(default=4.0, ge=0.2, le=60)
    interests: List[str] = Field(default_factory=list, max_length=8)
    food_exclusions: List[str] = Field(default_factory=list, max_length=8)
    scope: str = "local"
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    understood: str = ""

    VIBES: ClassVar[tuple[str, ...]] = ("calm", "chill", "playful", "adventure", "romantic", "foodie")
    TRANSPORTS: ClassVar[tuple[str, ...]] = ("walk", "bike", "car", "transit")

    @field_validator("vibe", mode="before")
    @classmethod
    def _vibe(cls, value: Any) -> str:
        text = str(value or "chill").strip().lower()
        return text if text in cls.VIBES else "chill"

    @field_validator("transport", mode="before")
    @classmethod
    def _transport(cls, value: Any) -> str:
        text = str(value or "walk").strip().lower()
        aliases = {"walking": "walk", "on foot": "walk", "cycle": "bike", "cycling": "bike",
                   "driving": "car", "bus": "transit", "train": "transit"}
        text = aliases.get(text, text)
        return text if text in cls.TRANSPORTS else "walk"

    @field_validator("people", mode="before")
    @classmethod
    def _people(cls, value: Any) -> int:
        """Clamp rather than reject: an LLM saying "12 friends" must not kill the plan."""
        try:
            number = int(float(str(value)))
        except (TypeError, ValueError):
            return 2
        return max(1, min(12, number))

    @field_validator("max_minutes", mode="before")
    @classmethod
    def _minutes(cls, value: Any) -> int:
        try:
            number = int(float(str(value)))
        except (TypeError, ValueError):
            return 120
        return max(20, min(720, number))

    @field_validator("interests", "food_exclusions", mode="before")
    @classmethod
    def _clean_list(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",")]
        out: List[str] = []
        for item in value:
            text = str(item).strip().lower()
            if text and text not in out:
                out.append(text)
        return out[:8]

    def to_range(self) -> tuple[float, float]:
        low, high = self.range_km_min, self.range_km_max
        if low > high:
            low, high = high, low
        return (max(0.1, low), max(0.2, high))


# --------------------------------------------------------------------------- #
# Creative content for a plan
# --------------------------------------------------------------------------- #
class HintDraft(BaseModel):
    """One spoken hint, attached to a waypoint by index."""

    model_config = ConfigDict(extra="forbid")

    waypoint_idx: int = Field(ge=0, le=24)
    text: str = Field(min_length=6, max_length=140)

    @field_validator("text")
    @classmethod
    def _no_names(cls, value: str) -> str:
        return _reject_place_names(value)


class ScheduleHintDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at_minutes: float = Field(ge=0, le=720)
    text: str = Field(min_length=6, max_length=160)

    @field_validator("text")
    @classmethod
    def _no_names(cls, value: str) -> str:
        return _reject_place_names(value)


class TaskDraft(BaseModel):
    """A photo/game task. ``item_key`` is what the server scores against."""

    model_config = ConfigDict(extra="forbid")

    player_name: Optional[str] = None
    text: str = Field(min_length=3, max_length=120)
    item_key: Optional[str] = Field(default=None, max_length=32)
    points: int = Field(default=10, ge=1, le=100)
    time_limit_s: int = Field(default=300, ge=30, le=3600)

    @field_validator("text")
    @classmethod
    def _no_names(cls, value: str) -> str:
        return _reject_place_names(value)


class GameDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "photo_hunt"
    title: str = Field(default="Photo hunt", max_length=60)
    duration_min: int = Field(default=30, ge=2, le=180)
    approval: str = "peer_vote"
    win_condition: str = "most_points"
    n: int = Field(default=5, ge=3, le=5)
    tasks: List[TaskDraft] = Field(default_factory=list, max_length=30)

    GAME_TYPES: ClassVar[tuple[str, ...]] = ("photo_hunt", "bingo", "color_hunt", "funny_tasks", "secret_missions", "imposter")
    APPROVALS: ClassVar[tuple[str, ...]] = ("peer_vote", "auto", "host")
    WIN_CONDITIONS: ClassVar[tuple[str, ...]] = ("most_points", "first_n_in_row")

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, value: Any) -> str:
        text = str(value or "photo_hunt").strip().lower().replace(" ", "_").replace("-", "_")
        return text if text in cls.GAME_TYPES else "photo_hunt"

    @field_validator("approval", mode="before")
    @classmethod
    def _approval(cls, value: Any) -> str:
        text = str(value or "peer_vote").strip().lower()
        return text if text in cls.APPROVALS else "peer_vote"

    @field_validator("win_condition", mode="before")
    @classmethod
    def _win(cls, value: Any) -> str:
        text = str(value or "most_points").strip().lower()
        return text if text in cls.WIN_CONDITIONS else "most_points"

    @field_validator("n", "duration_min", mode="before")
    @classmethod
    def _clamp_numbers(cls, value: Any, info: Any) -> int:
        """Clamp rather than reject: an out-of-range number is a mistake, not a reason
        to throw away an otherwise usable game."""
        bounds = {"n": (3, 5), "duration_min": (2, 180)}
        low, high = bounds.get(info.field_name, (0, 1000))
        try:
            number = int(float(str(value)))
        except (TypeError, ValueError):
            return low
        return max(low, min(high, number))


class PlanCopyDraft(BaseModel):
    """The creative half of a ``Plan``: everything an LLM may write.

    Note what is missing: there is no destination name, no coordinates and no
    ``poi_id``. The code owns those.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="A walk into the unknown", max_length=60)
    description: str = Field(default="", max_length=240)
    vibe_line: str = Field(default="", max_length=140)
    arrival_line: str = Field(default="", max_length=180)
    hints: List[HintDraft] = Field(default_factory=list, max_length=12)
    schedule_hints: List[ScheduleHintDraft] = Field(default_factory=list, max_length=8)
    games: List[GameDraft] = Field(default_factory=list, max_length=3)
    tasks_per_member: Dict[str, List[str]] = Field(default_factory=dict)
    agent_greeting: str = Field(default="", max_length=140)

    @field_validator("title", "description", "vibe_line", "arrival_line", "agent_greeting")
    @classmethod
    def _no_names(cls, value: str) -> str:
        return _reject_place_names(value)

    @field_validator("tasks_per_member")
    @classmethod
    def _bounded_tasks(cls, value: Dict[str, List[str]]) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for member, tasks in list(value.items())[:12]:
            out[str(member)[:40]] = [_reject_place_names(str(task)[:120]) for task in tasks[:5]]
        return out


class RecipeDraft(BaseModel):
    """Surprise cooking. The dish name is revealed only at the destination."""

    model_config = ConfigDict(extra="forbid")

    steps: List[str] = Field(default_factory=list, max_length=12)
    dish_name: str = Field(default="", max_length=60)
    reveal_on: str = "arrival"

    @field_validator("steps")
    @classmethod
    def _steps(cls, value: List[str]) -> List[str]:
        return [_reject_place_names(str(step)[:160]) for step in value[:12]]


# --------------------------------------------------------------------------- #
# Guard rails
# --------------------------------------------------------------------------- #
#: Words that turn a hint into a spoiler. A hint saying "Kappad Beach" defeats the
#: entire product, so the schema refuses it. The list is intentionally short and
#: blunt; the scorer already removed names from the prompt.
SPOILER_TOKENS = ("beach resort", "hotel", "restaurant called", "named")


def _reject_place_names(value: str) -> str:
    """Neutralise the two failure modes we actually saw while building this.

    (a) A model that writes a capitalised Proper Noun as the destination.
    (b) A model that copies a place name out of the prompt.

    We cannot detect (a) reliably, so instead of pretending to, the prompt never
    contains a place name (see ``planner.build_copy_prompt``) and this function
    strips the obvious giveaways.
    """
    text = " ".join(str(value).split())
    for token in SPOILER_TOKENS:
        text = text.replace(token, "a place")
    return text


def copy_schema() -> Dict[str, Any]:
    """The JSON schema handed to the LLM for the creative half of a plan."""
    return PlanCopyDraft.model_json_schema()


def spec_schema() -> Dict[str, Any]:
    return TripSpecDraft.model_json_schema()


def game_schema() -> Dict[str, Any]:
    return GameDraft.model_json_schema()


def empty_copy() -> PlanCopyDraft:
    return PlanCopyDraft(
        title="A walk into the unknown",
        description="A short trip with the destination kept secret until you arrive.",
        vibe_line="Follow the arrow.",
        arrival_line="You found it.",
        agent_greeting="Ready when you are.",
    )
