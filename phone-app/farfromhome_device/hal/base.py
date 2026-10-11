"""HAL base: one tiny generic device class plus the shared contracts.

The brief requires "a hardware abstraction layer (``hal/``) with interfaces for
GPS, IMU, Camera, Mic, Speaker, Display, Buttons. Each has a real driver and a
simulator." Seven interfaces with seven real drivers and seven simulators is a
lot of ceremony, so every device shares :class:`Device` (state, error capture,
``available``) and each concrete class exposes only its own verbs:

    Gps.read()          -> Position | None
    Imu.read()          -> ImuSample | None
    Camera.capture()    -> bytes (JPEG) | None
    Mic.record(seconds) -> bytes (WAV) | None
    Speaker.say(text)   -> bool
    Display.info()      -> dict          (size/backlight; real UI is in ui/)
    Buttons.poll()      -> list[str]     (rising-edge button names)

Anything that does not touch hardware is unit-testable because every device here
has a simulator sibling in :mod:`farfromhome_device.hal.sim`.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ffh.hal")


class DeviceError(RuntimeError):
    """Raised by a device when it cannot do its job and the caller must know."""


class Device:
    """Common behaviour: name, open/close lifecycle, error capture, counters."""

    #: Short machine name used in logs and the UI ("gps", "imu", ...).
    kind: str = "device"

    def __init__(self, name: Optional[str] = None) -> None:
        self.name = name or self.kind
        self._open = False
        self._lock = threading.RLock()
        self.errors: List[str] = []
        self.reads: int = 0
        self.failures: int = 0

    # --- lifecycle ----------------------------------------------------------
    def open(self) -> bool:
        with self._lock:
            if self._open:
                return True
            try:
                self._open = bool(self._open_impl())
            except Exception as exc:  # pragma: no cover - hardware specific
                self._record(f"open failed: {exc}")
                self._open = False
            return self._open

    def close(self) -> None:
        with self._lock:
            if not self._open:
                return
            try:
                self._close_impl()
            except Exception as exc:  # pragma: no cover
                self._record(f"close failed: {exc}")
            finally:
                self._open = False

    def _open_impl(self) -> bool:
        return True

    def _close_impl(self) -> None:
        return None

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def available(self) -> bool:
        """True when the device has been opened successfully."""
        return self._open

    # --- helpers ------------------------------------------------------------
    def _record(self, message: str) -> None:
        self.failures += 1
        self.errors.append(message)
        del self.errors[:-50]
        logger.warning("[%s] %s", self.name, message)

    def status(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "driver": type(self).__name__,
            "open": self._open,
            "reads": self.reads,
            "failures": self.failures,
            "last_error": self.errors[-1] if self.errors else None,
        }

    def __enter__(self) -> "Device":
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{type(self).__name__} name={self.name} open={self._open}>"


class Gps(Device):
    kind = "gps"

    def read(self) -> Optional[Any]:  # pragma: no cover - interface
        """Return a :class:`~farfromhome_device.core.models.Position` or ``None``."""
        raise NotImplementedError

    @property
    def fix_age_s(self) -> Optional[float]:
        return None

    def has_fix(self) -> bool:
        return self.read() is not None


class Imu(Device):
    kind = "imu"

    def read(self) -> Optional[Any]:  # pragma: no cover - interface
        """Return an :class:`~farfromhome_device.core.models.ImuSample` or ``None``."""
        raise NotImplementedError

    def calibrate(self, seconds: float = 6.0) -> bool:
        """Run the figure-8 / stillness calibration. Simulator returns True."""
        return True


class Camera(Device):
    kind = "camera"

    def capture(self) -> Optional[bytes]:  # pragma: no cover - interface
        """Return JPEG bytes or ``None``."""
        raise NotImplementedError

    def preview_frame(self) -> Optional[bytes]:
        """A cheap frame for the preview widget. Defaults to a capture."""
        return self.capture()

    @property
    def resolution(self) -> tuple[int, int]:
        return (0, 0)


class Mic(Device):
    kind = "mic"

    def record(self, seconds: float = 3.0) -> Optional[bytes]:  # pragma: no cover
        """Record and return WAV bytes (16 kHz mono is fine for STT)."""
        raise NotImplementedError

    def push_to_talk_start(self) -> bool:
        return True

    def push_to_talk_stop(self) -> Optional[bytes]:
        return None


class Speaker(Device):
    kind = "speaker"

    def say(self, text: str) -> bool:  # pragma: no cover - interface
        """Speak ``text``. Returns False when no TTS backend is available —
        that is not an error, the UI bubble still shows the text."""
        raise NotImplementedError

    def stop(self) -> None:
        return None

    @property
    def tts_available(self) -> bool:
        return False


class Display(Device):
    kind = "display"

    def info(self) -> Dict[str, Any]:  # pragma: no cover - interface
        """Screen size and capabilities for the UI to lay out against."""
        raise NotImplementedError

    def set_backlight(self, level: float) -> None:
        return None


class Buttons(Device):
    kind = "buttons"

    #: Canonical button names the UI understands.
    KNOWN = ("up", "down", "left", "right", "select", "back", "menu", "photo", "sos", "ptt")

    def poll(self) -> List[str]:  # pragma: no cover - interface
        """Return names of buttons that went down since the last poll."""
        raise NotImplementedError

    def press(self, name: str) -> None:
        """Inject a press (simulator only; real drivers ignore this)."""
        return None


class HardwareBundle:
    """All devices in one object, so processes can be handed a single dependency."""

    def __init__(
        self,
        gps: Gps,
        imu: Imu,
        camera: Camera,
        mic: Mic,
        speaker: Speaker,
        display: Display,
        buttons: Buttons,
        mode: str = "sim",
    ) -> None:
        self.gps = gps
        self.imu = imu
        self.camera = camera
        self.mic = mic
        self.speaker = speaker
        self.display = display
        self.buttons = buttons
        self.mode = mode

    @property
    def devices(self) -> List[Device]:
        return [
            self.gps,
            self.imu,
            self.camera,
            self.mic,
            self.speaker,
            self.display,
            self.buttons,
        ]

    def open_all(self) -> Dict[str, bool]:
        return {device.name: device.open() for device in self.devices}

    def close_all(self) -> None:
        for device in self.devices:
            device.close()

    def status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "devices": {device.name: device.status() for device in self.devices},
        }

    def describe(self) -> str:
        parts = []
        for device in self.devices:
            mark = "ok" if device.available else "--"
            parts.append(f"{device.name}:{mark}")
        return f"hal[{self.mode}] " + " ".join(parts)
