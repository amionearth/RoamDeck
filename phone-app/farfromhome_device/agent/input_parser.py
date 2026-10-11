"""Turning what the user said or tapped into a validated ``TripSpec``.

Brief §5.1: *"Voice input goes through speech-to-text then an LLM call that fills
``TripSpec`` (with a button-based fallback UI)."*

So there are three layers, in order:

1. :func:`parse_buttons` — a ``TripSpec`` built from the on-screen choices. This
   always works, offline, with no model.
2. :func:`parse_heuristic` — a keyword parser over the transcribed text. Works
   offline and is what the tests assert against, so the button path and the voice
   path agree on obvious phrases.
3. :func:`parse_with_llm` — the LLM fills :class:`TripSpecDraft`; the result is
   merged on top of the heuristic result, so a vague answer cannot lose the
   fields the user set by hand.

Every default that gets filled in is recorded in ``TripSpec.defaults_applied`` so
the UI can say *"I assumed 2 hours — change it?"* rather than silently guessing.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.models import AssistantMood, Transport, TripSpec
from .llm_router import LlmRouter, ask_llm
from .schemas import TripSpecDraft, spec_schema

logger = logging.getLogger("ffh.agent.input")


DEFAULT_INTERESTS = ["coffee", "viewpoint"]

#: Keyword -> interest. Ordered longest-first at match time so "coffee shop" wins
#: over "shop".
INTEREST_KEYWORDS: Dict[str, str] = {
    "coffee": "coffee", "cafe": "coffee", "espresso": "coffee", "chai": "coffee",
    "tea": "coffee", "bakery": "coffee",
    "food": "food", "eat": "food", "lunch": "food", "dinner": "food", "snack": "food",
    "restaurant": "food", "breakfast": "food", "street food": "food",
    "nature": "nature", "forest": "nature", "trees": "nature", "green": "nature",
    "park": "park", "garden": "park",
    "view": "viewpoint", "viewpoint": "viewpoint", "sunset": "viewpoint",
    "sunrise": "viewpoint", "lookout": "viewpoint", "hill": "viewpoint",
    "beach": "beach", "sea": "beach", "shore": "beach", "coast": "beach",
    "water": "beach", "waves": "beach",
    "market": "market", "bazaar": "market", "shopping": "market",
    "temple": "temple", "church": "temple", "mosque": "temple", "shrine": "temple",
    "history": "heritage", "historic": "heritage", "old": "heritage", "ruins": "heritage",
    "museum": "museum", "art": "museum", "gallery": "museum",
    "bar": "bar", "beer": "bar", "drink": "bar",
    "book": "bookshop", "books": "bookshop", "library": "bookshop",
}

MOOD_KEYWORDS: Dict[str, AssistantMood] = {
    "calm": AssistantMood.CALM, "quiet": AssistantMood.CALM, "peaceful": AssistantMood.CALM,
    "chill": AssistantMood.CHILL, "relaxed": AssistantMood.CHILL, "easy": AssistantMood.CHILL,
    "playful": AssistantMood.PLAYFUL, "fun": AssistantMood.PLAYFUL, "silly": AssistantMood.PLAYFUL,
    "adventure": AssistantMood.ADVENTURE, "adventurous": AssistantMood.ADVENTURE,
    "explore": AssistantMood.ADVENTURE, "wild": AssistantMood.ADVENTURE,
    "romantic": AssistantMood.ROMANTIC, "date": AssistantMood.ROMANTIC,
    "foodie": AssistantMood.FOODIE, "hungry": AssistantMood.FOODIE,
}

TRANSPORT_KEYWORDS: Dict[str, Transport] = {
    "walk": Transport.WALK, "walking": Transport.WALK, "on foot": Transport.WALK,
    "stroll": Transport.WALK, "wander": Transport.WALK,
    "bike": Transport.BIKE, "bicycle": Transport.BIKE, "cycle": Transport.BIKE,
    "cycling": Transport.BIKE, "scooter": Transport.BIKE,
    "car": Transport.CAR, "drive": Transport.CAR, "driving": Transport.CAR,
    "taxi": Transport.CAR, "auto": Transport.CAR,
    "bus": Transport.TRANSIT, "train": Transport.TRANSIT, "transit": Transport.TRANSIT,
    "metro": Transport.TRANSIT, "ferry": Transport.TRANSIT,
}

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "a couple": 2, "couple": 2, "pair": 2, "three of us": 3, "just me": 1,
    "solo": 1, "alone": 1, "myself": 1, "we": 2, "us": 2,
}

#: Regex alternation over the word numbers, longest first so "a couple" wins over
#: "couple" and no phrase is shadowed by a shorter one.
WORD_NUMBER_PATTERN = "|".join(
    re.escape(word) for word in sorted(WORD_NUMBERS, key=len, reverse=True)
)

FOOD_EXCLUSION_KEYWORDS = (
    "vegetarian", "vegan", "no meat", "no pork", "no beef", "no dairy",
    "gluten", "nut allergy", "peanut", "no seafood", "jain", "halal", "kosher",
)


def _find_first(text: str, mapping: Dict[str, Any], default: Any = None) -> Any:
    """Longest keyword wins, so "coffee shop" beats "shop"."""
    lowered = f" {text.lower()} "
    for keyword in sorted(mapping, key=len, reverse=True):
        if f" {keyword}" in lowered:
            return mapping[keyword]
    return default


# --------------------------------------------------------------------------- #
# 1. Buttons
# --------------------------------------------------------------------------- #
def parse_buttons(
    vibe: str = "chill",
    people: int = 2,
    transport: str = "walk",
    max_minutes: int = 120,
    interests: Optional[Sequence[str]] = None,
    food_exclusions: Optional[Sequence[str]] = None,
    start_lat: Optional[float] = None,
    start_lng: Optional[float] = None,
    range_km: Tuple[float, float] = (1.0, 4.0),
) -> TripSpec:
    """The always-available path: whatever the user tapped, plus defaults."""
    applied: List[str] = []
    if interests is None:
        interests = list(DEFAULT_INTERESTS)
        applied.append("interests")

    spec = TripSpec(
        vibe=AssistantMood(vibe) if vibe in [mood.value for mood in AssistantMood] else AssistantMood.CHILL,
        people=max(1, min(12, int(people))),
        transport=Transport(transport) if transport in [mode.value for mode in Transport] else Transport.WALK,
        max_minutes=max(20, min(720, int(max_minutes))),
        range_km=(float(range_km[0]), float(range_km[1])),
        interests=[str(item).lower() for item in interests][:8],
        food_exclusions=[str(item).lower() for item in (food_exclusions or [])][:8],
        start_lat=start_lat,
        start_lng=start_lng,
        defaults_applied=applied,
    )
    return spec


# --------------------------------------------------------------------------- #
# 2. Heuristic parse
# --------------------------------------------------------------------------- #
def extract_range_km(text: str) -> Optional[Tuple[float, float]]:
    """Find "2 km", "1 to 3 kilometres", "between 2 and 8 km", "under 5 km"."""
    lowered = text.lower()

    # An explicit interval first: "between 2 and 8 km" mentions its unit once.
    interval = re.search(
        r"(?:between|from)\s+(\d+(?:\.\d+)?)\s*(?:and|to|-)\s*(\d+(?:\.\d+)?)\s*"
        r"(?:km|kilometres?|kilometers?|k\b)?",
        lowered,
    )
    if interval:
        first, second = float(interval.group(1)), float(interval.group(2))
        return (min(first, second), max(first, second))

    matches = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*(?:km|kilometre|kilometer|k\b)", lowered)]
    if not matches:
        # A bare "3 km" never appears as a bare number, but "2 to 8" might omit the
        # unit entirely when the sentence already said kilometres.
        bare = re.search(r"(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)", lowered)
        if bare and ("km" in lowered or "kilometre" in lowered or "kilometer" in lowered):
            first, second = float(bare.group(1)), float(bare.group(2))
            return (min(first, second), max(first, second))
        return None
    if len(matches) >= 2:
        return (min(matches[0], matches[1]), max(matches[0], matches[1]))
    value = matches[0]
    if "under" in lowered or "less than" in lowered or "within" in lowered:
        return (0.5, value)
    return (max(0.5, value * 0.5), value)


WORD_DURATIONS = (
    ("half an hour", 30),
    ("half hour", 30),
    ("an hour and a half", 90),
    ("an hour", 60),
    ("one hour", 60),
    ("all day", 360),
    ("all afternoon", 180),
    ("all morning", 180),
)


def extract_minutes(text: str) -> Optional[int]:
    """Understand "90 minutes", "two hours and 30 minutes", "half an hour"."""
    lowered = text.lower()

    # Word-number durations first: "two hours and 30 minutes" also contains a
    # digit ("30 minutes") which must be added to the hours, not returned alone.
    for phrase, count in WORD_DURATIONS:
        if phrase in lowered:
            return count

    word_hours = re.search(
        rf"\b({WORD_NUMBER_PATTERN})\s*(?:hours?|hrs?)\b", lowered
    )
    if word_hours:
        hours = WORD_NUMBERS.get(word_hours.group(1), 1)
        extra = 0
        word_minutes = re.search(
            rf"(?:and\s+)?({WORD_NUMBER_PATTERN})\s*(?:minutes?|mins?)\b", lowered
        )
        if word_minutes:
            extra = WORD_NUMBERS.get(word_minutes.group(1), 0)
        else:
            digit_minutes = re.search(r"(\d+)\s*(?:minutes?|mins?)\b", lowered)
            if digit_minutes:
                extra = int(digit_minutes.group(1))
        return max(20, hours * 60 + extra)

    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h\b)", lowered)
    minute_match = re.search(r"(\d+)\s*(?:minutes?|mins?|min\b)", lowered)
    if hour_match:
        hours = float(hour_match.group(1))
        extra = int(minute_match.group(1)) if minute_match else 0
        return max(20, int(hours * 60) + extra)
    if minute_match:
        return max(20, int(minute_match.group(1)))
    return None


def extract_people(text: str) -> Optional[int]:
    lowered = text.lower()
    digit = re.search(r"\b(\d{1,2})\s*(?:people|persons?|of us|friends?|mates?|adults?)\b", lowered)
    if digit:
        return max(1, min(12, int(digit.group(1))))
    for phrase, count in WORD_NUMBERS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            return count
    return None


def parse_heuristic(text: str, start_lat: Optional[float] = None,
                    start_lng: Optional[float] = None) -> TripSpec:
    """Offline keyword parse. No model, no network, deterministic."""
    lowered = (text or "").lower()
    applied: List[str] = []

    vibe = _find_first(lowered, MOOD_KEYWORDS, None)
    if vibe is None:
        vibe = AssistantMood.CHILL
        applied.append("vibe")

    transport = _find_first(lowered, TRANSPORT_KEYWORDS, None)
    if transport is None:
        transport = Transport.WALK
        applied.append("transport")

    people = extract_people(lowered)
    if people is None:
        people = 2
        applied.append("people")

    minutes = extract_minutes(lowered)
    if minutes is None:
        minutes = 120
        applied.append("max_minutes")

    interests: List[str] = []
    for keyword in sorted(INTEREST_KEYWORDS, key=len, reverse=True):
        if keyword in lowered:
            interest = INTEREST_KEYWORDS[keyword]
            if interest not in interests:
                interests.append(interest)
    if not interests:
        interests = list(DEFAULT_INTERESTS)
        applied.append("interests")

    exclusions = [token for token in FOOD_EXCLUSION_KEYWORDS if token in lowered]

    range_km = extract_range_km(lowered)
    if range_km is None:
        # Scale the default range with the requested time.
        if transport == Transport.WALK:
            high = max(1.0, minutes / 60.0 * 3.0)
        elif transport == Transport.BIKE:
            high = max(2.0, minutes / 60.0 * 10.0)
        else:
            high = max(5.0, minutes / 60.0 * 25.0)
        range_km = (max(0.5, high * 0.25), high)
        applied.append("range_km")

    return TripSpec(
        vibe=vibe,
        people=people,
        transport=transport,
        max_minutes=minutes,
        range_km=range_km,
        interests=interests[:8],
        food_exclusions=exclusions,
        start_lat=start_lat,
        start_lng=start_lng,
        raw_text=text or "",
        defaults_applied=sorted(set(applied)),
    )


# --------------------------------------------------------------------------- #
# 3. LLM parse
# --------------------------------------------------------------------------- #
def merge_specs(base: TripSpec, draft: TripSpecDraft, raw_text: str = "") -> TripSpec:
    """Overlay the LLM's reading on the heuristic one.

    The heuristic result is the floor: if the model says nothing about transport,
    the keyword result survives. If it contradicts a field the heuristic was
    confident about, the model wins — it understood the sentence, the keyword
    matcher only saw words.
    """
    return TripSpec(
        vibe=AssistantMood(draft.vibe) if draft.vibe in [mood.value for mood in AssistantMood] else base.vibe,
        people=draft.people or base.people,
        transport=Transport(draft.transport) if draft.transport in [mode.value for mode in Transport] else base.transport,
        max_minutes=draft.max_minutes or base.max_minutes,
        range_km=draft.to_range() if (draft.range_km_min or draft.range_km_max) else base.range_km,
        interests=draft.interests or base.interests,
        food_exclusions=draft.food_exclusions or base.food_exclusions,
        start_lat=base.start_lat,
        start_lng=base.start_lng,
        scope=draft.scope or base.scope,
        raw_text=raw_text or base.raw_text,
        defaults_applied=base.defaults_applied,
    )


def build_parse_prompt(text: str, start_lat: Optional[float], start_lng: Optional[float]) -> str:
    """The prompt for the spec parser. Contains **no** names or coordinates."""
    location_hint = ""
    if start_lat is not None and start_lng is not None:
        # We tell the model a location exists, never where it is: brief §5.1
        # "Do not send names, contact details or exact home addresses to LLMs".
        location_hint = "The trip starts wherever the handheld currently is."
    return (
        "Read this request for a short mystery trip and fill the JSON schema.\n"
        "Rules:\n"
        "- Only describe mood, group size, transport, time, distance, interests and food exclusions.\n"
        "- If something is not mentioned, keep the schema default.\n"
        f"- {location_hint}\n"
        "- Do not invent place names, and do not repeat any name from the request.\n\n"
        f'Request: "{text.strip()[:400]}"'
    )


async def parse_with_llm(
    router: Optional[LlmRouter],
    text: str,
    start_lat: Optional[float] = None,
    start_lng: Optional[float] = None,
) -> TripSpec:
    """Parse with the heuristic, refine with the LLM when one is available."""
    base = parse_heuristic(text, start_lat, start_lng)
    if router is None or not text.strip():
        return base

    result = await ask_llm(
        router,
        task="parse",
        prompt=build_parse_prompt(text, start_lat, start_lng),
        schema=spec_schema(),
        model_cls=TripSpecDraft,
        temperature=0.1,
        max_tokens=400,
    )
    if result is None:
        logger.info("spec parse fell back to the offline heuristic")
        return base
    try:
        draft = TripSpecDraft.model_validate_json(result.text)
    except Exception as exc:
        logger.info("spec parse draft was invalid (%s); using the heuristic", exc)
        return base
    merged = merge_specs(base, draft, raw_text=text)
    logger.info("spec parsed with %s: %s", result.provider, merged.describe())
    return merged
