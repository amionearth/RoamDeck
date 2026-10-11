/**
 * Server address resolution for the native app.
 *
 * The problem this file exists to solve: a Capacitor app is served from a **local**
 * origin (`http://localhost` on Android, `capacitor://localhost` on iOS). A relative
 * `fetch("/api/health")` therefore asks the phone itself, not the laptop, and fails
 * with a confusing network error. Everything must go to an absolute address.
 *
 * There is also no way to *guess* the laptop's address at build time: the laptop's
 * WiFi IP changes between venues, and an app built against one address is broken at
 * the next demo. So the address is:
 *
 * 1. **overridden by the user** in Settings (persisted, checked first),
 * 2. **auto-discovered** by probing a candidate list in parallel,
 * 3. **re-discovered** whenever the network changes or a request fails,
 * 4. **cached** in localStorage so a cold start with no WiFi still boots.
 *
 * Priorities for the demo, in the order they actually work:
 *   `http://<laptop-ip>:8888`  (same WiFi, the usual case)
 *   `https://<tunnel>.trycloudflare.com`  (venue WiFi with client isolation)
 *   `http://localhost:8888`  (the browser on the laptop itself)
 */

const STORAGE_KEY = "ffh.serverUrl";
const LAST_KNOWN_KEY = "ffh.lastServerUrl";
const PROBE_TIMEOUT_MS = 2500;

/**
 * The port the FarFromHome server listens on.
 *
 * **8888, not 8000.** Port 8000 is the busiest development port there is - Jupyter,
 * Django, `python -m http.server`, half the frameworks in existence - and on the machine
 * this was built on it was already taken. The server's own `PORT` in `.env` is set to
 * match, and the server prints the address it is actually on at startup, so the two are
 * never a guess.
 *
 * Override per build with `VITE_SERVER_PORT`, and note that the candidate list also
 * probes `FALLBACK_PORTS`, so a server that is still on 8000 is found rather than
 * requiring an edit.
 */
export const DEFAULT_PORT = Number(
  (import.meta.env.VITE_SERVER_PORT as string | undefined)?.trim() || 8888
);

/** Also worth probing: the old default, so a server that has not been restarted is found. */
const FALLBACK_PORTS = [8888, 8000].filter((port, index, all) => all.indexOf(port) === index);

/** Every port a FarFromHome server might be on, in the order worth trying. */
export function candidatePorts(): number[] {
  return [DEFAULT_PORT, ...FALLBACK_PORTS.filter((port) => port !== DEFAULT_PORT)];
}

/**
 * Candidate addresses to probe when nothing is known yet.
 *
 * `VITE_SERVER_URL` and `VITE_SERVER_CANDIDATES` (comma separated) let a build or a
 * dev session seed this list, which is how you point a fresh build at a specific
 * laptop without opening Settings on the phone.
 */
function candidateList(): string[] {
  const found = new Set<string>();

  const fromEnv = (import.meta.env.VITE_SERVER_URL as string | undefined)?.trim();
  if (fromEnv) found.add(normalise(fromEnv));

  const listFromEnv = (import.meta.env.VITE_SERVER_CANDIDATES as string | undefined)?.trim();
  if (listFromEnv) {
    for (const part of listFromEnv.split(",")) {
      const value = part.trim();
      if (value) found.add(normalise(value));
    }
  }

  // The last address that worked, if any. Cheap and usually correct.
  const lastKnown = readLocal(LAST_KNOWN_KEY);
  if (lastKnown) found.add(normalise(lastKnown));

  // Whatever origin the app is on. On the laptop's browser this IS the server, and in
  // `vite dev` the proxy handles it.
  if (typeof window !== "undefined" && window.location.protocol.startsWith("http")) {
    found.add(normalise(window.location.origin));
  }

  const ports = candidatePorts();

  // The same host on each server port. Covers `vite dev` on 5173 with the API on 8888.
  if (typeof window !== "undefined" && window.location.hostname) {
    for (const port of ports) {
      found.add(`${window.location.protocol}//${window.location.hostname}:${port}`);
    }
  }

  // The usual private-network addresses, on each port. A phone on the laptop's hotspot is
  // 192.168.43.x on Android and 172.20.10.x on iOS; a home or venue WiFi is 192.168.0/1.x
  // or 10.x. Probing is parallel, so a longer list costs latency, not time.
  for (const host of LAN_GUESSES) {
    for (const port of ports) {
      found.add(`http://${host}:${port}`);
    }
  }

  return [...found];
}

/** Common gateway addresses. Probing is parallel, so length costs little. */
const LAN_GUESSES = [
  "192.168.1.50",
  "192.168.1.1",
  "192.168.0.1",
  "192.168.43.1",
  "172.20.10.1",
  "10.0.0.1",
];

