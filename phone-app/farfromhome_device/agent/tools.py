"""Planner tools. Every external dependency sits behind an interface, and every
one of them has an offline implementation so the planner runs on a plane.

The brief (§5.1) names five tools, and they are all here:

==================  ===========================================================
``PlacesProvider``  OpenStreetMap (Overpass + Nominatim) by default; an optional
                    Google Places provider behind a config flag. The free OSM path
                    must work on its own, so it is the only *required* one.
``RoutingProvider`` OSRM for distance/time per transport mode; falls back to
                    straight-line great-circle distance with a mode factor.
``ReviewsProvider`` ratings and snippets when available, otherwise empty.
``SafetyChecker``   opening hours, daylight at arrival, public-place filter,
                    distance from the emergency contact (rules in code).
``SocialSignals``   a stub that returns nothing, so server data can be plugged in
                    later without changing the planner.
==================  ===========================================================
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from ..core.models import Transport, TripSpec
from ..core.safety import SafetyReport, check_trip_safety, is_public_place
from ..engine.geo import haversine_m, move_towards, path_length_m
from ..net.diagnostics import primary_local_ip  # noqa: F401  (re-exported for convenience)

logger = logging.getLogger("ffh.agent.tools")


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@dataclass
class Place:
    """One candidate place, normalised across providers."""

    poi_id: str
    name: str
    lat: float
    lng: float
    category: str = "other"
    tags: Dict[str, Any] = field(default_factory=dict)
    distance_m: float = 0.0
    opening_hours: str = ""
    rating: Optional[float] = None
    review_count: int = 0
    review_snippets: List[str] = field(default_factory=list)
    source: str = "osm"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "poi_id": self.poi_id,
            "name": self.name,
            "lat": self.lat,
            "lng": self.lng,
            "category": self.category,
            "tags": self.tags,
            "distance_m": round(self.distance_m, 1),
            "opening_hours": self.opening_hours,
            "rating": self.rating,
            "review_count": self.review_count,
            "source": self.source,
        }


@dataclass
class RouteEstimate:
    distance_m: float
    duration_s: float
    transport: str
    source: str = "straight_line"

    @property
    def duration_min(self) -> float:
        return self.duration_s / 60.0


# --------------------------------------------------------------------------- #
# PlacesProvider
# --------------------------------------------------------------------------- #
CATEGORY_FILTERS: Dict[str, str] = {
    "coffee": 'node["amenity"="cafe"]',
    "cafe": 'node["amenity"="cafe"]',
    "food": 'node["amenity"~"restaurant|fast_food"]',
    "nature": 'node["leisure"~"park|garden|nature_reserve"]',
    "park": 'node["leisure"~"park|garden"]',
    "viewpoint": 'node["tourism"="viewpoint"]',
    "beach": 'node["natural"="beach"]',
    "market": 'node["amenity"="marketplace"]',
    "temple": 'node["amenity"="place_of_worship"]',
    "heritage": 'node["historic"]',
    "museum": 'node["tourism"="museum"]',
    "bar": 'node["amenity"="bar"]',
    "bookshop": 'node["shop"="books"]',
}


class PlacesProvider:
    """Interface. ``find`` must never raise: an empty list is the failure mode."""

    name = "places"

    async def find(
        self,
        lat: float,
        lng: float,
        radius_m: float,
        interests: Sequence[str],
        limit: int = 30,
        timeout_s: float = 12.0,
    ) -> List[Place]:  # pragma: no cover - interface
        raise NotImplementedError

    async def close(self) -> None:
        return None


class OverpassPlaces(PlacesProvider):
    """OpenStreetMap over the public Overpass API. The default, free path."""

    name = "overpass"

    DEFAULT_ENDPOINTS = (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.osm.ch/api/interpreter",
    )

    def __init__(self, endpoint: Optional[str] = None, session: Any = None) -> None:
        self.endpoint = endpoint or self.DEFAULT_ENDPOINTS[0]
        self._session = session

    def build_query(self, lat: float, lng: float, radius_m: float, interests: Sequence[str]) -> str:
        """Overpass QL. Kept as its own method so it can be unit-tested offline."""
        selectors: List[str] = []
        for interest in interests:
            selector = CATEGORY_FILTERS.get(interest.strip().lower())
            if selector:
                selectors.append(f"  {selector}(around:{int(radius_m)},{lat},{lng});")
        if not selectors:
            for selector in ('node["tourism"="viewpoint"]', 'node["leisure"="park"]',
                             'node["amenity"="cafe"]'):
                selectors.append(f"  {selector}(around:{int(radius_m)},{lat},{lng});")
        body = "\n".join(selectors)
        return f"[out:json][timeout:25];\n(\n{body}\n);\nout center tags 60;"

    #: OSM tag -> our destination category, for stamp rarity.
    CATEGORY_MAP = (
        ("natural", "beach", "beach"),
        ("leisure", "park", "park"),
        ("leisure", "garden", "park"),
        ("amenity", "cafe", "cafe"),
        ("amenity", "restaurant", "cafe"),
        ("amenity", "marketplace", "market"),
        ("amenity", "place_of_worship", "temple"),
        ("historic", "*", "heritage"),
        ("tourism", "viewpoint", "viewpoint"),
        ("tourism", "museum", "heritage"),
    )

    @classmethod
    def categorise(cls, tags: Dict[str, Any]) -> str:
        for key, value, category in cls.CATEGORY_MAP:
            if key not in tags:
                continue
            if value == "*" or str(tags.get(key, "")).startswith(value):
                return category
        return "other"

    @staticmethod
    def parse_response(payload: Dict[str, Any], origin_lat: float, origin_lng: float) -> List[Place]:
        places: List[Place] = []
        for element in payload.get("elements", []):
            tags = element.get("tags") or {}
            name = tags.get("name") or tags.get("name:en")
            if not name:
                continue
            lat = element.get("lat") or (element.get("center") or {}).get("lat")
            lng = element.get("lon") or (element.get("center") or {}).get("lon")
            if lat is None or lng is None:
                continue
            places.append(
                Place(
                    poi_id=f"osm:{element.get('type', 'node')}/{element.get('id')}",
                    name=str(name),
                    lat=float(lat),
                    lng=float(lng),
                    category=OverpassPlaces.categorise(tags),
                    tags=tags,
                    distance_m=haversine_m(origin_lat, origin_lng, float(lat), float(lng)),
                    opening_hours=str(tags.get("opening_hours", "")),
                    source="osm",
                )
            )
        return places

    async def find(
        self,
        lat: float,
        lng: float,
        radius_m: float,
        interests: Sequence[str],
        limit: int = 30,
        timeout_s: float = 12.0,
    ) -> List[Place]:
        query = self.build_query(lat, lng, radius_m, interests)
        payload = await _post_json(
            self.endpoint, {"data": query}, timeout_s=timeout_s, session=self._session
        )
        if not payload:
            # Try the mirrors before giving up: Overpass rate-limits aggressively.
            for endpoint in self.DEFAULT_ENDPOINTS:
                if endpoint == self.endpoint:
                    continue
                payload = await _post_json(
                    endpoint, {"data": query}, timeout_s=timeout_s, session=self._session
                )
                if payload:
                    self.endpoint = endpoint
                    break
        if not payload:
            return []
        places = self.parse_response(payload, lat, lng)
        places.sort(key=lambda place: place.distance_m)
        return places[:limit]


class NominatimGeocoder:
    """Free-text place search. Used for "near <somewhere>" and for tests."""

    def __init__(self, base_url: str = "https://nominatim.openstreetmap.org", session: Any = None,
                 user_agent: str = "FarFromHome-Handheld/1.0") -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.user_agent = user_agent

    async def search(self, query: str, limit: int = 3, timeout_s: float = 10.0) -> List[Place]:
        payload = await _get_json(
            f"{self.base_url}/search",
            {"q": query, "format": "jsonv2", "limit": limit},
            timeout_s=timeout_s,
            session=self.session,
            headers={"User-Agent": self.user_agent},
        )
        if not payload:
            return []
        out: List[Place] = []
        for item in payload:
            try:
                out.append(
                    Place(
                        poi_id=f"osm:{item.get('osm_type', 'node')}/{item.get('osm_id')}",
                        name=str(item.get("display_name", query)),
                        lat=float(item["lat"]),
                        lng=float(item["lon"]),
                        category="other",
                        tags=item,
                        source="nominatim",
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out


class GooglePlaces:
    """Optional Google Places provider, behind a config flag and an API key.

    The brief is explicit: *"it has reviews, but billing applies beyond a small
    free credit, so the free OSM path must work on its own."* This class therefore
    refuses to be constructed without a key and is never the default.
    """

    name = "google"

    def __init__(self, api_key: str, endpoint: str = "https://maps.googleapis.com/maps/api/place") -> None:
        if not api_key:
            raise ValueError("Google Places needs an API key; leave it unset to use OSM")
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.session: Any = None

    TYPE_MAP = {
        "cafe": "cafe", "restaurant": "cafe", "bar": "cafe", "bakery": "cafe",
        "park": "park", "beach": "beach", "tourist_attraction": "viewpoint",
        "museum": "heritage", "church": "temple", "hindu_temple": "temple",
        "market": "market", "store": "market",
    }

    async def find(self, lat: float, lng: float, radius_m: float, interests: Sequence[str],
                   limit: int = 30, timeout_s: float = 12.0) -> List[Place]:
        keyword = ",".join(interests[:3]) if interests else "interesting place"
        payload = await _get_json(
            f"{self.endpoint}/nearbysearch/json",
            {
                "location": f"{lat},{lng}",
                "radius": int(min(50000, max(100, radius_m))),
                "keyword": keyword,
                "key": self.api_key,
            },
            timeout_s=timeout_s,
            session=self.session,
        )
        if not payload:
            return []
        places: List[Place] = []
        for item in payload.get("results", [])[:limit]:
            types = item.get("types") or []
            category = "other"
            for kind in types:
                if kind in self.TYPE_MAP:
                    category = self.TYPE_MAP[kind]
                    break
            location = (item.get("geometry") or {}).get("location") or {}
            if "lat" not in location:
                continue
            places.append(
                Place(
                    poi_id=f"google:{item.get('place_id')}",
                    name=str(item.get("name", "Unnamed")),
                    lat=float(location["lat"]),
                    lng=float(location["lng"]),
                    category=category,
                    tags={"types": types, "opening_hours": (item.get("opening_hours") or {}).get("open_now")},
                    distance_m=haversine_m(lat, lng, float(location["lat"]), float(location["lng"])),
                    rating=item.get("rating"),
                    review_count=int(item.get("user_ratings_total", 0) or 0),
                    source="google",
                )
            )
        return places


# --------------------------------------------------------------------------- #
# RoutingProvider
# --------------------------------------------------------------------------- #
TRANSPORT_SPEED_MPS = {
    Transport.WALK.value: 1.35,
    Transport.BIKE.value: 4.2,
    Transport.CAR.value: 9.5,
    Transport.TRANSIT.value: 7.0,
}

#: Straight-line distance underestimates street distance. These factors come from
#: comparing OSRM output with great-circle distance on a handful of Kozhikode
#: routes; they are deliberately conservative (over-estimate time, not under).
DETOUR_FACTOR = {
    Transport.WALK.value: 1.28,
    Transport.BIKE.value: 1.3,
    Transport.CAR.value: 1.35,
    Transport.TRANSIT.value: 1.6,
}


class RoutingProvider:
    """Interface."""

    name = "routing"

    async def route(self, points: Sequence[Tuple[float, float]], transport: str,
                    timeout_s: float = 12.0) -> Optional[RouteEstimate]:  # pragma: no cover
        raise NotImplementedError


class OsrmRouting(RoutingProvider):
    """OSRM. Falls back to :class:`StraightLineRouting` on any failure."""

    name = "osrm"

    def __init__(self, base_url: str = "https://router.project-osrm.org", session: Any = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.fallback = StraightLineRouting()

    PROFILE = {
        Transport.WALK.value: "foot",
        Transport.BIKE.value: "bike",
        Transport.CAR.value: "driving",
        Transport.TRANSIT.value: "driving",
    }

    async def route(self, points: Sequence[Tuple[float, float]], transport: str,
                    timeout_s: float = 12.0) -> Optional[RouteEstimate]:
        if len(points) < 2:
            return RouteEstimate(0.0, 0.0, transport, source="trivial")
        profile = self.PROFILE.get(transport, "foot")
        coordinates = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
        payload = await _get_json(
            f"{self.base_url}/route/v1/{profile}/{coordinates}",
            {"overview": "false", "steps": "false"},
            timeout_s=timeout_s,
            session=self.session,
        )
        try:
            route = payload["routes"][0]  # type: ignore[index]
            return RouteEstimate(
                distance_m=float(route["distance"]),
                duration_s=float(route["duration"]),
                transport=transport,
                source="osrm",
            )
        except (KeyError, IndexError, TypeError):
            logger.debug("OSRM gave no usable route for %s; using straight line", transport)
            return await self.fallback.route(points, transport)


class StraightLineRouting(RoutingProvider):
    """Great-circle distance with a per-mode detour factor. Works with no network."""

    name = "straight_line"

    async def route(self, points: Sequence[Tuple[float, float]], transport: str,
                    timeout_s: float = 12.0) -> Optional[RouteEstimate]:
        raw = path_length_m(list(points))
        factor = DETOUR_FACTOR.get(transport, 1.3)
        distance = raw * factor
        speed = TRANSPORT_SPEED_MPS.get(transport, 1.35)
        return RouteEstimate(distance, distance / speed, transport, source="straight_line")


# --------------------------------------------------------------------------- #
# ReviewsProvider
# --------------------------------------------------------------------------- #
class ReviewsProvider:
    """Interface. Returns ratings and snippets when available, otherwise empty."""

    name = "reviews"

    async def reviews_for(self, place: Place, timeout_s: float = 8.0) -> Place:  # pragma: no cover
        raise NotImplementedError


class NullReviews(ReviewsProvider):
    """The default. OSM has no ratings, and the brief says empty is fine."""

    name = "none"

    async def reviews_for(self, place: Place, timeout_s: float = 8.0) -> Place:
        return place


class RatingFromTags(ReviewsProvider):
    """Derives a weak signal from OSM tags rather than pretending to have reviews.

    OSM sometimes carries ``stars`` (rare, from imports) or a ``wikidata`` link.
    We use them to rank, and we never invent a review count.
    """

    name = "tags"

    async def reviews_for(self, place: Place, timeout_s: float = 8.0) -> Place:
        raw = place.tags.get("stars") or place.tags.get("rating")
        if raw:
            try:
                place.rating = float(str(raw).split("/")[0])
            except ValueError:
                pass
        return place


# --------------------------------------------------------------------------- #
# SocialSignals (stub, by design)
# --------------------------------------------------------------------------- #
@dataclass
class SocialSignal:
    poi_id: str
    score: float = 0.0
    reason: str = ""


class SocialSignals:
    """**Stub for now** (brief §5.1): "returns nothing so server data can be plugged
    in later". The interface is deliberately the shape the real feature needs:
    a per-place signal the scorer can add to a candidate's score.
    """

    name = "social_stub"

    def __init__(self, enabled: bool = False, client: Any = None) -> None:
        self.enabled = enabled
        self.client = client
        self.calls = 0

    async def signals_for(self, places: Sequence[Place]) -> Dict[str, SocialSignal]:
        self.calls += 1
        if not self.enabled:
            return {}
        # Kept as an explicit seam: when the server exposes community data, fetch
        # it here and return {poi_id: SocialSignal(...)}. Until then, nothing.
        return {}


# --------------------------------------------------------------------------- #
# SafetyChecker
# --------------------------------------------------------------------------- #
class SafetyChecker:
    """Wraps :mod:`farfromhome_device.core.safety` with the trip's context."""

    name = "safety"

    def __init__(
        self,
        contact_lat: Optional[float] = None,
        contact_lng: Optional[float] = None,
        contact_name: str = "",
        contact_radius_km: float = 15.0,
    ) -> None:
        self.contact_lat = contact_lat
        self.contact_lng = contact_lng
        self.contact_name = contact_name
        self.contact_radius_km = contact_radius_km

    def check(self, spec: TripSpec, place: Place, eta: Any = None) -> SafetyReport:
        return check_trip_safety(
            spec=spec,
            destination_lat=place.lat,
            destination_lng=place.lng,
            destination_category=place.category,
            destination_tags=place.tags,
            contact_lat=self.contact_lat,
            contact_lng=self.contact_lng,
            contact_name=self.contact_name,
            contact_radius_km=self.contact_radius_km,
            eta=eta,
        )

    def reject_reason(self, place: Place) -> Optional[str]:
        allowed, reason = is_public_place(place.category, place.tags)
        return None if allowed else reason


