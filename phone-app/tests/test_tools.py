"""Offline tests for the developer tools.

The tools are part of the quality bar (brief §14: a fake-player script, a GPX
replay, a plan fuzzer), so they get tests like anything else — but only their
testable halves: the photo synthesis, the task-to-colour guess, the GPX writer and
reader. Anything that needs a live server is in ``test_server_integration.py``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from farfromhome_device.hal.sim import parse_gpx, write_demo_gpx


# --------------------------------------------------------------------------- #
# tools/fake_phone.py
# --------------------------------------------------------------------------- #
def test_fake_phone_module_imports_without_a_server() -> None:
    """Importing a tool must not require a server, a network or Qt."""
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "tools" / "fake_phone.py"
    spec = importlib.util.spec_from_file_location("fake_phone_tool", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "FakePhone")
    assert hasattr(module, "sample_photo")


def test_sample_photo_is_a_jpeg_with_the_requested_dominant_colour() -> None:
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "tools" / "fake_phone.py"
    spec = importlib.util.spec_from_file_location("fake_phone_tool2", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    from PIL import Image

    for name in ("red", "blue", "yellow"):
        data = module.sample_photo(name)
        assert data[:2] == b"\xff\xd8", "sample_photo must produce a JPEG"
        with Image.open(io.BytesIO(data)) as image:
            centre = image.convert("RGB").getpixel((450, 337))
        # The requested colour dominates the frame, so the server's colour check has
        # something real to compare against.
        expected = module.COLOURS[name]
        assert all(abs(centre[index] - expected[index]) < 60 for index in range(3)), (name, centre)


def test_fake_phone_guesses_the_colour_from_a_task() -> None:
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "tools" / "fake_phone.py"
    spec = importlib.util.spec_from_file_location("fake_phone_tool3", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    assert module._guess_colour({"item_key": "red", "text": "irrelevant"}) == "red"
    assert module._guess_colour({"item_key": "", "text": "Photograph something purple"}) == "purple"
    # With no clue at all it still returns a colour rather than failing.
    assert module._guess_colour({"text": "Photograph anything"}) in module.COLOURS


# --------------------------------------------------------------------------- #
# tools/gpx_replay.py
# --------------------------------------------------------------------------- #
def test_write_demo_gpx_round_trips(tmp_path: Path) -> None:
    path = write_demo_gpx(tmp_path / "walk.gpx", start_lat=11.2588, start_lng=75.7804)
    assert path.is_file()
    track = parse_gpx(path)
    assert len(track) > 10
    # The generated walk is the brief's 4.1 km wander.
    assert 3.5 < track.distance_m / 1000.0 < 4.6
    assert track.start[0] == pytest.approx(11.2588, abs=1e-4)
    assert track.end[1] == pytest.approx(75.7804, abs=0.02)


def test_gpx_has_a_quiet_zone_of_nothing_but_track_points(tmp_path: Path) -> None:
    path = write_demo_gpx(tmp_path / "walk.gpx")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<?xml")
    assert "<trkpt" in text
    assert "topografix" in text, "the GPX namespace must be there for other tools"


def test_parse_gpx_rejects_a_file_with_one_point(tmp_path: Path) -> None:
    path = tmp_path / "bad.gpx"
    path.write_text(
        '<?xml version="1.0"?><gpx><trk><trkseg>'
        '<trkpt lat="11.0" lon="75.0"/></trkseg></trk></gpx>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        parse_gpx(path)


def test_parse_gpx_reads_a_hand_written_route(tmp_path: Path) -> None:
    path = tmp_path / "route.gpx"
    path.write_text(
        '<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1">'
        "<rte><rtept lat=\"11.0\" lon=\"75.0\"><ele>10</ele></rtept>"
        "<rtept lat=\"11.01\" lon=\"75.0\"><ele>12</ele></rtept></rte></gpx>",
        encoding="utf-8",
    )
    track = parse_gpx(path)
    assert len(track) == 2
    assert track.points[0][3] == 10.0


def test_gpx_replay_module_exposes_a_main() -> None:
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "tools" / "gpx_replay.py"
    spec = importlib.util.spec_from_file_location("gpx_replay_tool", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
    assert callable(module.replay)


# --------------------------------------------------------------------------- #
# tools/plan_fuzzer.py
# --------------------------------------------------------------------------- #
def test_plan_fuzzer_generates_varied_requests() -> None:
    import importlib.util
    import random

    path = Path(__file__).resolve().parent.parent / "tools" / "plan_fuzzer.py"
    spec = importlib.util.spec_from_file_location("plan_fuzzer_tool", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    rng = random.Random(4)
    texts = {module.make_request(rng)[0] for _ in range(60)}
    assert len(texts) > 30, "the fuzzer must actually vary its requests"
    for text, start in (module.make_request(rng) for _ in range(20)):
        assert isinstance(text, str)
        assert len(start) == 2
        assert -90 <= start[0] <= 90 and -180 <= start[1] <= 180


def test_plan_fuzzer_nonsense_inputs_are_actually_nonsense() -> None:
    import importlib.util
    import random

    path = Path(__file__).resolve().parent.parent / "tools" / "plan_fuzzer.py"
    spec = importlib.util.spec_from_file_location("plan_fuzzer_tool2", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    rng = random.Random(0)
    texts = [module.make_request(rng, nonsense=True)[0] for _ in range(20)]
    assert any(text.strip() == "" for text in texts)
    assert any(len(text) > 200 for text in texts)


def test_start_points_cover_both_hemispheres() -> None:
    """A planner that assumes northern latitudes must fail loudly somewhere."""
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "tools" / "plan_fuzzer.py"
    spec = importlib.util.spec_from_file_location("plan_fuzzer_tool3", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    latitudes = [lat for lat, _ in module.START_POINTS]
    assert any(lat < 0 for lat in latitudes), "no southern-hemisphere start point"
    assert any(lat > 45 for lat in latitudes), "no high-latitude start point"
