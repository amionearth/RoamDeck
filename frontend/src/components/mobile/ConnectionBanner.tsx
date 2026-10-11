import React, { useEffect, useState } from "react";
import { AlertTriangle, Check, RefreshCw, Wifi, WifiOff } from "lucide-react";
import { discoverServer, isServerUrlPinned, onServerChange, serverBase } from "../../lib/server";
import { getNetworkState, watchNetwork } from "../../lib/native";
import { drain, status as outboxStatus } from "../../lib/outbox";

/**
 * The one banner that stops most demo disasters.
 *
 * It answers three questions at all times, in this order of importance:
 *
 * 1. can this phone reach the laptop at all, and at which address;
 * 2. is the queue backed up, and is that because we are offline;
 * 3. is something wrong that the user needs to fix (a refused permission, a bad
 *    address) rather than wait out.
 *
 * Everything it shows comes from a real probe, not a guess. That is why the address is
 * printed: when the QR does not open on someone else's phone, this is the string to
 * read out.
 */

export interface ConnectionBannerProps {
  /** Called with a human summary whenever the state changes, for the parent to log. */
  onState?: (state: BannerState) => void;
}

export interface BannerState {
  online: boolean;
  serverUrl: string;
  pinned: boolean;
  queued: number;
  checking: boolean;
}

export const ConnectionBanner: React.FC<ConnectionBannerProps> = ({ onState }) => {
  const [serverUrl, setServerUrl] = useState<string>(serverBase());
  const [pinned, setPinned] = useState(isServerUrlPinned());
  const [online, setOnline] = useState(true);
  const [deviceOnline, setDeviceOnline] = useState(true);
  const [queued, setQueued] = useState(0);
  const [checking, setChecking] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => onServerChange((url) => {
    setServerUrl(url ?? "");
    setPinned(isServerUrlPinned());
  }), []);

  useEffect(() => {
    void watchNetwork((state) => setOnline(state.connected)).then((stop) => () => stop());
  }, []);

  // Poll the queue and the server side by side. Cheap: one count from IndexedDB and one
  // request that also drains the queue.
  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      try {
        const summary = await outboxStatus();
        if (!cancelled) setQueued(summary.queued);

        const network = await getNetworkState();
        if (!network.connected) {
          if (!cancelled) setDeviceOnline(false);
          return;
        }
        if (!cancelled) setChecking(true);
        const result = await drain({ maxRows: 8 });
        if (!cancelled) {
          setDeviceOnline(result.stoppedFor !== "offline");
          setQueued(result.remaining);
        }
      } catch {
        if (!cancelled) setDeviceOnline(false);
      } finally {
        if (!cancelled) setChecking(false);
      }
    };

    void tick();
    const timer = window.setInterval(tick, 12000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    onState?.({ online: deviceOnline, serverUrl, pinned, queued, checking });
  }, [deviceOnline, serverUrl, pinned, queued, checking, onState]);

  const retry = async () => {
    setChecking(true);
    try {
      await discoverServer(true);
      await drain({ maxRows: 20 });
    } finally {
      setChecking(false);
    }
  };

  const reachable = online && deviceOnline && Boolean(serverUrl);

  if (reachable && queued === 0) {
    return null; // Nothing to say. Do not take up screen space.
  }

  const tone = !online || !deviceOnline ? "ffh-banner--bad" : queued > 0 ? "ffh-banner--warn" : "ffh-banner--good";
  const Icon = !online ? WifiOff : !deviceOnline ? AlertTriangle : queued > 0 ? RefreshCw : Check;

  return (
    <div style={{ padding: "10px 16px 0" }}>
      <button
        type="button"
        className={`ffh-banner ${tone} ffh-haptic-tap`}
        style={{ width: "100%", textAlign: "left", cursor: "pointer", fontFamily: "inherit" }}
        onClick={() => setExpanded((value) => !value)}
      >
        <Icon size={17} />
        <span className="ffh-grow">
          {!online
            ? "This phone has no network."
            : !deviceOnline
              ? `Cannot reach the server at ${serverUrl || "any known address"}.`
              : queued > 0
                ? `${queued} item${queued === 1 ? "" : "s"} waiting to upload.`
                : `Connected to ${serverUrl}`}
        </span>
        {checking && <span className="ffh-spinner" />}
      </button>

      {expanded && (
        <div className="ffh-card" style={{ marginTop: 8, borderRadius: 14 }}>
          <div className="ffh-mono" style={{ fontSize: 13, color: "var(--ffh-text-dim)", wordBreak: "break-all" }}>
            {serverUrl || "no server address found yet"}
            {pinned ? "  (set by hand)" : "  (found automatically)"}
          </div>
          <div className="ffh-divider" />
          <p className="ffh-muted" style={{ margin: 0 }}>
            {!online
              ? "Turn on WiFi or mobile data. The trip keeps running from what is stored on this phone."
              : "Check that the laptop is running with --host 0.0.0.0, that port 8000 is allowed through its firewall, and that this phone is on the same WiFi (mobile data off)."}
          </p>
          <div className="ffh-row" style={{ marginTop: 14, gap: 10 }}>
            <button type="button" className="ffh-btn ffh-btn--quiet" onClick={retry} disabled={checking}>
              {checking ? "Checking..." : "Look again"}
            </button>
            <a className="ffh-link" href="#/settings">
              Server settings
            </a>
          </div>
        </div>
      )}
    </div>
  );
};
