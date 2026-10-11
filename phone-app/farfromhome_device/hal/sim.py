"""Simulators for every HAL device.

Requirement (brief §0.3): *"Everything must run without real hardware. Every
sensor and network dependency sits behind an interface with a simulator, so I can
test on a laptop."*

So the simulators are first-class, not stubs:

* :class:`SimGps` replays a GPX file if given one, otherwise synthesises a walk
  from a start point along a configurable route. It can also be *stepped* in fake
  time, which is what makes the engine tests deterministic and fast.
* :class:`SimImu` produces magnetometer/gyro/accelerometer samples consistent with
  a scripted truth heading, including a configurable noise level and a "drift"
  mode used to prove the fusion filter rejects a bad magnetometer.
* :class:`SimCamera` writes real JPEGs (Pillow), optionally containing a dominant
  colour, so the colour-hunt scoring path can be exercised with no camera.
* :class:`SimMic` synthesises WAV audio; :class:`SimSpeaker` records what would
  have been spoken.
* :class:`SimDisplay` reports the screen geometry; :class:`SimButtons` is a queue
  that the UI, tests and :file:`tools/gpx_replay.py` can inject presses into.
"""

from __future__ import annotations

import io
import logging
import math
import random
import struct
import time
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..core.models import ImuSample, Position, utcnow
from ..engine.geo import bearing_deg, haversine_m, interpolate, move_towards
from .base import Buttons, Camera, Display, Gps, HardwareBundle, Imu, Mic, Speaker

logger = logging.getLogger("ffh.hal.sim")


# --------------------------------------------------------------------------- #
# GPX
# --------------------------------------------------------------------------- #
class GpxTrack:
    """A parsed GPX track: ordered ``(lat, lng, time|None, elevation|None)``."""

    def __init__(self, points: Sequence[Tuple[float, float, Optional[datetime], Optional[float]]],
                 name: str = "") -> None:
        self.points = list(points)
        self.name = name

    def __len__(self) -> int:
        return len(self.points)

    @property
    def distance_m(self) -> float:
        return sum(
            haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(self.points, self.points[1:])
        )

    @property
    def start(self) -> Tuple[float, float]:
        return (self.points[0][0], self.points[0][1])

    @property
    def end(self) -> Tuple[float, float]:
        return (self.points[-1][0], self.points[-1][1])

    def bounding_box(self) -> Tuple[float, float, float, float]:
        lats = [point[0] for point in self.points]
        lngs = [point[1] for point in self.points]
        return (min(lats), min(lngs), max(lats), max(lngs))


def parse_gpx(path: Path | str) -> GpxTrack:
    """Read a GPX 1.0/1.1 track with ``xml.etree`` (no extra dependency).

    Accepts ``<trkpt>`` (preferred), ``<rtept>`` and ``<wpt>`` in that order of
    preference, so hand-written route files work too.
    """
    import xml.etree.ElementTree as ET

    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"GPX file not found: {file_path}")

    tree = ET.parse(str(file_path))
    root = tree.getroot()

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def collect(wanted: str) -> List[Any]:
        return [element for element in root.iter() if local_name(element.tag) == wanted]

    candidates = collect("trkpt") or collect("rtept") or collect("wpt")
    points: List[Tuple[float, float, Optional[datetime], Optional[float]]] = []
    for element in candidates:
        try:
            lat = float(element.attrib["lat"])
            lng = float(element.attrib["lon"])
        except (KeyError, ValueError):
            continue
        when: Optional[datetime] = None
        elevation: Optional[float] = None
        for child in element:
            name = local_name(child.tag)
            if name == "time" and child.text:
                text = child.text.strip().replace("Z", "+00:00")
                try:
                    when = datetime.fromisoformat(text)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                except ValueError:
                    when = None
            elif name in ("ele", "elevation") and child.text:
                try:
                    elevation = float(child.text)
                except ValueError:
                    elevation = None
        points.append((lat, lng, when, elevation))

    if len(points) < 2:
        raise ValueError(f"GPX {file_path} has fewer than two usable points")

    name_element = next((element for element in root.iter() if local_name(element.tag) == "name"), None)
    return GpxTrack(points, name=(name_element.text if name_element is not None and name_element.text else ""))


