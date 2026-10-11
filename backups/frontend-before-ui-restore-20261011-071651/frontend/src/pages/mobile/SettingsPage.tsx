import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  BellRing,
  Check,
  Copy,
  Database,
  Link2,
  MapPin,
  RefreshCw,
  Server,
  ShieldCheck,
  Smartphone,
  Trash2,
} from "lucide-react";
import { api, currentServer, getNetworkInfo } from "../../lib/api";
import {
  candidatePorts,
  clearServerUrl,
  discoverServer,
  isServerUrlPinned,
  probe,
  serverCandidates,
  serverBase,
  setServerUrl,
} from "../../lib/server";
import { platformLabel, requestLocationPermission, tapFeedback } from "../../lib/native";
import { clearAll, drain, resetBackoff, status as outboxStatus, type OutboxRow } from "../../lib/outbox";
import { useAuthStore } from "../../lib/authToken";
import { pollDevicePairing, startDevicePairing } from "../../lib/auth";

/**
 * Settings: the four things that actually go wrong, each with its own panel.
 *
 * 1. **The server address.** Printed, editable, and re-discoverable. When the QR does
 *    not open on someone else's phone, this is the string to read out.
 * 2. **Pairing.** The handheld flow, so the blind-trip endpoints accept this phone.
 * 3. **Permissions.** Location and motion, with a real test rather than a claim.
 * 4. **The queue.** What is waiting, why, and a button to force it.
 *
 * Everything here reports what it measured. There is no "looks fine" that was not
 * actually probed.
 */

