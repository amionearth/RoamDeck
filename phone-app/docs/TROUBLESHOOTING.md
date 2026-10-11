# Troubleshooting

Ordered by likelihood. The first section is the one that costs people an evening.

---

## 1. "The phone scans the QR and nothing opens"

Brief 02 §A1 lists the five causes in order of likelihood, and the fix for each is
in the product. Work down this list.

### 1.1 The link contains `localhost`

**Symptom:** the phone shows a connection error, or opens its *own* localhost.
The handheld's screen shows an address like `http://localhost:8888/g/QX7K2`.

**Cause:** the server built the join URL from `window.location.origin` or from a
`PUBLIC_BASE_URL` left at localhost.

**Fix, in order:**

1. The handheld already rewrites it. If the lobby shows a **LAN** address and the QR
   still fails, this is not your problem — go to 1.2.
2. If the lobby shows `localhost` anyway, `SERVER_URL` on the handheld is a loopback
   address too. Set it to the server machine's WiFi IP:
   ```
   SERVER_URL=http://192.168.1.50:8888
   ```
3. On the **server**, set `PUBLIC_BASE_URL` in `layover/.env` to the same address, so
   the website's QR cards (Games page, Friends page) are right too.

**Check it in one command:**

```powershell
python run.py --self-test
```

The `network` section prints the address phones should use.

### 1.2 The server only listens on localhost

**Symptom:** the handheld works, the phone cannot load anything.

**Fix:** start the server bound to all interfaces:

```powershell
cd C:\Users\LENOVO\Desktop\project\layover
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8888
```

The startup banner prints `Phones and the device can reach this server at …` and a
terminal QR, plus a warning if it is bound to `127.0.0.1`. **If you see that
warning, stop and fix it.**

### 1.3 The firewall blocks the port

**Symptom:** the phone times out; `ping` might work.

**Two-minute test.** On the phone, with WiFi on and **mobile data off**, open:

```
http://<laptop-wifi-ip>:8888/api/health
```

* JSON appears → the network is fine; your problem is 1.1.
* Nothing loads → it is binding, firewall or isolation (1.2, 1.3, 1.4).

**Fix on Windows:**

1. Settings → Network & Internet → WiFi → the network's properties → set the
   profile to **Private**.
2. In an **administrator** prompt:

```powershell
netsh advfirewall firewall add rule name="FarFromHome 8888" dir=in action=allow protocol=TCP localport=8888 profile=private
```

### 1.4 The WiFi isolates clients

**Symptom:** every device has internet, nothing can talk to anything local. This is
normal on hostel, college, office and event WiFi.

**How to be sure:** the handheld's own diagnostics say so:

**Settings → Run network check**, or

```powershell
python run.py --self-test
```

If TCP connects but `/api/health` never answers, the client reports: *"This WiFi may
isolate clients."*

**Fixes, best first:**

1. **A phone hotspot.** Laptop, handheld and all player phones on it. This is the
   most reliable option at a venue and needs no setup.
2. **A tunnel.** On the server machine:
   ```powershell
   cloudflared tunnel --url http://localhost:8888
   ```
   Set `SERVER_URL=https://<the-tunnel-address>` on the handheld **and**
   `PUBLIC_BASE_URL` to the same on the server. The tunnel also gives phones HTTPS,
   which some phone browsers insist on for camera access.
3. A travel router.

### 1.5 The phone is on mobile data

**Symptom:** the QR fails for one person while everyone else is fine.

Phones switch to mobile data when a WiFi network has no internet.

**Fix:** the lobby screen says it out loud — *"Connect your phone to the WiFi:
{SSID}"* — so read it to them. If the SSID line is missing, the handheld could not
read the network name (`iwgetid`); that is cosmetic, the QR still works.

---

## 2. The handheld will not pair

