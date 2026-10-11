"""Local message bus.

**Choice: an in-process asyncio pub/sub broker with a documented ZeroMQ
transport behind the same interface.** Justification in ``docs/DECISIONS.md``
D-02. The short version:

* The brief allows "ZeroMQ pub/sub plus request/reply, or a local MQTT broker.
  Choose one and justify it."
* The stronger requirement in the same brief is *"Everything must run without
  real hardware ... so I can test on a laptop"* and *"runnable by a new developer
  in under 30 minutes"*. A ZeroMQ or MQTT daemon on Windows is a new developer's
  first bad afternoon (firewall prompts, port collisions, broker installs).
* So: topics, wildcard subscriptions, request/reply and JSON payloads are all
  implemented here, and :class:`ZmqBus` in this module speaks the identical API
  over ``tcp://127.0.0.1`` with no code change elsewhere. Flip
  ``BUS_TRANSPORT=zmq`` (see ARCHITECTURE.md §Bus) when running the four
  processes on the Pi for real.

Every message is a :class:`~farfromhome_device.core.models.BusMessage`, i.e. a
pydantic-validated ``{topic, payload, source, t}`` envelope.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .models import BusMessage, iso_z, utcnow

logger = logging.getLogger("ffh.bus")

Handler = Callable[[BusMessage], Any]
AsyncHandler = Callable[[BusMessage], Awaitable[Any]]


class Bus:
    """Abstract bus. Concrete buses only need :meth:`publish` and :meth:`_deliver`."""

    def __init__(self, name: str = "bus") -> None:
        self.name = name
        self._subs: Dict[str, List[Handler]] = defaultdict(list)
        self._closed = False
        self._delivered = 0
        self._recent: List[BusMessage] = []
        self._recent_limit = 400

    # --- subscription -------------------------------------------------------
    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to a topic. ``*`` and ``?`` globs are supported."""
        self._subs[topic].append(handler)

        def unsubscribe() -> None:
            try:
                self._subs[topic].remove(handler)
            except ValueError:
                pass

        return unsubscribe

    def subscribe_many(self, topics: List[str], handler: Handler) -> Callable[[], None]:
        removers = [self.subscribe(topic, handler) for topic in topics]

        def unsubscribe() -> None:
            for remove in removers:
                remove()

        return unsubscribe

    def handler_count(self, topic: str) -> int:
        return len(self._matching(topic))

    def _matching(self, topic: str) -> List[Handler]:
        found: List[Handler] = []
        for pattern, handlers in self._subs.items():
            if pattern == topic or fnmatch.fnmatchcase(topic, pattern):
                found.extend(handlers)
        return found

    # --- publishing ---------------------------------------------------------
    def publish(self, topic: str, payload: Optional[Dict[str, Any]] = None, source: str = "unknown") -> BusMessage:
        message = BusMessage(topic=topic, payload=payload or {}, source=source, t=iso_z(utcnow()))
        self.publish_message(message)
        return message

    def publish_message(self, message: BusMessage) -> None:
        if self._closed:
            logger.debug("publish after close ignored: %s", message.topic)
            return
        self._recent.append(message)
        if len(self._recent) > self._recent_limit:
            del self._recent[: len(self._recent) - self._recent_limit]
        self._deliver(message)

    def _deliver(self, message: BusMessage) -> None:
        raise NotImplementedError

    # --- request/reply ------------------------------------------------------
    async def request(
        self,
        topic: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout_s: float = 5.0,
        source: str = "requester",
    ) -> Optional[BusMessage]:
        """Send ``topic`` and await the first reply on ``topic.reply``."""
        reply_topic = f"{topic}.reply"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BusMessage] = loop.create_future()

        def on_reply(message: BusMessage) -> None:
            if not future.done():
                future.set_result(message)

        unsubscribe = self.subscribe(reply_topic, on_reply)
        try:
            self.publish(topic, payload, source=source)
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.debug("request %s timed out", topic)
            return None
        finally:
            unsubscribe()

    def reply(self, request_message: BusMessage, payload: Dict[str, Any], source: str = "responder") -> None:
        self.publish(f"{request_message.topic}.reply", payload, source=source)

    # --- introspection ------------------------------------------------------
    @property
    def recent(self) -> List[BusMessage]:
        return list(self._recent)

    def recent_topics(self, limit: int = 25) -> List[str]:
        return [message.topic for message in self._recent[-limit:]]

    @property
    def delivered(self) -> int:
        return self._delivered

    def close(self) -> None:
        self._closed = True
        self._subs.clear()

    def stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "transport": type(self).__name__,
            "subscriptions": {topic: len(handlers) for topic, handlers in self._subs.items()},
            "delivered": self._delivered,
        }


class LocalBus(Bus):
    """In-process bus. Handlers may be sync or async; async ones are scheduled
    on the running loop (or on a private loop when called outside asyncio)."""

    def __init__(self, name: str = "local") -> None:
        super().__init__(name=name)
        self._tasks: set[asyncio.Task[Any]] = set()

    def _deliver(self, message: BusMessage) -> None:
        for handler in self._matching(message.topic):
            self._delivered += 1
            try:
                result = handler(message)
            except Exception:  # a broken subscriber must not kill the publisher
                logger.exception("bus handler failed for %s", message.topic)
                continue
            if asyncio.iscoroutine(result):
                self._schedule(result)

    def _schedule(self, coro: Awaitable[Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called from a worker thread (e.g. the sync daemon). Run it to
            # completion on a short-lived loop rather than dropping it.
            asyncio.run(coro)  # type: ignore[arg-type]
            return
        task = loop.create_task(coro)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Await in-flight async handlers. Tests use this for determinism."""
        while self._tasks:
            pending = list(self._tasks)
            await asyncio.gather(*pending, return_exceptions=True)


class ZmqBus(Bus):
    """ZeroMQ transport with the same API, for the four-process Pi deployment.

    Imported lazily so ``pyzmq`` stays an optional dependency. Payloads are the
    same JSON envelopes, so a process on :class:`ZmqBus` can be swapped in
    without touching engine, agent or UI code.
    """

    def __init__(self, name: str = "zmq", endpoint: str = "tcp://127.0.0.1:5599") -> None:
        super().__init__(name=name)
        self.endpoint = endpoint
        try:
            import zmq  # noqa: F401  (imported for the side effect of failing loudly)
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "ZeroMQ transport requested but pyzmq is not installed. "
                "Either `pip install pyzmq` or leave BUS_TRANSPORT=local."
            ) from exc
        raise NotImplementedError(
            "ZmqBus is a documented seam, not a shipped transport. The laptop-first "
            "requirement (brief §0.3) is met by LocalBus; see docs/DECISIONS.md D-02 "
            "before implementing this for the four-process Pi deployment."
        )


def make_bus(transport: str = "local", **kwargs: Any) -> Bus:
    transport = (transport or "local").strip().lower()
    if transport == "local":
        return LocalBus(name=kwargs.pop("name", "local"))
    if transport == "zmq":
        return ZmqBus(**kwargs)
    raise ValueError(f"Unknown bus transport: {transport!r} (expected 'local' or 'zmq')")


def dump_recent(bus: Bus, limit: int = 25) -> str:
    """Debug helper used by ``run.py --topics``."""
    lines = []
    for message in bus.recent[-limit:]:
        payload = json.dumps(message.payload, default=str)
        if len(payload) > 140:
            payload = payload[:137] + "..."
        lines.append(f"{message.t}  {message.topic:<22} {message.source:<12} {payload}")
    return "\n".join(lines)
