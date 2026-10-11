import React, { useEffect } from "react";
import { HashRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../lib/auth";
import { useAuthStore } from "../lib/authToken";
import { useAppStore } from "../lib/appStore";
import { isNative } from "../lib/native";
import { TabBar } from "../components/mobile/TabBar";
import { CompassPage } from "../pages/mobile/CompassPage";
import { ChatPage } from "../pages/mobile/ChatPage";
import { HubPage } from "../pages/mobile/HubPage";
import { SettingsPage } from "../pages/mobile/SettingsPage";
import { SosPage } from "../pages/mobile/SosPage";

// Pages that already exist in the web app and are reused as they are, because they are
// shared surfaces rather than phone-hardware surfaces: the social side of the product.
import { Feed } from "../pages/Feed";
import { Passport } from "../pages/Passport";
import { Leaderboard } from "../pages/Leaderboard";
import { Friends } from "../pages/Friends";
import { Profile } from "../pages/Profile";
import { Games } from "../pages/Games";
import { GameDetail } from "../pages/GameDetail";
import { GameJoin } from "../pages/GameJoin";
import { TripPage } from "../pages/Trip";
import { DevicePage } from "../pages/Device";
import { DemoPage } from "../pages/Demo";
import { Login } from "../pages/Login";
import { Register } from "../pages/Register";
import { RadioPage } from "../pages/Radio";
import { TripPoster } from "../pages/TripPoster";

/**
 * The preflight: pick a server address and load anything persisted **before** the first
 * screen draws.
 *
 * Why it matters: a native app is served from a local origin, so there is no address to
 * talk to until discovery has run. Doing it here means no screen has to handle "the
 * server address is not known yet" and no request is fired at nothing.
 */
const Preflight: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [ready, setReady] = React.useState(false);
  const [note, setNote] = React.useState("Looking for your server");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // Native chrome first, so the black canvas and the dark status bar are set before
      // any content draws. Both plugins are optional; a failure here is cosmetic.
      if (isNative()) {
        try {
          const { StatusBar, Style } = await import("@capacitor/status-bar");
          await StatusBar.setStyle({ style: Style.Dark });
          await StatusBar.setBackgroundColor({ color: "#0B0B0D" });
        } catch {
          /* the web fallback has no status bar */
        }
        try {
          const { SplashScreen } = await import("@capacitor/splash-screen");
          await SplashScreen.hide();
        } catch {
          /* no splash plugin installed: nothing to hide */
        }
      }

      // Restore tokens next: the API layer reads them for every request.
      await useAuthStore.getState().hydrate();

      // Then find the server. A failure is not fatal: the app is usable offline for
      // planning from the built-in place set, and the banner explains the rest.
      try {
        const { discoverServer } = await import("../lib/server");
        const found = await discoverServer();
        if (!cancelled && !found) setNote("No server found yet - planning will use the built-in places");
      } catch {
        if (!cancelled) setNote("Could not look for the server");
      }
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!ready) {
    return (
      <div
        className="ffh"
        style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 18, minHeight: "100dvh" }}
      >
        <div className="ffh-spinner" style={{ width: 34, height: 34, borderWidth: 3 }} />
        <div className="ffh-muted">{note}</div>
      </div>
    );
  }
  return <>{children}</>;
};

/** Keeps the tab bar in step with the router, so a deep link lights the right tab. */
const Shell: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const tab = useAppStore((state) => state.tab);
  const setTab = useAppStore((state) => state.setTab);
  const tripRunning = useAppStore((state) => state.queueVersion) >= 0; // placeholder for future alerting
  void tripRunning;

  const path = location.pathname;
  const onMainTab = path === "/" || path === "/chat" || path === "/hub";

  useEffect(() => {
    if (path === "/") setTab("compass");
    else if (path === "/chat") setTab("chat");
    else if (path === "/hub") setTab("hub");
  }, [path, setTab]);

  return (
    <>
      <Routes>
        <Route path="/" element={<CompassPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/hub" element={<HubPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/sos" element={<SosPage />} />

        {/* Reused from the website: shared surfaces, not phone-hardware ones. */}
        <Route path="/feed" element={<Feed />} />
        <Route path="/passport" element={<Passport />} />
        <Route path="/leaderboard" element={<Leaderboard />} />
        <Route path="/friends" element={<Friends />} />
        <Route path="/u/:username" element={<Profile />} />
        <Route path="/games" element={<Games />} />
        <Route path="/games/:id" element={<GameDetail />} />
        <Route path="/g/:code" element={<GameJoin />} />
        <Route path="/trips/:id" element={<TripPage />} />
        <Route path="/trips/:id/poster" element={<TripPoster />} />
        <Route path="/device" element={<DevicePage />} />
        <Route path="/demo" element={<DemoPage />} />
        <Route path="/radio" element={<RadioPage />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>

      {onMainTab && (
        <TabBar
          active={tab}
          onChange={(next) => {
            setTab(next);
            navigate(next === "compass" ? "/" : `/${next}`);
          }}
        />
      )}
    </>
  );
};

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A phone on a patchy connection should not hammer the server, and stale data is
      // better than a spinner when the WiFi drops mid-walk.
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

/**
 * The native app shell.
 *
 * `HashRouter` is deliberate: a Capacitor app is served from a local origin, and hash
 * routing means every deep link and every reload resolves without a server-side route
 * fallback. The website keeps `BrowserRouter`.
 */
export const MobileApp: React.FC = () => (
  <QueryClientProvider client={queryClient}>
    <AuthProvider>
      <HashRouter>
        <div className="ffh">
          <Preflight>
            <Shell />
          </Preflight>
        </div>
      </HashRouter>
    </AuthProvider>
  </QueryClientProvider>
);

export default MobileApp;
