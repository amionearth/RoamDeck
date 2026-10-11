# RoamDeck Mobile Client

A vanilla HTML/CSS/JavaScript app wrapped in **Capacitor 6** for Android and iOS.
The phone app talks to the existing **Python FastAPI backend** — no new server needed.

| Port | What runs there |
|------|----------------|
| **8000** | Python FastAPI backend (your existing server) |
| **8888** | Phone app dev server (browser testing) |

---

## How it works — the big picture

```
┌─────────────────────────────┐          ┌──────────────────────────────┐
│   Phone / Browser           │          │   Your PC / Server           │
│                             │          │                              │
│   RoamDeck app              │  Wi-Fi / │   Python FastAPI             │
│   (Capacitor webview)       │ internet │   backend                    │
│                             │  ──────► │   port 8000                  │
│   • Login / Register        │          │                              │
│   • Social feed             │  REST    │   /api/auth/login            │
│   • BlindTrip passport      │  HTTP    │   /api/feed                  │
│   • Games (photo tasks)     │  ──────► │   /api/g/{code}/…            │
│   • Leaderboard & friends   │          │   /api/trips/{id}/…          │
│                             │  WS      │                              │
│   GPS, Camera, Compass      │  ──────► │   /ws  (real-time events)    │
└─────────────────────────────┘          └──────────────────────────────┘
```

- All API calls go to `http://<SERVER_IP>:8000/api/…`
- Auth uses a **Bearer token** stored in `localStorage` (sent as `Authorization: Bearer <token>` header)
- Real-time game events use a **WebSocket** at `ws://<SERVER_IP>:8000/ws`
- Camera and GPS use Capacitor plugins with an automatic browser fallback
- The server IP is set inside the app at runtime — no rebuild needed when the IP changes

---

## Step 1 — Start the backend server

Open a terminal in the **RoamDeck root folder** (the folder that contains `backend/`):

```powershell
# Windows PowerShell
cd C:\Users\LENOVO\Desktop\project\layover\RoamDeck
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
# Linux / macOS
cd /path/to/RoamDeck
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

When it starts you will see something like:

```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     LAN address: http://192.168.1.100:8000   ← use this IP in the app
```

**Keep this terminal open.** The server must stay running while you use the app.

> **First time only** — install Python dependencies first:
> ```powershell
> pip install -r requirements.txt
> # or if there is no requirements.txt:
> pip install fastapi uvicorn sqlalchemy pillow qrcode
> ```

---

## Step 2 — Run the phone app in a browser (quickest way to test)

```powershell
# Open a second terminal, go into mobile-client
cd mobile-client

# Install Node packages (first time only)
npm install

# Start the dev server on port 8888
npm run dev
```

Then open **http://localhost:8888** in your browser (or on a phone on the same Wi-Fi).

### Set the server address on first launch

1. The login screen shows `Connecting to … [Change]`
2. Tap / click **Change**
3. Enter your server's LAN IP — the one printed by uvicorn, e.g. `192.168.1.100`
4. Tap **Save** — the app tests the connection and confirms ✅
5. Register or log in

> If you are testing on the same PC as the server, enter `localhost` or `127.0.0.1`.

---

## Step 3 — Build and install the Android APK

### Prerequisites

Install these before running the build script:

| Tool | Version | Download |
|------|---------|----------|
| Node.js | 18 or newer | https://nodejs.org |
| JDK | 17 or newer | https://adoptium.net |
| Android Studio | Hedgehog or newer | https://developer.android.com/studio |
| Android SDK (API 33+) | — | installed via Android Studio → SDK Manager |

After installing Android Studio, set the environment variable:

```powershell
# Windows — add to System Environment Variables
ANDROID_HOME = C:\Users\<you>\AppData\Local\Android\Sdk

