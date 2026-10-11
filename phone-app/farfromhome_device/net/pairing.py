"""Pairing: the handheld never asks for a username or password.

Flow (brief 03 §3, verified against ``backend/app/routers/device.py``):

1. ``POST /api/device/register`` ``{"name": "Gizmo-01"}`` with **no** auth header
   → ``{"device_id", "pairing_code", "poll_secret"}``.
2. Show the code on screen: *"Enter K7P2QX on the website under Devices."*
3. Poll ``GET /api/device/pairing/{device_id}?secret={poll_secret}`` every 3 s.
   * ``pending``  → keep polling.
   * ``paired``   → save ``token`` to the token file (mode 600) and use it.
   * ``expired``  → register again and show the new code.
   * ``401``      → the secret is wrong; treat as expired and re-register.
4. The user types the code on the website, which calls ``POST /api/devices/pair``.

Re-pairing: any ``401`` from a normal call means the token is dead. The client
deletes the token file and calls :func:`pair` again (brief 03 §14 first bullet).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests

from ..core.config import Settings
from ..core.models import RegisterDeviceResponse, iso_z

logger = logging.getLogger("ffh.net.pairing")

PAIRING_POLL_INTERVAL_S = 3.0
PAIRING_MAX_ATTEMPTS = 200  # ~10 minutes

#: Paths that must never be sent with an Authorization header.
NO_AUTH_PATHS = ("/device/register", "/device/pairing/", "/health", "/network-info")


class NotPaired(RuntimeError):
    """Raised when the server rejects our token. Callers re-pair."""


class PairingError(RuntimeError):
    """Raised when pairing cannot even be attempted (server unreachable)."""


@dataclass
class PairingSession:
    device_id: str
    pairing_code: str
    poll_secret: str
    started_at: str = field(default_factory=iso_z)
    attempts: int = 0

    def display(self) -> Dict[str, str]:
        return {
            "title": "Pair this handheld",
            "code": self.pairing_code,
            "instruction": f"Enter {self.pairing_code} on the website under Devices.",
            "device_id": self.device_id,
        }


def read_token(path: Path) -> Optional[str]:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def write_token(path: Path, token: str) -> None:
    """Write the token and lock it down to the owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows/odd filesystems
        logger.debug("could not chmod the token file to 600 (platform limitation)")


def clear_token(path: Path) -> None:
    try:
        path.unlink()
        logger.info("device token removed; the handheld will pair again")
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("could not remove the token file: %s", exc)


class PairingClient:
    """Talks to the pairing endpoints only. Safe to use before a token exists."""

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    def register(self, name: Optional[str] = None) -> PairingSession:
        url = f"{self.settings.api_base}/device/register"
        payload = {"name": name or self.settings.device_name}
        try:
            response = self.session.post(url, json=payload, timeout=self.settings.json_timeout_s)
        except requests.RequestException as exc:
            self.last_error = f"cannot reach the server at {self.settings.server_url}: {exc}"
            raise PairingError(self.last_error) from exc

        if response.status_code >= 400:
            self.last_error = f"register failed: HTTP {response.status_code} {response.text[:200]}"
            raise PairingError(self.last_error)

        data = RegisterDeviceResponse.model_validate(response.json())
        logger.info("pairing code %s issued for device %s", data.pairing_code, data.device_id)
        return PairingSession(
            device_id=data.device_id,
            pairing_code=data.pairing_code,
            poll_secret=data.poll_secret,
        )

    def poll_once(self, session_info: PairingSession) -> Dict[str, Any]:
        url = f"{self.settings.api_base}/device/pairing/{session_info.device_id}"
        session_info.attempts += 1
        try:
            response = self.session.get(
                url,
                params={"secret": session_info.poll_secret},
                timeout=self.settings.json_timeout_s,
            )
        except requests.RequestException as exc:
            self.last_error = f"poll failed: {exc}"
            return {"status": "unreachable", "message": self.last_error}

        if response.status_code == 401:
            logger.warning("pairing secret rejected; the code must be reissued")
            return {"status": "expired", "message": "The pairing secret was rejected."}
        if response.status_code >= 400:
            self.last_error = f"poll failed: HTTP {response.status_code}"
            return {"status": "error", "message": self.last_error}
        try:
            return response.json()
        except ValueError:
            return {"status": "error", "message": "the server sent unreadable JSON"}

    def wait_for_claim(
        self,
        session_info: PairingSession,
        on_code: Optional[Callable[[PairingSession], None]] = None,
        on_tick: Optional[Callable[[int, str], None]] = None,
        max_attempts: int = PAIRING_MAX_ATTEMPTS,
        stop_flag: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        """Poll until claimed. Returns the token, or ``None`` if it never was."""
        if on_code:
            on_code(session_info)
        for attempt in range(max_attempts):
            if stop_flag and stop_flag():
                return None
            if attempt:
                time.sleep(PAIRING_POLL_INTERVAL_S)
            status = self.poll_once(session_info)
            state = status.get("status")
            if on_tick:
                on_tick(attempt + 1, state or "unknown")
            if state == "paired" and status.get("token"):
                write_token(self.settings.token_file, status["token"])
                logger.info("paired as %s", self.settings.device_name)
                return status["token"]
            if state in ("expired", "error", "unreachable"):
                logger.warning("pairing stopped: %s", status.get("message") or state)
                return None
        logger.warning("pairing timed out after %d attempts", max_attempts)
        return None

    def pair_blocking(self, **kwargs: Any) -> Optional[str]:
        """Register and wait for a claim. For headless/CLI use."""
        try:
            session_info = self.register()
        except PairingError as exc:
            self.last_error = str(exc)
            return None
        return self.wait_for_claim(session_info, **kwargs)


def ensure_pairing(
    settings: Settings,
    session: Optional[requests.Session] = None,
    interactive: bool = False,
) -> Optional[str]:
    """Return a usable token, pairing if necessary.

    ``interactive=False`` (headless) returns ``None`` when there is no token and
    the server is unreachable rather than blocking for ten minutes.
    """
    token = read_token(settings.token_file)
    if token:
        return token
    client = PairingClient(settings, session=session)
    if not interactive:
        logger.info("no device token; pairing is required (run with the UI to show the code)")
        return None
    return client.pair_blocking(on_code=lambda info: print(info.display()["instruction"]))
