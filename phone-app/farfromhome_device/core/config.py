"""Configuration loading.

Sources, in order of precedence (last wins):

1. ``.env.example`` defaults baked into :class:`Settings`.
2. ``./.env`` (never committed).
3. Real environment variables.

Precedence is deliberately "env wins over file" so systemd ``Environment=``
lines and one-off PowerShell ``$env:SERVER_URL=...`` overrides work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


# --------------------------------------------------------------------------- #
# .env parsing
# --------------------------------------------------------------------------- #
def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_env_file(path: Path) -> Dict[str, str]:
    """Minimal, dependency-free ``.env`` reader.

    Supports ``KEY=value``, ``export KEY=value``, ``#`` comments, blank lines,
    and quoted values. It deliberately does NOT support interpolation, because
    guessing wrong there is worse than not supporting it.
    """
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        # Keep an inline '#' only when it is not inside quotes.
        value = _strip_quotes(value)
        out[key] = value
    return out


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _as_float(value: object, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
DEFAULT_PRODUCT_NAME = "FarFromHome"

#: Where the token, outbox, trips and photos live, relative to the repo root.
DEFAULT_DATA_DIR = "./data"


@dataclass
class Settings:
    """Everything the handheld needs to know that is not a secret.

    LLM provider keys are **not** here: they live on the server (brief §5.1
    "Production rule"). The ``llm_dev_*`` fields exist only for laptop testing
    and are refused unless ``llm_dev_mode`` is on.
    """

    # --- identity / network -------------------------------------------------
    server_url: str = "http://127.0.0.1:8888"
    device_name: str = "Gizmo-01"
    product_name: str = DEFAULT_PRODUCT_NAME

    # --- paths --------------------------------------------------------------
    repo_root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent)
    data_dir: Path = field(default_factory=lambda: Path(DEFAULT_DATA_DIR))

    # --- hardware -----------------------------------------------------------
    device_mode: str = "auto"  # auto | sim | real

    # --- simulator ----------------------------------------------------------
    sim_gpx: str = ""
    sim_start_lat: float = 11.2588
    sim_start_lng: float = 75.7804
    sim_speed_mps: float = 1.4
    sim_heading_deg: float = 45.0

    # --- UI -----------------------------------------------------------------
    ui_theme: str = "dark"
    ui_fullscreen: bool = False
    speech_enabled: bool = True
    headless: bool = False

    # --- safety -------------------------------------------------------------
    emergency_contact_name: str = ""
    emergency_contact_phone: str = ""

    # --- dev-only LLM access (laptop only) ----------------------------------
    llm_dev_mode: bool = False
    llm_dev_provider: str = ""
    llm_dev_api_key: str = ""
    llm_dev_base_url: str = ""
    llm_dev_model: str = ""

    # --- timeouts (brief §4 "General rules") --------------------------------
    json_timeout_s: float = 10.0
    photo_timeout_s: float = 60.0
    sos_timeout_s: float = 5.0
    heartbeat_interval_s: float = 30.0

    # --- engine tuning ------------------------------------------------------
    engine_hz: float = 8.0
    gps_point_interval_s: float = 5.0
    gps_batch_size: int = 50
    gps_batch_interval_s: float = 15.0
    gps_max_accuracy_m: float = 50.0
    off_route_tolerance_m: float = 120.0
    off_route_seconds: float = 45.0

    # --- photo --------------------------------------------------------------
    photo_max_edge_px: int = 1920
    photo_quality: int = 85
    photo_target_bytes: int = 2 * 1024 * 1024
    strip_exif_by_default: bool = True

    # ---------------------------------------------------------------------- #
    # Derived helpers
    # ---------------------------------------------------------------------- #
    @property
    def api_base(self) -> str:
        return f"{self.server_url.rstrip('/')}/api"

    @property
    def ws_url(self) -> str:
        base = self.server_url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :] + "/ws"
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :] + "/ws"
        return "ws://" + base + "/ws"

    # --- paths --------------------------------------------------------------
    @property
    def token_file(self) -> Path:
        return self.data_dir / "device_token"

    @property
    def outbox_db(self) -> Path:
        return self.data_dir / "outbox.db"

    @property
    def trips_dir(self) -> Path:
        return self.data_dir / "trips"

    @property
    def photo_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def sim_dir(self) -> Path:
        return self.data_dir / "sim"

    def trip_dir(self, trip_id: str) -> Path:
        return self.trips_dir / trip_id

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.trips_dir,
            self.photo_dir,
            self.cache_dir,
            self.sim_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def as_public_dict(self) -> Dict[str, object]:
        """Safe to print in logs: no token, no key."""
        return {
            "server_url": self.server_url,
            "device_name": self.device_name,
            "device_mode": self.device_mode,
            "data_dir": str(self.data_dir),
            "headless": self.headless,
            "llm_dev_mode": self.llm_dev_mode,
        }


def load_settings(
    env_file: Optional[Path] = None,
    overrides: Optional[Dict[str, object]] = None,
) -> Settings:
    """Build a :class:`Settings` from ``.env`` + process environment + overrides."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    env_path = Path(env_file) if env_file else repo_root / ".env"
    file_env = parse_env_file(env_path)

    def pick(key: str, default: object = None) -> object:
        if key in os.environ:
            return os.environ[key]
        if key in file_env:
            return file_env[key]
        return default

    data_raw = str(pick("DATA_DIR", DEFAULT_DATA_DIR) or DEFAULT_DATA_DIR)
    data_dir = Path(data_raw)
    if not data_dir.is_absolute():
        data_dir = (repo_root / data_dir).resolve()

    settings = Settings(
        server_url=str(pick("SERVER_URL", "http://127.0.0.1:8888")).rstrip("/"),
        device_name=str(pick("DEVICE_NAME", "Gizmo-01")),
        product_name=str(pick("APP_NAME", pick("PRODUCT_NAME", DEFAULT_PRODUCT_NAME))),
        repo_root=repo_root,
        data_dir=data_dir,
        device_mode=str(pick("DEVICE_MODE", "auto")).strip().lower(),
        sim_gpx=str(pick("SIM_GPX", "") or ""),
        sim_start_lat=_as_float(pick("SIM_START_LAT"), 11.2588),
        sim_start_lng=_as_float(pick("SIM_START_LNG"), 75.7804),
        sim_speed_mps=_as_float(pick("SIM_SPEED_MPS"), 1.4),
        sim_heading_deg=_as_float(pick("SIM_HEADING_DEG"), 45.0),
        ui_theme=str(pick("UI_THEME", "dark")),
        ui_fullscreen=_as_bool(pick("UI_FULLSCREEN"), False),
        speech_enabled=_as_bool(pick("SPEECH_ENABLED"), True),
        headless=_as_bool(pick("HEADLESS"), False),
        emergency_contact_name=str(pick("EMERGENCY_CONTACT_NAME", "") or ""),
        emergency_contact_phone=str(pick("EMERGENCY_CONTACT_PHONE", "") or ""),
        llm_dev_mode=_as_bool(pick("LLM_DEV_MODE"), False),
        llm_dev_provider=str(pick("LLM_DEV_PROVIDER", "") or ""),
        llm_dev_api_key=str(pick("LLM_DEV_API_KEY", "") or ""),
        llm_dev_base_url=str(pick("LLM_DEV_BASE_URL", "") or ""),
        llm_dev_model=str(pick("LLM_DEV_MODEL", "") or ""),
        json_timeout_s=_as_float(pick("JSON_TIMEOUT_S"), 10.0),
        photo_timeout_s=_as_float(pick("PHOTO_TIMEOUT_S"), 60.0),
        sos_timeout_s=_as_float(pick("SOS_TIMEOUT_S"), 5.0),
        heartbeat_interval_s=_as_float(pick("HEARTBEAT_INTERVAL_S"), 30.0),
        engine_hz=_as_float(pick("ENGINE_HZ"), 8.0),
        gps_point_interval_s=_as_float(pick("GPS_POINT_INTERVAL_S"), 5.0),
        gps_batch_interval_s=_as_float(pick("GPS_BATCH_INTERVAL_S"), 15.0),
        gps_max_accuracy_m=_as_float(pick("GPS_MAX_ACCURACY_M"), 50.0),
        off_route_tolerance_m=_as_float(pick("OFF_ROUTE_TOLERANCE_M"), 120.0),
        off_route_seconds=_as_float(pick("OFF_ROUTE_SECONDS"), 45.0),
        photo_max_edge_px=_as_int(pick("PHOTO_MAX_EDGE_PX"), 1920),
        photo_quality=_as_int(pick("PHOTO_QUALITY"), 85),
        strip_exif_by_default=_as_bool(pick("STRIP_EXIF"), True),
    )

    if overrides:
        for key, value in overrides.items():
            if not hasattr(settings, key):
                raise AttributeError(f"Unknown setting override: {key}")
            setattr(settings, key, value)

    if settings.device_mode not in ("auto", "sim", "real"):
        settings.device_mode = "auto"

    settings.ensure_dirs()
    return settings


_settings: Optional[Settings] = None


def get_settings(reload: bool = False, **kwargs: object) -> Settings:
    """Process-wide settings singleton."""
    global _settings
    if _settings is None or reload:
        _settings = load_settings(**kwargs)  # type: ignore[arg-type]
    return _settings
