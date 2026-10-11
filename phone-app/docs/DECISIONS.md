# Decisions

Every choice the brief asked to be recorded, plus the ones I made without being
asked because they were risky enough to deserve a written reason.

Format: **what**, **why**, and **what it costs**. Where the brief said "justify it
in `DECISIONS.md`", the decision is marked **[brief]**.

---

## D-01 UI toolkit: Qt / PySide6 **[brief]**

*The brief: "pick one lightweight option suited to the screen (pygame or
Qt/PySide6) and justify it."*

**Decision: Qt via PySide6.**

Why:

* **One toolkit, two machines.** The same screens run on the laptop (Windows, for
  development) and on the Pi's panel. pygame's text rendering and layout would have
  to be hand-rolled; Qt gives me real widgets, real font shaping, and an actual
  stylesheet.
* **The screen is not a game.** Six of the eleven screens are forms, lists and
  dialogs (plan, task, lobby, leaderboard, settings, SOS). Those are widget-shaped,
  not sprite-shaped.
* **The compass needs real vector painting.** `QPainter` with antialiasing and a
  radial gradient is a few lines; pygame would need a font and a blit per frame.
* **`QStackedWidget` is the screen manager** I would otherwise write.
* **Input.** Buttons, encoder and a future touch screen all map onto Qt's event
  system without a translation layer.
* **Camera and QR.** `QPixmap.loadFromData` renders a JPEG frame or a QR PNG
  directly, with no image surface juggling.

What it costs: PySide6 is a large wheel (~120 MB) and Qt wants a real display
server or `eglfs`. The mitigations are that `--headless` runs the whole product
without Qt (and CI proves it), and the Pi units set `QT_QPA_PLATFORM=eglfs`.

---

## D-02 Message bus: in-process asyncio pub/sub, with ZeroMQ behind the same API **[brief]**

*The brief: "ZeroMQ pub/sub plus request/reply, or a local MQTT broker. Choose one
and justify it."*

**Decision: an in-process `LocalBus` as the default, with `ZmqBus` as a documented
seam behind the identical API.**

Why:

* The brief's own priority list puts *"Everything must run without real hardware …
so I can test on a laptop"* (§0.3) and *"runnable by a new developer in under 30
minutes"* (§16) above the choice of transport. A ZeroMQ or MQTT broker on Windows
means a daemon to install, a port to unblock in the firewall, and a new failure
mode before the first test runs.
* The bus's *real* job here is decoupling, and `LocalBus` provides exactly that:
  topics, wildcard subscriptions, request/reply, JSON envelopes, and an
  `AsyncBridge` so Qt and asyncio can share it.
* `ZmqBus` exists so the four-process deployment has a place to grow into without
  touching the engine, agent or UI. It raises `NotImplementedError` with a pointer
  to this decision rather than pretending to work.

What it costs: in the four-process deployment, bus messages do not cross process
boundaries yet, so `engine` and `ui` must currently run in the same process as the
bus they share (or be wired to ZeroMQ). **This is the one honest gap in the
four-process story, and it is recorded here rather than hidden.** The systemd units
are written for the split; the transport work is the remaining step.

---

## D-03 Also ship a single-process mode **[not asked]**

**Decision: `DeviceRuntime` plus `python -m farfromhome_device.app.main all`.**

The brief asks for four processes. Shipping only that would make the laptop demo
four terminals and a debugging session. The same components start in one process
via `DeviceRuntime`, and both paths are exercised (the demo uses `all`; the units
use the split). Written down because "we also do it the other way" is the kind of
thing that surprises whoever reads the code next.

---

## D-04 Target the server's **real** endpoints, and document every difference **[not asked]**

**Decision: the client calls what the running server serves, not what the briefs
describe, and every disagreement goes in `docs/SERVER_CONTRACT.md`.**

The three briefs and the server code disagree in four places. The server wins,
because it is what will answer on the day:

