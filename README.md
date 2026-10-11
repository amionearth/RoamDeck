# RoamDeck by Far From Home

> A Raspberry Pi-powered AI travel companion that turns every walk into a quest.

RoamDeck is a handheld device that guides you with a compass arrow to hidden places, gives you photo quests, and connects you with friends without needing a phone app. Your phone opens a browser page. Your laptop acts as the local cloud. The Raspberry Pi is the adventure device.

This repository contains the prototype code and documentation for **RoamDeck**, a project by **Far From Home**.


---

## What is RoamDeck?

RoamDeck is a small handheld Raspberry Pi device with an SPI display, GPS, compass, camera, microphone, speaker, and buttons. It is designed to be carried on walks, trips, and adventures.

Instead of staring at a map on your phone, RoamDeck gives you a single arrow. The arrow points toward a hidden destination. As you get closer, the device gives you clues, haptic feedback, and photo challenges.

Your phone is not the main device. It connects through a web browser to a laptop or cloud server. There is no app to install. The phone handles the social side: feed, invites, leaderboards, and collages.

RoamDeck is for people who want to explore, play, and share memories without being glued to a screen.

---

## The Story

**Far From Home** is about leaving your comfort zone and finding new places. RoamDeck was built around that idea.

The first prototype was a Raspberry Pi 4B with an SPI display, a GPS module, and a compass. The goal was simple: build a device that points you toward an adventure.

Over time, the idea grew into a full social travel app. But the core stayed the same: one arrow, one destination, one story.

**RoamDeck** is the next step. It is a dedicated device, a local server, and a browser-based social layer. No app store. No subscriptions. Just you, the device, and the world.

---

## Features

- **Compass Arrow Navigation** — A single arrow points to your next destination.
- **AI Trip Planner** — The server suggests routes and hidden places using AI.
- **Photo Quests** — Take a photo to complete a challenge. The server verifies it.
- **Bingo** — Complete a grid of travel tasks.
- **Friend Meetup** — See friends on the same trip. When you get within 500 meters, the arrow turns off and walkie-talkie mode begins.
- **Walkie-Talkie** — Push-to-talk voice chat over WebRTC.
- **Social Feed** — Your phone browser shows photos, likes, comments, and trip memories.
- **No Phone App** — The phone connects through a web page or PWA. No install required.
- **Laptop as Local Cloud** — During development, your laptop runs the server. Later, move it to the cloud.
- **Cloud-Ready** — The same server code can run on a VPS, Render, Railway, or Fly.io.
- **Open Source** — Built with Python, Pygame, FastAPI, and open protocols.

---

## How It Works

1. You turn on RoamDeck.
2. The Raspberry Pi boots into a fullscreen Pygame app.
3. The app fetches a target from the laptop server.
4. It reads GPS and compass data.
5. It calculates the distance and bearing to the target.
6. It draws an arrow on the SPI display.
7. As you move, the arrow updates.
8. When you get close, the server sends a clue.
9. You take a photo to complete the quest.
10. Your phone browser shows the photo in the social feed.

The laptop server does the heavy work: AI, database, social feed, and photo verification. The Pi only handles the device UI and sensors.

---

## Architecture

```
+-----------------------------+
|   Nemai's RoamDeck (Pi)     |
|                             |
|  - SPI display              |
|  - GPS module               |
|  - Compass / IMU            |
|  - Camera                   |
|  - Mic + Speaker            |
|  - PTT button               |
|  - Haptic motor             |
+-------------+---------------+
              |
              | Wi-Fi / Phone Hotspot
              |
              v
+-----------------------------+
|   Laptop / Cloud Server     |
|                             |
|  - FastAPI / HTTP server    |
|  - SQLite / PostgreSQL      |
|  - AI trip planner          |
|  - Photo verification       |
|  - Social feed              |
|  - WebSocket / WebRTC       |
+-------------+---------------+
              ^
              |
              | Browser / PWA
              |
+-----------------------------+
|   Phone (No App Install)    |
|                             |
|  - Social feed              |
|  - Invites                  |
|  - Leaderboards             |
|  - Collages                 |
|  - Settings                 |
+-----------------------------+
```

The Pi and the phone both connect to the same server. The Pi handles the journey. The phone handles the memories.

---

## Hardware Requirements

### Current Prototype