# --------------------------------------------------------------------------- #
# Candidate scoring — **in code, not in the LLM** (brief §1.2)
# --------------------------------------------------------------------------- #
@dataclass
class ScoredCandidate:
    place: Place
    score: float
    reasons: Dict[str, float] = field(default_factory=dict)
    route: Optional[RouteEstimate] = None
    safety: Optional[SafetyReport] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "place": self.place.to_dict(),
            "score": round(self.score, 3),
            "reasons": {key: round(value, 3) for key, value in self.reasons.items()},
            "route": None
            if not self.route
            else {
                "distance_m": round(self.route.distance_m, 1),
                "duration_min": round(self.route.duration_min, 1),
                "source": self.route.source,
            },
            "safety_ok": None if not self.safety else self.safety.ok,
        }


#: How much each interest is worth if the place matches it.
INTEREST_WEIGHTS = {
    "coffee": 1.0, "cafe": 1.0, "food": 1.2, "nature": 1.1, "park": 1.0,
    "viewpoint": 1.4, "beach": 1.5, "market": 0.9, "temple": 1.2,
    "heritage": 1.3, "museum": 1.1, "bar": 0.7, "bookshop": 0.6,
}


def score_candidate(
    place: Place,
    spec: TripSpec,
    route: Optional[RouteEstimate],
    safety: Optional[SafetyReport],
    social: Optional[SocialSignal] = None,
) -> ScoredCandidate:
    """Score one candidate. Pure, so it is trivially testable and explainable.

    Weights (documented in DECISIONS.md D-10):

    * **interest match** (0-3) — does the place's category match what was asked for;
    * **range fit** (0-2) — inside ``range_km`` is best, too close is dull, too far
      is punished hard;
    * **time fit** (0-2) — the estimated duration against ``max_minutes``;
    * **rarity** (0-1) — heritage/viewpoint/beach beat a cafe;
    * **reviews** (0-1) — only when a provider actually has them;
    * **social** (0-1) — always 0 until the stub is enabled.
    """
    reasons: Dict[str, float] = {}

    interests = {interest.lower() for interest in spec.interests}
    interest_score = 0.0
    for interest in interests:
        if interest == place.category or interest in place.category:
            interest_score = max(interest_score, INTEREST_WEIGHTS.get(interest, 0.8) * 2.0)
    if place.category == "other" and interests:
        interest_score = 0.3
    reasons["interest"] = min(3.0, interest_score)

    distance_km = (route.distance_m if route else place.distance_m) / 1000.0
    low, high = spec.range_km
    if low <= distance_km <= high:
        range_score = 2.0 - 0.4 * abs(distance_km - (low + high) / 2.0) / max(0.1, (high - low) / 2.0 + 0.1)
    elif distance_km < low:
        range_score = 0.8
    else:
        overshoot = (distance_km - high) / max(1.0, high)
        range_score = max(0.0, 1.2 - 1.6 * overshoot)
    reasons["range"] = min(2.0, max(0.0, range_score))

    duration_min = route.duration_min if route else distance_km / 1.35 * 1.28 * 60
    budget = max(10.0, spec.max_minutes)
    if duration_min <= budget:
        time_score = 2.0 * (1.0 - min(0.6, abs(budget * 0.6 - duration_min) / budget))
    else:
        time_score = max(0.0, 1.0 - (duration_min - budget) / budget)
    reasons["time"] = min(2.0, max(0.0, time_score))

    rarity = {
        "beach": 1.0, "viewpoint": 0.9, "heritage": 0.9, "temple": 0.8,
        "market": 0.6, "park": 0.6, "cafe": 0.4, "other": 0.3,
    }.get(place.category, 0.3)
    reasons["rarity"] = rarity

    review_score = 0.0
    if place.rating is not None:
        review_score = min(1.0, max(0.0, (place.rating - 2.5) / 2.5))
        if place.review_count:
            review_score = min(1.0, review_score * (1.0 + min(0.5, place.review_count / 200.0)))
    reasons["reviews"] = review_score

    reasons["social"] = social.score if social else 0.0

    if safety is not None and not safety.ok:
        # A hard safety failure: the candidate should never be chosen at all (the
        # planner filters these out first), but if one slips through, bury it.
        reasons["safety_penalty"] = -5.0
    elif safety is not None and safety.errors:
        reasons["safety_penalty"] = -5.0
    else:
        # Warnings are advisory. A missing emergency contact is a nudge, not a
        # reason to send someone somewhere worse, so it costs almost nothing.
        penalty = 0.0
        for issue in (safety.warnings if safety else []):
            penalty += -0.2 if issue.code == "no_emergency_contact" else -0.6
        reasons["safety_penalty"] = penalty

    total = (
        reasons["interest"]
        + reasons["range"]
        + reasons["time"]
        + reasons["rarity"] * 1.0
        + reasons["reviews"]
        + reasons["social"]
        + reasons["safety_penalty"]
    )
    return ScoredCandidate(
        place=place, score=total, reasons=reasons, route=route, safety=safety
    )