| # | Brief says | Server does | Our behaviour |
|---|---|---|---|
| G-01 | client-made `game_id` in `POST /device/games` | server mints its own UUID | we omit `game_id` from the wire body and track a `local_game_id` for the outbox |
| G-02 | points as `{"points": [...]}` | a bare JSON **array** | we send the array; a test asserts the documented shape is rejected |
| G-03 | `POST /llm/plan` and `/llm/text` gateway | not implemented | the `server` provider detects a 404 and steps aside; templates take over |
| G-04 | device can create a feed post | no device-facing endpoint | the trip row *is* the feed item; `queue_feed_post` logs and returns 0 |

Also recorded there: `.../state` returns `scores` keyed by player id and no
`players`/`pending_votes`/`winner`, so `GameStateResponse.normalised()` accepts both
shapes rather than betting on one.

---

## D-05 HAL: one `Device` base class, seven concrete devices **[brief]**

*The brief: "interfaces for GPS, IMU, Camera, Mic, Speaker, Display, Buttons. Each
has a real driver and a simulator."*

**Decision: a shared `Device` base (lifecycle, error capture, counters) with
per-device verb sets, and simulators that are real implementations rather than
stubs.**

Why the simulators are real: `SimGps` replays a GPX file or a generated route with
noise and warm-up fixes; `SimImu` produces magnetometer/gyro/accelerometer samples
consistent with a truth heading and can be told to fail; `SimCamera` writes actual
JPEGs with a dominant colour, so the colour-hunt path is exercised offline. A stub
that returns `None` would prove nothing.

---

## D-06 Spherical earth, local flat projection for segment maths

**Decision: haversine on a 6371.0088 km sphere; equirectangular projection for
point-to-segment distance.**

Why: trip legs are a few kilometres, where the spherical error is under 0.5 % —
an order of magnitude below GPS noise. The point-to-segment case is computed in a
local metric frame centred on the query point, which keeps the code short and
avoids the 3-D vector cross-product that is easy to get subtly wrong. The tests
pin the accuracy (`test_geo.py`).

---

## D-07 Rough solar day-length instead of a solar-position library

**Decision: `daylight_window()` uses the standard declination/hour-angle
approximation.**

Why: the only consumer is the "will you arrive after dark?" warning. A proper
library is hundreds of kilobytes and an extra dependency for a warning that is
already approximate. The function clamps at polar latitudes and is tested there.

---

## D-08 Fusion weights computed per sample, not constants

**Decision: the complementary filter computes its magnetometer and GPS-course
weights from measurements every sample.**

Why: this handheld's magnetometer sits centimetres from a LiPo pack, a Pi 5 and a
display. A fixed weight either trusts it too much (wrong heading near a battery) or
too little (drift). Computing the weight from the field-magnitude deviation, the
rotation rate and the GPS speed makes the filter degrade honestly, and each rule is
a unit test.

---

## D-09 `at_time` reveals are spaced evenly across the plan

**Decision: a waypoint marked `at_time` fires at
`max(default, budget·0.8) · (idx+1) / count`.**

Why: it is the simplest rule that produces a sensible drip of hints and cannot
fail. The plan's schedule carries explicit hints for the things it wants said at a
particular moment; the per-waypoint case just needs "spread them out". Recorded
because a reader will wonder where the number came from.

---

## D-10 Scoring weights

**Decision: the weights live in `agent/tools.py` as one function,
`score_candidate()`, with every term named in `ScoredCandidate.reasons`.**

