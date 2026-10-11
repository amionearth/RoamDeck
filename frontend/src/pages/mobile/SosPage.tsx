import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Check, Phone, ShieldAlert } from "lucide-react";
import { sendSos } from "../../lib/outbox";
import { getCurrentFix, tapFeedback } from "../../lib/native";
import { tripEngine } from "../../lib/engine";

/**
 * SOS.
 *
 * Three deliberate decisions, all from brief 03 §9 and §11:
 *
 * 1. **Hold for three seconds**, not a single tap. A one-tap emergency button in a
 *    pocket is a liability; a hold is unambiguous.
 * 2. **Send immediately with a 5 second timeout *and* queue a copy.** The immediate
 *    call is what saves time; the queued copy is what survives the WiFi dropping a
 *    second later. The screen says which of the two happened, honestly.
 * 3. **Always tell the user to call for help directly.** This app relays a location to
 *    a server that logs it and notifies a contact; it is not a substitute for a phone
 *    call, and saying so is part of not over-promising.
 */
export const SosPage: React.FC = () => {
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [contact, setContact] = useState<{ name: string; phone: string } | null>(null);
  const startedAt = useRef<number | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    // The trusted contact is set on the website; read it so the screen can name them.
    void (async () => {
      try {
        const { api } = await import("../../lib/api");
        const me = await api.get<any>("/api/me");
        if (me?.trusted_contact_name || me?.trusted_contact_phone) {
          setContact({ name: me.trusted_contact_name ?? "", phone: me.trusted_contact_phone ?? "" });
        }
      } catch {
        /* not signed in, or offline. The screen works either way. */
      }
    })();
  }, []);

  const stopTimer = () => {
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
    startedAt.current = null;
  };

  useEffect(() => stopTimer, []);

  const begin = () => {
    if (sent) return;
    startedAt.current = Date.now();
    void tapFeedback("heavy");
    timer.current = window.setInterval(() => {
      if (startedAt.current === null) return;
      const elapsed = Date.now() - startedAt.current;
      setProgress(Math.min(1, elapsed / 3000));
      if (elapsed >= 3000) {
        stopTimer();
        void fire();
      }
    }, 60);
  };

  const cancel = () => {
    if (sent) return;
    stopTimer();
    setProgress(0);
    setStatus("Released too early. Hold the button for three full seconds.");
  };

  const fire = async () => {
    setSent(true);
    setStatus("Sending your location...");
    void tapFeedback("heavy");

    let lat = 0;
    let lng = 0;
    try {
      const fix = await getCurrentFix({ timeoutMs: 6000 });
      lat = fix.lat;
      lng = fix.lng;
    } catch {
      const snapshot = tripEngine.snapshot();
      if (snapshot.position) {
        lat = snapshot.position.lat;
        lng = snapshot.position.lng;
      }
    }

    const plan = tripEngine.getPlan();
    const result = await sendSos({
      lat,
      lng,
      trip_id: plan?.trip_id ?? null,
      at: new Date().toISOString(),
    });
    setStatus(result.message);
  };

  return (
    <div className="ffh-scroll" style={{ padding: "20px 20px 0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span className="ffh-icon-btn" style={{ background: "rgba(255,77,77,0.15)", borderColor: "rgba(255,77,77,0.4)", color: "var(--ffh-danger)" }}>
          <ShieldAlert size={20} />
        </span>
        <h1 className="ffh-title" style={{ fontSize: 38, color: "var(--ffh-danger)" }}>
          Emergency
        </h1>
      </div>

      <p className="ffh-muted" style={{ marginTop: 14 }}>
        Holding the button sends your current location to the FarFromHome server, which records it and notifies your
        trusted contact. It keeps retrying every 15 seconds until the server confirms.
      </p>

      <section style={{ marginTop: 22 }}>
        <button
          type="button"
          className={`ffh-btn ffh-btn--danger ffh-btn--block ffh-haptic-tap${!sent && progress > 0 ? " ffh-mic--listening" : ""}`}
          style={{ minHeight: 132, fontSize: 22, flexDirection: "column", gap: 6 }}
          onPointerDown={begin}
          onPointerUp={cancel}
          onPointerLeave={cancel}
          onContextMenu={(event) => event.preventDefault()}
        >
          {sent ? <Check size={30} /> : <AlertTriangle size={30} />}
          {sent ? "SOS sent" : progress > 0 ? "Keep holding..." : "Hold for SOS"}
        </button>

        <div
          style={{
            height: 8,
            borderRadius: 999,
            background: "var(--ffh-surface-2)",
            overflow: "hidden",
            marginTop: 14,
          }}
        >
          <div
            style={{
              width: `${progress * 100}%`,
              height: "100%",
              background: "var(--ffh-danger)",
              transition: "width 0.06s linear",
            }}
          />
        </div>

        {status && (
          <div className={`ffh-banner ${sent ? "ffh-banner--good" : "ffh-banner--warn"}`} style={{ marginTop: 16 }}>
            <span>{status}</span>
          </div>
        )}
      </section>

      <section style={{ marginTop: 22 }}>
        <div className="ffh-card">
          <div className="ffh-row" style={{ gap: 12 }}>
            <span className="ffh-game-card__icon" style={{ width: 44, height: 44, flex: "none" }}>
              <Phone size={20} />
            </span>
            <div className="ffh-grow">
              <div style={{ fontWeight: 700, fontSize: 15 }}>
                {contact?.name ? `Your trusted contact: ${contact.name}` : "No trusted contact set"}
              </div>
              <div className="ffh-muted" style={{ marginTop: 2 }}>
                {contact?.phone
                  ? contact.phone
                  : "Add one on the website under your profile. Until then this app can only log the event."}
              </div>
            </div>
          </div>
          <div className="ffh-divider" />
          <p className="ffh-muted" style={{ margin: 0 }}>
            This is not a substitute for calling emergency services. If you are in danger, call for help directly
            first, then use this to share where you are.
          </p>
        </div>
      </section>

      <div className="ffh-row" style={{ gap: 10, marginTop: 20 }}>
        <Link to="/" className="ffh-btn ffh-btn--ghost ffh-btn--block" style={{ textDecoration: "none" }}>
          Back to the compass
        </Link>
      </div>
    </div>
  );
};
