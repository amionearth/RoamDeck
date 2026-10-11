# Running and building the mobile app

Everything on this page is Windows PowerShell unless it says otherwise. The iOS part
needs a Mac; that is Apple's rule, not ours.

> **The one thing to get right first:** every device must be on the **same WiFi**, and
> the server must be told to listen on **all** interfaces. Nine times out of ten, a
> problem here is one of those two.

---

## The port is 8888

The FarFromHome server runs on **8888**, and the mobile app looks for it there first.
Port 8000 was already taken on this machine (and it is the most contested port in
development — Jupyter, Django, `python -m http.server`), so `PORT=8888` in `.env` is the
single source of truth.

Three things follow from that:

1. **Start the server on 8888**, either by leaving `PORT=8888` in `.env` or by passing it
   explicitly:
   ```powershell
   python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8888
   ```
2. **The mobile app already knows.** It probes `8888` on every candidate address, and it
   also probes `8000` as a fallback, so a server that has not been restarted yet is still
   found rather than silently unreachable.
3. **If something is already listening on 8000**, that is not this server. The app's
   Settings screen (**Hub → Settings → Run the check**) shows what answered on every
   candidate and what `/api/health` returned, which is how you tell a real FarFromHome
   server from a leftover `http.server`.

If you need a different port, change it in one place — `PORT` in `.env` — and start the
server with a matching `--port`. The app finds it as long as it is reachable; the
candidate list is in `frontend/src/lib/server.ts`, and `VITE_SERVER_PORT` overrides the
first choice at build time.

---

## 0. Find the laptop's WiFi address

```powershell
ipconfig
```

Read the **IPv4 Address** under the **Wireless LAN adapter Wi-Fi** heading. It looks
like `192.168.1.50`. Everything below calls it `$LAPTOP_IP`.

```powershell
# Put it in a variable so the rest of this page can be pasted as-is.
$LAPTOP_IP = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.InterfaceAlias -like "*Wi-Fi*" -and $_.IPAddress -notlike "169.254*" }
).IPAddress
$LAPTOP_IP
```

On Linux run `hostname -I`; on macOS run `ipconfig getifaddr en0`.

---

## 1. Start the server (terminal 1)

```powershell
cd C:\Users\LENOVO\Desktop\project\layover

# Install once
python -m pip install -r requirements.txt

# Point PUBLIC_BASE_URL at the laptop, or every QR code will contain "localhost"
# and no phone will be able to open it.
(Get-Content .env) -replace '^PUBLIC_BASE_URL=.*', "PUBLIC_BASE_URL=`"http://$LAPTOP_IP`:8888`"" |
    Set-Content .env

# Bind to ALL interfaces. Without 0.0.0.0 only this laptop can reach the server.
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8888
```

The server prints a banner with the address phones should use, a QR code of that
address, and a **warning if it is bound to 127.0.0.1**. If you see that warning, stop
and fix it before going further.

`--reload` is convenient while editing the backend, but do **not** use it for a demo:
a reload mid-game loses the session.

### Allow port 8888 through the firewall (once)

The first time Windows asks, allow access on **Private** networks. If it never asked:

```powershell
# Administrator PowerShell
netsh advfirewall firewall add rule name="FarFromHome 8888" dir=in action=allow protocol=TCP localport=8888 profile=private
```

Also make sure the WiFi network is set to **Private** (Settings → Network & Internet →
Wi-Fi → your network → Private). On Public, Windows blocks incoming connections.

### Prove it from the phone

On the phone, **WiFi on, mobile data off**, open:

```
http://<LAPTOP_IP>:8888/api/health
```

You should see `{"ok":true,"app":"FarFromHome",...}`.

* **JSON appears** → the network is fine. Any remaining QR problem is the URL inside it.
* **Nothing loads** → the server is not on `0.0.0.0`, the firewall is blocking, or the
  WiFi isolates clients. See `phone-app/docs/TROUBLESHOOTING.md` §1.

---

## 2. Run the app in a browser on the laptop (fastest loop)

```powershell
cd C:\Users\LENOVO\Desktop\project\layover\frontend
npm install          # once
npm run dev -- --host 0.0.0.0
```

* **Desktop view (the website):** <http://localhost:5173/>
* **Mobile view (the app):** <http://localhost:5173/?shell=mobile>

`?shell=mobile` forces the mobile shell; `?shell=web` forces the website. Without a
parameter, a phone-sized touch device gets the mobile shell automatically.

To open the app on a **real phone** over the WiFi, use the laptop's address:

```
http://<LAPTOP_IP>:5173/?shell=mobile
```

The dev server proxies `/api` and `/ws` to port 8888, so nothing else is needed. (A
phone browser can also open the app on port 8888 once you have built the frontend,
because FastAPI serves `frontend/dist` itself.)

---

## 3. Build the Android APK

One-time prerequisites:

* **Node.js 18+** — `node --version`
* **JDK 17** — Capacitor's Android build needs 17; the Android Studio bundled JDK works
  ```powershell
  java -version
  ```
* **Android SDK** — easiest through
  [Android Studio](https://developer.android.com/studio). Let it install the SDK and set
  `ANDROID_HOME` if the installer did not:

  ```powershell
  $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
  $env:PATH = "$env:PATH;$env:ANDROID_HOME\platform-tools;$env:ANDROID_HOME\tools"
  [Environment]::SetEnvironmentVariable("ANDROID_HOME", $env:ANDROID_HOME, "User")
  ```

Check everything is in place:

```powershell
cd C:\Users\LENOVO\Desktop\project\layover\frontend
npx cap doctor
```

### Build

```powershell
cd C:\Users\LENOVO\Desktop\project\layover\frontend

