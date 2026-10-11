"""Real hardware drivers, plus graceful degradation when a part is missing.

None of these are required on a laptop. Each one tries its proper path first and
falls back to a simulator flavour only when the caller asks for it
(:func:`build_real_bundle` returns devices with ``available == False`` rather
than lying about working hardware — the UI shows a red dot for those).

Target hardware assumed (brief §15 asks the product owner to confirm):

    GPS    u-blox NEO-M9N on /dev/serial0 (NMEA), or gpsd on 127.0.0.1:2947
    IMU    ICM-20948 / MPU-9250 on I2C bus 1 (0x68/0x69), or an LSM6DS3
    Camera Pi Camera Module 3 via picamera2, else any V4L2 device via OpenCV
    Mic    ALSA capture (``arecord``) — USB mic on the Pi
    Speaker ALSA playback of Piper TTS output, or ``espeak-ng`` as a last resort
    Display DSI/HDMI panel; backlight via /sys/class/backlight
    Buttons GPIO BCM pins, or the keyboard when running on a laptop
"""

from __future__ import annotations

import io
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..core.models import ImuSample, Position, utcnow
from ..engine.geo import normalise_deg
from .base import Buttons, Camera, Display, Gps, HardwareBundle, Imu, Mic, Speaker
from .sim import SimButtons, SimCamera, SimGps, SimImu, SimMic, SimSpeaker

logger = logging.getLogger("ffh.hal.real")


def _which(program: str) -> Optional[str]:
    return shutil.which(program)


def _run(command: Sequence[str], timeout: float = 20.0) -> Optional[bytes]:
    """Run a command and return stdout, or ``None`` on any failure."""
    try:
        completed = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("command %s failed: %s", command[0], exc)
        return None
    if completed.returncode != 0:
        logger.debug("command %s exited %s: %s", command[0], completed.returncode,
                     completed.stderr[:200])
        return None
    return completed.stdout


# --------------------------------------------------------------------------- #
# GPS
# --------------------------------------------------------------------------- #
def nmea_checksum_ok(sentence: str) -> bool:
    if "*" not in sentence:
        return False
    body, _, checksum = sentence.strip().partition("*")
    if body.startswith("$"):
        body = body[1:]
    try:
        expected = int(checksum[:2], 16)
    except ValueError:
        return False
    actual = 0
    for character in body:
        actual ^= ord(character)
    return actual == expected


def parse_nmea_sentence(sentence: str) -> Optional[Dict[str, Any]]:
    """Parse the handful of sentence types we need. Returns a partial dict.

    Kept as a free function so it is unit-testable with canned sentences.
    """
    sentence = sentence.strip()
    if not sentence.startswith("$"):
        return None
    if not nmea_checksum_ok(sentence):
        return None
    body = sentence[1:].split("*", 1)[0]
    fields = body.split(",")
    kind = fields[0][2:] if len(fields[0]) > 2 else ""

    if kind == "GGA" and len(fields) >= 10:
        quality = int(fields[6] or 0)
        if quality == 0:
            return {"fix": False}
        hdop = float(fields[8]) if fields[8] else 1.0
        return {
            "fix": True,
            "lat": _nmea_degrees(fields[2], fields[3]),
            "lng": _nmea_degrees(fields[4], fields[5]),
            "satellites": int(fields[7] or 0),
            "hdop": hdop,
            "accuracy_m": max(1.0, hdop * 3.0),
            "altitude_m": float(fields[9]) if fields[9] else None,
            "source": "GGA",
        }
    if kind in ("RMC", "GLL") and len(fields) >= 7:
        if kind == "RMC":
            if fields[2] != "A":
                return {"fix": False}
            lat = _nmea_degrees(fields[3], fields[4])
            lng = _nmea_degrees(fields[5], fields[6])
            speed_knots = float(fields[7]) if len(fields) > 7 and fields[7] else 0.0
            course = float(fields[8]) if len(fields) > 8 and fields[8] else 0.0
            return {
                "fix": True,
                "lat": lat,
                "lng": lng,
                "speed_mps": speed_knots * 0.514444,
                "course_deg": course,
                "source": "RMC",
            }
        lat = _nmea_degrees(fields[1], fields[2])
        lng = _nmea_degrees(fields[3], fields[4])
        return {"fix": True, "lat": lat, "lng": lng, "source": "GLL"}
    return None


