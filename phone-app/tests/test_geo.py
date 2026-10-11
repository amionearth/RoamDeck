"""Bearing and distance maths (brief M1 acceptance check: "unit tests for bearing math")."""

from __future__ import annotations

import math

import pytest

from farfromhome_device.engine.geo import (
    arrow_angle,
    bearing_deg,
    bounding_box,
    compass_point,
    daylight_window,
    distance_to_path_m,
    distance_to_segment_m,
    haversine_m,
    interpolate,
    is_after_dark,
    move_towards,
    normalise_deg,
    normalise_signed_deg,
    path_length_m,
    simplify_path,
    turn_direction,
)


# --------------------------------------------------------------------------- #
# distance
# --------------------------------------------------------------------------- #
def test_haversine_one_degree_of_latitude() -> None:
    assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(111195.0, rel=1e-4)


def test_haversine_one_degree_of_longitude_at_the_equator() -> None:
    assert haversine_m(0.0, 0.0, 0.0, 1.0) == pytest.approx(111195.0, rel=1e-4)


def test_haversine_shrinks_with_latitude() -> None:
    """A degree of longitude is about 78 km at Kozhikode's latitude, not 111."""
    assert haversine_m(11.2588, 0.0, 11.2588, 1.0) == pytest.approx(109_100.0, rel=0.01)


def test_haversine_is_symmetric() -> None:
    forward = haversine_m(11.1, 75.1, 11.9, 75.9)
    backward = haversine_m(11.9, 75.9, 11.1, 75.1)
    assert forward == pytest.approx(backward, rel=1e-12)


def test_haversine_zero_for_the_same_point() -> None:
    assert haversine_m(11.2588, 75.7804, 11.2588, 75.7804) == 0.0


def test_haversine_antipodal_is_half_the_circumference() -> None:
    assert haversine_m(0.0, 0.0, 0.0, 180.0) == pytest.approx(math.pi * 6371008.8, rel=1e-6)


# --------------------------------------------------------------------------- #
# bearing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "lat2, lng2, expected",
    [
        (1.0, 0.0, 0.0),      # due north
        (0.0, 1.0, 90.0),     # due east
        (-1.0, 0.0, 180.0),   # due south
        (0.0, -1.0, 270.0),   # due west
    ],
)
def test_cardinal_bearings_from_the_origin(lat2: float, lng2: float, expected: float) -> None:
    assert bearing_deg(0.0, 0.0, lat2, lng2) == pytest.approx(expected, abs=1e-6)


def test_bearing_is_always_in_range() -> None:
    for lat in (-80.0, -20.0, 0.0, 20.0, 80.0):
        for lng in (-179.0, -45.0, 0.0, 45.0, 179.0):
            value = bearing_deg(11.0, 75.0, lat, lng)
            assert 0.0 <= value < 360.0


def test_bearing_to_the_same_point_is_zero_not_nan() -> None:
    assert bearing_deg(11.2588, 75.7804, 11.2588, 75.7804) == 0.0


def test_bearing_reverses_by_roughly_180_degrees() -> None:
    forward = bearing_deg(11.2, 75.7, 11.4, 75.9)
    backward = bearing_deg(11.4, 75.9, 11.2, 75.7)
    delta = abs(normalise_signed_deg(backward - forward))
    assert delta == pytest.approx(180.0, abs=1.0)


# --------------------------------------------------------------------------- #
# arrow angle — the one number the compass draws
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bearing, heading, expected",
    [
        (0.0, 0.0, 0.0),
        (90.0, 0.0, 90.0),
        (350.0, 10.0, 340.0),
        (10.0, 350.0, 20.0),
        (180.0, 0.0, 180.0),
        (0.0, 90.0, 270.0),
    ],
)
def test_arrow_angle(bearing: float, heading: float, expected: float) -> None:
    assert arrow_angle(bearing, heading) == pytest.approx(expected)


