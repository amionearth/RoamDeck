"""Process entry point used by the systemd units: ``python -m farfromhome_device.app.main <role>``.

Roles:

    agent   plan on demand
    engine  run the live trip loop
    ui      screens, input, audio, camera, games
    syncd   drain the outbox
    all     all of the above in one process (small Pi, demo, debugging)

The four-role split is the brief's process model (§5.0). ``all`` exists because one
process is sometimes the right answer; it is the same components either way.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any, Dict, List, Optional

from .processes import (
    DeviceRuntime,
    install_signal_handlers,
    run_agent,
    run_engine,
    run_syncd_process,
    start_runtime_window,
)
from ..core.config import load_settings
from ..core.infrastructure import build_infrastructure
from ..hal import build_hardware

logger = logging.getLogger("ffh.app.main")

ROLES = ("agent", "engine", "ui", "syncd", "all")


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-26s %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    for noisy in ("asyncio", "urllib3", "PIL", "websockets.client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _build(settings: Any, with_hardware: bool = True) -> Any:
    hardware = None
    if with_hardware:
        hardware = build_hardware(
            mode=settings.device_mode,
            start_lat=settings.sim_start_lat,
            start_lng=settings.sim_start_lng,
            speed_mps=settings.sim_speed_mps,
            gpx_path=settings.sim_gpx or None,
        )
    return build_infrastructure(settings, hardware=hardware)


# --------------------------------------------------------------------------- #
# roles
# --------------------------------------------------------------------------- #
def role_agent(settings: Any) -> int:
    infra = _build(settings, with_hardware=False)
    stop = asyncio.Event()
    install_signal_handlers(stop)

    async def main() -> None:
        await run_agent(infra, stop=stop)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        infra.close()
    return 0


def role_engine(settings: Any) -> int:
    infra = _build(settings, with_hardware=True)
    stop = asyncio.Event()
    install_signal_handlers(stop)

    async def main() -> None:
        await run_engine(infra, stop=stop)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        infra.close()
    return 0


def role_syncd(settings: Any) -> int:
    infra = _build(settings, with_hardware=False)
    stop = asyncio.Event()
    install_signal_handlers(stop)

    async def main() -> None:
        await run_syncd_process(infra, stop=stop, interactive=False)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        infra.close()
    return 0


def role_ui(settings: Any) -> int:
    """The UI on its own: it listens to the bus and renders. No engine object."""
    hardware = build_hardware(
        mode=settings.device_mode,
        start_lat=settings.sim_start_lat,
        start_lng=settings.sim_start_lng,
        speed_mps=settings.sim_speed_mps,
        gpx_path=settings.sim_gpx or None,
    )
    infra = build_infrastructure(settings, hardware=hardware)
    from ..games.host import GameHost
    from ..net.client import DeviceClient
    from ..net.outbox import Outbox
    from ..ui.persona import Persona, VoiceInput

    outbox = Outbox(infra.storage, bus=infra.bus)
    client = DeviceClient(settings, outbox=outbox, bus=infra.bus)
    persona = Persona(settings, infra.bus, hardware=hardware)
    persona.attach()
    voice = VoiceInput(settings, hardware=hardware)
    host = GameHost(settings, client, storage=infra.storage, bus=infra.bus)

    services: Dict[str, Any] = {
        "hardware": hardware,
        "storage": infra.storage,
        "clock": infra.clock,
        "client": client,
        "persona": persona,
        "offline": False,
        "start_lat": settings.sim_start_lat,
        "start_lng": settings.sim_start_lng,
    }
    try:
        return start_runtime_window(
            _UiShim(infra, host=host, persona=persona, voice=voice, services=services)
        )
    finally:
        persona.detach()
        infra.close()


class _UiShim:
    """Makes a bare UI role look like a :class:`DeviceRuntime` to the shell."""

    def __init__(self, infra: Any, *, host: Any, persona: Any, voice: Any, services: Dict[str, Any]) -> None:
        self.infra = infra
        self.engine = None
        self.game_host = host
        self.persona = persona
        self.voice = voice
        self.syncd = None
        self._services = services

    def services(self) -> Dict[str, Any]:
        return self._services


def role_all(settings: Any) -> int:
    """All four components in one process."""
    runtime = DeviceRuntime(settings, offline=False).build(with_network=True)
    stop = asyncio.Event()
    install_signal_handlers(stop)

    def run_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def main() -> None:
            tasks = [
                asyncio.ensure_future(run_agent(runtime.infra, stop=stop)),
                asyncio.ensure_future(runtime.run_syncd()),
                asyncio.ensure_future(runtime.run_game_polling()),
            ]
            await stop.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        try:
            loop.run_until_complete(main())
        finally:
            loop.close()

    import threading

    thread = threading.Thread(target=run_loop, name="ffh-background", daemon=True)
    thread.start()

    try:
        return start_runtime_window(runtime)
    finally:
        stop.set()
        thread.join(timeout=5.0)
        runtime.close()


ROLE_FUNCTIONS = {
    "agent": role_agent,
    "engine": role_engine,
    "ui": role_ui,
    "syncd": role_syncd,
    "all": role_all,
}


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m farfromhome_device.app.main",
        description="Run one FarFromHome handheld process. Used by the systemd units.",
    )
    parser.add_argument("role", choices=ROLES, help="which process to run")
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    parser.add_argument("--server", help="override SERVER_URL")
    parser.add_argument("--headless", action="store_true", help="no GUI (agent/engine/syncd are headless anyway)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    overrides: Dict[str, Any] = {}
    if args.server:
        overrides["server_url"] = args.server.rstrip("/")
    if args.headless:
        overrides["headless"] = True
    settings = load_settings(overrides=overrides)
    logger.info("starting role %s (device %s, server %s)", args.role, settings.device_name, settings.server_url)
    return ROLE_FUNCTIONS[args.role](settings)


if __name__ == "__main__":
    sys.exit(main())
