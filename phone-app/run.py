#!/usr/bin/env python3
"""FarFromHome handheld: one entry point, several modes.

    python run.py                      # normal: GUI, real/sim hardware, talks to the server
    python run.py --simulate           # laptop demo: simulated hardware, offline planner
    python run.py --simulate --online  # simulated hardware but real server calls
    python run.py --headless           # no GUI (SSH, CI, a Pi with no screen)
    python run.py --demo               # full scripted trip, prints every milestone, exits
    python run.py --self-test          # check hardware, storage, network and templates, then exit
    python run.py --pair               # pair this handheld and print the code
    python run.py --topics             # print the last bus messages after a short run

The four-process production shape is in ``systemd/``; this script is the single
process developer path, and it starts the same components.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Make `python run.py` work from any directory without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from farfromhome_device import __product__, __version__
from farfromhome_device.app.processes import DeviceRuntime, start_runtime_window
from farfromhome_device.core.config import load_settings

logger = logging.getLogger("ffh.run")


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #
def configure_logging(verbosity: int = 0, log_file: Optional[Path] = None) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    # These libraries are chatty at DEBUG and tell us nothing useful.
    for noisy in ("asyncio", "urllib3", "requests", "PIL", "websockets.client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description=(
            f"{__product__} handheld {__version__} - the Raspberry Pi 5 blind-trip client. "
            "Every sensor and the network have simulators, so it runs on a laptop."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python run.py --simulate                 offline laptop demo with a fake walk\n"
            "  python run.py --headless --simulate      same, no GUI\n"
            "  python run.py --demo                     scripted end-to-end run, then exit\n"
            "  python run.py --self-test                check this machine's hardware\n"
        ),
    )
    parser.add_argument("--simulate", "--sim", action="store_true",
                        help="use simulated hardware (GPS replay, fake camera, scripted IMU)")
    parser.add_argument("--real", action="store_true", help="force real hardware drivers")
    parser.add_argument("--headless", action="store_true", help="no GUI: print to the console instead")
    parser.add_argument("--offline", action="store_true",
                        help="never touch the network: offline place set, straight-line routing, template plan")
    parser.add_argument("--online", action="store_true", help="force server calls even when simulating")
    parser.add_argument("--demo", action="store_true", help="run the scripted demo trip and exit")
    parser.add_argument("--self-test", action="store_true", help="check hardware/config and exit")
    parser.add_argument("--pair", action="store_true", help="pair this handheld now")
    parser.add_argument("--topics", action="store_true", help="with --demo, print the bus message log")
    parser.add_argument("--server", type=str, default=None, help="override SERVER_URL")
    parser.add_argument("--name", type=str, default=None, help="override DEVICE_NAME")
    parser.add_argument("--gpx", type=str, default=None, help="replay this GPX file instead of a synthetic walk")
    parser.add_argument("--speed", type=float, default=None, help="simulated speed in m/s (default 1.4)")
    parser.add_argument("--fast", action="store_true", help="run the simulated walk 20x faster")
    parser.add_argument("--duration", type=float, default=None, help="seconds to run before exiting")
    parser.add_argument("--yes", "-y", action="store_true", help="do not wait for confirmation")
    parser.add_argument("--fullscreen", action="store_true", help="start the UI fullscreen")
    parser.add_argument("--log-file", type=str, default=None, help="also write logs here")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v for info, -vv for debug")
    parser.add_argument("--version", action="version", version=f"{__product__} handheld {__version__}")
    return parser


def settings_from_args(args: argparse.Namespace) -> Any:
    overrides: Dict[str, Any] = {}
    if args.server:
        overrides["server_url"] = args.server.rstrip("/")
    if args.name:
        overrides["device_name"] = args.name
    if args.gpx:
        overrides["sim_gpx"] = args.gpx
    if args.speed:
        overrides["sim_speed_mps"] = args.speed
    if args.fullscreen:
        overrides["ui_fullscreen"] = True
    if args.headless:
        overrides["headless"] = True
    if args.simulate:
        overrides["device_mode"] = "sim"
    elif args.real:
        overrides["device_mode"] = "real"

    settings = load_settings(overrides=overrides)
    return settings


# --------------------------------------------------------------------------- #
# modes
# --------------------------------------------------------------------------- #
def mode_self_test(settings: Any) -> int:
    """Check every dependency and print a report. The 'runnable in 30 minutes' aid."""
    from farfromhome_device.hal import build_hardware, hardware_report, resolve_mode

    print(f"\n{__product__} handheld {__version__} - self test\n" + "=" * 58)
    print(f"  python          {sys.version.split()[0]} ({sys.executable})")
    print(f"  config          {settings.data_dir}")
    print(f"  server          {settings.server_url}")

    resolved = resolve_mode(settings.device_mode)
    print(f"  device mode     {settings.device_mode} -> {resolved}")

    optional = {
        "PySide6": "the GUI (headless mode works without it)",
        "qrcode": "QR codes for phone game sessions",
        "requests": "HTTP to the server",
        "websockets": "live game updates (REST polling works without it)",
        "PIL": "photo preparation, collage and stamp",
        "yaml": "game templates",
        "numpy": "nothing critical",
        "vosk": "offline speech-to-text",
        "whisper": "offline speech-to-text",
    }
    print("\n  optional packages")
    for module, why in optional.items():
        try:
            __import__(module)
            print(f"    ok    {module:<12} {why}")
        except ImportError:
            print(f"    --    {module:<12} {why}")

    print("\n  hardware")
    try:
        hardware = build_hardware(
            mode=settings.device_mode,
            start_lat=settings.sim_start_lat,
            start_lng=settings.sim_start_lng,
            speed_mps=settings.sim_speed_mps,
            gpx_path=settings.sim_gpx or None,
        )
        report = hardware_report(hardware)
        print(f"    platform      {report['platform']}")
        print(f"    raspberry pi  {report['raspberry_pi']}")
        for name, status in report["devices"].items():
            mark = "ok" if status.get("open") else "--"
            print(f"    {mark}    {name:<10} {status.get('driver')}")
        hardware.close_all()
    except Exception as exc:
        print(f"    FAIL  hardware could not be built: {exc}")
        return 2

    print("\n  game templates")
    try:
        from farfromhome_device.games.template_loader import template_summaries

        for template in template_summaries():
            print(
                f"    {template['type']:<18} {template['item_count']:>2} items, "
                f"{template['players']['min']}-{template['players']['max']} players, "
                f"{template['default_duration']} min"
            )
        if not template_summaries():
            print("    FAIL  no templates loaded")
            return 2
    except Exception as exc:
        print(f"    FAIL  {exc}")
        return 2

    print("\n  storage")
    try:
        from farfromhome_device.core.db import Storage

        storage = Storage(settings.outbox_db)
        print(f"    {storage.stats()}")
        storage.close()
    except Exception as exc:
        print(f"    FAIL  {exc}")
        return 2

    print("\n  network")
    from farfromhome_device.net.client import DeviceClient
    from farfromhome_device.net.diagnostics import run_diagnostics

    try:
        diagnostics = run_diagnostics(settings.server_url, client=DeviceClient(settings))
    except Exception as exc:
        print(f"    FAIL  {exc}")
        return 2
    for check in diagnostics.checks:
        print(f"    {'ok' if check.ok else '--'}    {check.name:<16} {check.detail}")
    if diagnostics.reason():
        print(f"    reason: {diagnostics.reason()}")
    for fix in diagnostics.fixes():
        print(f"    fix:    {fix}")
    if diagnostics.base_url:
        print(f"    phones should use: {diagnostics.base_url}")

    print("\n" + "=" * 58)
    print("  self test finished. Offline demo:  python run.py --simulate --offline")
    print("=" * 58 + "\n")
    return 0


def mode_pair(settings: Any) -> int:
    from farfromhome_device.net.client import DeviceClient

    client = DeviceClient(settings)
    if client.paired:
        print(f"Already paired. Token file: {settings.token_file}")
        answer = input("Pair again anyway? [y/N] ").strip().lower()
        if answer != "y":
            return 0
    try:
        session_info = client.register()
    except Exception as exc:
        print(f"Could not reach the server: {exc}")
        return 2
    print("\n" + "=" * 46)
    print(f"  Enter this code on the website, under Devices:  {session_info.pairing_code}")
    print("=" * 46)
    print("Waiting for the claim... (Ctrl-C to stop)\n")
    token = client.pairing.wait_for_claim(
        session_info,
        on_tick=lambda attempt, state: print(f"  [{attempt:>3}] {state}"),
    )
    if token:
        client.set_token(token)
        print(f"\nPaired. Token saved to {settings.token_file}")
        return 0
    print("\nPairing did not complete.")
    return 1


def mode_topics(settings: Any, args: argparse.Namespace) -> int:
    """Run a short simulated trip and dump the bus traffic."""
    from farfromhome_device.core.bus import dump_recent
    from farfromhome_device.hal import build_hardware

    hardware = build_hardware(
        mode="sim",
        start_lat=settings.sim_start_lat,
        start_lng=settings.sim_start_lng,
        speed_mps=settings.sim_speed_mps,
        gpx_path=settings.sim_gpx or None,
    )
    runtime = DeviceRuntime(settings, hardware=hardware, offline=True).build(with_network=False)
    loop = asyncio.new_event_loop()
    try:
        plan = loop.run_until_complete(runtime.plan_trip("a chill walk for two, about an hour"))
        if plan is None:
            print("planning failed")
            return 2
        runtime.start_trip(plan)
        engine = runtime.engine
        for _ in range(240):
            engine.tick()
            hardware.gps.advance(6.0)
            hardware.imu.rotate(1.5)
    finally:
        loop.close()
    print(dump_recent(runtime.bus, limit=40))
    runtime.close()
    return 0


def mode_demo(settings: Any, args: argparse.Namespace) -> int:
    """The one-command demo: plan, walk, reveal, game, wrap-up. No internet needed."""
    from farfromhome_device.core.bus import dump_recent
    from farfromhome_device.hal import build_hardware

    hardware = build_hardware(
        mode="sim",
        start_lat=settings.sim_start_lat,
        start_lng=settings.sim_start_lng,
        speed_mps=settings.sim_speed_mps,
        gpx_path=settings.sim_gpx or None,
        canned_phrases=[
            "a playful walk for three with coffee and a viewpoint",
            "something calm by the water for two",
        ],
    )
    runtime = DeviceRuntime(settings, hardware=hardware, offline=True).build(with_network=False)

    # `--fast` walks further per tick but keeps the simulated clock consistent with
    # the distance walked, so the engine's "implausible jump" guard never trips and
    # the demo still exercises the real code path.
    if args.fast:
        metres_per_tick, clock_step = 120.0, 86.0
    else:
        metres_per_tick, clock_step = 12.0, 9.0
    print(
        f"\n{__product__} demo - simulated walk, {metres_per_tick:.0f} m and "
        f"{clock_step:.0f} s per tick\n" + "-" * 60
    )
    loop = asyncio.new_event_loop()
    try:
        print("[1/7] planning a trip from a spoken request")
        plan = loop.run_until_complete(
            runtime.plan_trip("a playful walk for three with coffee and a viewpoint, about an hour")
        )
        if plan is None:
            print("      planning failed")
            return 2
        print(f"      title        {plan.title}")
        print(f"      destination  (hidden) {plan.destination.category.value}, {plan.route_km:.2f} km away")
        print(f"      waypoints    {len(plan.waypoints)}")
        print(f"      games        {', '.join(game.type.value for game in plan.games)}")
        print(f"      fallback     {plan.fallback.type} ({plan.fallback.reason or 'LLM used'})")

        print("\n[2/7] starting the engine")
        runtime.start_trip(plan)
        engine = runtime.engine
        print(f"      state        {engine.state.value}")

        print("\n[3/7] walking")
        reveal_count = 0
        ticks = 0
        for _tick in range(1, 900):
            ticks = _tick
            engine.tick()
            hardware.gps.advance_metres(metres_per_tick, time_step_s=clock_step)
            hardware.imu.rotate(2.0)
            if hardware.gps.walk_complete:
                # The simulated walker has reached the end of the route; stand at
                # the destination so the geofence and the reveal fire.
                hardware.gps.pin(plan.destination.lat, plan.destination.lng)
            if engine.state.value == "arrived":
                break
            if engine.revealer.history and len(engine.revealer.history) > reveal_count:
                reveal_count = len(engine.revealer.history)
                latest = engine.revealer.history[-1]
                print(f"      reveal       {latest.key}: {latest.text or latest.label}")
        print(f"      walked       {engine.distance_m / 1000:.2f} km in {ticks} ticks")
        print(f"      state        {engine.state.value}")
        print(f"      track points {engine.stats.points_logged} logged, {engine.stats.points_queued} queued")

        print("\n[4/7] arriving")
        print(f"      destination  {plan.destination.name}")
        print(f"      category     {plan.destination.category.value}")

        print("\n[5/7] taking photos")
        for _ in range(3):
            message = loop.run_until_complete(runtime.take_photo(category="waypoint"))
            print(f"      {message}")

        print("\n[6/7] a game with phones")
        host = runtime.game_host
        ok, message = host.configure(
            "photo_hunt",
            [{"name": "Asha", "color": "#D32F2F"}, {"name": "Dev", "color": "#1565C0"}],
            duration_min=10,
        )
        loop.run_until_complete(host.generate(offline=True))
        created, reason = loop.run_until_complete(host.create_session())
        print(f"      server session: {'created' if created else 'unavailable - ' + reason}")
        loop.run_until_complete(host.enable_offline_mode())
        loop.run_until_complete(host.start())
        print(f"      state        {host.state.value}")
        from farfromhome_device.games.base import PhotoSubmission

        players = list(host.module.scoreboard.scores)
        for index, player_id in enumerate(players):
            task = host.tasks[index % len(host.tasks)]
            submission = PhotoSubmission(
                photo_id=f"demo-{index}",
                player_id=player_id,
                player_name=host.module.scoreboard.scores[player_id].player_name,
                task_id=task["task_id"],
                thumb_url="",
            )
            host.module.on_photo(submission)
        # Peer-vote games need other players' votes before anything scores; the demo
        # casts them so the scoreboard shows real points rather than all zeroes.
        if host.module.scoreboard.approval_mode == "peer_vote":
            for submission in list(host.module.scoreboard.submissions.values()):
                for voter in players:
                    if voter != submission.player_id:
                        host.module.on_vote(submission.photo_id, voter, True)
        loop.run_until_complete(host.end(reason="demo"))
        results = host.results_payload()
        print(f"      winner       {results['winner'] or 'draw'}")
        print(f"      penalty      {results['penalty']}")
        for entry in results["scores"]:
            print(f"        {entry['name']:<10} {entry['score']:>3} pts ({entry['submissions']} photos)")

        print("\n[7/7] wrap-up")
        summary, collage, stamp = runtime.build_wrapup()
        print(f"      summary      {summary}")
        print(f"      collage      {collage or 'not built'}")
        print(f"      stamp        {stamp or 'not built'}")

        print("\n  outbox")
        print(
            f"      {runtime.storage.outbox_count()} item(s) in the outbox, "
            f"{engine.stats.points_queued} GPS points handed to the sync layer"
        )
        print("      (offline demo: no network client is created, so nothing is uploaded")
        print("       and the outbox stays empty on purpose)")
        for row in runtime.storage.outbox_peek(6):
            print(f"        {row['method']:<5} {row['path']:<44} attempt {row['attempts']}")

        if args.topics:
            print("\n  bus messages")
            print(dump_recent(runtime.bus, limit=30))
    finally:
        loop.close()
        runtime.close()

    print("\n" + "-" * 60)
    print("  demo finished")
    print("-" * 60 + "\n")
    return 0


def mode_run(settings: Any, args: argparse.Namespace) -> int:
    """Normal run: build everything, start the background loop, open the UI."""
    from farfromhome_device.hal import build_hardware, resolve_mode

    resolved = resolve_mode(settings.device_mode)
    print(f"{__product__} handheld {__version__}")
    print(f"  device   {settings.device_name}")
    print(f"  hardware {settings.device_mode} -> {resolved}")
    print(f"  server   {settings.server_url}")
    print(f"  data     {settings.data_dir}")
    if args.offline:
        print("  mode     offline (no server calls)")

    hardware = build_hardware(
        mode=settings.device_mode,
        start_lat=settings.sim_start_lat,
        start_lng=settings.sim_start_lng,
        speed_mps=settings.sim_speed_mps,
        gpx_path=settings.sim_gpx or None,
    )
    print(f"  {hardware.describe()}")

    use_network = bool(args.online) and not args.offline
    runtime = DeviceRuntime(settings, hardware=hardware, offline=not use_network).build(
        with_network=use_network
    )
    services = runtime.services()
    services["resolved_mode"] = resolved

    loop_holder: Dict[str, Any] = {}
    if args.simulate and args.fast:
        # Accelerate the simulated walk so a laptop demo finishes quickly.
        original_read = hardware.gps.read

        def fast_read() -> Any:
            position = original_read()
            hardware.gps.advance(settings.sim_speed_mps * 20)
            return position

        hardware.gps.read = fast_read  # type: ignore[method-assign]

    def run_background_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_holder["loop"] = loop
        stop = asyncio.Event()
        loop_holder["stop"] = stop

        async def main() -> None:
            tasks = []
            if runtime.client is not None:
                tasks.append(asyncio.ensure_future(runtime.run_syncd()))
                tasks.append(asyncio.ensure_future(runtime.run_game_polling()))
            if args.duration:
                async def timed() -> None:
                    await asyncio.sleep(args.duration or 0)
                    stop.set()
                tasks.append(asyncio.ensure_future(timed()))
            await stop.wait()
            for task in tasks:
                task.cancel()

        try:
            loop.run_until_complete(main())
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    thread = threading.Thread(target=run_background_loop, name="ffh-background", daemon=True)
    thread.start()
    time.sleep(0.3)

    if args.headless:
        # Build the UI services, then block on the console renderer.
        from farfromhome_device.ui.headless import HeadlessUI

        ui = HeadlessUI(runtime.bus)
        ui.attach()
        ui.banner(f"{__product__} - {settings.device_name} (headless)")
        ui.status_line("hint", "run with --simulate --headless for a scripted walk")
        stop_event = threading.Event()

        def handle_signal(*_: Any) -> None:
            stop_event.set()

        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                try:
                    signal.signal(getattr(signal, name), handle_signal)
                except Exception:
                    pass
        try:
            while not stop_event.is_set():
                time.sleep(0.4)
        except KeyboardInterrupt:
            pass
        finally:
            ui.detach()
            runtime.close()
            if "stop" in loop_holder:
                loop_holder["stop"].set()
            thread.join(timeout=3.0)
        return 0

    try:
        code = start_runtime_window(runtime)
    finally:
        if "stop" in loop_holder:
            loop_holder["stop"].set()
        runtime.close()
        thread.join(timeout=3.0)
    return code


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = settings_from_args(args)
    log_file = Path(args.log_file) if args.log_file else None
    configure_logging(args.verbose, log_file=log_file)

    logger.debug("settings: %s", settings.as_public_dict())

    if args.self_test:
        return mode_self_test(settings)
    if args.pair:
        return mode_pair(settings)
    if args.topics:
        return mode_topics(settings, args)
    if args.demo:
        return mode_demo(settings, args)
    return mode_run(settings, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
