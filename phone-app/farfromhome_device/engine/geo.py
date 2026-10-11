"""Geodesy helpers. Pure functions, no dependencies, heavily unit-tested.

Everything here is a great-circle calculation on a spherical earth (mean radius
6371008.8 m). Trip legs are a few kilometres, so the spherical error (under
0.5 %) is far below GPS noise. Documented in ``docs/DECISIONS.md`` D-06.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

EARTH_RADIUS_M = 6371008.8


def to_radians(degrees: float) -> float:
    return math.radians(degrees)


def to_degrees(radians: float) -> float:
    return math.degrees(radians)


def normalise_deg(angle: float) -> float:
    """Fold any angle into ``[0, 360)``."""
    return angle % 360.0


def normalise_signed_deg(angle: float) -> float:
    """Fold any angle into ``(-180, 180]``. Handy for turn direction."""
    value = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if value == -180.0 else value


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    phi1, phi2 = to_radians(lat1), to_radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = to_radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, clockwise from north."""
    phi1, phi2 = to_radians(lat1), to_radians(lat2)
    d_lambda = to_radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    if x == 0.0 and y == 0.0:
        return 0.0
    return normalise_deg(to_degrees(math.atan2(y, x)))


def arrow_angle(bearing: float, heading: float) -> float:
    """``(bearing - heading) mod 360`` — the compass rotation the UI draws."""
    return normalise_deg(bearing - heading)


def compass_point(angle: float) -> str:
    """16-point compass label for an angle."""
    points = (
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    )
    index = int((normalise_deg(angle) / 22.5) + 0.5) % 16
    return points[index]


def turn_direction(bearing: float, heading: float) -> str:
    """Human hint: is the target to the left or right?"""
    delta = normalise_signed_deg(bearing - heading)
    magnitude = abs(delta)
    if magnitude <= 12.0:
        return "straight"
    if magnitude >= 168.0:
        return "turn around"
    return "right" if delta > 0 else "left"


def interpolate(lat1: float, lon1: float, lat2: float, lon2: float, fraction: float) -> Tuple[float, float]:
    """Linear interpolation. Fine for legs under a few km; used by the GPS sim."""
    fraction = max(0.0, min(1.0, fraction))
    return (lat1 + (lat2 - lat1) * fraction, lon1 + (lon2 - lon1) * fraction)


def move_towards(
    lat: float,
    lon: float,
    bearing: float,
    distance_m: float,
) -> Tuple[float, float]:
    """Project a point ``distance_m`` along ``bearing`` (spherical destination formula)."""
    angular = distance_m / EARTH_RADIUS_M
    phi1 = to_radians(lat)
    lambda1 = to_radians(lon)
    theta = to_radians(bearing)

    sin_phi2 = math.sin(phi1) * math.cos(angular) + math.cos(phi1) * math.sin(angular) * math.cos(theta)
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi2)))
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(angular) * math.cos(phi1),
        math.cos(angular) - math.sin(phi1) * math.sin(phi2),
    )
    # Fold longitude back into (-180, 180].
    lon2 = ((to_degrees(lambda2) + 540.0) % 360.0) - 180.0
    return (to_degrees(phi2), lon2)