def test_arrow_angle_is_wrapped() -> None:
    for bearing in range(0, 360, 7):
        for heading in range(0, 360, 11):
            assert 0.0 <= arrow_angle(bearing, heading) < 360.0


# --------------------------------------------------------------------------- #
# angle helpers
# --------------------------------------------------------------------------- #
def test_normalise_deg_folds_into_range() -> None:
    assert normalise_deg(-10.0) == 350.0
    assert normalise_deg(370.0) == 10.0
    assert normalise_deg(0.0) == 0.0
    assert normalise_deg(720.0) == 0.0


def test_normalise_signed_deg_picks_the_short_way() -> None:
    assert normalise_signed_deg(190.0) == pytest.approx(-170.0)
    assert normalise_signed_deg(-190.0) == pytest.approx(170.0)
    assert normalise_signed_deg(10.0) == pytest.approx(10.0)


@pytest.mark.parametrize(
    "bearing, heading, expected",
    [
        (0.0, 0.0, "straight"),
        (5.0, 0.0, "straight"),
        (40.0, 0.0, "right"),
        (320.0, 0.0, "left"),
        (180.0, 0.0, "turn around"),
    ],
)
def test_turn_direction(bearing: float, heading: float, expected: str) -> None:
    assert turn_direction(bearing, heading) == expected


@pytest.mark.parametrize(
    "angle, expected",
    [(0.0, "N"), (45.0, "NE"), (90.0, "E"), (180.0, "S"), (270.0, "W"), (359.0, "N")],
)
def test_compass_point(angle: float, expected: str) -> None:
    assert compass_point(angle) == expected


# --------------------------------------------------------------------------- #
# projection and interpolation
# --------------------------------------------------------------------------- #
def test_move_towards_north_by_one_degree() -> None:
    lat, lng = move_towards(0.0, 0.0, 0.0, 111195.0)
    assert lat == pytest.approx(1.0, abs=1e-3)
    assert lng == pytest.approx(0.0, abs=1e-6)


def test_move_towards_east_keeps_latitude() -> None:
    lat, lng = move_towards(11.0, 75.0, 90.0, 1000.0)
    assert lat == pytest.approx(11.0, abs=1e-6)
    assert lng > 75.0


def test_move_towards_wraps_longitude() -> None:
    _, lng = move_towards(0.0, 179.9, 90.0, 100_000.0)
    assert -180.0 <= lng <= 180.0


def test_move_towards_round_trips_with_haversine() -> None:
    lat, lng = move_towards(11.2588, 75.7804, 137.0, 2500.0)
    assert haversine_m(11.2588, 75.7804, lat, lng) == pytest.approx(2500.0, rel=1e-3)


def test_interpolate_midpoint() -> None:
    lat, lng = interpolate(0.0, 0.0, 2.0, 4.0, 0.5)
    assert (lat, lng) == (1.0, 2.0)


def test_interpolate_clamps() -> None:
    assert interpolate(0.0, 0.0, 1.0, 1.0, -3.0) == (0.0, 0.0)
    assert interpolate(0.0, 0.0, 1.0, 1.0, 7.0) == (1.0, 1.0)


# --------------------------------------------------------------------------- #
# point-to-segment distance (the off-route check)
# --------------------------------------------------------------------------- #
def test_distance_to_segment_perpendicular() -> None:
    # 0.001 degrees of latitude is about 111 m; a point 0.001 east is ~111 m away.
    distance = distance_to_segment_m(0.0, 0.001, 0.0, 0.0, 0.0, 0.0)
    assert distance == pytest.approx(111.0, rel=0.02)