# 1. Build the web app and copy it into the native project.
#    (`android:sync` = `npm run build` + `npx cap sync android`)
npm run android:sync

# 2. Build the debug APK.  Gradle needs to be called from the android folder.
cd android
.\gradlew.bat assembleDebug
cd ..

# 3. Where it landed:
Get-Item .\android\app\build\outputs\apk\debug\app-debug.apk |
    Select-Object FullName, Length, LastWriteTime
```

All three steps in one go:

```powershell
cd C:\Users\LENOVO\Desktop\project\layover\frontend
npm run android:build
```

### Install it on a phone

Over USB, with **Developer options → USB debugging** on and the cable plugged in:

```powershell
# Confirm the phone is visible
adb devices

adb install -r .\android\app\build\outputs\apk\debug\app-debug.apk
```

Or copy `app-debug.apk` to the phone (a USB drive, a chat message to yourself, anything)
and tap it. Android will ask you to allow installing from that source.

### Open it in Android Studio instead

```powershell
npx cap open android
```

Useful for the emulator, the logcat, and signed release builds (**Build → Generate Signed
Bundle / APK**). For a release build you need a keystore; the debug APK is enough to test.

### After every code change

The native project contains a **copy** of the web build:

```powershell
npm run android:sync      # rebuild + copy
cd android; .\gradlew.bat assembleDebug; cd ..
```

`npm run android:build` does both.

---

## 4. Point the app at the right server

The app finds the server by itself: it probes the last address that worked, the current
origin, the same host on port 8888, and a handful of common gateway addresses, all in
parallel. When it finds one it remembers it.

In the app: **Hub → Settings** (the gear in the top corner).

* The address in use is printed at the top, with whether it was found or set by hand.
* **Find it for me** clears any manual setting and searches again.
* Type an address and press **Save** to pin one; it is only saved if the server actually
  answers `/api/health`, so a typo cannot leave the app pointing at nothing.
* **Run the check** probes every candidate and prints the result for each, plus what the
  *server* thinks its own address is. That is the panel to read out when the QR fails.

For a build that should work on a specific laptop with no setup at all, bake the address
in at build time:

```powershell
# frontend\.env.local
"VITE_SERVER_URL=http://192.168.1.50:8888" | Set-Content .env.local
# Optionally offer several, comma separated:
# "VITE_SERVER_CANDIDATES=https://abc.trycloudflare.com,http://192.168.1.50:8888" | Add-Content .env.local

