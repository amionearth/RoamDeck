# The server contract, as the handheld actually calls it

**Source of truth: the server's source code**, read at
`C:\Users\LENOVO\Desktop\project\layover\backend\app\`. Where the three briefs and
the running server disagree, the server wins and the difference is recorded here.
`tests/test_server_integration.py` asserts these shapes against the live server, so
this document cannot quietly go stale.

All paths are relative to `SERVER_URL`, and every device path is under `/api`, so
`POST /device/trips` means `POST {SERVER_URL}/api/device/trips`.

---

## 1. Auth

| Purpose | Header |
|---|---|
| Device (handheld) | `Authorization: Bearer {device_token}` |
| Phone game player | `X-Player-Token: {player_token}` |
| Website user | httpOnly cookie `ffh_session` |

---

## 2. Pairing (brief 03 §3 — matches the server exactly)

| Call | Auth | Body | Response |
|---|---|---|---|
| `POST /device/register` | none | `{"name": "Gizmo-01"}` | `{"device_id", "pairing_code", "poll_secret"}` |
| `GET /device/pairing/{device_id}?secret={poll_secret}` | none | — | `{"status": "pending"}` or `{"status": "paired", "token", "owner_user_id"}` |
| `POST /devices/pair` | website | `{"code": "K7P2QX"}` | `{"status": "claimed", "device_name"}` |
| `POST /device/heartbeat` | device | `{"battery_pct": 87}` | `{"status": "ok"}` |
| `GET /devices` | website | — | list of devices |
| `DELETE /devices/{id}` | website | — | `{"status": "unpaired"}` |

Notes:

* Pairing codes are 6 characters from a 32-symbol alphabet (no `I`, `O`, `0`, `1`)
  and expire after **15 minutes** (`PairingCode.expires_at`), not the "about 10
  minutes" the brief mentions.
* There is **no `expired` status**. An unknown or wrong secret returns **401**, so
  the client treats 401 as "re-issue the code and try again".
* The token is returned exactly once, on the first poll after the claim.

---

## 3. Trips

| Call | Body | Response |
|---|---|---|
| `POST /device/trips` | the plan JSON (see below) | `{"status": "created"\|"exists", "trip_id"}` |
| `POST /device/trips/{id}/points` | **a bare JSON array** of `{lat,lng,t,acc}` | `{"status":"ok","points_added":n}` |
| `POST /device/trips/{id}/waypoints/{idx}/reveal` | `{"at": "..."}` (ignored by the server) | `{"status":"revealed","idx":n}` |
| `POST /device/trips/{id}/photos` | multipart | `{"status":"uploaded","photo_id"}` |
| `POST /device/trips/{id}/arrive` | `{"lat","lng","at"}` | `{"status":"arrived","destination_name","stamp_serial"}` |
| `POST /device/trips/{id}/finish` | `{"distance_m","summary"}` | `{"status":"finished"}` |

### 3.1 What `POST /device/trips` actually reads

`create_device_trip` takes the body as a free-form `Dict[str, Any]`, pulls out the
fields below, and stores the **whole body** in `Trip.plan_json`:

```
trip_id, title, description, mood, calendar_at,
destination: {name, lat, lng, category}
waypoints[]: {idx, lat, lng, radius_m, hint}
members[]:   {name, color}
```

Extra keys are preserved, which is why the handheld sends its `games`, `schedule`,
`safety` and `spec` blocks as well. `Plan.to_server_plan()` is the single place that
builds this body.

`destination.category` drives stamp rarity and must be one of
`beach, park, cafe, market, temple, heritage, viewpoint, other`.

### 3.2 Photo upload form fields

The server reads `file`, `taken_at`, `lat`, `lng`, `task_id`, `caption`, `category`,
`member_name`. It does **not** read `photo_id` (it mints its own), so re-uploading
is not de-duplicated by the server — see gap **G-05**. The handheld still sends
`photo_id`, because the outbox needs it and a future server can honour it.

`category` matters: a photo with `category="destination"` is hidden from every
viewer, including the trip owner, until `destination_revealed` flips.

---

## 4. Games

| Call | Caller | Body | Response |
|---|---|---|---|
| `POST /device/games` | handheld | see below | `{"game_id","code","join_url","qr_png_url","status":"open","ends_at":null}` |
| `POST /device/games/{id}/start` | handheld | — | `{"status":"running","started_at","ends_at"}` |
| `POST /device/games/{id}/end` | handheld | — | `{"status":"ended"}` |
| `GET /device/games/{id}/state` | handheld | — | see below |
| `GET /api/games/{id}/qr.png` | anyone | — | PNG |
| `GET /g/{code}/info` | phone | — | game + player list |
| `POST /g/{code}/join` | phone | `{"player_id"}` or `{"display_name"}` | `{"status","player_id","display_name","color","token"}` |
| `POST /g/{code}/start` | phone | — | starts the game (a phone can start it too) |
| `GET /g/{code}/me` | phone | header `X-Player-Token` | my tasks, score, `game_status` |
| `POST /g/{code}/submissions` | phone | multipart `task_id`, `photo`, `caption` | `{"status","photo_id","thumb_url","approval_status"}` |
| `GET /g/{code}/gallery` | phone | player token | photos by others awaiting my vote |
| `POST /g/{code}/vote` | phone | `{"photo_id","vote"}` | `{"status":"ok"}` |
| `GET /g/{code}/scoreboard` | phone | — | ranked players |

### 4.1 `POST /device/games` body

```json
{
  "trip_id": "…",
  "type": "photo_hunt",              // free-form; also bingo, color_hunt, …
  "duration_s": 900,
  "params": {"approval": "peer_vote", "win_condition": "most_points",
             "penalty": "loser buys a treat", "n": 5},
  "players": [{"name": "Asha", "color": "#D32F2F", "team": "A"}],
  "tasks":   [{"player_name": "Asha", "text": "Photograph something red",
               "item_key": "red", "points": 10, "time_limit_s": 300}]
}
```

* Creating a session leaves it **`open`** (the lobby). `ends_at` is `null` until
  `start`, which sets `ends_at = now + params.duration_s`. Note that the server
  reads `duration_s` out of `params` (it is copied there on create).
* Uploads from a phone while the session is `open` are refused with **400**
  `{"error": "game_not_started", "message": "Waiting for the host to start"}`.
* Uploads after it closes are refused with **409** `game_closed`.
* The bingo win check uses each task's **`idx`** (its position in the list sent at
  creation) as the grid cell, so tasks must arrive in row-major order. The handheld
  does that, and `item_key` is `r{row}c{col}` as a second, human-readable record.

### 4.2 `GET /device/games/{id}/state` really returns

```json
{"status": "running", "ends_at": "2026-10-11T11:50:00+00:00",
 "scores": {"<player_id>": 20, "<player_id>": 10}, "submissions_count": 3}
