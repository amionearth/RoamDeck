# FarFromHome (FFH) — Blind-Trip Travel Device Server & Web Platform

**FarFromHome** is the companion server and social platform for a handheld Raspberry Pi 5 blind-trip exploration device. 

The device guides travelers using only a pointing magnetic compass — **the destination remains strictly secret until arrival**. Along the way, the device orchestrates photo tasks, bingo challenges, and group games. The companion web application powers explorer profiles, live boarding passes, photo timelines, passports, leaderboards, and instant phone game participation via QR code.

---

## 1. Ground Rules & Spoiler Gating

> [!IMPORTANT]
> **The destination remains secret until the device reports arrival.**
> The backend enforces this strictly via `serialize_trip(trip, viewer)`: while `destination_revealed` is `false`, destination coordinates, place names, categories, unrevealed recipe hints, and destination-category photos are redacted from all API responses for **every viewer, including the trip owner**.

---

## 2. Tech Stack

- **Backend**: Python 3.11+, FastAPI, Uvicorn, SQLite in WAL mode via SQLAlchemy 2
- **Realtime**: WebSocket at `/ws` for live GPS breadcrumbs, arrival triggers, and game score updates
- **Images**: Pillow for thumbnail generation and privacy-preserving EXIF stripping
- **QR Codes**: Native `qrcode` generator served as PNG (`/api/games/{id}/qr.png`)
- **Frontend**: Vite, React 19, TypeScript, Tailwind CSS, Leaflet OpenStreetMap, Recharts (5-axis radar), `jsbarcode`, `html-to-image`
- **Weather**: Open-Meteo cached proxy (no external API key required)

---

## 3. Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+ and npm

### One-Command Setup

1. **Install backend dependencies:**
   ```bash
   pip install -r requirements.txt
   # (or: pip install fastapi uvicorn sqlalchemy pillow qrcode pytest websockets httpx pydantic python-multipart)
   ```

2. **Install frontend dependencies & build:**
   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   ```

3. **Seed demo data:**
   ```bash
   python tools/seed.py
   ```

4. **Start the server:**
   ```bash
   python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8888
   ```
   Open your browser at **`http://localhost:8888`**. The entire application (both API and SPA frontend) runs seamlessly on a single port (**8888**)!

---

## 4. Development Mode (Dual Ports)

For hot-module reloading during development:
- **Terminal 1 (Backend)**:
  ```bash
  python -m uvicorn backend.app.main:app --reload --port 8888
  ```
- **Terminal 2 (Frontend)**:
  ```bash
  cd frontend
  npm run dev
  ```
  Vite runs on `http://localhost:5173` and proxies `/api` and `/ws` to port 8888.

---

## 5. Stamp Rarity Algorithm

Stamps are awarded automatically when the Pi reports arrival at `POST /device/trips/{id}/arrive`.
Rarity score is computed as:
$$\text{Score} = \text{Category Weight} + \min(\text{Distance}_{\text{km}}, 10) \times 0.3 + \text{Golden Hour Bonus}$$

- **Category Weights**:
  - Beach: 1.0, Park: 1.0, Cafe: 1.0
  - Market: 2.0, Other: 2.0
  - Temple / Heritage: 3.0, Viewpoint: 3.0
- **Golden Hour Bonus**: +1.0 if arrival occurs within 1 hour of sunrise (~06:15) or sunset (~18:30).
- **Rarity Tiers**:
  - Score < 2.5: **Common** (`#7C97A0`)
  - Score < 4.0: **Rare** (`#1F4B8F`)
  - Score < 5.5: **Epic** (`#7B4FC2`)
  - Score ≥ 5.5: **Legendary** (`#F2A33A`)

---

## 6. Demo Tooling & Simulator

### 1. Safety Control Panel (`/demo`)
Navigate to `http://localhost:8888/demo` (enabled when `DEMO_MODE=true`):
- **Seed Demo Data**: Instantly populates 6 Kozhikode explorers, 8 finished journeys, placeholder photos, and stamps.
- **Start Fake Trip**: Creates an active blind trip with hidden destination `"???"` and live GPS coordinates.
- **Trigger Arrival**: Simulates the Pi arriving at the destination, triggering the perforated boarding pass tear animation and stamp award.
- **Start Fake Game**: Launches a photo hunt game with 3 simulated players.
- **Reset Database**: Cleans SQLite tables and media directory.

### 2. Standalone Fake Pi Simulator (`tools/fake_pi.py`)
```bash
# Simulate pairing handshake with web app
python tools/fake_pi.py --pair

# Replay a full blind trip: post plan, send GPS breadcrumbs, reveal waypoint, arrive, finish
python tools/fake_pi.py --trip

# Start a game session with 3 players
python tools/fake_pi.py --game --players 3
```

---

## 7. Event WiFi & Network Exposing

> [!TIP]
> Hackathon and venue WiFis frequently enable **client isolation**, which prevents phones from reaching laptop IP addresses directly.

**Recommended Fallbacks:**
1. **Phone Mobile Hotspot**: Connect your laptop and testing phones to the same mobile hotspot. Set `PUBLIC_BASE_URL="http://<your-hotspot-ip>:8888"` in `.env`.
2. **Cloudflare Tunnel (Zero-config HTTPS)**:
   ```bash
   cloudflared tunnel --url http://localhost:8888
   ```
   Set `PUBLIC_BASE_URL` in `.env` to the resulting `https://...trycloudflare.com` address. This enables cameras and full HTTPS support on mobile phones without network restrictions.

---

## 8. Automated Tests

Run backend tests for spoiler gating, game lifecycle, pairing, and scoring:
```bash
python -m pytest backend/tests
```
All tests run with zero external cloud dependencies.
