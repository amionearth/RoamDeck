# Capacitor mobile app — what was added to FarFromHome

This document describes the Android/iOS app that wraps the frontend and talks to the
existing FastAPI server. For the commands to run and build it, see
[MOBILE_APP_RUN.md](MOBILE_APP_RUN.md).

---

## 1. What it is, in one paragraph

The frontend is now **two shells over one codebase and one server**. Opened in a desktop
browser it is the website that already existed — left rail, bottom nav, paper-and-lagoon
palette. Opened on a phone, or inside the Capacitor shell, it is a dark, three-tab app
(**Compass**, **Chat**, **Hub**) with a live compass, an agent you talk to, and the game
hub. Capacitor wraps the same Vite build into an Android and an iOS app. The Python
server is unchanged in its role: it is the source of truth for accounts, trips, photos,
stamps and scores.

```
┌──────────────────────────── Android / iOS app ────────────────────────────┐
│  Capacitor shell (native WebView, local origin)                           │
│    ├── Compass  ← GPS + magnetometer + the trip engine                    │
│    ├── Chat     ← agent persona + planner                                 │
│    └── Hub      ← QR game host, mini-games, social links                  │
│  lib/native.ts   Geolocation · Motion · Camera · Network · Haptics        │
│  lib/outbox.ts   IndexedDB queue: strict order, backoff, idempotent       │
│  lib/server.ts   finds the laptop's WiFi address, and heals when it moves │
└───────────────────────────────────┬───────────────────────────────────────┘
                                    │ same WiFi: http://192.168.x.x:8888
                                    │ REST + WebSocket
┌───────────────────────────────────▼───────────────────────────────────────┐
│  FastAPI server (unchanged role)                                          │
│   /api/auth  /api/device/*  /api/g/*  /api/llm/*  /ws  /api/feed …        │
│   SQLite · photos on disk · LLM keys in .env                              │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 2. The one problem that had to be solved first

**A Capacitor app is served from a local origin.** Android uses `http://localhost`, iOS
uses `capacitor://localhost`. So:

* `fetch("/api/health")` asks the **phone**, not the laptop, and fails;
* `new WebSocket(\`${location.protocol}//${location.host}/ws\`)` opens a socket to the
  phone itself;
* the laptop's WiFi address changes between venues, so it cannot be baked into a build.

`frontend/src/lib/server.ts` solves it. Every request goes to an absolute address that is:

1. **user-set** in Settings (checked first, and only saved if `/api/health` answers);
2. **auto-discovered** by probing a candidate list **in parallel** and taking the first
   success — the last address that worked, the current origin, the same host on port 8888,
   and a handful of common gateway addresses;
3. **re-discovered** when a request fails at the network level, so moving between WiFi
   networks heals itself;
4. **cached** in `localStorage`/Preferences so a cold start with no WiFi still boots.

`api.ts` and `ws.ts` were rewritten to use it. That single change is what makes "the app
works on someone else's WiFi" true.

---

## 3. What is where

| Concern | File | Notes |
|---|---|---|
| Server address, discovery, WebSocket URL | `src/lib/server.ts` | the resolver described above |
| REST client | `src/lib/api.ts` | absolute URLs, Bearer token, self-healing retry |
| Auth + device pairing | `src/lib/auth.tsx`, `src/lib/authToken.ts` | cookie **and** Bearer, persisted across restarts |
| Native capabilities | `src/lib/native.ts` | geolocation, compass, camera, network, haptics, share, toast |
| Offline queue | `src/lib/outbox.ts` | IndexedDB, the same rules as the Python handheld |
| Trip engine | `src/lib/engine.ts` | reveal rules, geofences, off-route, track |
| Geodesy | `src/lib/geo.ts` | ported from the handheld's `engine/geo.py` |
| Planner | `src/lib/planner.ts` | OSM + code scoring + the server's LLM gateway |
| Design system | `src/styles/mobile.css` | scoped under `.ffh` |
| Shell | `src/mobile/MobileApp.tsx` | HashRouter, tabs, preflight |
| Screens | `src/pages/mobile/` | Compass, Chat, Hub, Settings, SOS |
| Capacitor config | `capacitor.config.ts` | |
| Android | `android/` | generated, `AndroidManifest.xml` hand-tuned |
| iOS | `ios/` | generated, `Info.plist` hand-tuned |

---

## 4. Design

Taken from the three reference screens (`Screen 1 – Compass`, `Screen 2 – AI Chat`,
`Screen 3 – QR & Games Hub`):

* **near-black canvas** (`#0B0B0D`) with exactly **one** accent (yellow `#FFD400`);
* **very large, tightly-tracked headings** in the accent colour;
* **soft cards** with a low-contrast border instead of shadows;
* a **floating pill tab bar** with a glowing active dot;
* **big tap targets** and generous spacing, because this is used outdoors, one-handed,
  often in sunlight.

The palette and every component live in `src/styles/mobile.css`, scoped under `.ffh` so
none of it can reach the website. Accessibility was not an afterthought: the whole
animation set is disabled under `prefers-reduced-motion`, the tab bar is a real
`role="tablist"`, and pinch-zoom is deliberately left enabled.

---

## 5. The blind-trip features

### Compass

`src/lib/engine.ts` is a port of the handheld's engine, and it keeps the two rules that
are the product:

* **the destination is never shown before the reveal** — the only accessor that returns it
  checks that the arrival rule has fired, and the arrow says "your destination" even
  afterwards, so exactly one screen can ever show a name;