| What you see | What it means | Fix |
|---|---|---|
| "Cannot reach the server" | wrong `SERVER_URL`, or the server is not running, or 1.3/1.4 | `python run.py --pair --server http://<ip>:8888` and read the error |
| the code is rejected with "Invalid pairing code" | it expired (15 minutes) | press Pair again for a fresh code |
| pairing times out | nobody entered the code on the website | the handheld polls for ~10 minutes; re-run `--pair` |
| every call returns 401 | the token was revoked (unpaired on the website) | the handheld deletes the token automatically and re-pairs; if not, delete `data/device_token` and pair again |

```powershell
python run.py --pair --server http://192.168.1.50:8888
```

---

## 3. Nothing walks

**Symptom:** the demo finishes without arriving, or the arrow never moves.

* The simulated walk follows **the plan's route**, so if the plan changed the track
  is rebuilt at trip start. Nothing to do.
* If you passed `--gpx`, the file's start point must be near the plan's start point.
  A GPX recorded in another city will never reach the destination — by design, since
  the engine refuses impossible jumps.
* `python tools/gpx_replay.py --speed 25` prints `geofences in order: yes|NO` and
  the expected list, which is the fastest way to see what happened.

---

## 4. The compass is wrong, or says "needs calibration"

**Symptom:** the arrow points somewhere that is not the direction of travel.

1. **Wave it in a figure of eight.** The persona says this when the field magnitude
   is disturbed. Then **Settings → Calibrate compass**, or
   `hardware.imu.calibrate()`.
2. **Move away from metal and the battery.** The magnetometer is centimetres from the
   LiPo pack and the Pi. Nothing in software can fix this; the filter only *detects*
   it and falls back to the gyro and GPS course.
3. **Check the field strength.** `EXPECTED_FIELD_UT` defaults to 48 µT; Kerala is
   about 42–45. If the local value is very different, the interference detector will
   be too eager or too lax. Set it from a known-good spot.
4. **Trust the GPS course while walking.** Above 0.7 m/s its weight grows to 1.0, so
   a bad magnetometer cannot dominate a walk — only a standstill.
5. **Check the IMU scale factor** (`docs/ASSUMPTIONS.md` question 2). A wrong
   magnetometer scale weakens interference detection but does not bend the heading.

The diagnostics tell you which source is winning:

**Settings**, or `python -c "print(engine.fusion.status())"` gives
`source`, `confidence`, `rejected_magnetometer_samples`, `gyro_bias_dps`.

---

## 5. The upload queue is not draining

Look at **Settings** (or `syncd.status_dict()`). The queue shows the head row, its
attempt count and the last error.

| Last status | What it means | What to do |
|---|---|---|
| 401 | the token is dead | re-pair; the row is kept and sent afterwards |
| 404 | the trip does not exist on the server yet | it will retry; the trip create is at the head |
| 409 | duplicate or `game_closed` | final, the row is removed |
| 400 / 422 | the request is wrong | dropped on purpose and logged as `outbox.dropped`; check the server log |
| 429 / 5xx | the server is busy or broken | backoff, up to 60 s per row |
| no status | the network is down | backoff; the queue survives a reboot |

**Force a retry now:** **Settings → Send the queue now**.

**The queue is never skipped out of order.** If the head row is waiting, everything
behind it waits. That is deliberate: a trip must be created before its points.

### "It uploaded but says zero sent"

The row only leaves the queue on a 2xx. If the server took the photo but the response
was lost, the row retries — and because the server ignores `photo_id`, you may get a
duplicate (gap G-05 in `docs/SERVER_CONTRACT.md`). One-line server fix, noted there.

---

## 6. The game QR is shown but the lobby stays empty

* **Status:** the lobby only accepts joins while the session is `open`.
* **Started already?** uploads before Start are refused with *"Waiting for the host to
  start"*; a player who joins late is fine, a *photo* before Start is not.
* **No WebSocket?** the handheld falls back to polling the state endpoint every 3 s.
  The status strip says *"live updates off (polling)"*. Joins still show up, a few
  seconds later.
* **Wrong address on the QR?** sections 1.1–1.5.
* **Phone page blank?** it should show *"Cannot reach the game. Check that your phone
  is on the same WiFi as the handheld."* A blank page there is a server bug.

---

