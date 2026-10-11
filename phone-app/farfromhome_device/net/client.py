"""The REST client: every endpoint the handheld calls, in one place.

Paths are the ones the **running server** actually serves
(``backend/app/routers/device.py``), not the prose in the briefs. Brief 03 writes
``{SERVER_URL}/api/device/...``; the server mounts the device router under
``/api``, so that matches. Where the two documents disagree, the server source
wins and the difference is listed in ``docs/SERVER_CONTRACT.md``.

Two categories of call:

* **Queued writes** (``queue_*``) — go into the outbox and are never awaited.
  Used for everything the user must not lose: the trip, GPS points, photo
  uploads, waypoint reveals, arrive, finish, feed posts.
* **Direct calls** (``create_game``, ``start_game``, ``end_game``, ``game_state``,
  ``heartbeat``, ``sos``, ``network_info``) — need an answer now, so they are
  synchronous with a short timeout.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from ..core.bus import Bus
from ..core.config import Settings
from ..core.models import (
    CreateGameRequest,
    CreateGameResponse,
    FinishBody,
    GameStateResponse,
    GPSPointItem,
    HealthResponse,
    NetworkInfo,
    PhotoMeta,
    Plan,
    Position,
    iso_z,
    utcnow,
)
from .outbox import Outbox, SendResult
from .pairing import NotPaired, PairingClient, clear_token, read_token, write_token

logger = logging.getLogger("ffh.net.client")


class ServerError(RuntimeError):
    """The server answered, and the answer was an error."""

    def __init__(self, status: int, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.payload = payload or {}

    @property
    def error_code(self) -> str:
        return str(self.payload.get("error", "") or self.payload.get("detail", ""))


class ServerUnreachable(RuntimeError):
    """We never got an answer (WiFi down, server not running)."""


class DeviceClient:
    """One HTTP session with the server, plus the outbox that fronts it."""

    def __init__(
        self,
        settings: Settings,
        outbox: Optional[Outbox] = None,
        bus: Optional[Bus] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.settings = settings
        self.outbox = outbox
        self.bus = bus
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": f"FarFromHome-Handheld/{settings.device_name}",
            }
        )
        self.pairing = PairingClient(settings, session=self.session)
        self._token: Optional[str] = read_token(settings.token_file)
        self._apply_token()
        self.last_health: Optional[HealthResponse] = None
        self.on_token_lost: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------ #
    # token handling
    # ------------------------------------------------------------------ #
    def _apply_token(self) -> None:
        if self._token:
            self.session.headers["Authorization"] = f"Bearer {self._token}"
        else:
            self.session.headers.pop("Authorization", None)

    @property
    def paired(self) -> bool:
        return bool(self._token)

    def set_token(self, token: str, persist: bool = True) -> None:
        self._token = token
        if persist:
            write_token(self.settings.token_file, token)
        self._apply_token()

    def forget_token(self) -> None:
        """On a 401: drop the token so the next boot pairs again (brief 03 §2)."""
        self._token = None
        clear_token(self.settings.token_file)
        self._apply_token()
        if self.on_token_lost:
            try:
                self.on_token_lost()
            except Exception:
                logger.exception("on_token_lost handler failed")

    # ------------------------------------------------------------------ #
    # low level
    # ------------------------------------------------------------------ #
    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.settings.api_base}{path}"

    def request_json(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        timeout: Optional[float] = None,
        auth: bool = True,
    ) -> Dict[str, Any]:
        """POST/GET a JSON body and return the decoded response.

        ``body`` may be a dict **or a list**: the GPS points endpoint takes a bare
        JSON array (``add_gps_points(id, points: List[GPSPointItem])``), while brief
        03 §6.2 documents ``{"points": [...]}``. Supporting both here means callers
        do not each have to know; the difference is recorded in
        docs/SERVER_CONTRACT.md G-02.
        """
        url = self._url(path)
        headers = {} if auth else {"Authorization": ""}
        try:
            response = self.session.request(
                method.upper(),
                url,
                json=body,
                timeout=timeout or self.settings.json_timeout_s,
                headers=headers,
            )
        except requests.RequestException as exc:
            raise ServerUnreachable(f"{self.settings.server_url} unreachable: {exc}") from exc
        return self._handle(response)

    def request_multipart(
        self,
        method: str,
        path: str,
        file_path: Path,
        form: Dict[str, str],
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        url = self._url(path)
        content_type = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
        try:
            with open(file_path, "rb") as handle:
                response = self.session.request(
                    method.upper(),
                    url,
                    data=form,
                    files={"file": (file_path.name, handle, content_type)},
                    timeout=timeout or self.settings.photo_timeout_s,
                )
        except requests.RequestException as exc:
            raise ServerUnreachable(f"photo upload to {url} failed: {exc}") from exc
        except OSError as exc:
            raise ServerUnreachable(f"cannot read local photo {file_path}: {exc}") from exc
        return self._handle(response)

    def _handle(self, response: requests.Response) -> Dict[str, Any]:
        if response.status_code == 401:
            self.forget_token()
            raise NotPaired("the server rejected the device token")
        payload: Dict[str, Any] = {}
        if response.content:
            try:
                decoded = response.json()
                payload = decoded if isinstance(decoded, dict) else {"data": decoded}
            except ValueError:
                payload = {"raw": response.text[:500]}
        if response.status_code >= 400:
            message = str(payload.get("detail") or payload.get("message") or payload.get("error") or "")
            raise ServerError(response.status_code, message or response.reason or "error", payload)
        return payload

    # ------------------------------------------------------------------ #
    # health / network info
    # ------------------------------------------------------------------ #
    def health(self) -> Optional[HealthResponse]:
        try:
            payload = self.request_json("GET", "/health", auth=False)
        except (ServerError, ServerUnreachable) as exc:
            logger.debug("health check failed: %s", exc)
            return None
        self.last_health = HealthResponse.model_validate(payload)
        return self.last_health

    def network_info(self) -> Optional[NetworkInfo]:
        """``GET /api/network-info`` — brief 02 §C2. Used to show the right address."""
        try:
            payload = self.request_json("GET", "/network-info", auth=False)
        except (ServerError, ServerUnreachable) as exc:
            logger.debug("network-info failed: %s", exc)
            return None
        return NetworkInfo.model_validate(payload)

    def heartbeat(self, battery_pct: int = 100) -> bool:
        try:
            self.request_json("POST", "/device/heartbeat", {"battery_pct": int(battery_pct)})
            return True
        except NotPaired:
            raise
        except (ServerError, ServerUnreachable) as exc:
            logger.debug("heartbeat failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # pairing passthrough
    # ------------------------------------------------------------------ #
    def register(self) -> Any:
        return self.pairing.register()

    def poll_pairing(self, session_info: Any) -> Dict[str, Any]:
        return self.pairing.poll_once(session_info)

    # ------------------------------------------------------------------ #
    # queued writes
    # ------------------------------------------------------------------ #
    def queue_trip(self, plan: Plan) -> int:
        """Send the plan. The server fills in destination, waypoints and calendar."""
        return self._enqueue("POST", "/device/trips", plan.to_server_plan(), kind="trip", ref_id=plan.trip_id)

    def queue_points(self, trip_id: str, points: List[Position] | List[GPSPointItem]) -> int:
        """Queue a batch of fixes.

        The body is a **bare JSON array**, which is what the running server's
        ``add_gps_points(id, points: List[GPSPointItem])`` validates. Brief 03 §6.2
        shows ``{"points": [...]}``; that shape returns 422. See
        docs/SERVER_CONTRACT.md G-02.
        """
        wire: List[Dict[str, Any]] = []
        for point in points:
            if isinstance(point, GPSPointItem):
                wire.append(point.model_dump(exclude_none=True))
            else:
                wire.append(point.to_wire())
        return self._enqueue(
            "POST",
            f"/device/trips/{trip_id}/points",
            wire,  # type: ignore[arg-type]
            kind="points",
            ref_id=trip_id,
        )

    def queue_waypoint_reveal(self, trip_id: str, idx: int) -> int:
        return self._enqueue(
            "POST",
            f"/device/trips/{trip_id}/waypoints/{idx}/reveal",
            {"at": iso_z(utcnow())},
            kind="reveal",
            ref_id=f"{trip_id}:{idx}",
        )

    def queue_photo(
        self,
        trip_id: str,
        file_path: Path | str,
        meta: Optional[PhotoMeta] = None,
        form_extra: Optional[Dict[str, str]] = None,
    ) -> int:
        """Queue a photo. The local file is kept until the server confirms it."""
        meta = meta or PhotoMeta()
        form = meta.to_form()
        if form_extra:
            form.update({key: str(value) for key, value in form_extra.items()})
        return self._enqueue(
            "POST",
            f"/device/trips/{trip_id}/photos",
            file_path=str(file_path),
            form=form,
            kind="photo",
            ref_id=meta.photo_id,
        )

    def queue_arrive(self, trip_id: str, lat: float, lng: float) -> int:
        return self._enqueue(
            "POST",
            f"/device/trips/{trip_id}/arrive",
            {"lat": lat, "lng": lng, "at": iso_z(utcnow())},
            kind="arrive",
            ref_id=trip_id,
        )

    def queue_finish(self, trip_id: str, distance_m: float, summary: str = "") -> int:
        body = FinishBody(distance_m=round(distance_m, 1), summary=summary)
        return self._enqueue(
            "POST",
            f"/device/trips/{trip_id}/finish",
            body.model_dump(),
            kind="finish",
            ref_id=trip_id,
        )

    def queue_feed_post(self, body: Dict[str, Any]) -> int:
        """A trip summary for the feed.

        There is **no** device-authenticated "create post" endpoint on the running
        server: the feed is built from ``Trip`` rows (``GET /api/feed`` serialises
        trips), and posting/replying needs a browser session cookie
        (``POST /api/trips/{id}/comments`` requires ``get_current_user``). The trip
        itself therefore *is* the feed post once the handheld has posted its plan
        and arrived.

        This method is kept as the single seam for a future device-facing social
        endpoint. It writes an event and returns 0 rather than queueing a request
        that would 401 forever. See docs/SERVER_CONTRACT.md G-04.
        """
        logger.info(
            "feed post skipped (no device-facing endpoint); the trip row is the feed item: %s",
            body.get("trip_id"),
        )
        return 0

    def _enqueue(self, method: str, path: str, body: Optional[Any] = None,
                 file_path: Optional[str] = None, form: Optional[Dict[str, str]] = None,
                 kind: str = "json", ref_id: Optional[str] = None) -> int:
        if self.outbox is None:
            raise RuntimeError("this client was built without an outbox")
        return self.outbox.enqueue(
            method, path, body=body, file_path=file_path, form=form, kind=kind, ref_id=ref_id
        )

    # ------------------------------------------------------------------ #
    # the outbox sender
    # ------------------------------------------------------------------ #
    def send_outbox_row(self, row: Any) -> SendResult:
        """Perform one outbox row's HTTP call. Passed to ``Outbox.flush_once``."""
        path = row["path"]
        method = row["method"]
        try:
            if row["file_path"]:
                form = _decode(row["form"]) or {}
                payload = self.request_multipart(method, path, Path(row["file_path"]), form)
            else:
                payload = self.request_json(method, path, _decode(row["body"]))
        except NotPaired as exc:
            return SendResult("unauthorised", status=401, message=str(exc))
        except ServerError as exc:
            return SendResult(
                Outbox.classify(exc.status, exc.error_code),
                status=exc.status,
                message=exc.message,
                payload=exc.payload,
            )
        except ServerUnreachable as exc:
            return SendResult("retry", message=str(exc))
        return SendResult("done", status=200, payload=payload)

    # ------------------------------------------------------------------ #
    # games (direct: these need an answer now — brief 02 §B5)
    # ------------------------------------------------------------------ #
    def create_game(self, request: CreateGameRequest) -> CreateGameResponse:
        payload = self.request_json("POST", "/device/games", request.model_dump(exclude_none=True))
        return CreateGameResponse.model_validate(payload)

    def start_game(self, server_game_id: str) -> Dict[str, Any]:
        return self.request_json("POST", f"/device/games/{server_game_id}/start")

    def end_game(self, server_game_id: str) -> Dict[str, Any]:
        return self.request_json("POST", f"/device/games/{server_game_id}/end")

    def game_state(self, server_game_id: str) -> GameStateResponse:
        payload = self.request_json("GET", f"/device/games/{server_game_id}/state")
        return GameStateResponse.model_validate(payload)

    # ------------------------------------------------------------------ #
    # SOS: immediate *and* queued (brief 03 §9, the one exception to the outbox)
    # ------------------------------------------------------------------ #
    def sos(self, lat: float, lng: float, trip_id: Optional[str] = None) -> Tuple[int, bool]:
        """Returns ``(outbox_row_id, delivered_now)``."""
        body = {"lat": lat, "lng": lng, "trip_id": trip_id, "at": iso_z(utcnow())}
        row_id = 0
        if self.outbox is not None:
            row_id = self.outbox.queue_sos(body)
        delivered = False
        try:
            self.request_json("POST", "/device/sos", body, timeout=self.settings.sos_timeout_s)
            delivered = True
            logger.warning("SOS delivered to the server at (%.5f, %.5f)", lat, lng)
        except (ServerError, ServerUnreachable, NotPaired) as exc:
            logger.error("SOS could not be delivered immediately (%s); it stays queued", exc)
        return row_id, delivered


def _decode(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    import json

    try:
        return json.loads(raw)
    except ValueError:
        return None