def synthetic_track(
    start_lat: float,
    start_lng: float,
    legs: Sequence[Tuple[float, float]] = ((0.0, 900.0), (55.0, 1100.0), (-35.0, 1400.0), (100.0, 700.0)),
    samples_per_meter: float = 0.1,
) -> GpxTrack:
    """Build a believable walk so ``--simulate`` needs no GPX file.

    ``legs`` are ``(bearing_deg, distance_m)`` pairs. The default makes a
    4.1 km L-shaped wander, which is the demo trip in the brief's screenshots.
    """
    lat, lng = start_lat, start_lng
    points: List[Tuple[float, float, Optional[datetime], Optional[float]]] = [(lat, lng, None, None)]
    for bearing, distance in legs:
        step_m = max(5.0, 1.0 / max(0.001, samples_per_meter))
        travelled = 0.0
        while travelled < distance:
            lat, lng = move_towards(lat, lng, bearing, step_m)
            travelled += step_m
            points.append((lat, lng, None, None))
    return GpxTrack(points, name="synthetic walk")


# --------------------------------------------------------------------------- #
# GPS
# --------------------------------------------------------------------------- #
class SimGps(Gps):
    """Replays a track at a configurable speed, optionally in fake time.

    Two modes:

    * **realtime** (default): :meth:`read` returns a new fix when wall-clock time
      has advanced past the interval between track points.
    * **stepped**: :meth:`advance` moves the cursor explicitly. The engine tests
      use this so a 45-minute walk runs in milliseconds.
    """

    def __init__(
        self,
        track: Optional[GpxTrack] = None,
        start_lat: float = 11.2588,
        start_lng: float = 75.7804,
        speed_mps: float = 1.4,
        accuracy_m: float = 4.0,
        noise_m: float = 1.5,
        seed: int = 7,
        warmup_fixes: int = 3,
    ) -> None:
        super().__init__(name="gps")
        self.track = track or synthetic_track(start_lat, start_lng)
        self.speed_mps = max(0.2, speed_mps)
        self.accuracy_m = accuracy_m
        self.noise_m = noise_m
        self.warmup_fixes = warmup_fixes
        self._random = random.Random(seed)
        self._cursor = 0.0  # fractional index into the track
        self._fixes = 0
        self._last_emit = 0.0
        self.emit_interval_s = 1.0
        self.lost = False  # flip to simulate GPS loss
        self._last_position: Optional[Position] = None
        self._pace_jitter = 0.0
        #: Seconds of *simulated* time elapsed. Fixes are stamped with the wall
        #: clock plus this, so a caller that steps the simulation faster than real
        #: time still produces self-consistent timestamps. Without this the engine's
        #: "implausible jump" check would reject every fix during a fast replay.
        self._sim_seconds = 0.0
        self._wall_origin = utcnow()
        #: When set, every fix is pinned to this point with no noise. The demo uses
        #: it to place the walker exactly at the final waypoint once the track ends,
        #: which is what a real walker standing at the destination looks like.
        self._pinned: Optional[Tuple[float, float]] = None
        #: A one-shot fix handed in by :meth:`feed`, returned exactly as given.
        self._pending_fix: Optional[Position] = None

    # --- control ------------------------------------------------------------
    def _open_impl(self) -> bool:
        if len(self.track) < 2:
            raise ValueError("SimGps needs a track with at least two points")
        self._cursor = 0.0
        self._fixes = 0
        self._last_emit = 0.0
        return True

    def set_track(self, track: GpxTrack) -> None:
        self.track = track
        self._cursor = 0.0

    def follow_plan(self, plan: Any, samples_per_meter: float = 0.1) -> GpxTrack:
        """Route the simulated walk through the plan's waypoints to the destination.

        Without this, a generic synthetic walk never passes through the geofences and
        the demo would never arrive. Building the track *from the plan* means the
        simulated user walks the route the engine is checking against, which is what
        a real user with the arrow does.
        """
        path: List[Tuple[float, float]] = [(plan.spec.start_lat or 0.0, plan.spec.start_lng or 0.0)]
        path += [(waypoint.lat, waypoint.lng) for waypoint in sorted(plan.waypoints, key=lambda w: w.idx)]
        path.append((plan.destination.lat, plan.destination.lng))
        return self.follow_path([point for point in path if point[0] or point[1]], samples_per_meter)

    def follow_path(
        self,
        path: Sequence[Tuple[float, float]],
        samples_per_meter: float = 0.1,
    ) -> GpxTrack:
        """Replace the track with a walk that visits every point in ``path`` in order."""
        if len(path) < 2:
            raise ValueError("follow_path needs at least two points")
        points: List[Tuple[float, float, Optional[datetime], Optional[float]]] = [
            (path[0][0], path[0][1], None, None)
        ]
        step_m = max(5.0, 1.0 / max(0.001, samples_per_meter))
        for (lat1, lng1), (lat2, lng2) in zip(path, path[1:]):
            leg_m = haversine_m(lat1, lng1, lat2, lng2)
            if leg_m < 1.0:
                continue
            travelled = 0.0
            while travelled < leg_m:
                travelled = min(leg_m, travelled + step_m)
                lat, lng = interpolate(lat1, lng1, lat2, lng2, travelled / leg_m)
                points.append((lat, lng, None, None))
            # Anchor the leg exactly on its endpoint so a geofence is always crossed.
            if (points[-1][0], points[-1][1]) != (lat2, lng2):
                points.append((lat2, lng2, None, None))
        track = GpxTrack(points, name="planned route")
        self.set_track(track)
        return track

    def seek(self, fraction: float) -> None:
        """Jump to a fraction of the track (0..1). Used by the arrival demo."""
        self._cursor = max(0.0, min(1.0, fraction)) * (len(self.track) - 1)

    def advance(self, seconds: float, time_step_s: Optional[float] = None) -> Optional[Position]:
        """Step the simulation forward in fake time and return the new fix.

        ``advance(6)`` walks six simulated seconds and reports six seconds on the
        clock, which is what a GPX replay and the engine tests want.
        ``advance(840, time_step_s=6)`` walks 840 m but reports only six seconds,
        which is what a laptop demo wants: an accelerated walk that still looks
        like a plausible one to the engine's jump and dwell checks.
        """
        return self._step(seconds, time_step_s=time_step_s)

    def advance_metres(self, metres: float, time_step_s: float = 6.0) -> Optional[Position]:
        """Walk a fixed distance while the clock advances a normal walking interval."""
        return self._step(metres / max(0.2, self.speed_mps), time_step_s=time_step_s)

    def pin(self, lat: Optional[float] = None, lng: Optional[float] = None) -> None:
        """Stop walking and stand still at a point (defaults to the track's end).

        Pinning is noise-free, so it models a person who has arrived and stopped:
        the arrow keeps pointing, the geofence stays satisfied, and the trip
        distance stops growing.
        """
        if lat is None or lng is None:
            last = self.track.points[-1]
            lat, lng = last[0], last[1]
        self._pinned = (float(lat), float(lng))
        self._cursor = float(len(self.track) - 1)

    def feed(self, position: Position) -> None:
        """Serve this exact fix (with its own timestamp) on the next :meth:`read`.

        The engine tests use this to replay a scripted route through the real engine
        without inventing a GPS driver: the fix is returned untouched, then the
        simulator resumes normal behaviour.
        """
        self._pending_fix = position
        self._sim_seconds = max(
            self._sim_seconds, (position.t - self._wall_origin).total_seconds()
        )

    def unpin(self) -> None:
        self._pinned = None

    @property
    def pinned(self) -> bool:
        return self._pinned is not None

    @property
    def walk_complete(self) -> bool:
        return self._cursor >= len(self.track) - 1

    @property
    def progress(self) -> float:
        return self._cursor / max(1, len(self.track) - 1)

    @property
    def travelling(self) -> bool:
        return self._cursor < len(self.track) - 1

    # --- reading ------------------------------------------------------------
    def read(self) -> Optional[Position]:
        """Return the current fix.

        Always steps the simulation forward by the wall-clock interval since the
        last read, so the fix is never identical to the previous one. That matters
        for two reasons: the engine drops a fix that has not moved as a duplicate,
        and the fusion filter needs a usable GPS course.
        """
        if self._pending_fix is not None:
            fix, self._pending_fix = self._pending_fix, None
            return fix
        now = time.monotonic()
        elapsed = now - self._last_emit if self._last_emit else self.emit_interval_s
        self._last_emit = now
        return self._step(elapsed)

    def _step(self, seconds: float, time_step_s: Optional[float] = None) -> Optional[Position]:
        self.reads += 1
        #: Clock advance. Defaults to the walked duration; a caller replaying in
        #: fast-forward passes a realistic value instead.
        clock_step = max(0.0, seconds if time_step_s is None else time_step_s)
        if self.lost:
            self._sim_seconds += clock_step
            return None

        self._sim_seconds += clock_step
        self._pace_jitter = 0.85 + 0.3 * self._random.random()
        metres = self.speed_mps * self._pace_jitter * max(0.0, seconds)
        self._advance_metres(metres)

        if self._fixes < self.warmup_fixes:
            self._fixes += 1
            accuracy = self.accuracy_m * 3.0
        else:
            accuracy = self.accuracy_m * (0.7 + 0.6 * self._random.random())

        position = self._position_at_cursor(accuracy)
        self._last_position = position
        return position

    def _advance_metres(self, metres: float) -> None:
        remaining = metres
        while remaining > 0 and self._cursor < len(self.track) - 1:
            index = int(self._cursor)
            fraction = self._cursor - index
            lat1, lng1 = self.track.points[index][0], self.track.points[index][1]
            lat2, lng2 = self.track.points[index + 1][0], self.track.points[index + 1][1]
            segment_m = haversine_m(lat1, lng1, lat2, lng2)
            if segment_m < 0.01:
                self._cursor += 1
                continue
            travelled_in_segment = fraction * segment_m
            left_in_segment = segment_m - travelled_in_segment
            if left_in_segment > remaining:
                self._cursor = index + (travelled_in_segment + remaining) / segment_m
                remaining = 0.0
            else:
                remaining -= left_in_segment
                self._cursor = float(index + 1)

    def _position_at_cursor(self, accuracy: float) -> Position:
        if self._pinned is not None:
            lat, lng = self._pinned
            ahead_lat, ahead_lng = lat, lng
            behind_lat, behind_lng = (
                (self.track.points[-2][0], self.track.points[-2][1])
                if len(self.track) > 1
                else (lat, lng)
            )
        else:
            index = int(self._cursor)
            fraction = self._cursor - index
            if index >= len(self.track) - 1:
                lat, lng = self.track.points[-1][0], self.track.points[-1][1]
                ahead_lat, ahead_lng = lat, lng
                behind_lat, behind_lng = self.track.points[-2][0], self.track.points[-2][1]
            else:
                lat1, lng1 = self.track.points[index][0], self.track.points[index][1]
                lat2, lng2 = self.track.points[index + 1][0], self.track.points[index + 1][1]
                lat, lng = interpolate(lat1, lng1, lat2, lng2, fraction)
                behind_lat, behind_lng = lat1, lng1
                ahead_lat, ahead_lng = lat2, lng2

        # Jitter proportional to the accuracy, so the fusion filter has something
        # honest to work with.
        if self.noise_m > 0 and self._pinned is None:
            delta = self.noise_m / 111320.0
            lat += self._random.gauss(0.0, delta)
            lng += self._random.gauss(0.0, delta)

        course = bearing_deg(behind_lat, behind_lng, ahead_lat, ahead_lng)
        return Position(
            lat=lat,
            lng=lng,
            accuracy_m=accuracy,
            speed_mps=0.0 if self._pinned is not None else self.speed_mps * self._pace_jitter,
            course_deg=course,
            satellites=9 if self._fixes > self.warmup_fixes else 4,
            t=self._wall_origin + timedelta(seconds=self._sim_seconds),
        )

    @property
    def fix_age_s(self) -> Optional[float]:
        return 0.0 if self._last_position else None

    def status(self) -> Dict[str, Any]:
        status = super().status()
        status.update(
            {
                "progress": round(self.progress, 3),
                "track_points": len(self.track),
                "track_km": round(self.track.distance_m / 1000.0, 2),
                "lost": self.lost,
            }
        )
        return status