export const SettingsPage: React.FC = () => {
  const [serverUrl, setUrl] = useState(serverBase());
  const [pinned, setPinned] = useState(isServerUrlPinned());
  const [draft, setDraft] = useState(serverBase());
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "good" | "warn" | "bad"; text: string } | null>(null);
  const [queue, setQueue] = useState<{ queued: number; head: OutboxRow | null; items: OutboxRow[] } | null>(null);
  const [pairing, setPairing] = useState<{ code: string; deviceId: string; secret: string } | null>(null);
  const [paired, setPaired] = useState(false);
  const [diag, setDiag] = useState<string[]>([]);
  const [networkInfo, setNetworkInfo] = useState<{ lan_ip: string; base_url: string; port: number } | null>(null);

  const refreshQueue = useCallback(async () => {
    const summary = await outboxStatus();
    setQueue({ queued: summary.queued, head: summary.head, items: summary.items });
  }, []);

  useEffect(() => {
    void refreshQueue();
    setPaired(Boolean(useAuthStore.getState().deviceToken));
    void getNetworkInfo().then(setNetworkInfo);
  }, [refreshQueue]);

  const runCheck = async () => {
    setBusy("check");
    const lines: string[] = [];
    lines.push(`platform: ${platformLabel()}`);
    lines.push(`address in use: ${serverBase() || "none"}`);
    lines.push(`address source: ${isServerUrlPinned() ? "set by hand" : "found automatically"}`);
    lines.push("");
    lines.push("probing every candidate:");
    for (const candidate of serverCandidates()) {
      const ok = await probe(candidate, 2000);
      lines.push(`  ${ok ? "ok  " : "--- "} ${candidate}`);
    }
    lines.push("");
    const health = await api.get<any>("/api/health").catch(() => null);
    lines.push(`/api/health: ${health ? `ok, ${health.app}` : "no answer"}`);
    const info = await getNetworkInfo();    if (info) {
      lines.push(`server thinks its own address is: ${info.base_url}`);
      lines.push(`server LAN ip: ${info.lan_ip}  port: ${info.port}`);
      if (/localhost|127\.0\.0\.1/.test(info.base_url)) {
        lines.push("");
        lines.push("WARNING: the server's own base URL is loopback, so QR codes it");
        lines.push("generates will point at the phone itself. Set PUBLIC_BASE_URL in");
        lines.push("the server's .env to the laptop's WiFi address.");
      }
      if (info.port !== candidatePorts()[0]) {
        lines.push("");
        lines.push(`NOTE: the server is on port ${info.port}, not ${candidatePorts()[0]}.`);
        lines.push("It was found because both ports are probed. Set PORT in the server's");
        lines.push(".env to match, so the addresses in QR codes agree with this screen.");
      }
    }
    setNetworkInfo(info);
    setDiag(lines);
    setBusy(null);
  };

  const saveServer = async () => {
    setBusy("server");
    setMessage(null);
    const ok = await setServerUrl(draft, true);
    setBusy(null);
    if (ok) {
      setUrl(draft.replace(/\/+$/, ""));
      setPinned(true);
      setMessage({ tone: "good", text: "Saved, and the server answered." });
      await discoverServer(true);
    } else {
      setMessage({
        tone: "bad",
        text: `Nothing answered at ${draft}/api/health. Check the laptop is running the server on 0.0.0.0 and that this phone is on the same WiFi.`,
      });
    }
  };

  const autoFind = async () => {
    setBusy("auto");
    clearServerUrl();
    const found = await discoverServer(true);
    setBusy(null);
    setPinned(isServerUrlPinned());
    if (found) {
      setUrl(found);
      setDraft(found);
      setMessage({ tone: "good", text: `Found the server at ${found}.` });
    } else {
      setMessage({ tone: "bad", text: "No server answered on any candidate address." });
    }
  };

  const beginPairing = async () => {
    setBusy("pair");
    setMessage(null);
    try {
      const info = await startDevicePairing(`Phone (${platformLabel()})`);
      setPairing({ code: info.pairing_code, deviceId: info.device_id, secret: info.poll_secret });
      setMessage({
        tone: "good",
        text: "Enter the code on the website under Devices. This screen will notice when it is claimed.",
      });
    } catch (error) {
      setMessage({ tone: "bad", text: `Could not start pairing: ${(error as Error).message}` });
    } finally {
      setBusy(null);
    }
  };

  // Poll for the claim every 3 seconds, exactly like the handheld does.
  useEffect(() => {
    if (!pairing) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      const result = await pollDevicePairing(pairing.deviceId, pairing.secret);
      if (cancelled) return;
      if (result.status === "paired" && result.token) {
        useAuthStore.getState().setDevice(pairing.deviceId, result.token);
        setPaired(true);
        setPairing(null);
        setMessage({ tone: "good", text: "This phone is paired. The blind-trip features are available." });
        void tapFeedback("medium");
      } else if (result.status === "expired") {
        setPairing(null);
        setMessage({ tone: "warn", text: "The pairing code expired. Start again for a fresh one." });
      }
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pairing]);

  const testLocation = async () => {
    setBusy("location");
    const granted = await requestLocationPermission();
    setBusy(null);
    setMessage(
      granted
        ? { tone: "good", text: "Location works." }
        : { tone: "bad", text: "Location was refused or is unavailable. Turn it on for FarFromHome in the phone's settings." }
    );
  };

  const copyAddress = async () => {
    try {
      await navigator.clipboard.writeText(serverUrl);
      setMessage({ tone: "good", text: "Address copied." });
    } catch {
      setMessage({ tone: "warn", text: "Could not copy. Read the address off the screen." });
    }
  };

  return (
    <div className="ffh-scroll">
      <header style={{ padding: "18px 20px 0", display: "flex", alignItems: "center", gap: 12 }}>
        <Link to="/settings" className="ffh-icon-btn" aria-label="Back" onClick={() => window.history.back()}>
          <Server size={20} />
        </Link>
        <div className="ffh-grow">
          <h1 className="ffh-title" style={{ fontSize: 36 }}>
            Settings
          </h1>
        </div>
      </header>

      {message && (
        <section style={{ padding: "16px 20px 0" }}>
          <div
            className={`ffh-banner ${
              message.tone === "good" ? "ffh-banner--good" : message.tone === "warn" ? "ffh-banner--warn" : "ffh-banner--bad"
            }`}
          >
            <span>{message.text}</span>
          </div>
        </section>
      )}

      {/* ------------------------------------------------------ the server */}
      <section style={{ padding: "18px 20px 0" }}>
        <h2 className="ffh-section">
          <Server size={18} /> Server
        </h2>
        <div className="ffh-card">
          <div className="ffh-muted">In use now</div>
          <div className="ffh-mono" style={{ fontSize: 16, marginTop: 6, wordBreak: "break-all" }}>
            {serverUrl || "nothing found yet"}
          </div>
          <div className="ffh-muted" style={{ marginTop: 4, fontSize: 13 }}>
            {pinned ? "set by hand" : "found automatically"} · {platformLabel()}
          </div>

          <div className="ffh-divider" />

          <label className="ffh-muted" htmlFor="server-url">
            Laptop address (the WiFi IPv4 from <span className="ffh-mono">ipconfig</span>)
          </label>
          <div className="ffh-row" style={{ gap: 10, marginTop: 8 }}>
            <input
              id="server-url"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="http://192.168.1.50:8000"
              inputMode="url"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              style={{
                flex: 1,
                minWidth: 0,
                background: "var(--ffh-surface-2)",
                border: "1px solid var(--ffh-border)",
                borderRadius: 14,
                padding: "14px 16px",
                color: "var(--ffh-text)",
                fontSize: 15,
                outline: "none",
              }}
            />
            <button type="button" className="ffh-btn" onClick={saveServer} disabled={busy !== null}>
              {busy === "server" ? "..." : "Save"}
            </button>
          </div>

          <div className="ffh-row" style={{ gap: 10, marginTop: 10 }}>
            <button type="button" className="ffh-btn ffh-btn--quiet" onClick={autoFind} disabled={busy !== null}>
              {busy === "auto" ? "Looking..." : "Find it for me"}
            </button>
            <button type="button" className="ffh-btn ffh-btn--quiet" onClick={copyAddress}>
              <Copy size={16} /> Copy
            </button>
          </div>

          <p className="ffh-muted" style={{ marginTop: 12, fontSize: 13 }}>
            On Windows run <span className="ffh-mono">ipconfig</span> and read the IPv4 address under Wi-Fi. Start
            the server with <span className="ffh-mono">--host 0.0.0.0</span>, or only the laptop can reach it.
          </p>
        </div>
      </section>

      {/* ---------------------------------------------------------- network */}
      <section style={{ padding: "18px 20px 0" }}>
        <h2 className="ffh-section">
          <ShieldCheck size={18} /> Network check
        </h2>
        <div className="ffh-card">
          <button type="button" className="ffh-btn ffh-btn--block" onClick={runCheck} disabled={busy !== null}>
            {busy === "check" ? "Probing..." : "Run the check"}
          </button>
          {diag.length > 0 && (
            <pre
              className="ffh-mono"
              style={{
                marginTop: 14,
                marginBottom: 0,
                fontSize: 12,
                lineHeight: 1.6,
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
                color: "var(--ffh-text-dim)",
                maxHeight: 260,
                overflowY: "auto",
              }}
            >
              {diag.join("\n")}
            </pre>
          )}
          {networkInfo && (
            <p className="ffh-muted" style={{ marginTop: 10, fontSize: 13 }}>
              The server reports its own LAN address as <span className="ffh-mono">{networkInfo.lan_ip}</span> on port{" "}
              <span className="ffh-mono">{networkInfo.port}</span>.
            </p>
          )}
        </div>
      </section>

      {/* -------------------------------------------------------- handheld */}
      <section style={{ padding: "18px 20px 0" }}>
        <h2 className="ffh-section">
          <Smartphone size={18} /> Pair as a handheld
        </h2>
        <div className="ffh-card">
          <div className="ffh-row ffh-row--between">
            <span className="ffh-muted">Pairing</span>
            <span className={`ffh-pill ${paired ? "ffh-pill--good" : "ffh-pill--warn"}`}>
              {paired ? <Check size={14} /> : <AlertTriangle size={14} />}
              {paired ? "paired" : "not paired"}
            </span>
          </div>

          {paired ? (
            <>
              <p className="ffh-muted" style={{ marginTop: 12 }}>
                This phone can create trips, run games and queue GPS points. Unpair it here if you are handing the
                phone to someone else.
              </p>
              <button
                type="button"
                className="ffh-btn ffh-btn--ghost ffh-btn--block"
                style={{ marginTop: 14 }}
                onClick={() => {
                  useAuthStore.getState().clearDevice();
                  setPaired(false);
                  setMessage({ tone: "warn", text: "Unpaired. The queued items stay in the outbox." });
                }}
              >
                Unpair this phone
              </button>
            </>
          ) : pairing ? (
            <>
              <div style={{ textAlign: "center", marginTop: 16 }}>
                <div className="ffh-muted">Enter this code on the website, under Devices</div>
                <div className="ffh-mono" style={{ fontSize: 40, fontWeight: 800, letterSpacing: "0.2em", color: "var(--ffh-yellow)", marginTop: 8 }}>
                  {pairing.code}
                </div>
                <div className="ffh-row" style={{ gap: 8, justifyContent: "center", marginTop: 12 }}>
                  <span className="ffh-spinner" />
                  <span className="ffh-muted">waiting for the claim</span>
                </div>
              </div>
              <button
                type="button"
                className="ffh-btn ffh-btn--ghost ffh-btn--block"
                style={{ marginTop: 14 }}
                onClick={() => setPairing(null)}
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <p className="ffh-muted" style={{ marginTop: 12 }}>
                Pairing links this phone to a FarFromHome account, the same way the handheld does. It is what makes
                the blind-trip endpoints accept your requests.
              </p>
              <button type="button" className="ffh-btn ffh-btn--block" style={{ marginTop: 14 }} onClick={beginPairing} disabled={busy !== null}>
                {busy === "pair" ? "Asking the server..." : "Start pairing"}
              </button>
            </>
          )}
        </div>
      </section>

      {/* ----------------------------------------------------- permissions */}
      <section style={{ padding: "18px 20px 0" }}>
        <h2 className="ffh-section">
          <MapPin size={18} /> Permissions
        </h2>
        <div className="ffh-card">
          <p className="ffh-muted" style={{ marginTop: 0 }}>
            The compass needs location and motion. Both are used only while a trip is running, and the track is
            uploaded to your own server.
          </p>
          <button type="button" className="ffh-btn ffh-btn--quiet ffh-btn--block" style={{ marginTop: 12 }} onClick={testLocation} disabled={busy !== null}>
            {busy === "location" ? "Asking..." : "Test location"}
          </button>
        </div>
      </section>

      {/* --------------------------------------------------------- the queue */}
      <section style={{ padding: "18px 20px 0" }}>
        <h2 className="ffh-section">
          <Database size={18} /> Offline queue
        </h2>
        <div className="ffh-card">
          <div className="ffh-row ffh-row--between">
            <span className="ffh-muted">Waiting to upload</span>
            <span className="ffh-mono" style={{ fontSize: 18, fontWeight: 700 }}>
              {queue?.queued ?? 0}
            </span>
          </div>

          {queue?.head && (
            <>
              <div className="ffh-divider" />
              <div className="ffh-muted" style={{ fontSize: 13 }}>
                next: {queue.head.method} {queue.head.path}
              </div>
              <div className="ffh-muted" style={{ fontSize: 13, marginTop: 2 }}>
                attempts {queue.head.attempts}
                {queue.head.last_status ? ` · last status ${queue.head.last_status}` : ""}
              </div>
              {queue.head.last_error && (
                <div className="ffh-muted" style={{ fontSize: 12, marginTop: 6, wordBreak: "break-all" }}>
                  {queue.head.last_error}
                </div>
              )}
            </>
          )}

          <div className="ffh-row" style={{ gap: 10, marginTop: 14 }}>
            <button
              type="button"
              className="ffh-btn ffh-btn--quiet"
              onClick={async () => {
                setBusy("flush");
                await resetBackoff();
                const result = await drain({ maxRows: 30 });
                await refreshQueue();
                setBusy(null);
                setMessage({
                  tone: result.sent > 0 ? "good" : "warn",
                  text: result.sent > 0 ? `Sent ${result.sent}. ${result.remaining} left.` : `Nothing sent. ${result.lastError ?? "Still offline?"}`,
                });
              }}
              disabled={busy !== null}
            >
              <RefreshCw size={15} /> {busy === "flush" ? "Sending..." : "Send now"}
            </button>
            <button
              type="button"
              className="ffh-btn ffh-btn--ghost"
              onClick={async () => {
                await clearAll();
                await refreshQueue();
                setMessage({ tone: "warn", text: "Queue emptied. Anything unsent is gone." });
              }}
            >
              <Trash2 size={15} /> Empty
            </button>
          </div>

          {queue && queue.items.length > 0 && (
            <div style={{ marginTop: 14 }}>
              {queue.items.map((row) => (
                <div key={row.id} className="ffh-row ffh-row--between" style={{ padding: "6px 0", fontSize: 13 }}>
                  <span className="ffh-muted">
                    {row.kind} · {row.path.replace("/api/device/trips/", "").slice(0, 28)}
                  </span>
                  <span className="ffh-mono" style={{ color: "var(--ffh-text-faint)" }}>
                    {row.attempts}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="ffh-divider" />
          <div className="ffh-row" style={{ gap: 10 }}>
            <BellRing size={15} style={{ color: "var(--ffh-text-dim)" }} />
            <span className="ffh-muted" style={{ fontSize: 13 }}>
              SOS is never queued behind anything: it is sent immediately with a 5 second timeout and repeated every
              15 seconds until the server confirms.
            </span>
          </div>
        </div>
      </section>

      <section style={{ padding: "18px 20px 0" }}>
        <h2 className="ffh-section">
          <Link2 size={18} /> About
        </h2>
        <div className="ffh-card">
          <div className="ffh-muted" style={{ fontSize: 13, lineHeight: 1.6 }}>
            FarFromHome mobile app. The Python FastAPI server is the source of truth: accounts, trips, photos,
            stamps and scores all live there. This app plans, navigates, hosts games and queues everything it
            cannot send yet.
          </div>
          <div className="ffh-divider" />
          <Link to="/demo" className="ffh-link">
            Demo tools
          </Link>
        </div>
      </section>

      <div style={{ height: 40 }} />
    </div>
  );
};
