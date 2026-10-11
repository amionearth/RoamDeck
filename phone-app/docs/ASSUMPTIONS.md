# Assumptions and the Section 15 questions

Brief 01 §0.1 asks for "assumptions you intend to make"; §15 lists seven questions
it wants answered before anything risky is built. Brief 02 §"Do not ask questions"
says to make reasonable choices, record them here and keep going. **Both are
satisfied the same way: every question is answered with the assumption I built
against, and each one states how to change it.** Nothing here is a blocker; the one
item that must be answered before shipping a feature is flagged as such.

---

## Section 15: the questions, answered

### 1. Screen size and resolution, and which buttons exist

**Assumed: 800×480, DSI or HDMI panel, six buttons plus an encoder.**

The UI is laid out for 800×480 (`SimDisplay(800, 480)`) and reflows upward without
change. Buttons are the canonical set in `hal/base.py:Buttons.KNOWN`:
`up down left right select back menu photo sos ptt`.

* To change the screen: `SCREEN_WIDTH` / `SCREEN_HEIGHT` (or `SimDisplay(width,
  height)`), and `--fullscreen`.
* To remap the GPIO pins: `BUTTON_PINS=up:17,sos:27` in `.env`. The defaults are in
  `hal/real.py:GpioButtons.DEFAULT_PINS` and are **BCM numbers I chose** — they need
  checking against the actual wiring before the first flash.
* **Risk if wrong:** low. A wrong pin means one button does nothing; it cannot damage
  anything. The keyboard works in parallel on a laptop.

### 2. GPS, IMU and camera modules

**Assumed: u-blox NEO-M9N GPS on `/dev/serial0` (NMEA, 9600 baud); ICM-20948 or
MPU-9250 IMU on I2C bus 1 at 0x68; Pi Camera Module 3 via `picamera2`.**

Three drivers exist per device and the code picks the one that answers:
GPS → NMEA serial, else gpsd, else simulator; IMU → `smbus2` I2C, else scripted
simulator; camera → `picamera2`, else OpenCV V4L2, else simulator. `run.py
--self-test` prints which one was chosen.

* **Risk if wrong:** moderate for the IMU. The register map I use
  (`WHO_AM_I` 0x75, `ACCEL_XOUT_H` 0x3B, `GYRO_XOUT_H` 0x43, magnetometer via the
  aux master at 0x03) is shared by the MPU-9250 and ICM-20948 but the *magnetometer
  scale factor* (0.15 µT/LSB) is an assumption. **Check it against the datasheet and
  `imu.calibrate()` on the real part before trusting a heading.** The fusion filter
  tolerates a uniformly wrong scale (it uses field *magnitude deviation*, not
  absolute accuracy), but a wrong scale weakens the interference detection.

### 3. Hosting for the server, and a budget

**Assumed: the laptop, on the same WiFi, for the demo; nothing else for v1.**

`server_url` is read from config on every start, so pointing at a VPS or a tunnel is
a config change, not a code change. The tunnel path is already supported end to end
(`PUBLIC_BASE_URL` on the server, `https://…` in `SERVER_URL` on the handheld, and
`wss://` derived automatically).

* **Risk if wrong:** none. This is the easiest of the seven to change.

### 4. Google Places: acceptable or OSM-only?

**Assumed OSM-only.** `OverpassPlaces` is the default and the only provider the
planner reaches for. `GooglePlaces` exists, is never constructed unless
`use_google_places` is set **and** a key is present, and refuses to start without a
key. `docs/DECISIONS.md` D-10 scores reviews at 0 when they are absent, so OSM-only
does not break ranking — it just has one fewer signal.

* To enable: set `use_google_places` and a key. Billing is then the product owner's.

### 5. Minimum user age and the stranger-meetup policy **[MUST BE ANSWERED BEFORE SHIPPING]**

**Assumed: stranger meetups are OFF, and stay off.**

`safety.strangers_allowed_default()` returns `False` and is the single switch the
meetup feature would check. Meetup mode itself is not implemented beyond the
documented stubs (`Compass.meetup_band`, the `POST /device/meetups` paths in
`docs/SERVER_CONTRACT.md` §10 of brief 03), because brief §11 forbids shipping a
stranger feature before this question is answered.

**This is the one item in this document that blocks a feature rather than shaping
it.** If the answer is "minors may use it", the work needed is: verified accounts,
the safety screen, report/block, the 500 m cutoff with server-side hysteresis, and a
review of the whole meetup surface against the answer. Do not enable it by flipping
the switch alone.

### 6. Languages for voice and UI

**Assumed English only, with the seams in place.**

* All user-facing strings are in code, not a resource bundle, so adding a
  translation means extracting them (a mechanical change).
* `TripSpec.language` exists and is passed to the LLM as context.
* Vosk and Whisper both take a language; `build_transcriber()` passes `en` for
  Whisper and uses the model's own language for Vosk.
* The template content is English.

* **Risk if wrong:** medium. If a second language is needed for the demo, the
  template hints and game items are the work; the engine, compass and games are
  language-neutral.

### 7. Brand name, visual style, agent personality

**Assumed: the product is FarFromHome on the website and in the server, and this
program is its handheld client.** The working title "BlindTrip" from brief 01 is
kept in the README as the brief's own name for it.

* **Visual style:** taken from the website's existing tokens (`fog`, `paper`,
  `lagoon`, `deep_sea`, `signal`, `ink`, `mist`), declared once in
  `ui/screens/theme.py`. The handheld and the site therefore look like one product,
  which is the safest reading of "brand" when the server is already built.