- **Raspberry Pi 4B** (or Raspberry Pi Zero 2 W for a smaller final device)
- **SPI Display** - ILI9341, ST7789, or similar
- **GPS Module** - NEO-6M, NEO-M8N, or similar (UART)
- **Compass** - QMC5883L or HMC5883L (I2C)
- **IMU** - MPU6050 (optional, for better heading)
- **Camera** - Raspberry Pi Camera Module
- **Audio** - USB sound card or I2S mic/speaker
- **Push Button** - For PTT and menu control
- **Haptic Motor** - Vibration motor with transistor
- **Power** - 5V 3A power bank or LiPo UPS HAT
- **Storage** - 16GB or 32GB microSD card

### Recommended for Final Device

- Raspberry Pi Zero 2 W
- Custom PCB for sensors and power
- 3D-printed enclosure
- LiPo battery with charging circuit
- Smaller SPI display (240x240 or 320x240)

---

## Software Stack

- **Raspberry Pi OS** - Desktop for development, Lite for final device
- **Python 3** - Main programming language
- **Pygame** - Draws the UI on the SPI display
- **FastAPI** - Planned server framework
- **SQLite / PostgreSQL** - Database for trips, users, and photos
- **OpenAI / Anthropic API** - AI trip planning and photo verification
- **Overpass API** - Finds nearby places and hidden gems
- **WebRTC / LiveKit** - Walkie-talkie voice chat
- **HTML / JavaScript** - Phone browser interface
- **PWA** - Makes the browser page feel like an app without installing

---

## Getting Started

### 1. Run the server on Windows (one click)

The FastAPI server also serves the built website, so you only need to start one
process. After Python and the server's Python dependencies are installed, double-click
[`Start-FarFromHome.exe`](./Start-FarFromHome.exe). It starts the server without
showing a terminal, waits for its health endpoint, and opens the site in your browser
at `http://127.0.0.1:8000`.

Double-click [`Stop-FarFromHome.exe`](./Stop-FarFromHome.exe) when you are finished.
Server output and startup errors are saved in `data/launcher-logs/`. If the launcher
cannot start the server, it shows an error and the log location.

The launcher uses the repository's `.venv` if present, then `phone-app/.venv`, then
Python on `PATH`. The frontend is served from `frontend/dist`; build it with
`npm run build` from `frontend` if that directory is missing or out of date.

For manual startup, open a terminal in the repository and run:

```bash
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

On the same computer, open `http://127.0.0.1:8000`. For a phone on the same Wi-Fi,
open the laptop's Wi-Fi IP on port `8000` (for example `http://192.168.1.50:8000`).
Allow Python through Windows Firewall when prompted if phones need to connect.

### 2. Run the handheld client on a Raspberry Pi

```bash
cd phone-app
python run.py
```

For laptop testing without Pi hardware, see [`phone-app/README.md`](./phone-app/README.md).

---

## Configuration

All settings are at the top of `roamquest.py`.

| Variable | Description | Default |
|----------|-------------|---------|
| `SERVER_HOST` | Server bind address | `0.0.0.0` |
| `SERVER_PORT` | Server port | `8000` |
| `SERVER_URL` | URL the Pi uses to reach the server | `http://192.168.1.50:8000` |
| `WIDTH` | Display width in pixels | `480` |
| `HEIGHT` | Display height in pixels | `320` |
| `FPS` | Frames per second | `20` |
| `FAKE_LAT` | Fake latitude for testing | `12.9716` |
| `FAKE_LON` | Fake longitude for testing | `77.5946` |
| `FAKE_HEADING` | Fake compass heading in degrees | `0.0` |
| `FALLBACK_TARGET` | Target used if server is offline | `{...}` |
| `TARGETS` | Demo targets for the server | list of 4 places |

When you connect real GPS and compass, replace `FAKE_LAT`, `FAKE_LON`, and `FAKE_HEADING` with live readings.

---

## API Reference

### Current Prototype

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Phone-friendly server page |
| `GET` | `/api/target` | Returns the current target as JSON |
| `GET` | `/api/randomize` | Picks a new random target |

Example response from `/api/target`:

```json
{
  "lat": 12.9716,
  "lon": 77.5946,
  "hint": "Find the red door"
}
```

### Planned Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/trip/plan` | AI plans a new trip |
| `POST` | `/api/location` | Pi sends its location |
| `GET` | `/api/quest/next` | Get the next quest |
| `POST` | `/api/photo/upload` | Upload a photo for verification |
| `GET` | `/api/feed` | Social feed |
| `POST` | `/api/invite` | Invite a friend |
| `WS` | `/ws/live` | Live location and events |

---




## License

This project is licensed under the MIT License.

You are free to use, modify, and distribute this software. See the `LICENSE` file for details.

---



> **RoamDeck is not about the destination. It is about the story you bring back.**  
> - RoamDeck by Far From Home
