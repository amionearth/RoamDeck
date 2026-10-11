"""The four processes from the brief's process model, plus the single-process runtime.

======================  ================================================  ===========================
process                 responsibility                                    entry point
======================  ================================================  ===========================
``agent``               planning and on-demand LLM calls                   :func:`run_agent`
``engine``              deterministic live loop, **no LLM calls**          :func:`run_engine`
``ui``                  screens, input, audio, camera, games               :func:`run_ui_process`
``syncd``               outbox, uploads, retries                           :func:`run_syncd_process`
======================  ================================================  ===========================

:class:`DeviceRuntime` wires the same pieces into one process so
``python run.py --simulate`` works on a laptop with no systemd and no four
terminals. The four systemd units in ``systemd/`` are the production shape.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..core.bus import Bus
from ..core.config import Settings
from ..core.db import Storage
from ..core.infrastructure import Infrastructure, build_infrastructure
from ..core.models import Plan, TripSpec, iso_z, utcnow

logger = logging.getLogger("ffh.app")


# --------------------------------------------------------------------------- #
# signals
# --------------------------------------------------------------------------- #
def install_signal_handlers(stop: Any, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """SIGTERM/SIGINT -> ``stop.set()``. Degrades quietly where unsupported."""
    try:
        target = loop or asyncio.get_event_loop()
    except RuntimeError:
        target = None

    def handler(*_: Any) -> None:
        logger.info("shutdown signal received")
        try:
            stop.set()
        except Exception:
            pass

    for name in ("SIGTERM", "SIGINT"):
        attribute = getattr(signal, name, None)
        if attribute is None:
            continue
        installed = False
        if target is not None:
            try:
                target.add_signal_handler(attribute, handler)
                installed = True
            except (NotImplementedError, RuntimeError, ValueError):
                installed = False
        if not installed:
            try:
                signal.signal(attribute, handler)
            except Exception:
                logger.debug("could not install a handler for %s", name)


# --------------------------------------------------------------------------- #
# individual processes
# --------------------------------------------------------------------------- #
async def run_agent(
    infra: Infrastructure,
    stop: Optional[asyncio.Event] = None,
    initial_request: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Plans on demand. Idle costs nothing, which matters on a battery (brief §3)."""
    from ..agent.planner import PlanRequest

    stop = stop or asyncio.Event()
    planner = build_planner(infra)
    work: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_request(message: Any) -> None:
        payload = dict(message.payload or {})
        try:
            loop.call_soon_threadsafe(work.put_nowait, payload)
        except RuntimeError:
            logger.debug("trip.requested arrived with no running loop")

    unsubscribe = infra.bus.subscribe("trip.requested", on_request)
    if initial_request:
        work.put_nowait(initial_request)

    logger.info("agent process ready")
    results: Dict[str, Any] = {}
    try:
        while not stop.is_set():
            try:
                payload = await asyncio.wait_for(work.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            request = PlanRequest(
                text=str(payload.get("text", "")),
                start_lat=payload.get("start_lat"),
                start_lng=payload.get("start_lng"),
                players=list(payload.get("players") or []),
                game_types=list(payload.get("game_types") or ["photo_hunt"]),
                offline=bool(payload.get("offline", False)),
            )
            if payload.get("spec"):
                request.spec = TripSpec.model_validate(payload["spec"])

            outcome = await planner.plan(request)
            key = outcome.plan.trip_id if outcome.plan else f"failed-{len(results)}"
            results[key] = outcome.summary()
            if outcome.plan is not None:
                infra.storage.save_plan(outcome.plan)
                # syncd owns every network write, so hand it the plan.
                infra.bus.publish(
                    "ui.command",
                    {"action": "queue_plan", "plan": outcome.plan.model_dump(mode="json")},
                    source="agent",
                )
    finally:
        unsubscribe()
    return results


async def run_engine(
    infra: Infrastructure,
    plan: Optional[Plan] = None,
    stop: Optional[asyncio.Event] = None,
) -> Dict[str, Any]:
    """Runs one plan. If no plan is given, waits for one on ``plan.ready``."""
    from ..engine.state_machine import TripEngine

    stop = stop or asyncio.Event()
    wake = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_plan(message: Any) -> None:
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            pass

    unsubscribe = infra.bus.subscribe("plan.ready", on_plan)

    if plan is None:
        plan = infra.storage.latest_plan()
    if plan is None:
        logger.info("engine has no plan yet; waiting")
        while plan is None and not stop.is_set():
            try:
                await asyncio.wait_for(wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            wake.clear()
            plan = infra.storage.latest_plan()

    if plan is None:
        unsubscribe()
        logger.warning("engine stopped before a plan arrived")
        return {"state": "no_plan"}

    engine = TripEngine(infra, plan)
    logger.info("engine starting: %s", plan.title)
    task = engine.start_background()
    engine.start()
    try:
        await stop.wait()
    finally:
        engine.flush_points(force=True)
        engine.stop()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        unsubscribe()
    return engine.summary()


async def run_syncd_process(
    infra: Infrastructure,
    stop: Optional[asyncio.Event] = None,
    interactive: bool = False,
) -> Dict[str, Any]:
    """Outbox worker. Pairs on first run."""
    from ..net.client import DeviceClient
    from ..net.outbox import Outbox
    from ..net.pairing import ensure_pairing
    from ..net.syncd import SyncDaemon

    settings = infra.settings
    outbox = Outbox(infra.storage, bus=infra.bus)
    client = DeviceClient(settings, outbox=outbox, bus=infra.bus)

    if not client.paired:
        token = ensure_pairing(settings, session=client.session, interactive=interactive)
        if token:
            client.set_token(token)

    daemon = SyncDaemon(settings, infra.storage, client, clock=infra.clock, bus=infra.bus)
    task = daemon.start()
    stop = stop or asyncio.Event()
    try:
        await stop.wait()
    finally:
        daemon.stop()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    return daemon.status_dict()


def run_ui_process(infra: Infrastructure, context: Optional[Dict[str, Any]] = None) -> int:
    """Start the GUI on the calling thread (:func:`run` does this for the demo)."""
    from ..ui import QT_AVAILABLE
    from ..ui.headless import run_headless

    settings = infra.settings
    context = context or {}

    if settings.headless or not QT_AVAILABLE:
        if not QT_AVAILABLE and not settings.headless:
            logger.warning("PySide6 is unavailable; using the headless renderer")
        return run_headless(
            infra.bus,
            banner_text=f"{settings.product_name} - {settings.device_name}",
        )

    from ..ui.app import run_ui

    return run_ui(
        settings,
        infra.bus,
        engine=context.get("engine"),
        game_host=context.get("game_host"),
        persona=context.get("persona"),
        voice=context.get("voice"),
        syncd=context.get("syncd"),
        services=context.get("services"),
    )


# --------------------------------------------------------------------------- #
# factories
# --------------------------------------------------------------------------- #
def build_planner(infra: Infrastructure, offline: bool = False) -> Any:
    """Planner with the right providers for this run."""
    from ..agent.llm_router import LlmCache, LlmRouter, load_llm_config
    from ..agent.planner import Planner
    from ..agent.tools import (
        NullReviews,
        OfflinePlaces,
        OsrmRouting,
        OverpassPlaces,
        SafetyChecker,
        SocialSignals,
        StraightLineRouting,
    )
    from ..net.pairing import read_token

    settings = infra.settings
    token = read_token(settings.token_file) or ""
    llm_config = load_llm_config(
        server_url=settings.server_url,
        device_token=token,
        dev_mode=settings.llm_dev_mode,
        offline=offline,
        dev_provider=settings.llm_dev_provider,
        dev_api_key=settings.llm_dev_api_key,
        dev_base_url=settings.llm_dev_base_url,
        dev_model=settings.llm_dev_model,
    )
    router = LlmRouter(
        llm_config,
        cache=LlmCache(
            max_entries=llm_config.cache_max_entries,
            ttl_s=llm_config.cache_ttl_s,
            storage=infra.storage,
        ),
    )
    places = OfflinePlaces() if offline else OverpassPlaces()
    routing = StraightLineRouting() if offline else OsrmRouting()
    return Planner(
        settings,
        bus=infra.bus,
        router=router,
        places=places,
        routing=routing,
        reviews=NullReviews(),
        safety=SafetyChecker(
            contact_name=settings.emergency_contact_name,
        ),
        social=SocialSignals(enabled=False),
    )


# --------------------------------------------------------------------------- #
# single-process runtime
# --------------------------------------------------------------------------- #
class DeviceRuntime:
    """All four processes' pieces in one object, for the demo and for tests."""

    def __init__(self, settings: Settings, hardware: Any = None, offline: bool = True) -> None:
        self.settings = settings
        self.offline = offline
        self.hardware = hardware
        self.infra = build_infrastructure(settings, hardware=hardware)
        self.bus: Bus = self.infra.bus
        self.storage: Storage = self.infra.storage
        self.clock = self.infra.clock

        self.plan: Optional[Plan] = None
        self.engine: Any = None
        self.planner: Any = None
        self.game_host: Any = None
        self.persona: Any = None
        self.voice: Any = None
        self.client: Any = None
        self.outbox: Any = None
        self.syncd: Any = None

        self._stop = asyncio.Event()
        self._unsubscribers: List[Any] = []
        self._photo_counter = 0
        self.collage_path: Optional[Path] = None
        self.stamp_path: Optional[Path] = None

    # ------------------------------------------------------------------ #
    # wiring
    # ------------------------------------------------------------------ #
    def build(self, with_network: bool = True, with_persona: bool = True) -> "DeviceRuntime":
        from ..games.host import GameHost
        from ..net.client import DeviceClient
        from ..net.outbox import Outbox
        from ..ui.persona import Persona, VoiceInput

        self.planner = build_planner(self.infra, offline=self.offline or not with_network)

        if with_network:
            self.outbox = Outbox(self.storage, bus=self.bus)
            self.client = DeviceClient(self.settings, outbox=self.outbox, bus=self.bus)

        if with_persona:
            self.persona = Persona(self.settings, self.bus, hardware=self.hardware)
            self.persona.attach()
            self.voice = VoiceInput(self.settings, hardware=self.hardware)

        self.game_host = GameHost(
            self.settings,
            self.client,
            storage=self.storage,
            bus=self.bus,
            observers=self._game_observers(),
        )
        self._wire_bus()
        return self

    def _game_observers(self) -> Any:
        from ..games.host import HostObservers

        return HostObservers(
            on_state=self._on_game_state,
            on_qr=self._on_game_qr,
            on_lobby=self._on_game_lobby,
            on_score=self._on_game_score,
            on_ticker=self._on_game_ticker,
            on_results=self._on_game_results,
            on_error=self._on_game_error,
        )

    def _wire_bus(self) -> None:
        """Stand in for ``syncd``'s queue translation when running in-process."""
        if self.client is not None:
            self._unsubscribers.append(self.bus.subscribe("ui.command", self._on_ui_command))
            self._unsubscribers.append(self.bus.subscribe("photo.captured", self._on_photo_event))

    def _on_ui_command(self, message: Any) -> None:
        payload = message.payload or {}
        action = payload.get("action")
        trip_id = payload.get("trip_id") or (self.plan.trip_id if self.plan else None)
        if self.client is None or not trip_id:
            return
        if action == "queue_points":
            from ..core.models import GPSPointItem

            points = [GPSPointItem.model_validate(item) for item in payload.get("points", [])]
            if points:
                self.client.queue_points(trip_id, points)
        elif action == "queue_plan" and payload.get("plan"):
            self.client.queue_trip(Plan.model_validate(payload["plan"]))
        elif action == "queue_arrive":
            self.client.queue_arrive(trip_id, float(payload.get("lat", 0.0)), float(payload.get("lng", 0.0)))
        elif action == "queue_finish":
            self.client.queue_finish(trip_id, float(payload.get("distance_m", 0.0)), payload.get("summary", ""))
        elif action == "queue_reveal":
            self.client.queue_waypoint_reveal(trip_id, int(payload.get("idx", 0)))

    def _on_photo_event(self, message: Any) -> None:
        # The photo queue is filled by take_photo(); this keeps the bus honest.
        return None

    # --- game observers (the Qt shell also receives game.phase via the bus) ---
    def _on_game_state(self, state: Any, detail: Dict[str, Any]) -> None:
        logger.debug("game state: %s %s", state, detail)

    def _on_game_qr(self, target: Any) -> None:
        logger.info("join at %s (code %s)", target.address, target.code)

    def _on_game_lobby(self, players: Sequence[Dict[str, Any]]) -> None:
        joined = sum(1 for player in players if player.get("joined"))
        logger.info("lobby: %d/%d joined", joined, len(players))

    def _on_game_score(self, board: Sequence[Dict[str, Any]]) -> None:
        return None

    def _on_game_ticker(self, event: Any) -> None:
        logger.info("game: %s", event.message or event.kind)

    def _on_game_results(self, payload: Dict[str, Any]) -> None:
        logger.info("game over: %s", payload.get("winner") or "draw")

    def _on_game_error(self, message: str, detail: Dict[str, Any]) -> None:
        logger.warning("game error: %s", message)

    # ------------------------------------------------------------------ #
    # services for the UI
    # ------------------------------------------------------------------ #
    @property
    def start_lat(self) -> Optional[float]:
        track = getattr(self.hardware.gps, "track", None) if self.hardware else None
        return track.start[0] if track else None

    @property
    def start_lng(self) -> Optional[float]:
        track = getattr(self.hardware.gps, "track", None) if self.hardware else None
        return track.start[1] if track else None

    def services(self) -> Dict[str, Any]:
        return {
            "hardware": self.hardware,
            "storage": self.storage,
            "clock": self.clock,
            "client": self.client,
            "planner": self.planner,
            "last_plan": self.plan,
            "llm_router": getattr(self.planner, "router", None),
            "offline": self.offline,
            "start_lat": self.start_lat,
            "start_lng": self.start_lng,
            "resolved_mode": self.settings.device_mode,
            "syncd": self.syncd,
            "take_photo": self.take_photo,
            "sos": self.send_sos,
            "arrive": self.force_arrival,
            "build_wrapup": self.build_wrapup,
            "diagnose": self.diagnose,
            "start_offline_demo": self.start_offline_demo,
            "start_trip": self.start_trip,
            "parse_text": self.parse_text,
            "camera_preview": self.camera_preview,
            "wifi_ssid": None,
        }

    # ------------------------------------------------------------------ #
    # trip actions
    # ------------------------------------------------------------------ #
    async def plan_trip(self, text: str = "", spec: Optional[TripSpec] = None,
                        offline: Optional[bool] = None) -> Optional[Plan]:
        from ..agent.planner import PlanRequest

        if self.planner is None:
            self.planner = build_planner(self.infra, offline=self.offline)
        request = PlanRequest(
            text=text,
            spec=spec,
            start_lat=self.start_lat,
            start_lng=self.start_lng,
            offline=self.offline if offline is None else offline,
        )
        outcome = await self.planner.plan(request)
        self.plan = outcome.plan
        if self.plan is not None:
            self.storage.save_plan(self.plan)
            self.bus.publish("plan.ready", self.plan.public_summary(), source="runtime")
            if self.client is not None:
                self.client.queue_trip(self.plan)
        return self.plan

    async def parse_text(self, text: str) -> Optional[TripSpec]:
        from ..agent.input_parser import parse_with_llm

        router = getattr(self.planner, "router", None) if self.planner else None
        return await parse_with_llm(router, text, self.start_lat, self.start_lng)

    def start_trip(self, plan: Optional[Plan] = None) -> Any:
        """Start the engine for a plan (idle -> planned -> running)."""
        from ..engine.state_machine import TripEngine

        self.plan = plan or self.plan or self.storage.latest_plan()
        if self.plan is None:
            logger.warning("start_trip called with no plan")
            return None

        # A simulated walk should follow the route the engine is checking against,
        # otherwise the demo never crosses a geofence and never arrives.
        follow = getattr(self.hardware.gps, "follow_plan", None) if self.hardware else None
        if callable(follow):
            try:
                follow(self.plan)
            except Exception as exc:
                logger.warning("could not build a simulated route from the plan: %s", exc)

        if self.engine is not None:
            self.engine.stop()
        self.engine = TripEngine(self.infra, self.plan)
        self.engine.start()
        return self.engine

    def start_offline_demo(self) -> Optional[Plan]:
        """A cached demo trip that needs no server and no network (brief 03 §12 step 5)."""
        cached = self.storage.latest_plan()
        if cached is not None:
            self.plan = cached
            self.start_trip(cached)
            self.bus.publish("plan.ready", cached.public_summary(), source="runtime")
            if self.persona:
                self.persona.greet()
            return cached

        # Nothing cached: plan one synchronously through the offline providers.
        loop = asyncio.new_event_loop()
        try:
            plan = loop.run_until_complete(self.plan_trip("a chill walk by the water for two, about an hour"))
        finally:
            loop.close()
        if plan is not None:
            self.start_trip(plan)
            if self.persona:
                self.persona.greet()
        return plan

    def force_arrival(self) -> None:
        """Jump the simulated GPS to the destination and let the engine notice."""
        if self.plan is None:
            return
        gps = self.hardware.gps if self.hardware else None
        if gps is not None and hasattr(gps, "pin"):
            gps.pin(self.plan.destination.lat, self.plan.destination.lng)
        if self.engine is not None:
            # Tick once so the geofence and the reveal fire immediately rather than
            # waiting for the next scheduled tick.
            self.engine.tick()

    def camera_preview(self) -> Optional[bytes]:
        if self.hardware is None:
            return None
        try:
            return self.hardware.camera.preview_frame()
        except Exception:
            return None

    async def take_photo(self, category: str = "waypoint", caption: str = "",
                         member_name: str = "Explorer") -> str:
        """Capture, compress, strip EXIF, save, queue and announce."""
        from ..core.models import PhotoMeta, new_id
        from ..core.photos import prepare_photo

        hardware = self.hardware
        if hardware is None or not hardware.camera.available:
            return "No camera available."
        raw = await asyncio.to_thread(hardware.camera.capture)
        if not raw:
            return "The camera did not return an image."

        trip_id = self.plan.trip_id if self.plan else "no-trip"
        photo_id = new_id()
        target = self.settings.trip_dir(trip_id) / "photos" / f"{photo_id}.jpg"
        prepared = await asyncio.to_thread(
            prepare_photo,
            raw,
            target,
            max_edge=self.settings.photo_max_edge_px,
            quality=self.settings.photo_quality,
            target_bytes=self.settings.photo_target_bytes,
            strip_exif=self.settings.strip_exif_by_default,
        )
        if prepared is None:
            return "Could not save the photo."

        position = self.engine.position if self.engine else None
        self.storage.add_photo(
            photo_id=photo_id,
            local_path=str(prepared.path),
            trip_id=trip_id if self.plan else None,
            task_id=None,
            member_name=member_name,
            category=category,
            caption=caption,
            lat=position.lat if position else None,
            lng=position.lng if position else None,
            taken_at=iso_z(position.t) if position else iso_z(utcnow()),
            exif_stripped=prepared.exif_stripped,
        )
        self._photo_counter += 1
        self.bus.publish(
            "photo.captured",
            {
                "photo_id": photo_id,
                "trip_id": trip_id,
                "category": category,
                "path": str(prepared.path),
                "bytes": prepared.bytes_size,
            },
            source="runtime",
        )
        if self.client is not None and self.plan is not None:
            self.client.queue_photo(
                trip_id,
                prepared.path,
                PhotoMeta(
                    photo_id=photo_id,
                    taken_at=iso_z(utcnow()),
                    lat=position.lat if position else None,
                    lng=position.lng if position else None,
                    member_name=member_name,
                    category=category,
                    caption=caption,
                ),
            )
            return f"Photo {self._photo_counter} saved and queued ({prepared.bytes_size // 1024} kB)."
        return f"Photo {self._photo_counter} saved ({prepared.bytes_size // 1024} kB)."

    async def send_sos(self) -> str:
        """Send immediately with a short timeout **and** queue a copy (brief 03 §9)."""
        position = self.engine.position if self.engine else None
        lat = position.lat if position else 0.0
        lng = position.lng if position else 0.0
        trip_id = self.plan.trip_id if self.plan else None
        self.bus.publish(
            "sos.triggered",
            {"lat": lat, "lng": lng, "trip_id": trip_id, "at": iso_z(utcnow())},
            source="runtime",
        )
        if self.client is None:
            self.storage.log_event("sos.offline", {"lat": lat, "lng": lng, "trip_id": trip_id})
            contact = self.settings.emergency_contact_name or "your emergency contact"
            return (
                f"No server connection. Logged locally at {lat:.5f}, {lng:.5f}. "
                f"Call {contact} directly."
            )
        _, delivered = await asyncio.to_thread(self.client.sos, lat, lng, trip_id)
        if delivered:
            return "SOS sent to the server. Stay where you are if it is safe."
        return "Could not reach the server. It is queued and will retry. Call for help directly."

    def build_wrapup(self) -> tuple[str, Optional[str], Optional[str]]:
        """Collage + stamp + the summary line for the wrap-up screen."""
        from ..wrapup.collage import collage_from_storage
        from ..wrapup.stamp import stamp_for_plan

        if self.plan is None:
            return ("There is no finished trip to wrap up.", None, None)
        trip_id = self.plan.trip_id
        summary_data = self.engine.summary() if self.engine else {}
        summary_line = (
            f"{self.plan.destination.name} · "
            f"{summary_data.get('distance_m', 0) / 1000:.1f} km · "
            f"{summary_data.get('photos', self.storage.photo_count(trip_id))} photos · "
            f"{summary_data.get('waypoints_reached', 0)}/{len(self.plan.waypoints)} waypoints"
        )

        out_dir = self.settings.trip_dir(trip_id)
        collage = collage_from_storage(
            self.storage,
            trip_id,
            out_dir / "collage.jpg",
            title=self.plan.title,
            subtitle=self.plan.spec.describe(),
            stats={
                "km": round(summary_data.get("distance_m", 0) / 1000.0, 1),
                "photos": summary_data.get("photos", self.storage.photo_count(trip_id)),
            },
            destination_name=self.plan.destination.name,
        )
        if collage.ok:
            self.collage_path = collage.path

        # The stamp is only allowed to name the place because the trip has arrived.
        arrived = self.engine.state.value == "arrived" if self.engine else True
        if arrived:
            stamp = stamp_for_plan(
                self.plan,
                out_dir / "stamp.png",
                distance_km=self.plan.route_km,
                arrived_at=utcnow(),
            )
            if stamp.ok:
                self.stamp_path = stamp.path

        if self.client is not None:
            self.storage.log_event("wrapup.built", {"trip_id": trip_id, "collage": str(self.collage_path)})

        return (
            summary_line,
            str(self.collage_path) if self.collage_path else None,
            str(self.stamp_path) if self.stamp_path else None,
        )

    def diagnose(self) -> Dict[str, Any]:
        from ..net.diagnostics import describe_for_screen, run_diagnostics

        diagnostics = run_diagnostics(self.settings.server_url, client=self.client)
        return {**describe_for_screen(diagnostics), "raw": diagnostics.to_payload()}

    # ------------------------------------------------------------------ #
    # loop helpers
    # ------------------------------------------------------------------ #
    async def run_syncd(self) -> Any:
        from ..net.syncd import SyncDaemon

        if self.client is None:
            return None
        self.syncd = SyncDaemon(self.settings, self.storage, self.client, clock=self.clock, bus=self.bus)
        self.syncd.start()
        return self.syncd

    async def run_game_polling(self, interval_s: float = 3.0) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(interval_s)
            if self.game_host is not None and self.game_host.state.value in ("LOBBY", "RUNNING"):
                await self.game_host.poll_state_once()

    def close(self) -> None:
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:
                pass
        self._unsubscribers.clear()
        if self.engine is not None:
            try:
                self.engine.stop()
            except Exception:
                pass
        if self.persona is not None:
            self.persona.detach()
        if self.syncd is not None:
            try:
                self.syncd.stop()
            except Exception:
                pass
        self.infra.close()

    def status(self) -> Dict[str, Any]:
        return {
            "offline": self.offline,
            "has_plan": self.plan is not None,
            "engine": self.engine.live_status() if self.engine else None,
            "outbox": self.storage.outbox_count(),
            "photos": self.storage.photo_count(),
            "game": self.game_host.status() if self.game_host else None,
            "paired": bool(self.client and self.client.paired),
        }


def start_runtime_window(runtime: DeviceRuntime) -> int:
    """Run the Qt/headless shell on this thread for a :class:`DeviceRuntime`."""
    return run_ui_process(
        runtime.infra,
        {
            "engine": runtime.engine,
            "game_host": runtime.game_host,
            "persona": runtime.persona,
            "voice": runtime.voice,
            "syncd": runtime.syncd,
            "services": runtime.services(),
        },
    )