# --------------------------------------------------------------------------- #
# Offline provider: a small deterministic place set around a start point
# --------------------------------------------------------------------------- #
class OfflinePlaces(PlacesProvider):
    """Deterministic fake places around the start point, for offline demo and tests.

    Deliberately bland but real-shaped: a cafe, a park, a viewpoint, a market, a
    beach. Distances are spread across the **requested** range rather than the
    search radius, so the "range fit" scoring has something meaningful to chew on
    and a plan built offline still lands inside the distance the user asked for.
    """

    name = "offline"

    #: ``(name, fraction of the wanted maximum distance, category, bearing_deg)``
    TEMPLATE = (
        ("The Reading Room", 0.22, "cafe", 40.0),
        ("Old Banyan Park", 0.48, "park", 120.0),
        ("Salt Wind Point", 0.86, "viewpoint", 200.0),
        ("Thursday Market", 0.40, "market", 260.0),
        ("The Long Shore", 0.99, "beach", 320.0),
        ("Ferry Cafe", 0.66, "cafe", 80.0),
        ("Heritage Wall", 0.34, "heritage", 150.0),
        ("Green Mile Garden", 0.30, "park", 60.0),
    )

    def __init__(self, seed: int = 5, wanted_max_m: Optional[float] = None) -> None:
        self.seed = seed
        #: When set, all distances scale so the furthest place sits at this distance.
        self.wanted_max_m = wanted_max_m
        self.last_range_m: Optional[Tuple[float, float]] = None

    @classmethod
    def from_spec(cls, spec: TripSpec, seed: int = 5) -> "OfflinePlaces":
        return cls(seed=seed, wanted_max_m=spec.range_km[1] * 1000.0)

    async def find(self, lat: float, lng: float, radius_m: float, interests: Sequence[str],
                   limit: int = 30, timeout_s: float = 12.0) -> List[Place]:
        rng = random.Random(self.seed + int(lat * 1000) + int(lng * 1000))
        # The furthest template entry must land on the wanted maximum, and must
        # stay inside the search radius so the caller's own radius is respected.
        ceiling = min(radius_m, self.wanted_max_m if self.wanted_max_m else radius_m * 0.8)
        largest_fraction = max(entry[1] for entry in self.TEMPLATE)
        scale = max(80.0, ceiling / largest_fraction)

        places: List[Place] = []
        for index, (name, distance_fraction, category, bearing) in enumerate(self.TEMPLATE):
            distance = max(80.0, scale * distance_fraction)
            bearing_jitter = bearing + rng.uniform(-12.0, 12.0)
            place_lat, place_lng = move_towards(lat, lng, bearing_jitter, distance)
            places.append(
                Place(
                    poi_id=f"offline:{index}",
                    name=name,
                    lat=place_lat,
                    lng=place_lng,
                    category=category,
                    tags={"leisure" if category == "park" else "amenity": category, "name": name},
                    distance_m=haversine_m(lat, lng, place_lat, place_lng),
                    opening_hours="Mo-Su 08:00-20:00" if category in ("cafe", "market") else "24/7",
                    rating=None,
                    source="offline",
                )
            )
        places.sort(key=lambda place: place.distance_m)
        self.last_range_m = (places[0].distance_m, places[-1].distance_m) if places else None
        return places[:limit]