## 7. The server cannot be reached at game time

The lobby shows the reason and a **Retry** button, and offers **Play locally
instead**. Reasons come from `net/diagnostics.py`: WiFi down, `SERVER_URL` is
loopback, the server is not running, or client isolation.

Local mode runs the game on the handheld with no QR. Photos it takes still go
through the normal photo queue, and the result is logged locally.

---

## 8. The GUI will not start

| Message | Fix |
|---|---|
| `PySide6 is unavailable; falling back to the headless UI` | that is a warning, not an error; `pip install PySide6` for the GUI |
| `qt.qpa.plugin: could not load the Qt platform plugin "eglfs"` | on a Pi, install `libegl1 libgles2 libgl1 libxkbcommon0`, or set `QT_QPA_PLATFORM=xcb` for a desktop session |
| the window is tiny or clipped | `SCREEN_WIDTH` / `SCREEN_HEIGHT`, or `--fullscreen` |
| it exits immediately under systemd | run it by hand as the service user to see the real error: `sudo -u farfromhome /opt/farfromhome/phone-app/.venv/bin/python run.py --simulate` |

---

## 9. Planning fails or always uses a template

* **Always a template?** No LLM provider was reachable. That is a designed fallback,
  not a crash: the plan says `fallback.used = true` with the reason, and the trip is
  still walkable. Check `--verbose` output for the chain.
* **Keys:** LLM keys belong on the **server** in production. For laptop testing set
  `LLM_DEV_MODE=true` and a provider key in the handheld's `.env`. The dev path is
  refused otherwise, on purpose.
* **Rate-limited?** Free tiers are small. The cache absorbs repeats; a public demo
  should run `--offline` so it uses template content and cannot be rate-limited.
* **Overpass is down?** The planner falls back to the built-in offline place set and
  says so in the log. A plan is still produced.

---

## 10. The test suite fails

```powershell
python -m pytest -q                       # everything (skips the live-server test if nothing is listening)
python -m pytest tests/test_geo.py -q     # just the maths
python -m pytest -q -x                    # stop at the first failure
```

* **`test_server_integration` says "skipped".** Nothing is listening on
  `SERVER_URL`. Start the server, or set `FFH_TEST_SERVER` to where it is.
* **`test_server_integration` says "could not claim the pairing code".** It needs the
  server's SQLite file to claim a code without a browser session. Check
  `DATABASE_URL` in `layover/.env`, or claim the code by hand on the website and
  re-run.
* **A hardware test fails on the Pi.** `DEVICE_MODE=sim` forces simulators; that is
  the right mode for CI and for any machine without the parts.

---

## 11. "It works on the laptop but not on the Pi"

Work through, in order:

1. `python run.py --self-test` **on the Pi**. It prints the resolved device mode and
   which driver each device picked.
2. `timedatectl status` — the clock must be synced. GPS points are de-duplicated by
   timestamp, and a Pi that has been off for a week has a wrong clock.
3. `groups farfromhome` — it needs `dialout gpio video audio i2c render input`.
4. `ls -l /dev/serial0 /dev/i2c-1 /dev/video0` — the devices must exist, and I2C and
   the camera must be enabled in `raspi-config`.
5. `journalctl -u farfromhome-engine -f` — the engine logs what it found.
6. Free space: `df -h /var/lib/farfromhome`. A trip's photos stay until the server
   confirms them and the trip is finished.

---

## 12. Where to look for what

| Question | Command |
|---|---|
| What can this machine do? | `python run.py --self-test` |
| What happened in the last run? | `python run.py --simulate --offline --topics`, or `journalctl -u farfromhome-engine` |
| What is the outbox doing? | **Settings**, or `syncd.status_dict()` |
| Did the QR come out right? | `python -c "from farfromhome_device.net.qr import resolve_join_url as r; print(r(None,'QX7K2','http://192.168.1.50:8888').url)"` |
| Why did planning choose that? | `--verbose`, then look for `plan built:` and the candidate scores |
| Is the queue durable? | `python -m pytest tests/test_outbox.py -q` |
