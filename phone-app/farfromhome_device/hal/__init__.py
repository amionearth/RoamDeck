"""Hardware abstraction layer.

``python -c "from farfromhome_device.hal import build_hardware; print(build_hardware().describe())"``
is the quickest way to see what this machine can actually do.
"""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path
from typing import Any, Dict, Optional

from .base import (
    Buttons,
    Camera,
    Device,
    DeviceError,
    Display,
    Gps,
    HardwareBundle,
    Imu,
    Mic,
    Speaker,
)

logger = logging.getLogger("ffh.hal")

__all__ = [
    "Buttons",
    "Camera",
    "Device",
    "DeviceError",
    "Display",
    "Gps",
    "HardwareBundle",
    "Imu",
    "Mic",
    "Speaker",
    "build_hardware",
    "is_raspberry_pi",
    "resolve_mode",
    "hardware_report",
]


def is_raspberry_pi() -> bool:
    """True on a Pi (or a Pi-like SBC with the model file)."""
    model = Path("/proc/device-tree/model")
    try:
        if model.is_file() and "raspberry pi" in model.read_text(errors="ignore").lower():
            return True
    except OSError:
        pass
    if platform.machine().lower() in ("aarch64", "armv7l") and Path("/proc/device-tree").exists():
        return True
    return False


def resolve_mode(requested: str = "auto") -> str:
    """Turn ``auto`` into ``real`` or ``sim`` for this machine."""
    requested = (requested or "auto").strip().lower()
    if requested in ("sim", "simulate", "simulator"):
        return "sim"
    if requested in ("real", "hardware", "hw"):
        return "real"
    if os.getenv("FFH_FORCE_SIM", "").strip().lower() in ("1", "true", "yes"):
        return "sim"
    return "real" if is_raspberry_pi() else "sim"


def build_hardware(
    mode: str = "auto",
    start_lat: float = 11.2588,
    start_lng: float = 75.7804,
    speed_mps: float = 1.4,
    gpx_path: Optional[Path | str] = None,
    truth_heading: float = 45.0,
    display_size: tuple[int, int] = (800, 480),
    image_dir: Optional[Path | str] = None,
    button_script: Optional[list[str]] = None,
    canned_phrases: Optional[list[str]] = None,
    autostart: bool = True,
) -> HardwareBundle:
    """Build the hardware bundle for this machine.

    ``mode`` is ``auto`` (default), ``sim`` or ``real``. In ``auto`` mode a
    Raspberry Pi gets real drivers and every other machine gets simulators, which
    is exactly what the brief's "runs without real hardware" requirement needs.
    """
    resolved = resolve_mode(mode)

    if resolved == "sim":
        from .sim import build_sim_bundle

        bundle = build_sim_bundle(
            start_lat=start_lat,
            start_lng=start_lng,
            speed_mps=speed_mps,
            gpx_path=gpx_path,
            truth_heading=truth_heading,
            display_size=display_size,
            image_dir=image_dir,
            button_script=button_script,
            canned_phrases=canned_phrases,
        )
    else:
        from .real import build_real_bundle

        bundle = build_real_bundle(start_lat=start_lat, start_lng=start_lng, use_sim_fallback=True)

    if autostart:
        bundle.open_all()
    logger.info("hardware: %s", bundle.describe())
    return bundle


def hardware_report(bundle: HardwareBundle) -> Dict[str, Any]:
    """A dict the settings screen and ``--self-test`` print."""
    report = bundle.status()
    report["raspberry_pi"] = is_raspberry_pi()
    report["platform"] = f"{platform.system()} {platform.release()} ({platform.machine()})"
    report["python"] = platform.python_version()
    return report
