import React, { Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import "./styles/mobile.css";
import { isNative } from "./lib/native";

/**
 * Two shells, one codebase, one server.
 *
 * * **The website** (the existing app) keeps its paper-and-lagoon look, its left rail and
 *   its bottom nav, and is what you open on the laptop.
 * * **The mobile shell** is the Capacitor-wrapped app: the dark, yellow, three-tab design
 *   from the reference screens, with the compass, the agent and the game hub.
 *
 * The choice is made from the environment, in this order:
 *
 * 1. `?shell=mobile` / `?shell=web` in the URL, so either can be opened on purpose;
 * 2. running inside a native shell (`Capacitor.isNativePlatform()`);
 * 3. a touch-first, phone-sized viewport, because the mobile shell is the better
 *    experience on a phone browser too - it has the compass;
 * 4. otherwise the website.
 *
 * Both are lazy-loaded, so a laptop never downloads the mobile shell and a phone never
 * downloads the desktop rail.
 */

const WebApp = lazy(() => import("./App"));
const MobileApp = lazy(() => import("./mobile/MobileApp"));

function wantsMobileShell(): boolean {
  try {
    const forced = new URLSearchParams(window.location.search).get("shell");
    if (forced === "mobile") return true;
    if (forced === "web") return false;
  } catch {
    /* no URL search available */
  }

  if (isNative()) return true;

  const narrow = window.innerWidth <= 820;
  const touch = window.matchMedia?.("(pointer: coarse)")?.matches ?? false;
  return narrow && touch;
}

const mobile = wantsMobileShell();

const fallback = (
  <div
    style={{
      minHeight: "100dvh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      background: mobile ? "#0B0B0D" : undefined,
      color: mobile ? "#9A9CA5" : undefined,
      fontFamily: "system-ui, sans-serif",
    }}
  >
    Loading…
  </div>
);

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense fallback={fallback}>{mobile ? <MobileApp /> : <WebApp />}</Suspense>
  </React.StrictMode>
);
