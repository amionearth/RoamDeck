# FarFromHome - Assumptions and Architecture Decisions

## Product & Branding
1. **Configurable Product Name**: The product name defaults to `FarFromHome` and is loaded from the environment variable / config `FFH_NAME` (or setting `FFH-FarFromHome`), exposed via `/api/config` and injected into document titles and UI headers.
2. **Kozhikode Demo Default**: Default fallback coordinates are Kozhikode, Kerala (`11.2588, 75.7804`) for weather, demo trips, and map defaults.

## Security & Auth
1. **Cookie Session Auth**: Browser users authenticate with session token stored in httpOnly cookie `ffh_session`.
2. **Device Auth**: Devices authenticate with `Bearer <device_token>` in `Authorization` header after pairing.
3. **Player Auth**: Ephemeral game players receive a temporary player JWT/token valid for the specific game session until the game closes.
4. **EXIF Privacy**: All uploaded images have EXIF data stripped immediately on ingestion before being saved to `data/`.

## Spoiler Gating Rule
1. **Destination Secrecy**: While `destination_revealed` is False, the server redacts all destination coordinates, destination names, destination categories, unrevealed recipe components, unrevealed waypoints, and photos flagged with category `destination` for ALL viewers including the trip owner.
2. **Arrival Trigger**: Arrival is strictly reported by the Pi (`POST /device/trips/{id}/arrive`), which flips `destination_revealed = True`, computes distance, creates the stamp, and broadcasts a realtime `trip.arrived` WebSocket event.

## Game Engine
1. **Tick & Auto-close**: The server runs an async background task checking `ends_at` every 5 seconds.
2. **Peer Voting**: For `peer_vote`, approval threshold is `max(1, min(2, players - 1))`. For `auto`, submissions are approved automatically.
3. **Bingo**: Win condition `first_n_in_row` checks row/col/diag completion on a 5x5 grid whenever a task/tile is approved.

## Single Port Deployment
1. When running production-style, FastAPI serves the compiled SPA from `frontend/dist` with an SPA fallback route so the entire application runs cleanly on port 8888. In development, Vite runs on 5173 with a proxy to 8888.