| Term | Range | What it rewards |
|---|---|---|
| interest | 0–3 | the place's category matches what was asked for |
| range | 0–2 | inside `range_km`; too close is dull, too far is punished hard |
| time | 0–2 | estimated duration against `max_minutes` |
| rarity | 0–1 | heritage/viewpoint/beach beat a cafe (matches the server's stamp rarity) |
| reviews | 0–1 | only when a provider actually has ratings |
| social | 0–1 | always 0 until the stub is enabled |
| safety penalty | ≤0 | −5 for a hard failure, −0.6 per advisory warning, −0.2 for merely having no emergency contact |

Why one function: "code does the scoring" is the brief's rule (§1.2), and one pure
function with named reasons is the only version of that which can be reviewed and
explained to a user.

---

## D-11 The compass never says the destination's name, even after arrival

**Decision: `Compass.update(destination_revealed=True)` still labels the target
"your destination".**

The brief only says the compass must not show *distance*. I went further: the name
belongs to the arrival screen, so exactly one screen ever reveals the place. This
keeps a single rule true at every moment ("the compass is direction only") instead
of a rule with an exception. `test_engine.py` asserts it.

---

## D-12 Photo policy: strip EXIF here **and** let the server strip it again

**Decision: `core/photos.prepare_photo` strips metadata locally; the server keeps
its own strip on ingest.**

Why both: the server's strip is the guarantee for what it stores; the local strip
means a photo sitting in the outbox on a lost device has no location in it either.
The cost is one extra encode, which is milliseconds.

---

## D-13 The outbox body column stores a dict **or** a list

**Decision: `outbox.body` is JSON text that may encode either.**

Why: the server's GPS points endpoint takes a bare array while everything else takes
an object. Forcing one shape would mean a special case in the sender; storing the
body as JSON and sending it verbatim keeps `send_outbox_row` honest.

---

## D-14 The engine downsamples by *fix* time, not wall-clock time

**Decision: track-log and point-batch intervals are measured from `Position.t`.**

Why: a GPX replay and an accelerated demo must produce the same track as a real
walk. Measured on `time.monotonic()`, a replay logs every fix and a real walk logs
one per five seconds — the same code behaving two ways. Found by a failing test
(`test_engine_track_log_uses_the_fix_clock`), fixed, and kept as a regression test.

---

## D-15 Gyro bias is only estimated while still

**Decision: samples above 5 °/s are not folded into the bias estimate; once the
user moves, the estimate freezes.**

Why: the first version averaged the first 40 samples unconditionally. During a turn
that average *is* the turn, so the bias cancelled the rotation and the heading sat
still — the filter working perfectly on the wrong assumption. A unit test turning at
90 °/s for a second caught it. This is the single most valuable bug the tests found.

---

## D-16 Distance accumulation ignores GPS jitter below a floor

**Decision: a step smaller than `max(2 m, 0.5·accuracy)` is not added to the trip
distance.**

Why: summing every fix means standing still on a 4 m-accuracy fix "walks" a
kilometre an hour. Real movement is far above the floor, so the trip total stays
honest while the raw track is untouched.

---

## D-17 Templates live in YAML; a new game needs no code **[brief]**

*Brief 02 §B2: "Templates are YAML files; adding a game must not require new code."*

**Decision: `games/templates/games.yaml` holds every game; `template_loader.py`
validates it strictly and fails loudly with the offending entry.**

Items may be written as a mapping or a bare string, because forcing an `item_key`
onto 25 bingo tiles is exactly the ceremony that stops people from adding games.
Bingo needs 25 tiles for a 5x5 grid, so the loader warns when a bingo template has
fewer.

---

## D-18 The QR is drawn locally, never fetched **[brief]**

*Brief 02 §B3: "Draw the QR locally with the `qrcode` package. Never depend on an
online QR service."*

Implemented as asked, plus the localhost rewrite in `net/qr.py`, which is the rule
that fixes the most common real-world failure. `qr_png_url` is still accepted as a
fallback and is never required.

---

## D-19 Offline game results are logged, not queued as a fake session

**Decision: `GameHost.queue_results_if_offline()` writes an `events` row.**

Why: an offline game has no server session to upload to. Queueing a request against
a nonexistent game id would retry until the outbox gave up, and would be a lie in
the queue. The photos taken during an offline game still go through the normal photo
queue, so nothing is lost that the server could have accepted.

---

## D-20 `LocalScoreboard` copies the server's rules exactly

**Decision: the approval threshold is `max(1, min(2, players-1))`, a task scores
once, and bingo wins on any 5-cell line — copied from
`backend/app/services/games.py`.**

Why: a game that produces a different winner offline than online is worse than no
offline game. The tests assert against the server's formula, and the docstring says
where it came from, so a future change on the server is easy to mirror.

---

## D-21 Test-time decisions

* **No `pytest-asyncio`.** A three-line `run_async` helper and a per-test loop
  fixture do the job; another plugin is another install step for a new developer.
* **No network in the suite except `test_server_integration.py`,** which skips
  itself when nothing is listening.
* **The engine is ticked synchronously in tests** (`engine.tick()`), so nothing
  depends on real time and the suite stays under 20 seconds.
