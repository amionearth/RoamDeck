import os
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "FarFromHome")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "farfromhome-hackathon-secret-key-3.11")
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'app.db'}")
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "true").lower() in ("true", "1", "yes")
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "10"))
    CORS_ORIGINS: List[str] = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://localhost:8000,http://127.0.0.1:5173,http://127.0.0.1:8000"
        ).split(",")
        if origin.strip()
    ]
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    #: Extra origins allowed by regex. The defaults cover the native app shells
    #: (Capacitor serves from localhost / capacitor://localhost) and any private
    #: network address, so a phone on the same WiFi needs no configuration.
    CORS_ORIGIN_REGEX: str = os.getenv(
        "CORS_ORIGIN_REGEX",
        r"^(capacitor|ionic|file)://.*$"
        r"|^https?://localhost(:\d+)?$"
        r"|^https?://127\.0\.0\.1(:\d+)?$"
        r"|^https?://10\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?$"
        r"|^https?://192\.168\.\d{1,3}\.\d{1,3}(:\d+)?$"
        r"|^https?://172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}(:\d+)?$",
    )

settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
(settings.DATA_DIR / "users").mkdir(parents=True, exist_ok=True)
(settings.DATA_DIR / "trips").mkdir(parents=True, exist_ok=True)