def _nmea_degrees(value: str, hemisphere: str) -> float:
    """``ddmm.mmmm`` + hemisphere -> signed decimal degrees."""
    if not value:
        return 0.0
    dot = value.find(".")
    split_at = max(0, dot - 2) if dot > 0 else max(0, len(value) - 2)
    degrees = float(value[:split_at] or 0)
    minutes = float(value[split_at:] or 0)
    decimal = degrees + minutes / 60.0
    if hemisphere.upper() in ("S", "W"):
        decimal = -decimal
    return decimal


class NmeaGps(Gps):
    """Reads NMEA sentences from a serial port in a background thread."""

    def __init__(self, port: str = "/dev/serial0", baud: int = 9600) -> None:
        super().__init__(name="gps")
        self.port = port
        self.baud = baud
        self._serial: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._position: Optional[Position] = None
        self._last_fix_at: Optional[float] = None
        self._lock = threading.Lock()

    def _open_impl(self) -> bool:
        try:
            import serial  # pyserial
        except ImportError:
            self._record("pyserial not installed; cannot read NMEA over serial")
            return False
        if not Path(self.port).exists() and os.name != "nt":
            self._record(f"{self.port} not present")
            return False
        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=1.0)
        except Exception as exc:
            self._record(f"serial open failed: {exc}")
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, name="nmea-gps", daemon=True)
        self._thread.start()
        return True

    def _close_impl(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def _reader(self) -> None:
        buffer: Dict[str, Any] = {}
        while not self._stop.is_set():
            try:
                raw = self._serial.readline()
            except Exception as exc:
                self._record(f"serial read failed: {exc}")
                time.sleep(1.0)
                continue
            if not raw:
                continue
            try:
                line = raw.decode("ascii", errors="ignore")
            except Exception:
                continue
            parsed = parse_nmea_sentence(line)
            if not parsed:
                continue
            buffer.update(parsed)
            if parsed.get("source") == "GGA" or (buffer.get("lat") and buffer.get("lng")):
                if not buffer.get("fix", True):
                    continue
                if buffer.get("lat") is None or buffer.get("lng") is None:
                    continue
                with self._lock:
                    self._position = Position(
                        lat=float(buffer["lat"]),
                        lng=float(buffer["lng"]),
                        accuracy_m=float(buffer.get("accuracy_m", 5.0)),
                        altitude_m=buffer.get("altitude_m"),
                        speed_mps=buffer.get("speed_mps"),
                        course_deg=buffer.get("course_deg"),
                        satellites=buffer.get("satellites"),
                        t=utcnow(),
                    )
                    self._last_fix_at = time.monotonic()

    def read(self) -> Optional[Position]:
        self.reads += 1
        with self._lock:
            return self._position

    @property
    def fix_age_s(self) -> Optional[float]:
        if self._last_fix_at is None:
            return None
        return time.monotonic() - self._last_fix_at

    def status(self) -> Dict[str, Any]:
        status = super().status()
        status.update({"port": self.port, "fix_age_s": self.fix_age_s})
        return status


class GpsdGps(Gps):
    """Reads fixes from a local ``gpsd`` (TCP 127.0.0.1:2947)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2947) -> None:
        super().__init__(name="gps")
        self.host = host
        self.port = port
        self._socket: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._position: Optional[Position] = None
        self._last_fix_at: Optional[float] = None
        self._lock = threading.Lock()

    def _open_impl(self) -> bool:
        import socket

        try:
            self._socket = socket.create_connection((self.host, self.port), timeout=3.0)
        except OSError as exc:
            self._record(f"gpsd unreachable at {self.host}:{self.port} ({exc})")
            return False
        try:
            self._socket.sendall(b'?WATCH={"enable":true,"json":true}\n')
        except OSError as exc:
            self._record(f"gpsd WATCH failed: {exc}")
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, name="gpsd", daemon=True)
        self._thread.start()
        return True

    def _close_impl(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    def _reader(self) -> None:
        import json

        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = self._socket.recv(4096)
            except OSError:
                time.sleep(1.0)
                continue
            if not chunk:
                time.sleep(0.5)
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                try:
                    payload = json.loads(line.decode("utf-8", errors="ignore"))
                except ValueError:
                    continue
                if payload.get("class") != "TPV" or payload.get("mode", 0) < 2:
                    continue
                with self._lock:
                    self._position = Position(
                        lat=float(payload.get("lat", 0.0)),
                        lng=float(payload.get("lon", 0.0)),
                        accuracy_m=float(payload.get("eph", 5.0) or 5.0),
                        altitude_m=payload.get("alt"),
                        speed_mps=payload.get("speed"),
                        course_deg=payload.get("track"),
                        t=utcnow(),
                    )
                    self._last_fix_at = time.monotonic()

    def read(self) -> Optional[Position]:
        self.reads += 1
        with self._lock:
            return self._position

    @property
    def fix_age_s(self) -> Optional[float]:
        if self._last_fix_at is None:
            return None
        return time.monotonic() - self._last_fix_at


# --------------------------------------------------------------------------- #
# IMU
# --------------------------------------------------------------------------- #
class I2cImu(Imu):
    """MPU-9250 / ICM-20948 class 9-axis IMU over I2C.

    Register maps differ slightly between parts; the subset used here (WHO_AM_I,
    PWR_MGMT_1, ACCEL_XOUT_H, GYRO_XOUT_H, MAG via the auxiliary I2C master for
    the MPU-9250) is stable across both. Calibration zeroes the gyro bias and
    fits a hard-iron offset for the magnetometer.
    """

    WHO_AM_I = 0x75
    PWR_MGMT_1 = 0x6B
    ACCEL_XOUT_H = 0x3B
    GYRO_XOUT_H = 0x43
    MAG_XOUT_H = 0x03  # via EXT_SENS_DATA_00 when the aux master is enabled

    ACCEL_SCALE = 16384.0  # +/- 2g
    GYRO_SCALE = 131.0  # +/- 250 deg/s

    def __init__(self, bus_number: int = 1, address: int = 0x68) -> None:
        super().__init__(name="imu")
        self.bus_number = bus_number
        self.address = address
        self._bus: Any = None
        self._gyro_bias = (0.0, 0.0, 0.0)
        self._mag_offset = (0.0, 0.0, 0.0)
        self._calibrated = False

    def _open_impl(self) -> bool:
        try:
            from smbus2 import SMBus  # type: ignore
        except ImportError:
            self._record("smbus2 not installed; cannot talk to an I2C IMU")
            return False
        try:
            self._bus = SMBus(self.bus_number)
            who = self._read_byte(self.WHO_AM_I)
            logger.info("IMU WHO_AM_I=0x%02X at 0x%02X on i2c-%d", who, self.address, self.bus_number)
            # Wake the device and use the gyro PLL as the clock source.
            self._write_byte(self.PWR_MGMT_1, 0x01)
            time.sleep(0.05)
        except Exception as exc:  # pragma: no cover - hardware specific
            self._record(f"I2C IMU init failed: {exc}")
            return False
        return True

    def _close_impl(self) -> None:
        if self._bus:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None

    def _read_byte(self, register: int) -> int:
        return self._bus.read_byte_data(self.address, register)  # type: ignore[union-attr]

    def _write_byte(self, register: int, value: int) -> None:
        self._bus.write_byte_data(self.address, register, value)  # type: ignore[union-attr]

    def _read_block(self, register: int, length: int) -> List[int]:
        return list(self._bus.read_i2c_block_data(self.address, register, length))  # type: ignore[union-attr]

    @staticmethod
    def _signed(high: int, low: int) -> int:
        value = (high << 8) | low
        return value - 65536 if value >= 32768 else value

    def read(self) -> Optional[ImuSample]:
        self.reads += 1
        try:
            accel = self._read_block(self.ACCEL_XOUT_H, 6)
            gyro = self._read_block(self.GYRO_XOUT_H, 6)
            magnetometer = self._read_block(self.MAG_XOUT_H, 6)
        except Exception as exc:  # pragma: no cover
            self._record(f"IMU read failed: {exc}")
            return None

        ax = self._signed(accel[0], accel[1]) / self.ACCEL_SCALE * 9.81
        ay = self._signed(accel[2], accel[3]) / self.ACCEL_SCALE * 9.81
        az = self._signed(accel[4], accel[5]) / self.ACCEL_SCALE * 9.81
        gx = self._signed(gyro[0], gyro[1]) / self.GYRO_SCALE - self._gyro_bias[0]
        gy = self._signed(gyro[2], gyro[3]) / self.GYRO_SCALE - self._gyro_bias[1]
        gz = self._signed(gyro[4], gyro[5]) / self.GYRO_SCALE - self._gyro_bias[2]
        mx = self._signed(magnetometer[0], magnetometer[1]) * 0.15 - self._mag_offset[0]
        my = self._signed(magnetometer[2], magnetometer[3]) * 0.15 - self._mag_offset[1]
        mz = self._signed(magnetometer[4], magnetometer[5]) * 0.15 - self._mag_offset[2]

        return ImuSample(ax=ax, ay=ay, az=az, gx=gx, gy=gy, gz=gz, mx=mx, my=my, mz=mz, t=utcnow())

    def calibrate(self, seconds: float = 6.0) -> bool:
        """Average the gyro while still, then take a hard-iron magnetometer offset."""
        samples: List[ImuSample] = []
        deadline = time.monotonic() + max(1.0, seconds)
        while time.monotonic() < deadline:
            sample = self.read()
            if sample:
                samples.append(sample)
            time.sleep(0.02)
        if not samples:
            return False
        count = float(len(samples))
        self._gyro_bias = (
            sum(sample.gx for sample in samples) / count,
            sum(sample.gy for sample in samples) / count,
            sum(sample.gz for sample in samples) / count,
        )
        self._mag_offset = (
            (max(s.mx for s in samples) + min(s.mx for s in samples)) / 2.0,
            (max(s.my for s in samples) + min(s.my for s in samples)) / 2.0,
            (max(s.mz for s in samples) + min(s.mz for s in samples)) / 2.0,
        )
        self._calibrated = True
        logger.info("IMU calibrated: gyro bias %.2f/%.2f/%.2f dps", *self._gyro_bias)
        return True

    def tilt_compensated_heading(self, sample: ImuSample) -> Optional[float]:
        """Accelerometer-tilt-compensated magnetometer heading (brief §5.2 step 2)."""
        norm = math.sqrt(sample.ax ** 2 + sample.ay ** 2 + sample.az ** 2)
        if norm < 0.1:
            return None
        ax, ay, az = sample.ax / norm, sample.ay / norm, sample.az / norm
        roll = math.atan2(ay, az)
        pitch = math.atan2(-ax, math.sqrt(ay ** 2 + az ** 2))
        mx, my, mz = sample.mx, sample.my, sample.mz
        xh = mx * math.cos(pitch) + my * math.sin(roll) * math.sin(pitch) + mz * math.cos(roll) * math.sin(pitch)
        yh = my * math.cos(roll) - mz * math.sin(roll)
        if abs(xh) < 1e-9 and abs(yh) < 1e-9:
            return None
        return normalise_deg(math.degrees(math.atan2(-yh, xh)))


class NullImu(Imu):
    """Placeholder that reports itself unavailable, so the UI can show a warning."""

    def _open_impl(self) -> bool:
        self._record("no IMU available; heading will come from GPS course only")
        return False


# --------------------------------------------------------------------------- #
# Camera
# --------------------------------------------------------------------------- #
class PicameraCamera(Camera):
    """Pi Camera Module via ``picamera2``."""

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        super().__init__(name="camera")
        self.width = width
        self.height = height
        self._camera: Any = None

    def _open_impl(self) -> bool:
        try:
            from picamera2 import Picamera2  # type: ignore
        except ImportError:
            self._record("picamera2 not installed")
            return False
        try:
            self._camera = Picamera2()
            config = self._camera.create_still_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"}
            )
            self._camera.configure(config)
            self._camera.start()
            time.sleep(1.0)  # AGC/AWB settle
            return True
        except Exception as exc:  # pragma: no cover
            self._record(f"picamera2 init failed: {exc}")
            return False

    def _close_impl(self) -> None:
        if self._camera:
            try:
                self._camera.stop()
                self._camera.close()
            except Exception:
                pass
            self._camera = None

    def _grab(self) -> Optional[bytes]:
        if not self._camera:
            return None
        try:
            from PIL import Image

            array = self._camera.capture_array()
            image = Image.fromarray(array).convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90)
            return buffer.getvalue()
        except Exception as exc:  # pragma: no cover
            self._record(f"capture failed: {exc}")
            return None

    def capture(self) -> Optional[bytes]:
        self.reads += 1
        return self._grab()

    def preview_frame(self) -> Optional[bytes]:
        return self._grab()

    @property
    def resolution(self) -> tuple[int, int]:
        return (self.width, self.height)


class V4l2Camera(Camera):
    """Any camera OpenCV can open (USB webcam on the laptop, or a V4L2 device)."""

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720) -> None:
        super().__init__(name="camera")
        self.index = index
        self.width = width
        self.height = height
        self._capture: Any = None

    def _open_impl(self) -> bool:
        try:
            import cv2  # type: ignore
        except ImportError:
            self._record("opencv-python not installed; cannot use a V4L2/USB camera")
            return False
        capture = cv2.VideoCapture(self.index)
        if not capture.isOpened():
            self._record(f"could not open camera index {self.index}")
            return False
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._capture = capture
        return True

    def _close_impl(self) -> None:
        if self._capture:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

    def capture(self) -> Optional[bytes]:
        self.reads += 1
        if not self._capture:
            return None
        try:
            import cv2

            ok, frame = self._capture.read()
            if not ok:
                return None
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            return encoded.tobytes() if ok else None
        except Exception as exc:  # pragma: no cover
            self._record(f"capture failed: {exc}")
            return None

    @property
    def resolution(self) -> tuple[int, int]:
        return (self.width, self.height)


# --------------------------------------------------------------------------- #
# Mic / Speaker
# --------------------------------------------------------------------------- #
class AlsaMic(Mic):
    """Records a WAV with ``arecord`` and (optionally) transcribes it."""

    def __init__(self, device: str = "default", sample_rate: int = 16000) -> None:
        super().__init__(name="mic")
        self.device = device
        self.sample_rate = sample_rate
        self.stt_command = os.getenv("STT_COMMAND", "")  # e.g. a vosk script
        self._recording = False

    def _open_impl(self) -> bool:
        if not _which("arecord"):
            self._record("arecord not found; install alsa-utils")
            return False
        return True

    def record(self, seconds: float = 3.0) -> Optional[bytes]:
        self.reads += 1
        if not self.available:
            return None
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        output = _run(
            [
                "arecord", "-q", "-D", self.device,
                "-f", "S16_LE", "-r", str(self.sample_rate), "-c", "1",
                "-d", str(int(max(1, seconds))), path,
            ],
            timeout=seconds + 8.0,
        )
        try:
            data = Path(path).read_bytes()
        except OSError:
            data = None
        finally:
            Path(path).unlink(missing_ok=True)
        if output is None and data is None:
            self._record("arecord produced no audio")
        return data

    def transcribe(self, wav_bytes: Optional[bytes]) -> str:
        """Optional: hand the WAV to an external STT command.

        The brief asks for local STT (Whisper tiny/base or Vosk). Shipping a model
        in this repo would be hundreds of megabytes, so the integration point is a
        command: set ``STT_COMMAND`` to something that reads a WAV on stdin and
        prints text. Returns ``""`` when unset, and every caller has a
        button-based fallback (brief §5.1).
        """
        if not self.stt_command or not wav_bytes:
            return ""
        try:
            completed = subprocess.run(
                self.stt_command, shell=True, input=wav_bytes,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30.0, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._record(f"STT command failed: {exc}")
            return ""
        if completed.returncode != 0:
            self._record(f"STT command exited {completed.returncode}")
            return ""
        return completed.stdout.decode("utf-8", errors="ignore").strip()


class PiperSpeaker(Speaker):
    """Speaks with Piper (preferred) or ``espeak-ng`` (fallback).

    The brief names Piper as working well on a Pi. Both are called through a
    subprocess so neither becomes a Python dependency.
    """

    def __init__(self, piper_model: Optional[str] = None, rate: int = 165) -> None:
        super().__init__(name="speaker")
        self.piper_model = piper_model or os.getenv("PIPER_MODEL", "")
        self.rate = rate
        self._speaking = threading.Event()
        self._lock = threading.Lock()
        self._engine = ""

    def _open_impl(self) -> bool:
        if _which("piper") and self.piper_model:
            self._engine = "piper"
            return True
        if _which("espeak-ng"):
            self._engine = "espeak-ng"
            return True
        if _which("espeak"):
            self._engine = "espeak"
            return True
        if os.name == "nt":
            # On the laptop, PowerShell can speak. Cheap and it proves the path.
            self._engine = "powershell"
            return True
        self._record("no TTS backend (piper / espeak-ng) found; captions still work")
        return False

    @property
    def tts_available(self) -> bool:
        return bool(self._engine)

    def say(self, text: str) -> bool:
        cleaned = " ".join(text.split())
        if not cleaned:
            return False
        self.reads += 1
        if not self._engine:
            logger.info("[caption only] %s", cleaned)
            return False

        with self._lock:
            self._speaking.set()
            try:
                if self._engine == "piper":
                    command = f'echo "{cleaned}" | piper --model "{self.piper_model}" --output-raw | aplay -r 22050 -f S16_LE -t raw -'
                    _run(["bash", "-lc", command], timeout=30.0)
                elif self._engine in ("espeak-ng", "espeak"):
                    _run([self._engine, "-s", str(self.rate), cleaned], timeout=30.0)
                elif self._engine == "powershell":
                    escaped = cleaned.replace("'", "''")
                    _run(
                        ["powershell", "-NoProfile", "-Command",
                         f"Add-Type -AssemblyName System.Speech; "
                         f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                         f"$s.Rate = 1; $s.Speak('{escaped}')"],
                        timeout=30.0,
                    )
            finally:
                self._speaking.clear()
        return True

    def stop(self) -> None:
        if self._engine in ("espeak-ng", "espeak"):
            _run([self._engine, "--stop"] if self._engine == "espeak-ng" else ["pkill", "espeak"])

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()


# --------------------------------------------------------------------------- #
# Display / Buttons
# --------------------------------------------------------------------------- #
class SysfsBacklightDisplay(Display):
    """Reads the panel size from the environment and drives the backlight via sysfs."""

    def __init__(self, width: Optional[int] = None, height: Optional[int] = None) -> None:
        super().__init__(name="display")
        self.width = width or int(os.getenv("SCREEN_WIDTH", "800"))
        self.height = height or int(os.getenv("SCREEN_HEIGHT", "480"))
        self.backlight = 1.0
        self._backlight_path = self._find_backlight()

    @staticmethod
    def _find_backlight() -> Optional[Path]:
        base = Path("/sys/class/backlight")
        if not base.is_dir():
            return None
        for entry in sorted(base.iterdir()):
            brightness = entry / "brightness"
            if brightness.is_file():
                return brightness
        return None

    def info(self) -> Dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "dpi": int(os.getenv("SCREEN_DPI", "160")),
            "backlight": self.backlight,
            "simulated": False,
            "backlight_path": str(self._backlight_path) if self._backlight_path else None,
        }

    def set_backlight(self, level: float) -> None:
        self.backlight = max(0.05, min(1.0, level))
        if self._backlight_path is None:
            return
        try:
            maximum_path = self._backlight_path.parent / "max_brightness"
            maximum = int(maximum_path.read_text().strip()) if maximum_path.is_file() else 255
            self._backlight_path.write_text(str(int(maximum * self.backlight)))
        except OSError as exc:
            self._record(f"backlight write failed: {exc}")


class GpioButtons(Buttons):
    """GPIO buttons on the Pi; falls back to nothing on other platforms."""

    #: BCM pin per canonical button name. Override with BUTTON_PINS=up:17,sos:27
    DEFAULT_PINS: Dict[str, int] = {
        "up": 17,
        "down": 27,
        "left": 22,
        "right": 23,
        "select": 24,
        "back": 25,
        "photo": 5,
        "sos": 6,
        "menu": 13,
        "ptt": 19,
    }

    def __init__(self, pins: Optional[Dict[str, int]] = None) -> None:
        super().__init__(name="buttons")
        self.pins = dict(pins or self._pins_from_env() or self.DEFAULT_PINS)
        self._pressed: List[str] = []
        self._lock = threading.Lock()
        self._previous: Dict[str, int] = {}

    @staticmethod
    def _pins_from_env() -> Optional[Dict[str, int]]:
        raw = os.getenv("BUTTON_PINS", "").strip()
        if not raw:
            return None
        pins: Dict[str, int] = {}
        for pair in raw.split(","):
            name, _, pin = pair.partition(":")
            if name.strip() and pin.strip().isdigit():
                pins[name.strip()] = int(pin.strip())
        return pins or None

    def _open_impl(self) -> bool:
        try:
            from gpiozero import Button as GpioButton  # type: ignore
        except ImportError:
            self._record("gpiozero not installed; hardware buttons unavailable")
            return False
        try:
            for name, pin in self.pins.items():
                button = GpioButton(pin, bounce_time=0.05)
                button.when_pressed = (lambda n=name: self._on_press(n))
        except Exception as exc:  # pragma: no cover
            self._record(f"GPIO init failed: {exc}")
            return False
        return True

    def _on_press(self, name: str) -> None:
        with self._lock:
            self._pressed.append(name)

    def poll(self) -> List[str]:
        self.reads += 1
        with self._lock:
            pressed = list(self._pressed)
            self._pressed.clear()
        return pressed


# --------------------------------------------------------------------------- #
# Bundle construction
# --------------------------------------------------------------------------- #
def build_real_bundle(
    start_lat: float = 11.2588,
    start_lng: float = 75.7804,
    use_sim_fallback: bool = True,
) -> HardwareBundle:
    """Open every real device that exists; substitute simulators where it does not.

    ``use_sim_fallback`` is what makes ``DEVICE_MODE=auto`` pleasant: on the Pi
    you get the real GPS and a real camera, and if the IMU is unplugged you get a
    scripted heading instead of a crash.
    """
    gps: Gps
    gpsd = _which("gpsd") or Path("/run/gpsd.sock").exists()
    if os.name != "nt" and Path("/dev/serial0").exists():
        gps = NmeaGps()
    elif gpsd:
        gps = GpsdGps()
    else:
        gps = SimGps(start_lat=start_lat, start_lng=start_lng) if use_sim_fallback else NmeaGps()

    imu: Imu = I2cImu()
    if not imu.open():
        imu = SimImu() if use_sim_fallback else NullImu()

    camera: Camera
    if os.name != "nt":
        camera = PicameraCamera()
    else:
        camera = V4l2Camera()
    if not camera.open():
        camera = SimCamera() if use_sim_fallback else camera

    mic: Mic = AlsaMic()
    if not mic.open():
        mic = SimMic() if use_sim_fallback else AlsaMic()

    speaker: Speaker = PiperSpeaker()
    if not speaker.open():
        speaker = SimSpeaker() if use_sim_fallback else PiperSpeaker()

    display: Display = SysfsBacklightDisplay()
    display.open()

    buttons: Buttons = GpioButtons()
    if not buttons.open():
        buttons = SimButtons() if use_sim_fallback else GpioButtons()

    for device in (gps, imu, camera, mic, speaker, display, buttons):
        if not device.is_open:
            device.open()

    bundle = HardwareBundle(
        gps=gps, imu=imu, camera=camera, mic=mic, speaker=speaker,
        display=display, buttons=buttons, mode="real",
    )
    logger.info(bundle.describe())
    return bundle