```

It does **not** return the `players` array with `joined`/`submissions`, nor
`pending_votes`, nor `winner`, which brief 03 §7.3 shows. The handheld models both
shapes and normalises them (`GameStateResponse.normalised`), so it works either way.

---

## 5. WebSocket

* Connect to `ws://{host}/ws`. The server **ignores** query parameters, so
  `?token=…` is sent for forward compatibility only; no auth is enforced today.
* Subscribe with `{"subscribe": "game:{game_id}"}`; several are allowed.
* Messages are `{"event": "...", "data": {...}}`.

Events the server actually emits:

| Event | Data | Emitted from |
|---|---|---|
| `game.player_joined` | `{player:{id,display_name,color,team}, joined_count}` | `phone_games.join_game` |
| `game.started` | `{game_id, status, started_at, ends_at}` | start |
| `game.submission` | `{photo_id, player_id, player_name, task_id, thumb_url, status}` | phone upload |
| `game.submission_status` | `{photo_id, status, task_id}` | `evaluate_photo_approval` |
| `game.ended` | `{game_id, status, winner_player_id, ended_at}` | `close_game_session` |
| `trip.position`, `trip.arrived`, `trip.photo_added`, `trip.waypoint_revealed` | trip channel | device endpoints |
| `device.status`, `device.sos` | `user:{id}` channel | heartbeat, SOS |

`game.vote` and `game.score` (brief 03 §8) are **not** emitted. `game.submission_status`
is the vote event, and scores are read by polling the state endpoint after it. The
handheld listens for both the server's name and the brief's name.

Reconnect rules are the brief's: backoff 1, 2, 4, 8 (capped 15), **re-subscribe,
then call the state endpoint** to catch up.

---

## 6. SOS

`POST /device/sos` with `{"lat", "lng", "trip_id"}` (the server does not read `at`;
it timestamps the row itself). Responds `{"status": "sos_logged", "sos_id": n}` and
prints the trusted contact to the server log — messaging is mocked, as brief 03 §9
says.

---

## 7. Network info and health

| Call | Response |
|---|---|
| `GET /api/health` | `{"ok": true, "app": "FarFromHome", "server_time": "…+00:00"}` |
| `GET /api/network-info` | `{"lan_ip", "port", "base_url", "candidates": [...]}` |

`server_time` is ISO with `+00:00`, **not** `Z`; `parse_iso` accepts both. The
handheld aligns its clock to it (`core/clock.py`), which matters because the server
de-duplicates GPS points by timestamp.

---

## 8. Gaps between the briefs and the server — and what happened to each

This section started as a list of disagreements found by reading the server before
writing the client. Six of them have since been fixed **on the server**, so what
follows is the record: what the brief said, what the server did, what the fix is, and
what the handheld does either way. The fixed ones are marked ✔ and the two remaining
policy choices are marked ⚠.

### G-01 ✔ — no client-supplied `game_id`

Brief 03 §7.1 shows the handheld sending `game_id` and the server echoing it.
`CreateDeviceGameBody` had no such field and the server always minted its own UUID, so
an outbox retry of the create would have made a second session.