# Also add to PATH:
%ANDROID_HOME%\platform-tools
%ANDROID_HOME%\tools
```

Restart your terminal after setting these.

### Build the APK (Windows — one command)

```powershell
cd mobile-client
powershell -ExecutionPolicy Bypass -File setup-android.ps1
```

The script runs these steps automatically:
1. `npm install` — install Capacitor and dependencies
2. `npx cap add android` — create the Android project (skipped if already exists)
3. Copy `android-extras/network_security_config.xml` — allows HTTP to your LAN server
4. Patch `AndroidManifest.xml` — adds camera, location, internet permissions
5. `npx cap sync android` — copy your web app into the Android project
6. `gradlew assembleDebug` — compile the APK

**APK location when done:**
```
mobile-client\android\app\build\outputs\apk\debug\app-debug.apk
```

### Build the APK (Linux / macOS)

```bash
cd mobile-client
chmod +x setup-android.sh
./setup-android.sh
```

### Install on your Android phone

1. Enable **Developer Options** on your phone:
   - Go to **Settings → About Phone**
   - Tap **Build Number** 7 times
   - Go back to **Settings → Developer Options**
   - Enable **USB Debugging**

2. Connect your phone with a USB cable

3. Run:
   ```powershell
   adb install mobile-client\android\app\build\outputs\apk\debug\app-debug.apk
   ```

4. Open **RoamDeck** on your phone, tap **Change**, enter your server IP, and log in

---

## Step 4 — Using the app

### Login / Register
- Open the app → enter a username, display name, and password
- If you have an existing account, switch to the **Sign In** tab

### Feed
- Browse public BlindTrips posted by other users
- Like trips with the ❤️ button
- Tap **View** to see photos, comments, and trip details

### Passport
- View your collected place stamps
- Stamps are earned by completing BlindTrips
- Rarities: Common → Rare → Epic → Legendary

### Games
- Tap the **🎮 Game** tab
- Enter the 6-letter game code shown on the host screen (e.g. `ABCDEF`)
- Enter your display name and tap **Join Game**
- Complete photo tasks by tapping **📷** — the camera opens automatically
- Vote on other players' photos in the **Gallery** tab
- Watch scores update live in the **Scores** tab

### Leaderboard
- Switch between **Global** and **Friends** scope
- Sort by **Points**, **Places visited**, or **Photos approved**

### Friends
- Search by username or friend code
- Tap **Add** to send a request
- Accept incoming requests from the Friends tab
- Share your friend code from the card at the bottom

### Profile
- Edit display name, bio, and home city
- Tap the 📷 button on your avatar to update it
- Your XP, level, and rank title update automatically

---

## Open in Android Studio (optional)

If you want to use the Android emulator or Logcat:

```powershell
cd mobile-client
npx cap open android
```

This opens Android Studio with the project loaded. Press the ▶ Run button to launch on an emulator or connected device.

---

## iOS setup (macOS only)

```bash
cd mobile-client
npm install
npx cap add ios
npx cap sync ios
npx cap open ios    # opens Xcode
```

Requires macOS with Xcode 15+. A free Apple ID works for simulator testing; a paid Apple Developer account ($99/yr) is required to install on a real iPhone.

---

## Build a signed release APK

For distributing outside Android Studio:

```powershell
# 1. Generate a keystore (one time only)
keytool -genkey -v -keystore roamdeck.jks -alias roamdeck `
        -keyalg RSA -keysize 2048 -validity 10000

# 2. Add signing config to android/app/build.gradle (see Capacitor docs)

# 3. Build release
cd mobile-client\android
.\gradlew assembleRelease
# APK → app\build\outputs\apk\release\app-release.apk
```

---

## Project structure

