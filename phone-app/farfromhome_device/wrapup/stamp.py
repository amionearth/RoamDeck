"""The digital stamp (brief §9 item 10).

*"When a trip completes, generate a stamp image for that location. The user can put
it on their profile or download it."*

The *server* is the authority on stamps: it computes the rarity score and awards
the stamp on arrival (``backend/app/services/stamps.py``). The handheld draws a
**local copy** so the user sees and keeps something even with no connection, and so
the wrap-up screen has an image before the server's PNG has synced down.

Rarity tiers are copied from the server so the two agree:

    score = category_weight + min(distance_km, 10) * 0.3 + golden_hour_bonus
    < 2.5  Common     #7C97A0
    < 4.0  Rare       #1F4B8F
    < 5.5  Epic       #7B4FC2
    >= 5.5 Legendary  #F2A33A
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("ffh.wrapup.stamp")

CATEGORY_WEIGHTS: Dict[str, float] = {
    "beach": 1.0,
    "park": 1.0,
    "cafe": 1.0,
    "market": 2.0,
    "other": 2.0,
    "temple": 3.0,
    "heritage": 3.0,
    "viewpoint": 3.0,
}

RARITY_TIERS: Tuple[Tuple[float, str, str], ...] = (
    (2.5, "Common", "#7C97A0"),
    (4.0, "Rare", "#1F4B8F"),
    (5.5, "Epic", "#7B4FC2"),
    (float("inf"), "Legendary", "#F2A33A"),
)

STAMP_SIZE = (720, 720)


@dataclass
class StampResult:
    path: Optional[Path]
    rarity: str = ""
    colour: str = ""
    score: float = 0.0
    serial: str = ""
    place_name: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None

    def to_payload(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "path": str(self.path) if self.path else None,
            "rarity": self.rarity,
            "colour": self.colour,
            "score": round(self.score, 2),
            "serial": self.serial,
            "place_name": self.place_name,
            "reason": self.reason,
        }


def golden_hour_bonus(when: Optional[datetime]) -> float:
    if when is None:
        return 0.0
    hour = when.hour + when.minute / 60.0
    if abs(hour - 6.25) <= 1.0 or abs(hour - 18.5) <= 1.0:
        return 1.0
    return 0.0


def rarity_for(category: str, distance_km: float, when: Optional[datetime] = None) -> Tuple[str, str, float]:
    """``(tier, colour, score)`` using the server's exact formula."""
    weight = CATEGORY_WEIGHTS.get((category or "other").lower(), 2.0)
    score = weight + min(max(0.0, distance_km), 10.0) * 0.3 + golden_hour_bonus(when)
    for threshold, tier, colour in RARITY_TIERS:
        if score < threshold:
            return tier, colour, score
    return "Legendary", "#F2A33A", score


def make_serial(seed: str, rarity: str) -> str:
    """A stable, human-readable serial. Same trip -> same serial."""
    rng = random.Random(f"{seed}:{rarity}")
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return f"FFH-{rarity[:3].upper()}-" + "".join(rng.choice(alphabet) for _ in range(6))


def _font(size_px: int, bold: bool = False) -> Any:
    from PIL import ImageFont

    for name in (
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
        "segoeuib.ttf" if bold else "segoeui.ttf",
    ):
        try:
            return ImageFont.truetype(name, size_px)
        except Exception:
            continue
    return ImageFont.load_default()


def _polar_point(cx: float, cy: float, radius: float, angle_deg: float) -> Tuple[float, float]:
    radians = math.radians(angle_deg)
    return (cx + radius * math.cos(radians), cy + radius * math.sin(radians))


