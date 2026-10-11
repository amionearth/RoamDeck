"""FarFromHome Fake Pi 5 Device Simulator
Simulates the device hardware: pairing, GPS telemetry, waypoint reveal,
photo uploads, arrival triggers, and game sessions.
"""

import argparse
import io
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from PIL import Image, ImageDraw

SERVER_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8888")

def http_post(url, data=None, headers=None):
    hdrs = headers or {}
    if data is not None and not isinstance(data, (bytes, bytearray)):
        data = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        print(f"HTTP Error {e.code}: {err_msg}")
        raise

def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def generate_sample_photo_bytes(label: str) -> bytes:
    img = Image.new("RGB", (800, 600), color=(30, 127, 133))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 780, 580], outline=(255, 255, 255), width=3)
    draw.text((50, 50), f"Pi 5 Camera: {label}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()

def pair_device():
    print("[Pi] Registering device...")
    reg = http_post(f"{SERVER_URL}/api/device/register", {"name": "FarFromHome Pi 5 (Simulator)"})
    print(f"[Pi] Pairing Code: >>> {reg['pairing_code']} <<< (Enter this in the Web App /device screen)")
    device_id = reg["device_id"]
    secret = reg["poll_secret"]

    print("[Pi] Polling for user pairing claim...")
    for _ in range(60):
        time.sleep(2)
        res = http_get(f"{SERVER_URL}/api/device/pairing/{device_id}?secret={secret}")
        if res.get("status") == "paired":
            print(f"[Pi] Successfully paired! Device Token received.")
            return res["token"]
    raise TimeoutError("Pairing timed out")

def run_simulated_trip(token: str):
    headers = {"Authorization": f"Bearer {token}"}
    print("[Pi] Uploading Trip Plan...")
    plan = {
        "trip_id": f"sim-trip-{int(time.time())}",
        "title": "Secret Cliff Walk",
        "description": "Breeze off the ocean, finding rocky outcrops.",
        "mood": "adventure",
        "members": [{"name": "Explorer", "color": "#1E7F85"}],
        "destination": {
            "name": "Kappad Historic Beacon",
            "lat": 11.3811,
            "lng": 75.7227,
            "category": "viewpoint",
            "secret": True
        },
        "waypoints": [
            {"idx": 0, "lat": 11.2800, "lng": 75.7650, "hint": "Pass the old banyan tree by the lighthouse"},
            {"idx": 1, "lat": 11.3300, "lng": 75.7400, "hint": "Follow the scent of drying sea salt"}
        ],
        "recipe": {"dish_name": "Kallummakkaya Roast", "reveal_on": "arrival", "steps": ["Marinate mussels in red spices"]}
    }

    t_res = http_post(f"{SERVER_URL}/api/device/trips", plan, headers)
    trip_id = t_res["trip_id"]
    print(f"[Pi] Trip created: {trip_id}. Starting GPS trail simulation...")

    # Waypoints coordinates
    coords = [
        (11.2588, 75.7804),
        (11.2800, 75.7650),
        (11.3300, 75.7400),
        (11.3811, 75.7227),
    ]

    for idx, (lat, lng) in enumerate(coords[:-1]):
        print(f"[Pi] Sending GPS point: ({lat}, {lng})")
        http_post(f"{SERVER_URL}/api/device/trips/{trip_id}/points", [{"lat": lat, "lng": lng, "acc": 4.0}], headers)
        if idx == 1:
            print("[Pi] Waypoint 0 reached! Revealing hint...")
            http_post(f"{SERVER_URL}/api/device/trips/{trip_id}/waypoints/0/reveal", {}, headers)
        time.sleep(1)

    print("[Pi] Approaching destination! Reaching final waypoint...")
    dest_lat, dest_lng = coords[-1]
    http_post(f"{SERVER_URL}/api/device/trips/{trip_id}/points", [{"lat": dest_lat, "lng": dest_lng, "acc": 3.0}], headers)
    
    print("[Pi] Triggering ARRIVAL (revealing destination)...")
    arr_res = http_post(f"{SERVER_URL}/api/device/trips/{trip_id}/arrive", {"lat": dest_lat, "lng": dest_lng}, headers)
    print(f"[Pi] Arrived! Destination Revealed: {arr_res.get('destination_name')}, Stamp Serial: {arr_res.get('stamp_serial')}")

    print("[Pi] Finishing trip...")
    http_post(f"{SERVER_URL}/api/device/trips/{trip_id}/finish", {"distance_m": 8450.0, "summary": "Sensational blind journey across the rocky shores."}, headers)
    print("[Pi] Trip simulation completed successfully!")

def run_simulated_game(token: str, players_count: int = 3):
    headers = {"Authorization": f"Bearer {token}"}
    print(f"[Pi] Launching game session with {players_count} players...")
    game_data = {
        "type": "photo_hunt",
        "params": {"approval": "auto", "win_condition": "most_points", "penalty": "Loser buys fresh tender coconuts"},
        "players": [{"name": f"Player_{i+1}", "color": "#1E7F85" if i % 2 == 0 else "#F2A33A"} for i in range(players_count)],
        "tasks": [
            {"player_name": f"Player_{i+1}", "text": f"Spot something blue or sea-themed #{i+1}", "points": 15}
            for i in range(players_count)
        ],
        "duration_s": 300
    }

    g_res = http_post(f"{SERVER_URL}/api/device/games", game_data, headers)
    print(f"[Pi] Game started! Join Code: {g_res['code']}")
    print(f"[Pi] Join URL: {g_res['join_url']}")
    print(f"[Pi] QR Code available at: {SERVER_URL}{g_res['qr_png_url']}")

def main():
    parser = argparse.ArgumentParser(description="FarFromHome Fake Pi Simulator")
    parser.add_argument("--pair", action="store_true", help="Run pairing handshake")
    parser.add_argument("--trip", action="store_true", help="Simulate blind trip from plan to arrival")
    parser.add_argument("--game", action="store_true", help="Create and simulate game session")
    parser.add_argument("--players", type=int, default=3, help="Player count for game")
    parser.add_argument("--token", type=str, default="demo-device-token", help="Device bearer token")

    args = parser.parse_args()

    if args.pair:
        pair_device()
    elif args.trip:
        run_simulated_trip(args.token)
    elif args.game:
        run_simulated_game(args.token, args.players)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
