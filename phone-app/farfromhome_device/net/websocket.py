"""WebSocket client for live game updates.

Brief 03 §8 and brief 02 §C2. The contract, as implemented by the server
(``backend/app/main.py`` ``/ws`` + ``backend/app/realtime.py``):

* Connect to ``ws://{host}/ws`` — **no** token is required by the running server
  (brief 03 §8 suggests ``?token=...``; the server ignores query parameters, so we
  still send it for forward compatibility).
* Subscribe with ``{"subscribe": "game:{game_id}"}``. Several subscriptions are
  allowed. The server routes messages by channel name only, so a subscription is
  just a label.
* The server pushes ``{"event": "...", "data": {...}}``.

Events the handheld cares about (server names on the left, brief 03's names noted):

====================================  =========================================
``game.player_joined``                ``{player, joined_count}`` (brief 02 §C2)
``game.submission``                   ``{photo_id, player_name, task_id,
                                      thumb_url, status}``
``game.submission_status``            ``{photo_id, status, task_id}`` — the
                                      server's name for brief 03's ``game.vote``
``game.started`` / ``game.ended``     lifecycle
``game.score``                        not emitted by the running server; scores
                                      arrive via ``game.submission_status``
                                      followed by a state poll
``trip.position`` / ``trip.arrived``  the website's live view of us
====================================  =========================================

Behaviour required by the brief and implemented here:

* ping every 20 s to keep it alive;
* reconnect with backoff 1, 2, 4, 8, capped at 15 s;
* **re-subscribe, then call the state endpoint** to catch up on anything missed;
* the handheld must work with the WebSocket down — so every failure is swallowed
  and surfaced as a status event, never an exception in the game loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from ..core.bus import Bus
from ..core.config import Settings
from ..core.models import iso_z, utcnow

logger = logging.getLogger("ffh.net.ws")

try:  # optional at import time so the engine/agent run without it
    import websockets
    from websockets.exceptions import ConnectionClosed

    WEBSOCKETS_AVAILABLE = True
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]
    ConnectionClosed = Exception  # type: ignore[assignment,misc]
    WEBSOCKETS_AVAILABLE = False

PING_INTERVAL_S = 20.0
BACKOFF_SEQUENCE = (1.0, 2.0, 4.0, 8.0, 15.0)


@dataclass
class ConnectionStatus:
    connected: bool = False
    attempts: int = 0
    last_error: str = ""
    connected_since: Optional[str] = None
    subscriptions: Set[str] = field(default_factory=set)
    events_received: int = 0

    def to_payload(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "connected_since": self.connected_since,
            "subscriptions": sorted(self.subscriptions),
            "events_received": self.events_received,
        }


class LiveConnection:
    """A reconnecting WebSocket with subscriptions and event fan-out."""

    def __init__(
        self,
        settings: Settings,
        bus: Optional[Bus] = None,
        token: Optional[str] = None,
    ) -> None:
        self.settings = settings
        self.bus = bus
        self.token = token
        self.status = ConnectionStatus()
        self._socket: Any = None
        self._subscriptions: Set[str] = set()
        self._handlers: List[Callable[[str, Dict[str, Any]], Any]] = []
        self._task: Optional[asyncio.Task[Any]] = None
        self._running = False
        self._catch_up: Optional[Callable[[], Awaitable[None]]] = None

    # ------------------------------------------------------------------ #
    # wiring
    # ------------------------------------------------------------------ #
    def on_event(self, handler: Callable[[str, Dict[str, Any]], Any]) -> Callable[[], None]:
        self._handlers.append(handler)

        def remove() -> None:
            try:
                self._handlers.remove(handler)
            except ValueError:
                pass

        return remove

    def on_reconnect(self, catch_up: Callable[[], Awaitable[None]]) -> None:
        """Called after a reconnect: re-subscribe, then poll the state endpoint."""
        self._catch_up = catch_up

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    @property
    def url(self) -> str:
        base = self.settings.ws_url
        if self.token:
            return f"{base}?token={self.token}"
        return base

    async def connect(self) -> bool:
        if not WEBSOCKETS_AVAILABLE:
            self.status.last_error = "the websockets package is not installed"
            logger.info("live updates disabled: %s", self.status.last_error)
            return False
        try:
            self._socket = await websockets.connect(self.url, open_timeout=6.0, close_timeout=3.0)
        except Exception as exc:
            self.status.connected = False
            self.status.last_error = str(exc)
            logger.debug("websocket connect failed: %s", exc)
            self._publish_status()
            return False
        self.status.connected = True
        self.status.connected_since = iso_z(utcnow())
        self.status.last_error = ""
        logger.info("live connection open at %s", base_url_safe(self.url))
        for channel in self._subscriptions:
            await self._send_subscribe(channel)
        self._publish_status()
        return True

    async def subscribe(self, channel: str) -> None:
        self._subscriptions.add(channel)
        self.status.subscriptions = set(self._subscriptions)
        if self.status.connected:
            await self._send_subscribe(channel)

    async def unsubscribe(self, channel: str) -> None:
        self._subscriptions.discard(channel)
        self.status.subscriptions = set(self._subscriptions)
        if self.status.connected:
            await self._safe_send({"unsubscribe": channel})

    async def _send_subscribe(self, channel: str) -> None:
        await self._safe_send({"subscribe": channel})
        logger.debug("subscribed to %s", channel)

    async def _safe_send(self, payload: Dict[str, Any]) -> bool:
        if self._socket is None:
            return False
        try:
            await self._socket.send(json.dumps(payload))
            return True
        except Exception as exc:
            logger.debug("websocket send failed: %s", exc)
            return False

    async def run(self) -> None:
        """Reconnect forever. Cancellable; never raises."""
        self._running = True
        attempt = 0
        while self._running:
            if await self.connect():
                attempt = 0
                if self._catch_up:
                    try:
                        await self._catch_up()
                    except Exception:
                        logger.exception("catch-up after reconnect failed")
                try:
                    await self._read_loop()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.debug("websocket read loop ended: %s", exc)
                self.status.connected = False
                self._publish_status()
            attempt += 1
            self.status.attempts = attempt
            delay = BACKOFF_SEQUENCE[min(attempt - 1, len(BACKOFF_SEQUENCE) - 1)]
            if self._running:
                await asyncio.sleep(delay)

    async def _read_loop(self) -> None:
        ping_task = asyncio.ensure_future(self._ping_loop())
        try:
            while self._running and self._socket is not None:
                raw = await self._socket.recv()
                if isinstance(raw, bytes):
                    # Binary frames are push-to-talk audio (brief 03 §10). No audio
                    # feature is enabled until the meetup question is answered.
                    continue
                self._dispatch(raw)
        finally:
            ping_task.cancel()
            try:
                await ping_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _ping_loop(self) -> None:
        while self._running:
            await asyncio.sleep(PING_INTERVAL_S)
            if not await self._safe_send({"type": "ping", "t": iso_z(utcnow())}):
                # Try a protocol-level ping too; some servers only answer that.
                try:
                    if self._socket is not None:
                        await self._socket.ping()
                except Exception:
                    return

    def _dispatch(self, raw: Any) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("ignoring unreadable websocket frame")
            return
        event = str(message.get("event") or message.get("type") or "")
        data = message.get("data") or {}
        if not isinstance(data, dict):
            data = {"value": data}
        self.status.events_received += 1
        logger.debug("ws event %s", event)

        for handler in list(self._handlers):
            try:
                result = handler(event, data)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                logger.exception("websocket handler failed for %s", event)

        if self.bus and event:
            self.bus.publish("game.event", {"event": event, "data": data}, source="ws")

    async def close(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        if self._socket is not None:
            try:
                await self._socket.close()
            except Exception:
                pass
            self._socket = None
        self.status.connected = False

    def start_background(self) -> Optional[asyncio.Task[Any]]:
        if not WEBSOCKETS_AVAILABLE:
            logger.info("live updates unavailable (install websockets); using REST polling")
            return None
        self._task = asyncio.ensure_future(self.run())
        return self._task

    def _publish_status(self) -> None:
        if self.bus:
            self.bus.publish("connection.status", self.status.to_payload(), source="ws")

    def status_dict(self) -> Dict[str, Any]:
        return {**self.status.to_payload(), "url": base_url_safe(self.url)}


def base_url_safe(url: str) -> str:
    """Strip the token out of a URL before it reaches a log or the UI."""
    if "token=" not in url:
        return url
    head, _, _ = url.partition("token=")
    return head + "token=***"


class PollingFallback:
    """Poll ``GET /api/device/games/{id}/state`` every 3 s (brief 03 §7.3).

    Used whenever the WebSocket is down, which on event WiFi is most of the time.
    """

    def __init__(self, interval_s: float = 3.0) -> None:
        self.interval_s = interval_s
        self._task: Optional[asyncio.Task[Any]] = None

    async def run(self, fetch: Callable[[], Any], on_state: Callable[[Any], Any]) -> None:
        while True:
            try:
                state = await asyncio.to_thread(fetch)
                if state is not None:
                    result = on_state(state)
                    if asyncio.iscoroutine(result):
                        await result
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("game state poll failed")
            await asyncio.sleep(self.interval_s)

    def start(self, fetch: Callable[[], Any], on_state: Callable[[Any], Any]) -> asyncio.Task[Any]:
        self._task = asyncio.ensure_future(self.run(fetch, on_state))
        return self._task

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