# --------------------------------------------------------------------------- #
# IMU
# --------------------------------------------------------------------------- #
class SimImu(Imu):
    """IMU samples around a truth heading.

    ``truth_heading`` is what the world says the user is facing. The simulator
    adds gyro-consistent rotation and a magnetometer that can be told to drift or
    go completely wrong, which is how we prove the fusion filter degrades safely
    (brief §5.2, §12 "Magnetometer noisy").
    """

    def __init__(
        self,
        truth_heading: float = 45.0,
        magnetometer_error_deg: float = 0.0,
        noise_deg: float = 1.2,
        gyro_noise_dps: float = 0.4,
        seed: int = 11,
        sampling_hz: float = 20.0,
    ) -> None:
        super().__init__(name="imu")
        self.truth_heading = truth_heading
        self.magnetometer_error_deg = magnetometer_error_deg
        self.noise_deg = noise_deg
        self.gyro_noise_dps = gyro_noise_dps
        self.sampling_hz = sampling_hz
        self._random = random.Random(seed)
        self._last: Optional[ImuSample] = None
        self._last_wall = time.monotonic()
        self.magnetometer_healthy = True
        self.calibrated = False
        self._tilt_deg = 4.0

    def _open_impl(self) -> bool:
        self._last_wall = time.monotonic()
        return True

    def calibrate(self, seconds: float = 6.0) -> bool:
        self.calibrated = True
        self.magnetometer_error_deg = 0.0
        self.magnetometer_healthy = True
        return True

    def set_truth_heading(self, heading: float) -> None:
        self.truth_heading = heading % 360.0

    def rotate(self, delta_deg: float) -> None:
        self.truth_heading = (self.truth_heading + delta_deg) % 360.0

    def read(self) -> Optional[ImuSample]:
        now = time.monotonic()
        dt = max(1e-3, now - self._last_wall)
        self._last_wall = now
        self.reads += 1
        return self.sample(dt)

    def sample(self, dt: float = 0.05) -> ImuSample:
        """One sample with an explicit ``dt``. Tests call this directly."""
        gyro_z = self._random.gauss(0.0, self.gyro_noise_dps)
        # Gravity vector tilted by a small, slowly wandering angle.
        self._tilt_deg += self._random.gauss(0.0, 0.05)
        self._tilt_deg = max(-12.0, min(12.0, self._tilt_deg))
        tilt = math.radians(self._tilt_deg)

        magnetometer_heading = self.truth_heading + self.magnetometer_error_deg
        if not self.magnetometer_healthy:
            magnetometer_heading += self._random.gauss(0.0, 25.0)
        magnetometer_heading += self._random.gauss(0.0, self.noise_deg)

        heading_rad = math.radians(magnetometer_heading)
        # Field strength ~48 uT at this latitude; project onto the tilted frame.
        field = 48.0
        mx = field * math.sin(heading_rad) * math.cos(tilt)
        my = field * math.cos(heading_rad) * math.cos(tilt)
        mz = -field * math.sin(tilt)

        sample = ImuSample(
            ax=self._random.gauss(0.0, 0.05),
            ay=self._random.gauss(0.0, 0.05),
            az=9.81 + self._random.gauss(0.0, 0.05),
            gx=self._random.gauss(0.0, self.gyro_noise_dps),
            gy=self._random.gauss(0.0, self.gyro_noise_dps),
            gz=gyro_z,
            mx=mx,
            my=my,
            mz=mz,
            t=utcnow(),
        )
        self._last = sample
        return sample

    def status(self) -> Dict[str, Any]:
        status = super().status()
        status.update(
            {
                "truth_heading": round(self.truth_heading, 1),
                "magnetometer_healthy": self.magnetometer_healthy,
                "magnetometer_error_deg": self.magnetometer_error_deg,
                "calibrated": self.calibrated,
            }
        )
        return status


