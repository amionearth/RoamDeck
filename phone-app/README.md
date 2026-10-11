# BlindTrip handheld — Phone-App (Raspberry Pi 5 client)

This is the **handheld device client** for the FarFromHome / BlindTrip platform.
It runs on a Raspberry Pi 5 (4 GB) indoors on a desk *or* in someone's hand
outdoors, and it talks to the FarFromHome server in
`C:\Users\LENOVO\Desktop\project\layover` (FastAPI + SQLite + WebSocket).

> **Naming note.** The three build briefs in
> `C:\Users\LENOVO\Downloads\farfromhome-mobile-app-edits\` call this program the
> "mobile app" *and* the "Pi firmware" in different places; the server code calls
> it the **device** (`/api/device/*`, `Device`, `device_token`). It is one and the
> same thing: the handheld. This repo calls it the **handheld** and the Python
> package is `farfromhome_device`.

---

## What it does

* Takes a trip request (voice or buttons) and produces a **`Plan`** JSON with a
  destination that is **never shown** until arrival.
* Runs a deterministic **live engine**: heading fusion (magnetometer + gyro + GPS
  course), a pointing compass, geofences, reveal rules, off-route warning, track log.
* Hosts **games**: the handheld asks the server for a session, draws the **QR code
  on its own screen**, shows the lobby and live scoreboard, and ends the game.
* Works **offline-first**: every write goes into a local SQLite **outbox** and is
  uploaded by a background sync worker in strict order.
* Wraps up with a photo **collage**, a **stamp**, and a **feed post**.

## Quick start (laptop, no hardware, no server)

```powershell
cd C:\Users\LENOVO\Desktop\project\layover\phone-app
python -m pip install -r requirements.txt      # pydantic, requests, qrcode, PySide6, ...
python run.py --simulate --offline             # full fake trip, console + UI, zero network
python run.py --headless --simulate            # no GUI at all (CI / SSH)
```

Tests:

```powershell
python -m pytest -q
```

## With the real server

```powershell
# terminal 1 (server, must bind 0.0.0.0 so the handheld and phones can reach it)
cd C:\Users\LENOVO\Desktop\project\layover
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8888

# terminal 2 (handheld)
cd C:\Users\LENOVO\Desktop\project\layover\phone-app
copy .env.example .env      # then set SERVER_URL to the laptop's WiFi IP
python run.py
```

The handheld pairs itself: it shows a 6-character code, you type it into the
website under **Devices**, and the handheld receives its bearer token.
See `docs/PAIRING.md`.

## Layout

```
phone-app/
  run.py                  # single entry point (--simulate, --headless, --demo, --self-test)
  farfromhome_device/
    core/                 # config, pydantic models, SQLite, event bus, clock
    hal/                  # GPS/IMU/Camera/Mic/Speaker/Display/Buttons + simulators
    engine/               # fusion, compass, geofence, reveal, off-route, state machine
    agent/                # planner: tools, OSM providers, LLM router, schemas
    ui/                   # PySide6 screens, persona, voice, game widgets
    net/                  # pairing, outbox, REST client, WebSocket, job runner
    games/                # game templates (YAML) + local scoring
    wrapup/               # collage + stamp generation
  tools/                  # gpx_replay, fake_phone (joins a game), plan_fuzzer
  tests/                  # unit + integration tests
  systemd/                # unit files for the four processes
  deploy/                 # install script for the Pi
  docs/                   # ARCHITECTURE.md, DECISIONS.md, ASSUMPTIONS.md, CHANGELOG.md, ...
```

## Documentation

| File | What is in it |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | processes, bus topics, data flow, state machines |
| [docs/DECISIONS.md](docs/DECISIONS.md) | every choice I made and why, incl. the two the brief asked for |
| [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) | answers to the Section 15 questions and open risks |
| [docs/SERVER_CONTRACT.md](docs/SERVER_CONTRACT.md) | the exact endpoints this client calls, verified against the server source, **including the gaps I found** |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | "phones can't open the QR", WiFi isolation, GPS, compass drift |
| [docs/PAIRING.md](docs/PAIRING.md) | pairing + re-pairing on 401 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | milestone-by-milestone log |
## Safety

SOS is the only path that bypasses the outbox: it is sent immediately with a 5 s
timeout **and** queued, and repeated until the server confirms. See
`docs/ARCHITECTURE.md` §Safety. Read `docs/ASSUMPTIONS.md` §Age policy before
shipping any stranger feature — that question is still open with the product owner.
