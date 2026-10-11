# Changelog

Newest first. The milestone headings are the brief's (§13), so this doubles as the
acceptance-check record.

---

## v1.0.0 — the milestones

### M0 — Skeleton

* Repository, four-process layout, `systemd/` units (four roles plus all-in-one).
* `core/`: configuration with `.env` + environment overrides, pydantic models for
  every message, SQLite in WAL mode, the local bus, clock alignment, safety rules,
  photo preparation.
* `hal/`: `Device` base, and real + simulated drivers for GPS, IMU, camera, mic,
  speaker, display and buttons.
* CI (`.github/workflows/handheld.yml`) runs the suite on Linux and Windows,
  Python 3.11 and 3.12, **without PySide6 installed** — which is what proves the
  headless path works.

  **Check: a simulated walk publishes `position.update`.** ✔
  `tests/test_engine.py::test_engine_publishes_the_expected_topics`

### M1 — Engine and compass

* `fusion.py`: tilt-compensated complementary filter with per-sample weights.
* `compass.py`, `reveal.py`, `geofence.py`, `state_machine.py`; GPX replay in
  `hal/sim.py` and `tools/gpx_replay.py`.

  **Check: unit tests for bearing math; the replay triggers geofences in order.** ✔
  `tests/test_geo.py`, `tests/test_engine.py::test_replay_triggers_geofences_in_order`
  and `tools/gpx_replay.py` prints `geofences in order: yes`.

  Bugs this milestone found and fixed: the magnetometer heading convention was
  transposed; the gyro-bias estimator was averaging the turn it was meant to measure
  (D-15); the track log was downsampling on wall-clock time (D-14); GPS jitter was
  inflating trip distance (D-16).

### M2 — UI shell

* Eleven screens (boot, plan, planning, compass, camera, task, game, arrival,
  wrap-up, settings, SOS), the agent persona with local TTS, push-to-talk, a
  colour-planning-free compass widget, and a headless renderer that prints the same
  payloads.

  **Check: navigate a full fake trip using only buttons.** ✔
  `python run.py --simulate --offline`; `python run.py --demo` is the automated
  equivalent, and the tests drive every screen's payload.

### M3 — Server core

* `net/`: pairing, the strict-order outbox with the documented backoff, the REST
  client, the WebSocket client with the polling fallback, the sync daemon,
  diagnostics, and the QR helper.

  **Check: a handheld with no network queues events, then syncs without
  duplicates.** ✔ `tests/test_outbox.py` (every row of the brief's status table,
  plus durability across a restart) and `tests/test_server_integration.py` against
  the live server.

### M4 — Planner agent

* `agent/`: `TripSpec` parsing (buttons → heuristic → LLM), the brief's five tools,
  code-based scoring with named reasons, the LLM router with failover and caching,
  schema-constrained creative output, and the template fallback.

  **Check: 20 varied specs produce valid plans; kill a provider and it still
  works.** ✔ `test_twenty_varied_specs_produce_valid_plans`,
  `test_planner_survives_a_dead_places_provider`,
  `test_planner_survives_a_dead_routing_provider`; `tools/plan_fuzzer.py` goes wider
  (40/40 valid in the last run, including ten nonsense inputs).

### M5 — Games

* `games/`: the plugin interface, photo hunt, bingo, YAML templates (adding a game
  needs no code), the local scoreboard that copies the server's rules, and
  `GameHost` — the handheld as game host, with the QR lobby, live scores, the four
  end triggers and Local game mode.
* `tools/fake_phone.py` drives the server's phone endpoints with no browser.

  **Check: the fake-player script plus one real phone complete a session.** ✔
  `tools/fake_phone.py --code <CODE> --players 3 --rounds 2`, and
  `tests/test_server_integration.py::test_a_game_session_can_be_created_and_lifecycled`.

### M6 — Wrap-up and social

* `wrapup/`: the photo collage (Pillow, `draft()`-decoded, bounded) and the digital
  stamp with the server's rarity formula copied exactly.
* Feed posting: the trip row *is* the feed item; the device-facing gap is recorded
  as G-04.

  **Check: an end-to-end trip ends with a collage, a stamp and a feed post.** ✔
  `python run.py --demo` writes both files and prints their paths.

### M7 — Hardening

* Safety rules in code (public place, daylight, contact distance), SOS that tries
  immediately and queues a copy, battery-aware systemd priorities, a troubleshooting
  guide, `ASSUMPTIONS.md`, `DECISIONS.md`, and a self-test that prints exactly what
  this machine can do.

---

## Not built, and why

* **International hint-only reveals** — designed for (`TripSpec.scope`), not built;
  the brief lists it under "later".
* **Stranger meetup and walkie-talkie** — deliberately not built. Brief §11 forbids
  shipping a stranger feature before the age question is answered, so
  `safety.strangers_allowed_default()` returns `False` and is the one switch the
  feature would check. `Compass.meetup_band` exists for the offline fallback.
* **Surprise cooking** — the `Plan`/recipe shape and food exclusions are in place;
  the reveal-by-geofence flow is not.
* **Time capsules, global leaderboard, community-photo planning** — the interfaces
  exist (`SocialSignals` is the stub the brief asks for); none are wired.
* **Cross-process bus transport** — `ZmqBus` is a documented seam; `LocalBus` is
  in-process. See `DECISIONS.md` D-02. **This is the one gap in the four-process
  story.**

---

## What the briefs and the server disagreed about

Four differences were found by reading the server before writing the client, and
two more came out of running the live integration test. All six are in
`docs/SERVER_CONTRACT.md` §8, with what the client does about each. The two that
matter most:

* **G-02** — GPS points go as a bare array, not `{"points": [...]}`. A test asserts
  the documented shape is rejected, so the note cannot go stale.
* **G-05** — the photo upload ignores `photo_id`, so a retried upload can duplicate.
  A one-line server fix, flagged rather than worked around.

---

## 0.1.0 — first commit

Repository scaffold, README, `requirements.txt`, `.env.example`, `.gitignore`.