def build_stamp(
    output_path: Path | str,
    place_name: str,
    category: str,
    trip_title: str = "",
    distance_km: float = 0.0,
    arrived_at: Optional[datetime] = None,
    serial: Optional[str] = None,
    size: Tuple[int, int] = STAMP_SIZE,
    perfin: bool = True,
) -> StampResult:
    """Draw a stamp: perforated edge, rarity ring, place, date and serial.

    ``place_name`` is only ever passed **after** the destination has been revealed;
    the caller is responsible for that, and the wrap-up screen only offers the stamp
    once ``trip.arrived`` has fired.
    """
    from PIL import Image, ImageDraw

    width, height = size
    tier, colour, score = rarity_for(category, distance_km, arrived_at)
    serial = serial or make_serial(f"{place_name}:{trip_title}", tier)

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    centre = (width / 2, height / 2)
    outer = width * 0.46
    inner = outer - width * 0.045

    # --- perforated edge ----------------------------------------------
    if perfin:
        teeth = 44
        for index in range(teeth):
            angle = index * (360.0 / teeth)
            x, y = _polar_point(centre[0], centre[1], outer, angle)
            radius = width * 0.022
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=(0, 0, 0, 0))
        # Redraw the solid disc minus the notches by compositing a ring on top.
        ring = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ring_draw = ImageDraw.Draw(ring)
        ring_draw.ellipse(
            [centre[0] - inner, centre[1] - inner, centre[0] + inner, centre[1] + inner],
            fill=(255, 255, 255, 255),
        )
        image = ring

    draw = ImageDraw.Draw(image)
    draw.ellipse(
        [centre[0] - outer, centre[1] - outer, centre[0] + outer, centre[1] + outer],
        fill=(250, 247, 241, 255),
    )
    draw.ellipse(
        [centre[0] - outer, centre[1] - outer, centre[0] + outer, centre[1] + outer],
        outline=colour,
        width=max(4, int(width * 0.014)),
    )
    draw.ellipse(
        [centre[0] - inner, centre[1] - inner, centre[0] + inner, centre[1] + inner],
        outline=colour,
        width=max(2, int(width * 0.005)),
    )

    # --- text ----------------------------------------------------------
    draw.text((centre[0], centre[1] - outer * 0.62), "FARFROMHOME", fill=colour,
              font=_font(int(width * 0.058), True), anchor="mm")
    draw.text((centre[0], centre[1] - outer * 0.46), tier.upper(), fill=colour,
              font=_font(int(width * 0.038), True), anchor="mm")

    name = (place_name or "Unknown place").strip()
    if len(name) > 26:
        name = name[:25] + "\u2026"
    draw.text((centre[0], centre[1] - outer * 0.10), name, fill=(24, 34, 38),
              font=_font(int(width * 0.075), True), anchor="mm")

    if trip_title:
        title = trip_title if len(trip_title) <= 30 else trip_title[:29] + "\u2026"
        draw.text((centre[0], centre[1] + outer * 0.06), title, fill=(90, 104, 108),
                  font=_font(int(width * 0.042)), anchor="mm")

    draw.line(
        [centre[0] - outer * 0.5, centre[1] + outer * 0.26, centre[0] + outer * 0.5, centre[1] + outer * 0.26],
        fill=colour, width=2,
    )

    when = arrived_at or datetime.now()
    draw.text((centre[0], centre[1] + outer * 0.40), when.strftime("%d %b %Y"), fill=(24, 34, 38),
              font=_font(int(width * 0.05), True), anchor="mm")
    draw.text((centre[0], centre[1] + outer * 0.56), f"{category.upper()}  ·  {distance_km:.1f} km",
              fill=(90, 104, 108), font=_font(int(width * 0.036)), anchor="mm")
    draw.text((centre[0], centre[1] + outer * 0.72), f"SERIAL {serial}", fill=colour,
              font=_font(int(width * 0.034), True), anchor="mm")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, format="PNG", optimize=True)
    image.close()
    logger.info("stamp written to %s (%s, score %.2f)", out, tier, score)
    return StampResult(out, rarity=tier, colour=colour, score=score, serial=serial, place_name=place_name)


def stamp_for_plan(plan: Any, output_path: Path | str, distance_km: Optional[float] = None,
                   arrived_at: Optional[datetime] = None, trip_title: str = "") -> StampResult:
    """Draw the stamp for a plan's destination. Call only after arrival."""
    return build_stamp(
        output_path,
        place_name=plan.destination.name,
        category=plan.destination.category.value,
        trip_title=trip_title or plan.title,
        distance_km=plan.route_km if distance_km is None else distance_km,
        arrived_at=arrived_at,
    )
