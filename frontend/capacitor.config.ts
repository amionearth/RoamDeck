import type { CapacitorConfig } from "@capacitor/cli";

/**
 * Capacitor configuration.
 *
 * The app is a normal Vite build; Capacitor copies `dist/` into a native shell and
 * serves it from a local origin (`http://localhost` on Android, `capacitor://localhost`
 * on iOS). That is why the server address can never be inferred from
 * `window.location` — it is a *local* origin, not the server. Everything goes through
 * `src/lib/server.ts`, which resolves the laptop's WiFi address.
 */
const config: CapacitorConfig = {
  appId: "com.farfromhome.app",
  appName: "FarFromHome",
  webDir: "dist",

  // Ship the whole app inside the binary: no remote code, no white screen when the
  // WiFi is down, and the offline queue works from the first launch.
  bundledWebRuntime: false,

  android: {
    // Required for the geolocation and motion plugins to reach the sensors without a
    // second permission prompt, and for WebSockets to a plain-http LAN server.
    allowMixedContent: true,
    webContentsDebuggingEnabled: true,
  },

  ios: {
    // The camera and microphone are used by photo tasks and push-to-talk.
    limitsNavigationsToAppBoundDomains: false,
    contentInset: "always",
  },

  server: {
    // NO `url` here on purpose. Pointing `server.url` at the laptop would make the app
    // depend on a network round trip before it can even draw its first screen, and it
    // would break the "works offline" requirement. The app loads locally and talks to
    // the server over fetch/WebSocket instead.
    androidScheme: "https",
    iosScheme: "capacitor",
    cleartext: true,
  },

  plugins: {
    SplashScreen: {
      launchShowDuration: 900,
      backgroundColor: "#0B0B0D",
      showSpinner: false,
      androidScaleType: "CENTER_CROP",
    },
    StatusBar: {
      style: "DARK",
      backgroundColor: "#0B0B0D",
      overlaysWebView: false,
    },
    Keyboard: {
      resize: "body",
    },
  },
};

export default config;