def test_distance_to_segment_beyond_the_end_uses_the_endpoint() -> None:
    # The segment runs north; the point is north of its end.
    distance = distance_to_segment_m(2.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    assert distance == pytest.approx(111_195.0, rel=1e-3)


def test_distance_to_segment_on_the_line_is_zero() -> None:
    assert distance_to_segment_m(0.5, 0.0, 0.0, 0.0, 1.0, 0.0) < 1.0


def test_distance_to_degenerate_segment_is_point_distance() -> None:
    distance = distance_to_segment_m(0.0, 0.001, 0.0, 0.0, 0.0, 0.0)
    point_distance = haversine_m(0.0, 0.001, 0.0, 0.0)
    assert distance == pytest.approx(point_distance, rel=0.02)


def test_distance_to_path_picks_the_nearest_leg() -> None:
    # A path that runs east along the equator, then north up the meridian.
    path = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
    # A point exactly on the first leg.
    assert distance_to_path_m(0.0, 0.5, path) < 5.0
    # A point exactly on the second leg.
    assert distance_to_path_m(0.5, 1.0, path) < 5.0
    # A point well off both legs is far away.
    assert distance_to_path_m(0.5, 0.5, path) > 40_000.0


def test_distance_to_path_with_one_point() -> None:
    assert distance_to_path_m(1.0, 1.0, [(0.0, 0.0)]) == pytest.approx(haversine_m(0.0, 0.0, 1.0, 1.0))


def test_distance_to_path_empty_is_infinite() -> None:
    assert distance_to_path_m(0.0, 0.0, []) == float("inf")


def test_path_length_matches_the_sum_of_legs() -> None:
    path = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    expected = haversine_m(0.0, 0.0, 1.0, 0.0) + haversine_m(1.0, 0.0, 1.0, 1.0)
    assert path_length_m(path) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# simplify (used for drawing the wrap-up map)
# --------------------------------------------------------------------------- #
def test_simplify_keeps_the_endpoints() -> None:
    path = [(0.0, 0.0), (0.00001, 0.0), (0.00002, 0.0), (1.0, 0.0)]
    simplified = simplify_path(path, tolerance_m=5.0)
    assert simplified[0] == (0.0, 0.0)
    assert simplified[-1] == (1.0, 0.0)


def test_simplify_drops_a_straight_line() -> None:
    path = [(index * 0.001, 0.0) for index in range(50)]
    assert len(simplify_path(path, tolerance_m=5.0)) == 2


def test_simplify_keeps_a_real_corner() -> None:
    path = [(0.0, 0.0), (0.01, 0.0), (0.01, 0.01)]
    assert len(simplify_path(path, tolerance_m=5.0)) == 3


def test_simplify_handles_short_paths() -> None:
    assert simplify_path([], 5.0) == []
    assert simplify_path([(1.0, 2.0)], 5.0) == [(1.0, 2.0)]


def test_bounding_box() -> None:
    box = bounding_box([(1.0, 2.0), (3.0, 0.5), (2.0, 4.0)])
    assert box == (1.0, 0.5, 3.0, 4.0)


def test_bounding_box_of_nothing_is_none() -> None:
    assert bounding_box([]) is None


# --------------------------------------------------------------------------- #
# daylight (safety)
# --------------------------------------------------------------------------- #
def test_daylight_window_is_longer_in_summer_than_winter() -> None:
    import datetime as dt

    summer = daylight_window(11.25, 75.78, dt.date(2026, 6, 21))
    winter = daylight_window(11.25, 75.78, dt.date(2026, 12, 21))
    assert (summer[1] - summer[0]) > (winter[1] - winter[0])


def test_after_dark_at_midnight() -> None:
    import datetime as dt

    midnight = dt.datetime(2026, 3, 14, 0, 30, tzinfo=dt.timezone.utc)
    assert is_after_dark(11.25, 75.78, midnight) is True


def test_not_after_dark_at_noon() -> None:
    import datetime as dt

    noon = dt.datetime(2026, 3, 14, 12, 0, tzinfo=dt.timezone.utc)
    assert is_after_dark(11.25, 75.78, noon) is False


def test_daylight_window_of_a_polar_latitude_is_handled() -> None:
    """Inside the Arctic circle the maths must not raise; it clamps."""
    sunrise, sunset = daylight_window(78.0, 15.0)
    assert sunrise <= sunset