function normalise(raw: string): string {
  let value = raw.trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(value)) value = `http://${value}`;
  return value;
}

function readLocal(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocal(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode, or storage full. Not fatal. */
  }
}

// --------------------------------------------------------------------------- //
// state
// --------------------------------------------------------------------------- //
let current: string | null = readLocal(STORAGE_KEY) ?? readLocal(LAST_KNOWN_KEY);
let discovering: Promise<string | null> | null = null;
const listeners = new Set<(url: string | null) => void>();

function announce(url: string | null): void {
  current = url;
  for (const listener of listeners) {
    try {
      listener(url);
    } catch {
      /* a broken listener must not stop the others */
    }
  }
}

/** Subscribe to address changes (used by the connection banner). */
export function onServerChange(listener: (url: string | null) => void): () => void {
  listeners.add(listener);
  listener(current);
  return () => listeners.delete(listener);
}

/** The address in use right now, or `null` before the first successful probe. */
export function getServerUrl(): string | null {
  return current;
}

/**
 * The address to build a URL against. Falls back to the app's own origin so a
 * relative request still works in a plain browser during development.
 */
export function serverBase(): string {
  return current ?? (typeof window !== "undefined" ? window.location.origin : "");
}

/** The user set this by hand in Settings: trust it over discovery. */
export async function setServerUrl(raw: string, verify = true): Promise<boolean> {
  const candidate = normalise(raw);
  if (verify && !(await probe(candidate))) return false;
  writeLocal(STORAGE_KEY, candidate);
  writeLocal(LAST_KNOWN_KEY, candidate);
  announce(candidate);
  return true;
}

/** Forget a hand-set address and fall back to discovery. */
export function clearServerUrl(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  announce(readLocal(LAST_KNOWN_KEY));
}

/** Was the address set by hand? The Settings screen says so. */
export function isServerUrlPinned(): boolean {
  return Boolean(readLocal(STORAGE_KEY));
}

/** Every candidate we would probe, for the diagnostics panel. */
export function serverCandidates(): string[] {
  return candidateList();
}

// --------------------------------------------------------------------------- //
// probing
// --------------------------------------------------------------------------- //
/**
 * Is this address the FarFromHome server?
 *
 * Checks `/api/health` and that the answer is the one this server gives. A captive
 * portal or a random host on port 8888 will fail the second check, which is the
 * difference between "found the server" and "found something".
 */
export async function probe(base: string, timeoutMs = PROBE_TIMEOUT_MS): Promise<boolean> {
  const url = `${normalise(base)}/api/health`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) return false;
    const payload = (await response.json()) as { ok?: boolean; app?: string };
    return payload?.ok === true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Find a reachable server and remember it.
 *
 * Probes every candidate **in parallel** and takes the first success, so a cold start
 * costs one round trip rather than one per candidate. Concurrent calls share a single
 * discovery run.
 */
export async function discoverServer(force = false): Promise<string | null> {
  if (!force && current && (await probe(current))) {
    writeLocal(LAST_KNOWN_KEY, current);
    return current;
  }
  if (discovering) return discovering;

  discovering = (async () => {
    const candidates = candidateList();
    const attempts = candidates.map(async (candidate) => {
      const ok = await probe(candidate);
      if (!ok) throw new Error(candidate);
      return candidate;
    });

    try {
      // `Promise.any` gives us the first success and ignores the failures, which is
      // exactly the semantic wanted here.
      const winner = await Promise.any(attempts);
      writeLocal(LAST_KNOWN_KEY, winner);
      announce(winner);
      return winner;
    } catch {
      // Nothing answered. Keep the cached address so requests at least try, and let the
      // caller show "cannot reach the server" with the fix list.
      announce(current);
      return current;
    } finally {
      discovering = null;
    }
  })();

  return discovering;
}

/**
 * Called when a request fails suspiciously (network error or 404 from something that
 * is not our server). Re-discovers once, so moving between WiFi networks heals itself.
 */
export async function revalidateServer(): Promise<string | null> {
  return discoverServer(true);
}

/** Absolute URL for an API path. The one place that joins address and path. */
export function apiUrl(path: string): string {
  const clean = path.startsWith("/") ? path : `/${path}`;
  return `${serverBase()}${clean}`;
}

/** Absolute WebSocket URL, `ws://`/`wss://` chosen from the server's scheme. */
export function wsUrl(token?: string): string {
  const base = serverBase();
  const ws = base.replace(/^http:/i, "ws:").replace(/^https:/i, "wss:");
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${ws}/ws${query}`;
}
