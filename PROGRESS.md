# FarFromHome - Implementation Progress

## Status Legend
- `[DONE]` Completed, verified and functional
- `[MOCKED]` Implemented with local mocked logic
- `[IN_PROGRESS]` Currently being developed
- `[TODO]` Planned in current phase
- `[SKIP]` Stretch item deferred or skipped

---

## Phase 0: Scaffold & Architecture
- [x] Project workspace initialization
- [x] Database engine with SQLite WAL mode and SQLAlchemy 2 (`PRAGMA journal_mode=WAL`)
- [x] Session Auth (httpOnly cookie), Device Bearer Auth, Player Token Auth
- [x] App shell with desktop Left RailNav and mobile BottomNav
- [x] Design tokens: fog, paper, lagoon, deep-sea, signal, ink, mist
- [x] Seed script & demo explorer setup

## Phase 1: Trips, Photos, Realtime & Reveal
- [x] Device pairing flow (`POST /device/register`, `POST /devices/pair`, token exchange)
- [x] Device trip upload & plan ingest (`POST /device/trips`)
- [x] GPS breadcrumbs batch ingestion & WebSocket live broadcasting (`trip.position`)
- [x] Photo upload with EXIF stripping & thumbnail creation (Pillow)
- [x] Spoiler gating (`serialize_trip` strict redactions for unrevealed trips across owner, friends, strangers)
- [x] Automated tests for spoiler gating (`test_spoiler_gating.py` passing)
- [x] Destination reveal animated boarding pass (tear effect & thunk stamp landing)

## Phase 2: Game Sessions & Phone Game Page
- [x] Game session creation from device (`POST /device/games`)
- [x] QR code generation as PNG (`/api/games/{id}/qr.png`)
- [x] Mobile game page (`/g/:code`) with zero login requirement and thumb-friendly layout
- [x] Photo task submission with camera capture & peer-voting engine (`max(1, min(2, players-1))`)
- [x] Live scoreboard with WebSocket updates
- [x] Auto-closing scheduler (5s background tick) and 5x5 bingo win verification

## Phase 3: Social, Passport & Profile
- [x] Feed with cursor pagination & trip cards with blob masks
- [x] Trip detail page with interactive Leaflet map & photo timeline
- [x] Likes, comments, reviews (1 to 5 stars)
- [x] Friends management (requests, accepts, friend codes, search)
- [x] Stamps with rarity algorithm (common, rare, epic, legendary)
- [x] Passport grid with CODE128 barcode ticket stubs
- [x] Profile page (identity, career stats, top badges, playercard banner, radar chart, ring gauges)
- [x] Leaderboard (Friends/Global, points/places/photos)

## Phase 4: Demo Tooling & Polish
- [x] Demo control panel (`/demo`) with simulate trip, arrive, fake players, reset, and log telemetry
- [x] `fake_pi.py` standalone simulator (`--pair`, `--trip`, `--game --players 3`)
- [x] Seed data for Kozhikode region with offline generated placeholder images
- [x] Accessibility & responsive styling across mobile and desktop breakpoints
- [x] Single-port production bundle serving (`frontend/dist` served directly on port 8000)

## Phase 5 [STRETCH]
- [x] Trip poster generator (`/trips/:id/poster`) with 1080×1920 export, handwritten Caveat Brush title, amber torn tape, and route outline sticker
- [x] SOS emergency event logging and notification trigger

## Phase 6: Network Resolution, Zero-Localhost QR & Game Session Lifecycle
- [x] **C1: Never put localhost in a join link**: Added `network.py` helper prioritizing `PUBLIC_BASE_URL` (if non-loopback), arrival host (if non-loopback), and UDP socket-detected LAN IP + port. Warnings logged when multiple network adapters are detected.
- [x] **C2: New & updated endpoints**:
  - `GET /api/network-info` returning LAN IP, port, base_url, and IP candidates.
  - `GET /api/health` returning `{"ok": true, "app": "FarFromHome", "server_time": "..."}`.
  - `POST /device/games/{id}/start` starts game, sets `ends_at = now + duration_s`. Uploads before start refused with 400 (`"Waiting for the host to start"`). Session creation leaves session open in lobby.
  - WebSocket event `game.player_joined` (`{player, joined_count}`) and `game.submission` (with `thumb_url`).
  - `POST /device/games/{id}/end` freezes scores, revokes player tokens, emits `game.ended`. Subsequent uploads return 409 `game_closed`.
- [x] **C3: Startup message & Terminal QR**: Server prints clean connection banner with device/phone address, UTF-8 terminal QR code, and bind warning if bound to `127.0.0.1`.
- [x] **C4: Phone page states (`GameJoin.tsx`)**:
  - Open lobby state: shows `"Waiting for the host to start"` and live connected player list.
  - Closed state: shows friendly final score table and `"This game has ended"`.
  - Network failure state: shows `"Cannot reach the game. Check that your phone is on the same WiFi as the device."` with retry button.
- [x] **Website QR cards**: Friends page and Games page use server-provided QR endpoints (`/api/friends/qr.png`, `/api/games/{id}/qr.png`) rather than client-generated localhost URLs.