* **Agent personality:** friendly, short, dry, never cute, and it never invents
  facts. All the lines are in `ui/persona.py` and are trivially replaceable.
* **Risk if wrong:** low and cosmetic. Renaming is `DEVICE_NAME` plus a find-and-
  replace on the persona lines.

---

## Additional assumptions I made without being asked

### A. The two things called "the mobile app" are one thing

The three briefs call this program the "mobile app", the "Pi firmware" and "the
handheld" in different places, and brief 03's archive appears to have replaced the
word "Pi" with "mobile app" throughout (which is why it asks for a
`mobile app_token` and a `mobile app_name`). The server calls it a **device**
(`/api/device/*`, `Device`, `device_token`). I treated all of these as one program:
the handheld. `ASSUMPTIONS` says so, the README says so, and the code uses
`farfromhome_device`.

### B. "Phone-free handheld" means no app on the players' phones

Brief 01 §1 says the handheld is phone-free; brief 02 adds that other people join a
game by scanning a QR and using a web page. So: **the players use a browser, the
handheld uses no phone at all.** The phone page is the server's job; the handheld
only requests a session, draws the QR, shows the lobby and the live scores, and ends
the game.

### C. The server already exists and is the source of truth

`layover/backend` is a built system with auth, spoiler gating, stamps, a feed, peer
voting and a background game-expiry tick. I did not redesign any of it. The client
conforms to it, and the four disagreements are documented in
`docs/SERVER_CONTRACT.md` §8 instead of being papered over.

### D. Secrets live on the server, but a dev key is allowed locally

Brief §5.1's production rule is implemented (`ServerGatewayProvider`); the dev mode
that talks to Gemini/Groq/OpenRouter directly is refused unless `LLM_DEV_MODE=true`,
and its fields in `.env.example` are commented as laptop-only.

### E. "Everything must run without real hardware" is a hard requirement

Taken literally: `python run.py --simulate` runs the whole product with no GPS, no
IMU, no camera, no mic, no speaker, no buttons, no server and no LLM key. The test
suite (339 tests) additionally runs with no Qt installed, which is why `--headless`
exists.

### F. Free-tier model names and limits will be wrong by the time you read this

Brief §5.1 says to verify them at build time and make them config. I did both: the
defaults (`gemini-2.5-flash`, `llama-3.3-70b-versatile`,
`meta-llama/llama-3.3-70b-instruct:free`) were current when this was written, every
one is overridable from the environment, and each provider has an `rpm_limit` the
router honours. **Re-check them before a demo.** A wrong model name costs a failover
hop, not a failure, because the chain and the template fallback both survive it.

### G. Timestamps

The server emits ISO with `+00:00`; brief 03 §4 asks the client to send `Z`. Both are
accepted when parsing; the client always emits `Z`. Where the server ignores a
timestamp it still receives one, so a stricter server later needs no client change.

### H. Numbers I picked, and where to change them

| Number | Value | Where |
|---|---|---|
| off-route tolerance / dwell | 120 m / 45 s | `Settings` (`OFF_ROUTE_TOLERANCE_M`, `OFF_ROUTE_SECONDS`) |
| GPS point interval / batch | 5 s / 15 s, 50 per batch | `Settings` |
| GPS accuracy ceiling | 50 m | `GPS_MAX_ACCURACY_M` |
| photo target / max edge | 2 MB / 1920 px, q85 | `Settings` |
| emergency-contact radius | 15 km | `core/safety.py:DEFAULT_CONTACT_RADIUS_KM` |
| sunset margin | 45 min | `DEFAULT_SUNSET_MARGIN_MINUTES` |
| engine rate | 8 Hz | `ENGINE_HZ` |
| expected magnetic field | 48 µT | `fusion.DEFAULT_FIELD_STRENGTH_UT` (Kerala is ~42–45; recalibrate on site) |
| gyro bias "still" threshold | 5 °/s | `fusion.FusionConfig.gyro_bias_max_rate_dps` |
| heartbeat | 30 s | `HEARTBEAT_INTERVAL_S` |
| SOS retry | 15 s | `net/syncd.py:SOS_RETRY_SECONDS` |

### I. The destination stays hidden from the handheld's own UI

Brief §6.1 of file 03 says the server hides the destination from website viewers,
and that "the mobile app already knows it, so the mobile app screen must keep it
hidden itself". That is enforced structurally: no screen receives a plan, the
compass label is always "your destination", and only the arrival screen renders
`plan.destination.name`.

---

## Known risks, in the order I would worry about them

1. **The IMU magnetometer scale factor** (question 2). Verify against the datasheet
   before trusting a heading outdoors. The filter degrades gracefully, but
   interference detection depends on the magnitude being approximately right.
2. **The age policy** (question 5) blocks the stranger feature entirely. Do not ship
   meetup mode before it is answered.
3. **The four-process bus does not cross process boundaries yet.** The systemd units
   are written for the split, and `LocalBus` is in-process. `docs/DECISIONS.md` D-02
   says so plainly. The single-process `all` role is the deployment that works today.
4. **Free-tier LLM limits.** A public demo can exhaust a free tier in an afternoon.
   The cache helps; a demo that must not fail should run with `--offline` so it uses
   template content.
5. **Dependency drift.** `qrcode`, `PySide6`, `websockets` and `Pillow` are pinned by
   minimum version only. On a Pi, pin them exactly once the hardware is final.
6. **The dry-run integration test skips silently.** `tests/test_server_integration.py`
   reports "skipped" when no server is listening, which is right for CI and easy to
   misread locally. Run it deliberately with the server up before a demo.