npm run android:build
```

A baked-in address is only a hint: it is probed like any other candidate, so a build made
for one network still works on another once the user saves the right address.

---

## 5. Pair this phone as a handheld

The blind-trip endpoints (`/api/device/*`) need a device token. Until this phone is
paired, it can browse the social side but cannot create a trip.

1. In the app: **Hub → Settings → Pair as a handheld → Start pairing**.
2. A six-character code appears.
3. On the laptop, open <http://localhost:8888/device> and enter the code.
4. The app notices within three seconds and shows **paired**.

This is the same flow the Raspberry Pi handheld uses, so a phone and a handheld can both
be paired to the same account.

---

## 6. iOS: build it on a Mac

Windows cannot build an iOS app. The project is already generated and committed, so the
Mac needs only Xcode and CocoaPods.

On the **Windows** machine, get the project across:

```powershell
cd C:\Users\LENOVO\Desktop\project\layover
git add frontend
git commit -m "mobile app"
git push
```

Then on the **Mac**:

```bash
# 1. Xcode, and the command line tools, once
xcode-select --install
sudo xcodebuild -license accept

# 2. CocoaPods (Capacitor uses it for the native plugins)
sudo gem install cocoapods
#   Apple silicon, if the gem install complains:
#   brew install cocoapods

# 3. Get the code and build the web assets
git clone <your repo> farfromhome && cd farfromhome/frontend
npm install
npm run build
npx cap sync ios

# 4. Open in Xcode
npx cap open ios
```

In Xcode:

1. Select the **App** project in the navigator, then the **App** target.
2. **Signing & Capabilities** → tick *Automatically manage signing* → choose your team.
   A free Apple ID works for a personal device; the app expires after 7 days.
3. Change the **Bundle Identifier** to something unique to you, for example
   `com.yourname.farfromhome`. The default `com.farfromhome.app` will collide with mine.
4. Plug in the iPhone, select it as the destination, and press **Run** (⌘R).
5. On the phone: **Settings → General → VPN & Device Management → trust your developer
   certificate** the first time.

### iOS specifics that will bite you

* **Same WiFi.** The Mac must run the server; the iPhone must reach
  `http://<mac-ip>:8888/api/health` in Safari first. macOS also has a firewall:
  **System Settings → Network → Firewall → Options** and allow incoming connections for
  Python.
* **The compass needs a permission prompt that only a user gesture can trigger.** The app
  asks for it when a trip starts, from the Start button's tap, which is why it works. If
  it is refused, the header says *"no compass on this phone"* rather than showing a wrong
  arrow.
* **Plain HTTP is allowed** by the `NSAppTransportSecurity` entries in
  `ios/App/App/Info.plist`, which is what lets the app talk to `http://192.168.x.x:8888`
  without a certificate.
* **Microphone and speech** have their own usage strings in the same file. iOS refuses to
  show a prompt without one.
* **`npx cap open ios` after every web change.** Xcode builds the copy in
  `ios/App/App/public`; run `npm run build && npx cap sync ios` first or you will test the
  previous version and wonder why nothing changed.

---

## 7. Where things are

| What | Path |
|---|---|
| The web app and the mobile shell | `frontend/src` |
| Mobile design system (the dark, yellow look) | `frontend/src/styles/mobile.css` |
| The mobile shell: tabs and routes | `frontend/src/mobile/MobileApp.tsx` |
| The Compass / Chat / Hub screens | `frontend/src/pages/mobile/` |
| Server address discovery | `frontend/src/lib/server.ts` |
| GPS, compass, camera, network, haptics | `frontend/src/lib/native.ts` |
| The offline queue | `frontend/src/lib/outbox.ts` |
| The trip engine | `frontend/src/lib/engine.ts` |
| The planner | `frontend/src/lib/planner.ts` |
| Capacitor configuration | `frontend/capacitor.config.ts` |
| Android project (generated) | `frontend/android` |
| iOS project (generated) | `frontend/ios` |

---

## 8. Troubleshooting, in the order things actually break

**"Cannot reach the server" in the app.** Open **Hub → Settings → Run the check**. It
prints every candidate address and whether it answered. The usual causes, in order:
the server is not on `0.0.0.0`; the firewall; the phone is on mobile data; the WiFi
isolates clients (use a phone hotspot or a tunnel).

**The app found the server but trips fail with 401.** This phone is not paired. See §5.
The queue keeps the unsent rows and sends them once paired.

**The QR code does not open on another phone.** The address inside it is wrong for that
phone. Open **Hub → Settings → Run the check**: if it says the *server's own* base URL is
loopback, set `PUBLIC_BASE_URL` in the server's `.env` to `http://<LAPTOP_IP>:8888` and
restart the server.

**The compass does not move.** Android: check that *Location* and *Motion* are allowed for
FarFromHome in Settings → Apps. iOS: *Settings → Privacy → Motion & Fitness*. Web: Safari
requires the permission prompt, which the Start button triggers; Chrome on Android needs
no permission for `deviceorientation`.

**The arrow points the wrong way by a fixed amount.** A magnetometer near a speaker,
a magnet in a phone case or a car dashboard will do this. Move away from it, and hold the
phone flat for a moment. The header tells you whether it is using a real magnetic heading
or a relative one.

**The upload queue never empties.** Settings shows the head row, its attempt count and the
last status. `401` means re-pair. `404` means the trip has not been created yet and it will
retry. `409` is final and is removed. Anything else retries with a backoff up to 60
seconds per row, in strict order, so a stuck head holds the queue on purpose.

**The build fails with "SDK location not found".** `ANDROID_HOME` is not set or does not
point at a real SDK. See §3.

**Gradle is very slow the first time.** It is downloading the Android Gradle plugin and
the Capacitor libraries. The second build takes seconds.

**Everything is stale after an edit.** You forgot `npm run android:sync` (or
`npx cap sync ios`). The native project has its own copy of `dist`.