# --------------------------------------------------------------------------- #
# HTTP helpers (import requests lazily so the agent runs with no network stack)
# --------------------------------------------------------------------------- #
async def _post_json(url: str, payload: Dict[str, Any], timeout_s: float, session: Any = None) -> Optional[Dict[str, Any]]:
    def call() -> Optional[Dict[str, Any]]:
        try:
            import requests

            client = session or requests
            response = client.post(url, data=payload, timeout=timeout_s,
                                   headers={"User-Agent": "FarFromHome-Handheld/1.0"})
            if response.status_code >= 400:
                logger.debug("POST %s -> HTTP %s", url, response.status_code)
                return None
            return response.json()
        except Exception as exc:
            logger.debug("POST %s failed: %s", url, exc)
            return None

    return await asyncio.to_thread(call)


async def _get_json(
    url: str,
    params: Dict[str, Any],
    timeout_s: float,
    session: Any = None,
    headers: Optional[Dict[str, str]] = None,
) -> Optional[Any]:
    def call() -> Optional[Any]:
        try:
            import requests

            client = session or requests
            response = client.get(url, params=params, timeout=timeout_s,
                                  headers=headers or {"User-Agent": "FarFromHome-Handheld/1.0"})
            if response.status_code >= 400:
                logger.debug("GET %s -> HTTP %s", url, response.status_code)
                return None
            return response.json()
        except Exception as exc:
            logger.debug("GET %s failed: %s", url, exc)
            return None

    return await asyncio.to_thread(call)


def build_places_provider(config: Any) -> PlacesProvider:
    """Pick the places provider from config. OSM by default; Google only if asked."""
    google_key = getattr(config, "google_places_api_key", "") or ""
    if getattr(config, "use_google_places", False) and google_key:
        logger.info("using Google Places (billing applies)")
        return GooglePlaces(google_key)
    return OverpassPlaces()


def build_reviews_provider(config: Any) -> ReviewsProvider:
    if getattr(config, "use_google_places", False):
        return NullReviews()  # Google Places details would go here; not enabled by default
    return RatingFromTags()


def build_routing_provider(config: Any) -> RoutingProvider:
    if getattr(config, "offline", False):
        return StraightLineRouting()
    return OsrmRouting(getattr(config, "osrm_base_url", "https://router.project-osrm.org"))
