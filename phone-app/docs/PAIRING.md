# Pairing the handheld

The handheld never asks for a username or password. You claim it from the website
with a short code, once. This is the flow in brief 03 §3, implemented in
`farfromhome_device/net/pairing.py` and verified against the live server by
`tests/test_server_integration.py`.

---

## The happy path

### On the handheld

```powershell
python run.py --pair --server http://192.168.1.50:8888
```

or press **Pair this handheld** on the boot screen. Either way you get:

```
==============================================
  Enter this code on the website, under Devices:  K7P2QX
==============================================
Waiting for the claim... (Ctrl-C to stop)

  [  1] pending
  [  2] pending
```

### On the website

1. Open the server at `http://192.168.1.50:8888` (or `http://localhost:8888` if you
   are on the same machine).
2. Go to **Devices**.
3. Type the code, press Claim.

### Back on the handheld

```
  [  3] paired

Paired. Token saved to …\phone-app\data\device_token
```

The handheld writes the token with permissions `600`, uses
`Authorization: Bearer {token}` from then on, and heartbeats every 30 seconds while
online.

---

## What happens under the hood

| Step | Call | Notes |
|---|---|---|
| 1 | `POST /api/device/register` `{"name": "Gizmo-01"}` | **no** auth header; returns `device_id`, `pairing_code`, `poll_secret` |
| 2 | the code is shown on screen | the `poll_secret` stays on the handheld and is never displayed |
| 3 | `GET /api/device/pairing/{device_id}?secret={poll_secret}` every 3 s | `pending` → keep going; `paired` → save the token; `401` → re-register |
| 4 | the website calls `POST /api/devices/pair` `{"code": "K7P2QX"}` | needs a browser session; a handheld has no business logging in as a person |

The code is **6 characters** from a 32-symbol alphabet (no `I`, `O`, `0` or `1`, so
it cannot be misread out loud) and expires after **15 minutes**. If it expires,
register again — the handheld does this automatically when it sees a 401.

The token is returned **once**, on the first poll after the claim. There is no way to
recover it; if it is lost, pair again.

---

## Re-pairing

Any `401` from any call means the token is dead. The client reacts immediately:

1. it deletes `data/device_token`,
2. it raises `NotPaired` so the caller stops rather than looping,
3. it publishes `connection.status {"paired": false}` for the UI,
4. the next boot pairs again.

**Important:** rows already in the outbox are **kept**, not dropped. After
re-pairing, the queue drains normally — a photo taken before the token was revoked
still reaches the server. That is asserted in `tests/test_outbox.py`.

To pair again on purpose (for example after unpairing on the website):

```powershell
python run.py --pair --server http://192.168.1.50:8888
```

You are asked to confirm if a token already exists. To force it:

```powershell
Remove-Item phone-app\data\device_token
```

---

## Where things live

| What | Where | Notes |
|---|---|---|
| device token | `DATA_DIR/device_token` | mode `600`; **never logged** — `net/websocket.py:base_url_safe` strips it from URLs |
| health/heartbeat state | `DATA_DIR/outbox.db` | `meta` table |
| outbox | `DATA_DIR/outbox.db` | survives a reboot and a re-pair |
| the name shown on the website | `DEVICE_NAME` in `.env` | change it and re-pair to rename |

`DATA_DIR` is `./data` by default, `/var/lib/farfromhome` on the Pi.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `cannot reach the server at …` | wrong `SERVER_URL`, the server is down, firewall, or client isolation | `docs/TROUBLESHOOTING.md` §1–2 |
| `Invalid pairing code` on the website | typo, or the code expired | press Pair again |
| polling forever | nobody claimed the code | claim it; the handheld gives up after about 10 minutes |
| `pairing secret rejected` | the code was reissued | the handheld re-registers by itself; if you were running `--pair`, run it again |
| every call 401 | unpaired on the website | the handheld re-pairs automatically on the next boot |
| works, then stops after a day | the token was revoked | same as above; check the website's Devices list |

---

## For the demo

Pair **before** you are in front of an audience, and check it:

```powershell
python run.py --self-test
```

The `network` section shows the address phones should use, and the handheld reports
as paired if the token exists. Unpairing and re-pairing live takes about 20 seconds
and is a good thing to have rehearsed.

If the venue WiFi is hostile, pair over the tunnel address instead — everything else
works identically, and `wss://` is derived automatically for live updates.