# --------------------------------------------------------------------------- #
# Camera
# --------------------------------------------------------------------------- #
class SimCamera(Camera):
    """Generates real JPEGs with Pillow.

    Optionally bakes a dominant colour into the frame so the colour-hunt scoring
    path (and the server's colour check, brief 02 §8.5) can be exercised offline.
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        label: str = "sim",
        seed: int = 3,
        image_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(name="camera")
        self.width = width
        self.height = height
        self.label = label
        self._random = random.Random(seed)
        #: If set, captures cycle through the JPEGs in this folder (a cheap way
        #: to replay a real photo album).
        self.image_dir = Path(image_dir) if image_dir else None
        self._album: List[Path] = []
        self._album_cursor = 0
        self.sequences = 0

    def _open_impl(self) -> bool:
        if self.image_dir and self.image_dir.is_dir():
            self._album = sorted(
                path for path in self.image_dir.iterdir()
                if path.suffix.lower() in (".jpg", ".jpeg", ".png")
            )
            logger.info("SimCamera loaded %d album images from %s", len(self._album), self.image_dir)
        return True

    def capture(self) -> Optional[bytes]:
        self.reads += 1
        self.sequences += 1
        if self._album:
            path = self._album[self._album_cursor % len(self._album)]
            self._album_cursor += 1
            try:
                return path.read_bytes()
            except OSError as exc:
                self._record(f"album read failed: {exc}")
        return self.render(f"{self.label} #{self.sequences}")

    def render(self, caption: str, dominant: Optional[tuple[int, int, int]] = None) -> bytes:
        """Draw a JPEG with a caption and an optional dominant colour block."""
        from PIL import Image, ImageDraw

        width, height = self.width, self.height
        base = dominant or (
            self._random.randint(40, 220),
            self._random.randint(40, 220),
            self._random.randint(40, 220),
        )
        image = Image.new("RGB", (width, height), base)
        draw = ImageDraw.Draw(image)

        # A few shapes so the thumbnail is not a flat rectangle.
        for _ in range(6):
            x0 = self._random.randint(0, width - 120)
            y0 = self._random.randint(0, height - 120)
            x1 = x0 + self._random.randint(60, 220)
            y1 = y0 + self._random.randint(60, 220)
            shade = tuple(min(255, max(0, channel + self._random.randint(-70, 70))) for channel in base)
            draw.ellipse([x0, y0, x1, y1], fill=shade)

        draw.rectangle([8, 8, width - 8, height - 8], outline=(255, 255, 255), width=3)
        draw.text((24, 24), caption, fill=(255, 255, 255))
        draw.text((24, height - 40), f"{width}x{height} simulated capture", fill=(255, 255, 255))

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=82)
        return buffer.getvalue()

    def preview_frame(self) -> Optional[bytes]:
        return self.render("live preview")

    @property
    def resolution(self) -> tuple[int, int]:
        return (self.width, self.height)


# --------------------------------------------------------------------------- #
# Mic / Speaker
# --------------------------------------------------------------------------- #
class SimMic(Mic):
    """Synthesises WAV audio (a tone plus noise) instead of recording."""

    def __init__(self, sample_rate: int = 16000, canned_phrases: Optional[Sequence[str]] = None) -> None:
        super().__init__(name="mic")
        self.sample_rate = sample_rate
        self.canned_phrases = list(canned_phrases or [])
        self._phrase_cursor = 0
        self.last_phrase: Optional[str] = None

    def record(self, seconds: float = 3.0) -> Optional[bytes]:
        self.reads += 1
        frames = max(1, int(self.sample_rate * max(0.2, seconds)))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            samples = bytearray()
            for index in range(frames):
                value = int(6000 * math.sin(2 * math.pi * 220.0 * index / self.sample_rate))
                samples += struct.pack("<h", max(-32768, min(32767, value)))
            handle.writeframes(bytes(samples))
        return buffer.getvalue()

    def push_to_talk_stop(self) -> Optional[bytes]:
        return self.record(1.5)

    def next_canned_phrase(self) -> str:
        """What the fake speech-to-text should return next."""
        if not self.canned_phrases:
            return "a chill walk by the water for two, about an hour"
        phrase = self.canned_phrases[self._phrase_cursor % len(self.canned_phrases)]
        self._phrase_cursor += 1
        self.last_phrase = phrase
        return phrase

    def status(self) -> Dict[str, Any]:
        status = super().status()
        status["canned_phrases"] = len(self.canned_phrases)
        return status


class SimSpeaker(Speaker):
    """Records every utterance so tests and the demo log can assert on them."""

    def __init__(self, echo: bool = True) -> None:
        super().__init__(name="speaker")
        self.echo = echo
        self.spoken: List[str] = []
        self.last_spoken: Optional[str] = None

    def say(self, text: str) -> bool:
        self.reads += 1
        cleaned = text.strip()
        if not cleaned:
            return False
        self.spoken.append(cleaned)
        del self.spoken[:-200]
        self.last_spoken = cleaned
        if self.echo:
            logger.info("[agent says] %s", cleaned)
        return True

    @property
    def tts_available(self) -> bool:
        return True

    def status(self) -> Dict[str, Any]:
        status = super().status()
        status["spoken_count"] = len(self.spoken)
        return status


# --------------------------------------------------------------------------- #
# Display / Buttons
# --------------------------------------------------------------------------- #
class SimDisplay(Display):
    """Reports a screen size. The real UI lives in ``ui/``; this is the HAL view."""

    def __init__(self, width: int = 800, height: int = 480, dpi: int = 160) -> None:
        super().__init__(name="display")
        self.width = width
        self.height = height
        self.dpi = dpi
        self.backlight = 1.0

    def info(self) -> Dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
            "backlight": self.backlight,
            "simulated": True,
        }

    def set_backlight(self, level: float) -> None:
        self.backlight = max(0.05, min(1.0, level))


class SimButtons(Buttons):
    """A press queue that anything can push into."""

    def __init__(self, script: Optional[Iterable[str]] = None) -> None:
        super().__init__(name="buttons")
        self._queue: List[str] = list(script or [])
        self.history: List[Tuple[float, str]] = []

    def press(self, name: str) -> None:
        if name not in self.KNOWN:
            logger.debug("unknown button %r ignored", name)
            return
        self._queue.append(name)

    def press_many(self, names: Iterable[str]) -> None:
        for name in names:
            self.press(name)

    def poll(self) -> List[str]:
        self.reads += 1
        pressed = list(self._queue)
        self._queue.clear()
        now = time.time()
        self.history.extend((now, name) for name in pressed)
        del self.history[:-200]
        return pressed

    def pending(self) -> int:
        return len(self._queue)


# --------------------------------------------------------------------------- #
# Bundle construction (simulator flavour)
# --------------------------------------------------------------------------- #
def build_sim_bundle(
    start_lat: float = 11.2588,
    start_lng: float = 75.7804,
    speed_mps: float = 1.4,
    gpx_path: Optional[Path | str] = None,
    truth_heading: float = 45.0,
    display_size: tuple[int, int] = (800, 480),
    image_dir: Optional[Path | str] = None,
    button_script: Optional[Iterable[str]] = None,
    canned_phrases: Optional[Sequence[str]] = None,
) -> HardwareBundle:
    """All seven simulated devices, wired together."""
    track: Optional[GpxTrack] = None
    if gpx_path:
        try:
            track = parse_gpx(gpx_path)
        except (OSError, ValueError) as exc:
            logger.warning("could not read %s (%s); falling back to a synthetic walk", gpx_path, exc)

    return HardwareBundle(
        gps=SimGps(track=track, start_lat=start_lat, start_lng=start_lng, speed_mps=speed_mps),
        imu=SimImu(truth_heading=truth_heading),
        camera=SimCamera(image_dir=Path(image_dir) if image_dir else None),
        mic=SimMic(canned_phrases=canned_phrases),
        speaker=SimSpeaker(),
        display=SimDisplay(width=display_size[0], height=display_size[1]),
        buttons=SimButtons(script=button_script),
        mode="sim",
    )


def write_demo_gpx(path: Path | str, start_lat: float = 11.2588, start_lng: float = 75.7804,
                   points: int = 400) -> Path:
    """Write a synthetic walk as a real GPX file, so the replay tool has input."""
    track = synthetic_track(start_lat, start_lng)
    step = max(1, len(track) // max(2, points))
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    start_time = datetime.now(timezone.utc)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="farfromhome_device.sim" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <trk>",
        "    <name>FarFromHome demo walk</name>",
        "    <trkseg>",
    ]
    for index in range(0, len(track), step):
        lat, lng, _, _ = track.points[index]
        stamp = (start_time + timedelta(seconds=index)).isoformat(timespec="seconds").replace("+00:00", "Z")
        lines.append(f'      <trkpt lat="{lat:.6f}" lon="{lng:.6f}"><time>{stamp}</time></trkpt>')
    lines += ["    </trkseg>", "  </trk>", "</gpx>", ""]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