def distance_to_segment_m(
    lat: float,
    lon: float,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Approximate distance from a point to a line segment, in metres.

    Works in a local equirectangular projection centred on the point, which is
    accurate to well under a metre over the length of a walking leg and avoids
    3-D vector code that is easy to get subtly wrong.
    """
    # Local metric frame centred on the query point.
    metres_per_deg_lat = 111132.0
    metres_per_deg_lon = 111320.0 * math.cos(to_radians(lat))
    if abs(metres_per_deg_lon) < 1e-6:
        metres_per_deg_lon = 1e-6

    def project(plat: float, plon: float) -> Tuple[float, float]:
        return ((plon - lon) * metres_per_deg_lon, (plat - lat) * metres_per_deg_lat)

    ax, ay = project(lat1, lon1)
    bx, by = project(lat2, lon2)
    px, py = 0.0, 0.0

    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    closest_x, closest_y = ax + t * dx, ay + t * dy
    return math.hypot(px - closest_x, py - closest_y)


def distance_to_path_m(
    lat: float,
    lon: float,
    path: Sequence[Tuple[float, float]],
) -> float:
    """Shortest distance from a point to a polyline (the planned route).

    A path with fewer than two points degrades to point distance, which is the
    right answer when the plan has no route waypoints yet.
    """
    if not path:
        return float("inf")
    if len(path) == 1:
        return haversine_m(lat, lon, path[0][0], path[0][1])
    best = float("inf")
    for (lat1, lon1), (lat2, lon2) in zip(path, path[1:]):
        best = min(best, distance_to_segment_m(lat, lon, lat1, lon1, lat2, lon2))
    return best


def path_length_m(path: Sequence[Tuple[float, float]]) -> float:
    return sum(
        haversine_m(lat1, lon1, lat2, lon2)
        for (lat1, lon1), (lat2, lon2) in zip(path, path[1:])
    )


def average_position(points: Iterable[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """Mean lat/lng. Only used for smoothing short GPS bursts."""
    collected = list(points)
    if not collected:
        return None
    return (
        sum(point[0] for point in collected) / len(collected),
        sum(point[1] for point in collected) / len(collected),
    )


def bounding_box(path: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float, float, float]]:
    """``(min_lat, min_lng, max_lat, max_lng)`` — used to draw the summary map."""
    if not path:
        return None
    lats = [point[0] for point in path]
    lngs = [point[1] for point in path]
    return (min(lats), min(lngs), max(lats), max(lngs))


def simplify_path(path: Sequence[Tuple[float, float]], tolerance_m: float = 8.0) -> List[Tuple[float, float]]:
    """Ramer-Douglas-Peucker simplification.

    The engine logs a point every few seconds; a two-hour walk is thousands of
    rows. Simplifying for drawing keeps the wrap-up map cheap on a 4 GB Pi.
    """
    if len(path) < 3:
        return list(path)

    def rdp(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
        start, end = points[0], points[-1]
        worst_distance = -1.0
        worst_index = 0
        for index in range(1, len(points) - 1):
            distance = distance_to_segment_m(
                points[index][0], points[index][1], start[0], start[1], end[0], end[1]
            )
            if distance > worst_distance:
                worst_distance = distance
                worst_index = index
        if worst_distance > tolerance_m:
            left = rdp(points[: worst_index + 1])
            right = rdp(points[worst_index:])
            return left[:-1] + right
        return [start, end]

    return rdp(list(path))


def daylight_window(lat: float, lon: float, day: Optional[object] = None) -> Tuple[float, float]:
    """Very rough sunrise/sunset as fractional hours (local solar time).

    The brief only needs "warn when arrival would be after dark" for a safety
    check. A proper solar-position library is not worth 200 kB on a 4 GB Pi, so
    this uses the standard day-length approximation (declination from day-of-year,
    hour angle from latitude). Documented in DECISIONS.md D-07.
    """
    import datetime as _dt

    if day is None:
        day = _dt.date.today()
    # Day of year -> solar declination in radians.
    day_of_year = day.timetuple().tm_yday  # type: ignore[attr-defined]
    declination = math.radians(23.44) * math.sin(math.radians(360.0 / 365.0 * (day_of_year - 81)))
    phi = to_radians(lat)
    cos_hour_angle = -math.tan(phi) * math.tan(declination)
    cos_hour_angle = max(-1.0, min(1.0, cos_hour_angle))
    hour_angle_deg = to_degrees(math.acos(cos_hour_angle))
    half_day_hours = hour_angle_deg / 15.0
    # Local solar noon offset from the timezone meridian (lng/15 hours).
    noon = 12.0 - (lon / 15.0 - round(lon / 15.0))
    return (noon - half_day_hours, noon + half_day_hours)


def is_after_dark(lat: float, lon: float, when: object) -> bool:
    import datetime as _dt

    if not isinstance(when, _dt.datetime):
        return False
    local_hour = when.hour + when.minute / 60.0 + when.second / 3600.0
    sunrise, sunset = daylight_window(lat, lon, when.date())
    return local_hour < sunrise or local_hour > sunset