```
mobile-client/
│
├── www/                          ← the web app Capacitor loads
│   ├── index.html                ← single HTML shell (all screens in here)
│   ├── manifest.json             ← PWA manifest
│   ├── css/
│   │   └── app.css               ← dark-mode mobile UI, all styles
│   ├── img/
│   │   ├── default-avatar.svg
│   │   └── stamp-placeholder.svg
│   └── js/
│       ├── api.js                ← every HTTP/WS call to port 8000
│       ├── app.js                ← boot sequence, auth forms, page router
│       ├── camera.js             ← Capacitor Camera (falls back to <input> in browser)
│       ├── geo.js                ← Capacitor GPS (falls back to navigator.geolocation)
│       ├── ws.js                 ← WebSocket with auto-reconnect
│       └── pages/
│           ├── feed.js           ← social feed with like / comment
│           ├── trip.js           ← trip detail, photo grid, comments
│           ├── passport.js       ← stamp collection
│           ├── leaderboard.js    ← global / friends leaderboard
│           ├── friends.js        ← friend list, requests, search
│           ├── profile.js        ← profile editor, avatar upload
│           └── game.js           ← game lobby, tasks, gallery, scoreboard
│
├── android-extras/
│   └── network_security_config.xml   ← allows plain HTTP to LAN server
│
├── capacitor.config.json         ← appId=com.roamdeck.app, webDir=www
├── package.json                  ← Capacitor 6 + dev server script
├── setup-android.ps1             ← one-shot Windows APK builder
├── setup-android.sh              ← one-shot Linux/macOS APK builder
└── README.md                     ← this file
```

---

## All API endpoints

| Screen | Method | Endpoint |
|--------|--------|----------|
| Login | POST | `/api/auth/login` |
| Register | POST | `/api/auth/register` |
| Logout | POST | `/api/auth/logout` |
| My profile | GET | `/api/me` |
| Update profile | PATCH | `/api/me` |
| Upload avatar | POST | `/api/me/avatar` |
| Search users | GET | `/api/users/search?q=` |
| User profile | GET | `/api/users/{username}` |
| Social feed | GET | `/api/feed` |
| Trip detail | GET | `/api/trips/{id}` |
| Trip photos | GET | `/api/trips/{id}/photos` |
| Like trip | POST | `/api/trips/{id}/like` |
| Comments | GET/POST | `/api/trips/{id}/comments` |
| Passport stamps | GET | `/api/passport` |
| Badges | GET | `/api/badges` |
| Leaderboard | GET | `/api/leaderboard?scope=&metric=` |
| Friend list | GET | `/api/friends` |
| Send request | POST | `/api/friends/request` |
| Accept request | POST | `/api/friends/{id}/accept` |
| Remove friend | DELETE | `/api/friends/{id}` |
| Game info | GET | `/api/g/{code}/info` |
| Join game | POST | `/api/g/{code}/join` |
| My tasks | GET | `/api/g/{code}/me` |
| Submit photo | POST | `/api/g/{code}/submissions` |
| Vote on photo | POST | `/api/g/{code}/vote` |
| Gallery | GET | `/api/g/{code}/gallery` |
| Scoreboard | GET | `/api/g/{code}/scoreboard` |
| Health check | GET | `/api/health` |
| Real-time events | WebSocket | `/ws` |

---

## Troubleshooting

| Symptom | What to do |
|---------|-----------|
| "Server not reachable" on login screen | Start the backend (`uvicorn … --port 8000`); check PC firewall allows port 8000 |
| App loads but API calls fail | Tap **Change** and re-enter the correct server IP |
| Camera opens but photo never uploads | Grant Camera and Storage permissions in Android Settings → Apps → RoamDeck |
| GPS shows nothing | Grant Location permission; move outside; wait a few seconds for fix |
| APK blocked on install | Enable **Install unknown apps** in Android Settings for your file manager |
| Gradle build fails | Confirm `ANDROID_HOME` is set and JDK 17 is active — run `java -version` |
| `adb: command not found` | Add `%ANDROID_HOME%\platform-tools` to your PATH and restart terminal |
| CORS error in browser dev | Expected when server is on a different host — set the correct server IP in the app |
| WebSocket disconnects | Normal on mobile sleep — the app reconnects automatically in 3 seconds |
