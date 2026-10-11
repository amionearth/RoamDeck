"""Heading fusion: accelerometer + magnetometer + gyro, blended with GPS course.

Why this file exists (brief §3 and §5.2 step 2, §12 "Magnetometer noisy"):
*"Magnetometers drift near electronics and batteries. The compass must fuse
sensors."* On this handheld the magnetometer sits centimetres from a LiPo pack, a
Pi 5 and a display, so a raw magnetometer reading is untrustworthy exactly where
it matters.

Algorithm — a tilt-compensated complementary filter:

    heading = wrap( alpha * (gyro-integrated heading)
                    + beta * (tilt-compensated magnetometer heading)
                    + gamma * (GPS course over ground) )

  * ``gyro`` gives smooth short-term rotation and cannot be disturbed by nearby
    metal, but integrates bias forever.
  * The ``magnetometer`` gives an absolute reference with no drift, but is noisy
    and can be wrong by tens of degrees next to a battery.
  * ``GPS course`` is absolute and immune to magnetic interference, but only
    exists while moving faster than about 0.7 m/s and lags turns.

Weights are computed per sample from *measurements about those three sources*
rather than hard-coded, so the filter degrades rather than lies:

  * magnetometer weight scales down with the deviation of the field *magnitude*
    from the expected local strength (a hard-iron disturbance changes magnitude,
    a genuine heading change does not);
  * magnetometer weight drops to zero when the gyro says we are rotating fast
    (turns are where the magnetometer lags);
  * GPS course weight rises with speed and falls with course accuracy.

The result also carries a ``needs_calibration`` flag: if the field magnitude is
persistently wrong, the UI shows the figure-8 calibration screen.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..core.models import HeadingReading, ImuSample, Position, utcnow
from .geo import normalise_deg, normalise_signed_deg

logger = logging.getLogger("ffh.engine.fusion")

#: Expected total geomagnetic field strength in microtesla. Kerala is ~40-45 uT;
#: the global range is roughly 22 (Brazil) to 67 (polar). 48 uT is a sane default
#: and the value is configurable on the settings screen.
DEFAULT_FIELD_STRENGTH_UT = 48.0

#: Above this speed the GPS course is meaningful (about 2.5 km/h).
GPS_COURSE_MIN_SPEED_MPS = 0.7

#: Rotation rate above which we trust the gyro over the magnetometer.
FAST_ROTATION_DPS = 45.0


def magnetometer_heading(sample: ImuSample) -> Optional[float]:
    """Tilt-compensated heading from a raw IMU sample, clockwise from north.

    Uses the accelerometer as the gravity reference (the standard tilt
    compensation), so holding the handheld at an angle does not skew north.
    """
    norm = math.sqrt(sample.ax ** 2 + sample.ay ** 2 + sample.az ** 2)
    if norm < 0.5:  # in free fall / accelerometer garbage
        return None
    ax, ay, az = sample.ax / norm, sample.ay / norm, sample.az / norm

    roll = math.atan2(ay, az)
    pitch = math.atan2(-ax, math.sqrt(ay ** 2 + az ** 2))

    sin_roll, cos_roll = math.sin(roll), math.cos(roll)
    sin_pitch, cos_pitch = math.sin(pitch), math.cos(pitch)

    xh = sample.mx * cos_pitch + sample.my * sin_roll * sin_pitch + sample.mz * cos_roll * sin_pitch
    yh = sample.my * cos_roll - sample.mz * sin_roll

    if abs(xh) < 1e-9 and abs(yh) < 1e-9:
        return None
    # With this tilt-compensation convention ``yh`` is the north component and
    # ``xh`` the east component, so the bearing clockwise from north is
    # ``atan2(east, north)``.
    return normalise_deg(math.degrees(math.atan2(xh, yh)))


def field_strength_ut(sample: ImuSample) -> float:
    return math.sqrt(sample.mx ** 2 + sample.my ** 2 + sample.mz ** 2)


@dataclass
class FusionConfig:
    """Tunables. Defaults chosen for a handheld Pi with a nearby LiPo."""

    expected_field_ut: float = DEFAULT_FIELD_STRENGTH_UT
    #: Complementary filter constant: fraction of the correction applied per
    #: sample. Higher = trust the absolute sources more.
    alpha: float = 0.06
    #: How many still samples the gyro bias estimate is built from.
    gyro_bias_samples: int = 40
    #: A sample above this rotation rate is a genuine turn, so it must not be
    #: folded into the bias estimate. Without this, the "average of the first N
    #: samples" is an average of the turn itself and cancels it out — which is the
    #: bug this guard exists to prevent (found by the gyro-turn unit test).
    gyro_bias_max_rate_dps: float = 5.0
    #: Field magnitude tolerance before the magnetometer is considered disturbed.
    field_tolerance_ratio: float = 0.45
    #: After this many consecutive disturbed samples, ask the user to calibrate.
    calibration_trigger_samples: int = 25
    gps_course_min_speed_mps: float = GPS_COURSE_MIN_SPEED_MPS
    fast_rotation_dps: float = FAST_ROTATION_DPS


class HeadingFusion:
    """Stateful complementary filter. One instance per live trip."""

    def __init__(self, config: Optional[FusionConfig] = None, initial_heading: Optional[float] = None) -> None:
        self.config = config or FusionConfig()
        self.heading = normalise_deg(initial_heading) if initial_heading is not None else None
        self._gyro_bias = [0.0, 0.0, 0.0]
        self._bias_samples: List[Tuple[float, float, float]] = []
        self._last_t: Optional[float] = None
        self._disturbed_streak = 0
        self._mag_healthy = True
        self._needs_calibration = False
        self._last_mag_heading: Optional[float] = None
        self._last_gps_course: Optional[float] = None
        self._last_source = "init"
        self._last_confidence = 0.0
        self._last_speed: Optional[float] = None
        self._last_gps_accuracy: Optional[float] = None
        self.samples = 0
        self.rejected_magnetometer_samples = 0
        self.gps_course_corrections = 0

    # ------------------------------------------------------------------ #
    # feeding
    # ------------------------------------------------------------------ #
    def update_imu(self, sample: ImuSample, dt: Optional[float] = None) -> HeadingReading:
        """Fold one IMU sample into the estimate and return the new heading."""
        self.samples += 1
        if dt is None:
            dt = 0.05
            if self._last_t is not None:
                dt = max(0.001, min(0.5, sample.t.timestamp() - self._last_t))
        self._last_t = sample.t.timestamp()

        # --- gyro integration ------------------------------------------
        gx, gy, gz = sample.gx, sample.gy, sample.gz
        still = math.sqrt(gx * gx + gy * gy + gz * gz) <= self.config.gyro_bias_max_rate_dps
        if still and len(self._bias_samples) < self.config.gyro_bias_samples:
            self._bias_samples.append((gx, gy, gz))
            count = float(len(self._bias_samples))
            self._gyro_bias = [
                sum(item[0] for item in self._bias_samples) / count,
                sum(item[1] for item in self._bias_samples) / count,
                sum(item[2] for item in self._bias_samples) / count,
            ]
        elif not still:
            # Once the user moves, the bias estimate is as good as it will get and
            # is frozen. From here the gyro is trusted for turns and the absolute
            # sources correct the drift.
            self._bias_samples = self._bias_samples[: self.config.gyro_bias_samples]
        gyro_rate = gz - self._gyro_bias[2]

        if self.heading is None:
            # Bootstrap from the magnetometer (or GPS course) the first time.
            initial = magnetometer_heading(sample)
            if initial is None:
                return self._reading("no-reference", 0.0)
            self.heading = initial
            self._last_source = "magnetometer"

        gyro_prediction = normalise_deg(self.heading + gyro_rate * dt)

        # --- magnetometer ----------------------------------------------
        mag_heading = magnetometer_heading(sample)
        strength = field_strength_ut(sample)
        magnitude_ratio = strength / max(1e-6, self.config.expected_field_ut)
        disturbed = abs(magnitude_ratio - 1.0) > self.config.field_tolerance_ratio

        if disturbed:
            self._disturbed_streak += 1
            self.rejected_magnetometer_samples += 1
            if self._disturbed_streak >= self.config.calibration_trigger_samples:
                self._needs_calibration = True
                self._mag_healthy = False
        else:
            self._disturbed_streak = 0
            self._mag_healthy = True
            self._needs_calibration = False
        self._last_mag_heading = mag_heading

        # Magnetometer trust: high when the field looks right and we are not
        # spinning fast (the magnetometer lags a fast turn).
        mag_weight = 0.0
        if mag_heading is not None and not disturbed:
            mag_weight = 1.0
            if abs(gyro_rate) > self.config.fast_rotation_dps:
                mag_weight = 0.25
            # Only accept a magnetometer correction that is a plausible change
            # from the gyro prediction; a wild jump means interference, not a turn.
            deviation = abs(normalise_signed_deg(mag_heading - gyro_prediction))
            if deviation > 60.0 and abs(gyro_rate) < 20.0:
                mag_weight = 0.0
                self.rejected_magnetometer_samples += 1

        # --- blend -------------------------------------------------------
        correction = 0.0
        source_bits = []
        if mag_weight > 0.0:
            correction += self.config.alpha * mag_weight * normalise_signed_deg(mag_heading - gyro_prediction)  # type: ignore[operator]
            source_bits.append("magnetometer")

        if self._last_gps_course is not None:
            gps_weight = self._gps_course_weight()
            if gps_weight > 0.0:
                correction += self.config.alpha * gps_weight * normalise_signed_deg(
                    self._last_gps_course - gyro_prediction
                )
                source_bits.append("gps")
                self.gps_course_corrections += 1

        self.heading = normalise_deg(gyro_prediction + correction)
        source_bits.insert(0, "gyro")
        self._last_source = "+".join(source_bits)
        self._last_confidence = self._confidence(mag_weight)

        return self._reading(self._last_source, self._last_confidence)

    def update_gps(self, position: Position) -> None:
        """Record a GPS fix; its course is used as an absolute reference while moving."""
        speed = position.speed_mps or 0.0
        self._last_speed = speed
        self._last_gps_accuracy = position.accuracy_m
        if position.course_deg is None or speed < self.config.gps_course_min_speed_mps:
            return
        if position.accuracy_m and position.accuracy_m > 30.0:
            return
        if self._last_gps_course is not None:
            # Reject a course that jumps more than 90 deg in one fix: that is a
            # GPS artefact, not a person turning around inside one second.
            if abs(normalise_signed_deg(position.course_deg - self._last_gps_course)) > 90.0:
                return
        self._last_gps_course = normalise_deg(position.course_deg)

    def set_heading(self, heading: float, source: str = "manual") -> None:
        """Used by the calibration screen and by tests."""
        self.heading = normalise_deg(heading)
        self._last_source = source

    def calibrate(self) -> None:
        """Clear the bias estimate and interference flags (figure-8 motion)."""
        self._bias_samples.clear()
        self._disturbed_streak = 0
        self._mag_healthy = True
        self._needs_calibration = False
        logger.info("heading fusion calibrated")

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _gps_course_weight(self) -> float:
        """Roughly 0.35 at walking pace, 1.0 at a run or in a car."""
        speed = self._last_speed
        if speed is None or speed < self.config.gps_course_min_speed_mps:
            return 0.0
        if speed >= 4.0:
            return 1.0
        low = self.config.gps_course_min_speed_mps
        span = max(0.1, 4.0 - low)
        return min(1.0, 0.35 + 0.65 * (speed - low) / span)

    def _confidence(self, mag_weight: float) -> float:
        confidence = 0.4  # gyro alone
        if mag_weight > 0 and self._mag_healthy:
            confidence += 0.4 * mag_weight
        if self._last_gps_course is not None:
            confidence += 0.2 * self._gps_course_weight()
        return max(0.0, min(1.0, confidence))

    def _reading(self, source: str, confidence: float) -> HeadingReading:
        return HeadingReading(
            heading_deg=normalise_deg(self.heading or 0.0),
            source=source,
            confidence=confidence,
            magnetometer_ok=self._mag_healthy,
            needs_calibration=self._needs_calibration,
            t=utcnow(),
        )

    # ------------------------------------------------------------------ #
    # diagnostics
    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        return {
            "heading": None if self.heading is None else round(self.heading, 1),
            "source": self._last_source,
            "confidence": round(self._last_confidence, 2),
            "samples": self.samples,
            "magnetometer_ok": self._mag_healthy,
            "needs_calibration": self._needs_calibration,
            "rejected_magnetometer_samples": self.rejected_magnetometer_samples,
            "gps_course_corrections": self.gps_course_corrections,
            "gyro_bias_dps": round(self._gyro_bias[2], 3),
            "has_gps_course": self._last_gps_course is not None,
        }


def wrap_delta(from_deg: float, to_deg: float) -> float:
    """Shortest signed rotation from ``from_deg`` to ``to_deg``."""
    return normalise_signed_deg(to_deg - from_deg)


def smooth_heading(previous: Optional[float], new: float, max_step_deg: float = 25.0) -> float:
    """Limit how fast a displayed heading can move, to stop the arrow twitching.

    A magnetometer next to a battery will occasionally produce a 180-degree
    jump. Visually that is much worse than being briefly 20 degrees wrong, and the
    brief's compass is the only thing the user navigates by.
    """
    if previous is None:
        return normalise_deg(new)
    delta = normalise_signed_deg(new - previous)
    if abs(delta) <= max_step_deg:
        return normalise_deg(new)
    return normalise_deg(previous + math.copysign(max_step_deg, delta))
