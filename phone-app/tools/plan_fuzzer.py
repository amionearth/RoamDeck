"""``plan_fuzzer``: throw odd trip requests at the planner and check the contract.

Brief §14 asks for "a fuzzer for the planner" and M4's check is "20 varied specs
produce valid plans". The unit test does the 20; this tool goes wider, including
nonsense, and exits non-zero the moment a plan fails to validate — so it can be run
in CI as a cheap regression net.

    python tools/plan_fuzzer.py --count 200
    python tools/plan_fuzzer.py --count 50 --online     # hits the real map providers
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from farfromhome_device.agent.planner import PlanRequest, Planner
from farfromhome_device.agent.tools import (
    NullReviews,
    OfflinePlaces,
    OsrmRouting,
    OverpassPlaces,
    SafetyChecker,
    SocialSignals,
    StraightLineRouting,
)
from farfromhome_device.core.config import load_settings
from farfromhome_device.core.models import Plan

VIBES = ["calm", "chill", "playful", "adventure", "romantic", "foodie"]
TRANSPORTS = ["walk", "bike", "car", "transit"]
INTERESTS = ["coffee", "food", "nature", "viewpoint", "beach", "market", "temple", "heritage", "bar"]
PEOPLE_WORDS = ["just me", "two of us", "three friends", "four of us", "a group of six", "ten people"]
DURATIONS = ["20 minutes", "45 minutes", "an hour", "90 minutes", "two hours", "half a day", "all day"]
DISTANCES = ["under 1 km", "about 2 km", "between 1 and 3 km", "5 km", "10 km", "40 km"]
EXCLUSIONS = ["", "vegetarian", "no pork", "vegan and no nuts"]
START_POINTS = [
    (11.2588, 75.7804),   # Kozhikode
    (11.3811, 75.7227),   # Kappad
    (9.9312, 76.2673),    # Kochi
    (8.5241, 76.9366),    # Thiruvananthapuram
    (12.9716, 77.5946),   # Bengaluru
    (51.5074, -0.1278),   # London, to prove nothing assumes India
    (35.6762, 139.6503),  # Tokyo
    (-33.8688, 151.2093), # Sydney, southern hemisphere
]

NONSENSE = [
    "",
    "   ",
    "asdfghjkl",
    "a trip to the moon for 400 people in 3 seconds",
    "walk 10000 km in one minute",
    "romantic adventure foodie calm playful trip",
    "take me somewhere",
    "🚀🌊🍜",
    "a" * 500,
    "walk until I say stop",
]


def make_request(rng: random.Random, nonsense: bool = False) -> Tuple[str, Tuple[float, float]]:
    start = rng.choice(START_POINTS)
    if nonsense:
        return rng.choice(NONSENSE), start
    parts = [
        f"a {rng.choice(VIBES)} {rng.choice(TRANSPORTS)}",
        f"for {rng.choice(PEOPLE_WORDS)}",
        rng.choice(DURATIONS),
    ]
    if rng.random() < 0.7:
        parts.append("and " + " and ".join(rng.sample(INTERESTS, k=rng.randint(1, 3))))
    if rng.random() < 0.3:
        parts.append(rng.choice(DISTANCES))
    exclusion = rng.choice(EXCLUSIONS)
    if exclusion:
        parts.append(exclusion)
    return " ".join(parts), start


async def fuzz(count: int, online: bool, seed: Optional[int], verbose: bool) -> int:
    rng = random.Random(seed)
    settings = load_settings()

    if online:
        planner = Planner(
            settings,
            places=OverpassPlaces(),
            routing=OsrmRouting(),
            reviews=NullReviews(),
            safety=SafetyChecker(),
            social=SocialSignals(enabled=False),
        )
    else:
        planner = Planner(
            settings,
            places=OfflinePlaces(),
            routing=StraightLineRouting(),
            reviews=NullReviews(),
            safety=SafetyChecker(),
            social=SocialSignals(enabled=False),
        )

    failures: List[Dict[str, Any]] = []
    template_plans = 0
    distances: List[float] = []

    for index in range(count):
        nonsense = index % 10 == 9
        text, (lat, lng) = make_request(rng, nonsense=nonsense)
        outcome = await planner.plan(
            PlanRequest(
                text=text,
                start_lat=lat,
                start_lng=lng,
                offline=not online,
            )
        )
        if not outcome.ok or outcome.plan is None:
            failures.append({"text": text, "why": outcome.reason})
            if verbose:
                print(f"FAIL  {text!r}: {outcome.reason}")
            continue

        plan = outcome.plan
        try:
            # The engine will do exactly this, so it is the real contract test.
            Plan.model_validate(plan.model_dump())
        except Exception as exc:
            failures.append({"text": text, "why": f"revalidation failed: {exc}"})
            if verbose:
                print(f"FAIL  {text!r}: revalidation failed: {exc}")
            continue

        if plan.fallback.used:
            template_plans += 1
        distances.append(plan.route_km)

        # The mystery rules, re-checked on every single plan.
        summary = plan.public_summary()
        if plan.destination.name in str(summary):
            failures.append({"text": text, "why": "the destination name leaked into the summary"})
        if not plan.games:
            failures.append({"text": text, "why": "the plan has no games"})
        if plan.spec.people < 1:
            failures.append({"text": text, "why": "the plan has no people"})

        if verbose and index % 25 == 0:
            print(
                f"  {index:>4} {plan.title[:28]:<28} {plan.route_km:5.2f} km "
                f"{len(plan.waypoints)} wp  {text[:52]!r}"
            )

    print(f"\n{count} requests, {count - len(failures)} valid, {len(failures)} failed")
    print(f"template fallbacks: {template_plans}")
    if distances:
        print(
            f"route km: min {min(distances):.2f}, mean {sum(distances) / len(distances):.2f}, "
            f"max {max(distances):.2f}"
        )
    for failure in failures[:10]:
        print(f"  FAILED {failure['text'][:60]!r}: {failure['why']}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fuzz the planner")
    parser.add_argument("--count", type=int, default=60, help="how many requests to try")
    parser.add_argument("--online", action="store_true", help="use the real map providers")
    parser.add_argument("--seed", type=int, default=None, help="fix the random seed")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args()
    return asyncio.run(fuzz(args.count, args.online, args.seed, verbose=not args.quiet))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