* **the compass is direction only** — there is no distance to the destination anywhere in
  the snapshot.

It evaluates `never` / `on_proximity` / `on_arrival` / `at_time` reveals, each firing once,
so a GPS wobble in and out of a radius cannot re-trigger an animation or re-upload.
It reads `deviceorientation` (or the OS fused heading), and it is **honest about it**: the
header says "using the phone's compass" or "no compass on this phone" rather than drawing a
confident arrow from a relative angle. GPS jitter below a noise floor is not counted as
distance walked (the same fix the handheld needed). Off-route warnings wait for a 45-second
dwell, because the brief is clear that the user chooses their own way.

### Chat

One screen does the agent persona and the planning. The agent's lines are fixed copy, a
revealed hint, or the engine's coaching line — all leak-free by construction. There is
deliberately **no free-form "ask the model anything" path**, because that is the one thing
that could give the destination away. Voice uses the browser/OS recogniser (no key, no
model to ship), and typing and suggestion chips always work.

### Planning, and who does what

The brief's rule holds in the app: **code searches, filters, scores, routes and schedules;
the LLM writes text.**

1. `parseSpec` reads the request with keyword matching — deterministic and offline;
2. `findPlaces` queries Overpass, falling back to a built-in place set;
3. anything that is not a safe public place is filtered out **before** scoring;
4. `scoreCandidate` scores in code;
5. `POST /api/llm/plan` on the **server** writes the title, description and hints;
6. the plan is assembled and validated locally.

**The destination is never sent to the LLM.** The request carries the mood, group, mode,
duration, distance and category — nothing that identifies the place. A model that has not
been told cannot leak, which matters because free tiers may train on prompts. If the
gateway answers 503 (no key configured), the planner writes the copy itself and says so in
the transcript instead of pretending.

### Games

The Hub is the **host**: it creates a session with its own `game_id` (so a retry cannot
mint a second join code), shows the QR large, shows the join code in big letters and the
address underneath, and polls the lobby so the player list fills in live. It also takes a
typed join code, so a QR that will not scan (cracked screen, bright sun, dirty lens) never
stops the game.

---

## 6. Offline

`src/lib/outbox.ts` is the browser/native twin of
`phone-app/farfromhome_device/net/outbox.py`, with the same rules, because two clients that
behave differently are worse than one:

* **one queue, strict order** — a trip is always created before its points;
* **backoff** 2, 4, 8, 16, 32, then 60 seconds;
* **status handling** per brief 03 §4 — 2xx and 409 final, 400/422 dropped as unfixable,
  401 stops the queue and asks for re-pairing, 404/408/425/429/5xx retry;
* **photos** are blobs in IndexedDB, not base64 in localStorage, so a 2 MB upload does not
  freeze the UI thread;
* **SOS bypasses the queue** — sent immediately with a 5 second timeout *and* queued, then
  repeated every 15 seconds until confirmed.

The server side matters here too: it now honours `photo_id`, so a retried upload cannot
duplicate. See [SERVER_CONTRACT.md](../../phone-app/docs/SERVER_CONTRACT.md) §8.

---

## 7. Server changes made for this

All additive, all covered by `backend/tests/test_device_contract.py`:

| Change | Why |
|---|---|
| `POST /api/device/games` honours a client `game_id` | an outbox retry must not create a second session |
| `POST /api/device/trips/{id}/points` accepts a bare array **or** `{"points": […]}` | the brief documented the wrapped shape; both work now |
| Points de-duplicated by timestamp | a resend is a no-op; two fixes in the same second both survive |
| `POST /api/device/trips/{id}/photos` is idempotent on `photo_id` | a lost response to a successful upload used to duplicate the row |
| A closed game rejects its player tokens with 409 `game_closed` | closing a session now actually revokes the links |
| `GET /api/device/games/{id}/state` returns `players[]`, `pending_votes`, `winner` | the lobby needed to know who had joined without a WebSocket |
| `POST /api/llm/text`, `POST /api/llm/plan`, `GET /api/llm/status` | brief 01 §5.1: keys live on the server, the client calls the gateway |
| `allow_origin_regex` covering `capacitor://`, `ionic://`, `file://`, localhost and private-network origins | a native shell is not a web origin |
| `POST /api/device/trips/{id}/photos` returns a `thumb_url` | the ticker can show a thumbnail without a second request |

---

## 8. What is not done

* **Scanning a QR inside the app.** The Hub hosts and joins by code. In-app scanning needs
  a barcode plugin with platform caveats (`@capacitor/barcode-scanner` is iOS-focused); the
  camera path that matters for the product — photo tasks — works today. Adding it is
  contained: one button in the Hub that resolves to `/g/{code}`.
* **Background location.** The trip keeps running while the app is in the foreground, which
  is what the use case needs. True background tracking needs a foreground service on
  Android and a background mode on iOS, plus a review conversation for iOS.
* **Push notifications.** The trip reminder and the SOS notification go to the website and
  the log, as brief 03 §9 says messaging is mocked for the demo.
* **A native release build.** The debug APK is what the run guide produces. A signed
  release needs a keystore and, for the Play Store, a privacy policy and a data-safety
  form.
* **The compass has no calibration screen.** The handheld has one; the app relies on the OS.
  If field testing shows drift, port `engine/fusion.py`'s detector and reuse the header
  text that is already there.