**Fixed:** `CreateDeviceGameBody.game_id` is accepted; when it names an existing
session the endpoint returns that session (same id, same join code) instead of
creating another. Omitting it still works exactly as before.
**Client:** sends its `local_game_id` as `game_id`, so a retried create is now
genuinely idempotent.

### G-02 ✔ — GPS points were array-only

Brief 03 §6.2 documents `{"points": [...]}`. The signature was
`add_gps_points(id, points: List[GPSPointItem])`, so the wrapped shape returned **422**.

**Fixed:** the parameter is `Union[List[GPSPointItem], PointsBody]`, so both shapes are
accepted. The response now also reports `points_received` alongside `points_added`, and
points are de-duplicated by timestamp, so a resend is a no-op.
**Client:** still sends the bare array, which is now one of two accepted shapes.

### G-03 ✔ — no LLM gateway

Brief §6.4 and 03 §1 list `POST /llm/plan` and `POST /llm/text`. The server had no LLM
router at all, so the handheld could only ever produce template content.

**Fixed:** `POST /api/llm/text` and `POST /api/llm/plan` exist, behind the device
bearer token, with the brief's failover chain (Gemini → Groq → OpenRouter), a
per-device and global rate limit, an in-process cache with usage logging, and
`GET /api/llm/status`. With no keys configured they answer **503** with an explanatory
message, which the handheld's chain treats as "step past this and use a template" —
a supported outcome, not an error.
**Client:** `ServerGatewayProvider` already called `/api/llm/text`; it now gets text
instead of a 404, so LLM-written hints and tasks work without the handheld holding a key.

### G-04 ⚠ — no device-facing social endpoints

The feed is built from `Trip` rows (`GET /api/feed` serialises trips), and posting or
replying needs a browser session cookie (`get_current_user`). There is no
device-authenticated way to create a post.
**Client:** `queue_feed_post` logs and returns 0; the trip *is* the feed item once the
handheld has posted the plan and arrived. The seam is kept for a future device endpoint.
**Not fixed on purpose:** it is a product decision whether a handheld should be able to
write to the social feed on the owner's behalf, and the trip row already gives the feed
what it needs.

### G-05 ✔ — the photo upload was not idempotent

`upload_device_trip_photo` ignored the `photo_id` form field and minted its own, so a
retried upload created a second row. The outbox retries on 5xx and network failures, and
a lost response to a *successful* upload looks exactly like that.

**Fixed:** the upload is now keyed on `photo_id`. Re-sending returns
`{"status": "duplicate", "photo_id": ...}` with the existing row and writes no file. A
`photo_id` that belongs to a different trip returns **409**. Omitting the field still
works, so an older client is unaffected.
**Client:** already sent `photo_id`; the row now de-duplicates end to end.

### G-06 ✔ — a player token outlived its game

`close_game_session` cleared every `token_hash`, but the token is a signed blob that
stays verifiable, so an old link kept working after the game closed.
**Fixed:** `get_verified_player` now checks the game's status and answers **409
`game_closed`** once it is closed, which revokes every player link the moment the
session ends — matching "closing the session revokes all player tokens" (brief 02 §C2).
**Client:** unaffected; it already treats 409 as final.

### G-07 ✔ — `GET /device/games/{id}/state` was too thin

Brief 03 §7.3 asks for a `players` array with `joined`/`submissions`, plus
`pending_votes` and `winner`. The endpoint returned `scores` keyed by player id and
nothing else, so the handheld could not show who had joined from REST (only from the
WebSocket) and had to merge two shapes.

**Fixed:** the response now carries `players[]` (id, name, display_name, colour, team,
`joined`, `score`, `submissions`, `completed_tasks`), `pending_votes`, `winner`,
`started_at`, `duration_s`, `type`, `code`, `penalty` and `params`. The old `scores`
and `submissions_count` keys are still present, so nothing that reads them breaks.
`joined` is derived from `token_hash`, which is set when a phone claims a slot and
cleared when the session closes — the same signal the lobby needs.
**Client:** `GameStateResponse` already models both shapes and normalises them; it now
gets the richer one.

### Still worth knowing

* **`POST /g/{code}/join` creates a player if the name is unknown.** That is how a
  phone types its own name, and it is why a fake client joining with the wrong name
  shows up as an extra player rather than claiming a roster slot. Pass `player_id` from
  `GET /g/{code}/info` to claim the slot the handheld set up. See
  `tools/fake_phone.py`, which does this on request.
* **CORS.** The wrapped mobile app is served from a non-web origin (`capacitor://localhost`
  on iOS, `http://localhost` on Android), so `allow_origin_regex` now covers every
  local scheme plus any private-network address. That is what lets a phone on the same
  WiFi talk to the laptop's server with no configuration.

---

## 9. What the handheld sends that the server ignores

Recorded so nobody mistakes them for contracts:

* `photo_id` on the photo upload (G-05).
* `at` on waypoint reveal, arrive, finish and SOS — the server timestamps rows
  itself. The handheld sends them because brief 03 documents them and because a
  future server should have them.
* `games`, `schedule`, `safety`, `spec`, `photo_spots`, `schema_version` inside the
  trip body — preserved in `plan_json`, never read back.
