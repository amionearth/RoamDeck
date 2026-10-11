"""``gpx_replay``: run a recorded walk through the real engine, offline.

Brief §14 lists this as a tool, and M1's check is "the replay triggers geofences in
order". This is the manual way to see that happen with a real file, against a real
plan, on a laptop:

    python tools/gpx_replay.py --text "a chill walk for two" --speed 20
    python tools/gpx_replay.py --gpx my-walk.gpx --offline
    python tools/gpx_replay.py --make-gpx data/sim/walk.gpx     # write a demo file

If ``--gpx`` is not given, the walk is generated from the plan, which is the most
useful default: it proves the geofences and reveals fire in order for *this* plan.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from farfromhome_device.app.processes import DeviceRuntime
from farfromhome_device.core.config import load_settings
from farfromhome_device.hal import build_hardware
from farfromhome_device.hal.sim import parse_gpx, write_demo_gpx


def replay(
    text: str,
    gpx: Optional[str],
    offset_m: float,
    speed: float,
    offline: bool,
    quiet: bool,
    start_lat: float,
    start_lng: float,
) -> int:
    settings = load_settings(overrides={
        "device_mode": "sim",
        "sim_start_lat": start_lat,
        "sim_start_lng": start_lng,
        "headless": True,
    })

    hardware = build_hardware(
        mode="sim",
        start_lat=start_lat,
        start_lng=start_lng,
        speed_mps=1.4,
        gpx_path=gpx,
    )
    runtime = DeviceRuntime(settings, hardware=hardware, offline=offline).build(with_network=False)
    loop = asyncio.new_event_loop()
    try:
        plan = loop.run_until_complete(runtime.plan_trip(text))
        if plan is None:
            print("planning failed")
            return 2

        print(f"plan      {plan.title}")
        print(f"route     {plan.route_km:.2f} km, {len(plan.waypoints)} waypoints")
        print(f"source    {'GPX ' + str(gpx) if gpx else 'generated from the plan'}")
        print(f"speed     {speed:.0f} m per step on a walking clock\n")

        runtime.start_trip(plan)
        engine = runtime.engine
        seen_reveals: List[str] = []
        seen_geofences: List[str] = []

        for step in range(1, 4000):
            engine.tick()
            if gpx:
                hardware.gps.advance_metres(speed, time_step_s=6.0)
            else:
                hardware.gps.advance_metres(speed, time_step_s=6.0)
            hardware.imu.rotate(1.5)

            if hardware.gps.walk_complete:
                hardware.gps.pin(plan.destination.lat, plan.destination.lng)

            for reveal in engine.revealer.history:
                if reveal.key not in seen_reveals:
                    seen_reveals.append(reveal.key)
                    if not quiet:
                        print(f"  reveal    {reveal.key:<10} {reveal.text or reveal.label}")
            for key in engine.geofences.status()["entered"]:
                if key not in seen_geofences:
                    seen_geofences.append(key)
                    if not quiet:
                        print(f"  geofence  {key}")

            if engine.state.value == "arrived":
                print(f"\narrived at {plan.destination.name} after {engine.distance_m / 1000:.2f} km "
                      f"({step} steps)")
                break
        else:
            print("\nthe walk finished without arriving; check --speed and the start point")
            return 1

        expected_geofences = [f"wp:{waypoint.idx}" for waypoint in sorted(plan.waypoints, key=lambda w: w.idx)]
        expected_geofences.append("destination")
        ok = seen_geofences == expected_geofences
        print(f"geofences in order: {'yes' if ok else 'NO'}")
        if not ok:
            print(f"  expected {expected_geofences}")
            print(f"  saw      {seen_geofences}")
        print(f"reveals fired: {len(seen_reveals)}")
        print(f"track points:  {engine.stats.points_logged} logged, {engine.stats.points_queued} queued")
        print(f"off route:     {engine.off_route.status()}")
        print(f"heading:       {engine.heading.heading_deg if engine.heading else 0:.1f} deg "
              f"from {engine.heading.source if engine.heading else '?'}")
        return 0 if ok else 1
    finally:
        loop.close()
        runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a walk through the real engine")
    parser.add_argument("--text", default="a chill walk by the water for two, about an hour",
                        help="the trip request to plan")
    parser.add_argument("--gpx", help="replay this GPX file instead of a generated route")
    parser.add_argument("--make-gpx", help="write a demo GPX file here and exit")
    parser.add_argument("--speed", type=float, default=25.0, help="metres per engine step")
    parser.add_argument("--offline", action="store_true", default=True,
                        help="use the offline providers (default)")
    parser.add_argument("--online", action="store_true", help="use the real map providers")
    parser.add_argument("--start-lat", type=float, default=11.2588)
    parser.add_argument("--start-lng", type=float, default=75.7804)
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args()

    if args.make_gpx:
        path = write_demo_gpx(args.make_gpx, args.start_lat, args.start_lng)
        track = parse_gpx(path)
        print(f"wrote {path} ({len(track)} points, {track.distance_m / 1000:.2f} km)")
        return 0

    return replay(
        args.text,
        args.gpx,
        offset_m=0.0,
        speed=args.speed,
        offline=not args.online,
        quiet=args.quiet,
        start_lat=args.start_lat,
        start_lng=args.start_lng,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
