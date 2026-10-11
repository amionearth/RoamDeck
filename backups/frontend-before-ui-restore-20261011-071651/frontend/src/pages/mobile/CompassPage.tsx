import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  BedDouble,
  Coffee,
  Compass as CompassIcon,
  Eye,
  Landmark,
  MapPin,
  Play,
  ShoppingBasket,
  Trees,
  Utensils,
  Zap,
} from "lucide-react";
import { CompassDial, TrackSketch, formatDistance, formatDuration, useNow } from "../../components/mobile/CompassDial";
import { ConnectionBanner } from "../../components/mobile/ConnectionBanner";
import { tapFeedback } from "../../lib/native";
import { tripEngine, type TripSnapshot } from "../../lib/engine";
import { useAppStore } from "../../lib/appStore";

/**
 * The Compass screen: the home screen of the whole product.
 *
 * Layout follows the reference design - a huge yellow title, the dial, a bearing
 * readout, then a stack of cards. Everything on it is driven by one subscription to the
 * engine, so there is a single source of truth for "what may be shown right now":
 *
 * * **no plan yet** → an invitation to plan, plus a way to start from a template if the
 *   network is down;
 * * **planned** → the trip summary and a Start button;
 * * **running** → the arrow, the coaching line, the hints that have been revealed;
 * * **arrived** → the reveal, which is the only place the destination's name appears.
 */

const NEARBY_CATEGORIES = [
  { key: "coffee", label: "Café", icon: Coffee },
  { key: "food", label: "Food", icon: Utensils },
  { key: "viewpoint", label: "Views", icon: Eye },
  { key: "park", label: "Parks", icon: Trees },
  { key: "temple", label: "Temples", icon: Landmark },
  { key: "market", label: "Market", icon: ShoppingBasket },
];

export const CompassPage: React.FC = () => {
  const [snapshot, setSnapshot] = useState<TripSnapshot>(() => tripEngine.snapshot());
  const [nearby, setNearby] = useState<string | null>(null);
  const setPendingRequest = useAppStore((state) => state.setPendingRequest);
  const now = useNow();

  useEffect(() => tripEngine.onChange(setSnapshot), []);

  const started = snapshot.state === "running" || snapshot.state === "paused" || snapshot.state === "arrived";
  const elapsedSeconds = snapshot.minutesElapsed * 60 + (started ? 0 : 0);
  void now; // re-render every second so the clock moves

  const bearingText = useMemo(() => {
    if (snapshot.bearingDeg === null) return "--°";
    const value = Math.round(snapshot.bearingDeg) % 360;
    return `${String(value).padStart(3, "0")}°`;
  }, [snapshot.bearingDeg]);

  const cardinal = useMemo(() => {
    if (snapshot.bearingDeg === null) return "";
    const points = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
    return points[Math.round(snapshot.bearingDeg / 22.5) % 16];
  }, [snapshot.bearingDeg]);

  const start = async () => {
    await tapFeedback("medium");
    try {
      await tripEngine.start();
    } catch {
      /* the engine surfaces the reason through its own error field */
    }
  };

  return (
    <div className="ffh-scroll" style={{ paddingTop: 8 }}>
      <ConnectionBanner />

      <header style={{ padding: "18px 20px 0" }}>
        <h1 className="ffh-title">{snapshot.revealedDestination ? "You made it" : "Destination"}</h1>
        <p className="ffh-subtitle">
          {snapshot.revealedDestination
            ? "Blind trip complete"
            : snapshot.tripId
              ? snapshot.title
              : "Compass navigation"}
        </p>
        {/* Being honest about the heading source matters: a relative heading is not a
            compass, and the user should know which one they have. */}
        {started && (
          <p className="ffh-muted" style={{ fontSize: 12, marginTop: 6 }}>
            {snapshot.headingSource === "magnetic" || snapshot.headingSource === "absolute"
              ? "using the phone's compass"
              : snapshot.headingSource === "device"
                ? "using the device heading"
                : snapshot.headingSource === "relative"
                  ? "no compass on this phone - the arrow follows your direction of travel"
                  : "looking for the compass"}
          </p>
        )}
      </header>

      {/* ------------------------------------------------------------- no plan */}
      {!snapshot.tripId && <EmptyState onUseTemplate={() => setPendingRequest("a chill walk for two, about an hour")} />}

      {/* ------------------------------------------------------------- planned */}
      {snapshot.tripId && snapshot.state === "planned" && (
        <section style={{ padding: 20 }}>
          <div className="ffh-card">
            <div className="ffh-row ffh-row--between">
              <div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{snapshot.title}</div>
                <div className="ffh-muted" style={{ marginTop: 4 }}>
                  {snapshot.waypointsTotal} waypoints · {tripEngine.routeLengthKm().toFixed(1)} km
                </div>
              </div>
              <span className="ffh-pill">
                <MapPin size={14} /> hidden
              </span>
            </div>
            <div className="ffh-divider" />
            <p className="ffh-muted" style={{ margin: 0 }}>
              I know where we are going and you do not. When you press start, the arrow appears and the
              destination stays a secret until you arrive.
            </p>
            <button type="button" className="ffh-btn ffh-btn--block ffh-btn--lg" style={{ marginTop: 16 }} onClick={start}>
              <Play size={18} /> Start walking
            </button>
            <Link to="/settings" className="ffh-link" style={{ display: "inline-block", marginTop: 12 }}>
              Trip settings
            </Link>
          </div>
        </section>
      )}

      {/* ------------------------------------------------------------- running */}
      {snapshot.tripId && started && (
        <>
          <section style={{ padding: "6px 20px 0" }}>
            <div className="ffh-compass-wrap">
              <CompassDial
                arrowDeg={snapshot.arrowDeg}
                headingDeg={snapshot.headingDeg}
                label={snapshot.targetLabel}
                hasFix={snapshot.hasFix}
                headingIsAbsolute={snapshot.headingSource === "magnetic" || snapshot.headingSource === "absolute"}
              />
              <div className="ffh-bearing ffh-mono">
                {bearingText} {cardinal}
              </div>
            </div>
          </section>

          {/* The coaching line, in the agent's voice, from the engine. */}
          <section style={{ padding: "18px 20px 0" }}>
            <div className="ffh-card" style={{ display: "flex", gap: 14, alignItems: "center" }}>
              <span className="ffh-game-card__icon" style={{ width: 44, height: 44, flex: "none" }}>
                <Zap size={20} />
              </span>
              <div className="ffh-grow">
                <div style={{ fontWeight: 700, fontSize: 15 }}>
                  {snapshot.state === "arrived" ? "Arrived" : snapshot.hasFix ? "On your way" : "Finding you"}
                </div>
                <div className="ffh-muted" style={{ marginTop: 2 }}>
                  {tripEngine.coachingLine()}
                </div>
              </div>
            </div>
          </section>

          {snapshot.error && (
            <section style={{ padding: "12px 20px 0" }}>
              <div className="ffh-banner ffh-banner--warn">
                <AlertTriangle size={16} />
                <span>{snapshot.error}</span>
              </div>
            </section>
          )}

          {snapshot.offRoute && (
            <section style={{ padding: "12px 20px 0" }}>
              <div className="ffh-banner ffh-banner--warn">
                <CompassIcon size={16} />
                <span>
                  You are {Math.round(snapshot.offRouteMetres)} m off the planned line. No rush - the arrow still
                  points the way.
                </span>
              </div>
            </section>
          )}

          {/* Stats */}
          <section style={{ padding: "18px 20px 0" }}>
            <div className="ffh-grid-2">
              <Stat label="Walked" value={formatDistance(snapshot.distanceM)} />
              <Stat label="Out for" value={formatDuration(elapsedSeconds)} />
              <Stat
                label="Waypoints"
                value={`${snapshot.waypointsReached}/${snapshot.waypointsTotal}`}
              />
              <Stat
                label="Accuracy"
                value={snapshot.accuracy ? `±${Math.round(snapshot.accuracy)} m` : "--"}
              />
            </div>
          </section>

          {/* Revealed hints only: an unrevealed hint is a spoiler. */}
          {snapshot.visibleHints.length > 0 && (
            <section style={{ padding: "22px 20px 0" }}>
              <h2 className="ffh-section">Hints so far</h2>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {snapshot.visibleHints.map((hint) => (
                  <div key={hint.idx} className="ffh-card" style={{ padding: 16 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ffh-yellow)" }}>{hint.label}</div>
                    <div style={{ marginTop: 4, fontSize: 16, lineHeight: 1.45 }}>{hint.text}</div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* The track */}
          <section style={{ padding: "22px 20px 0" }}>
            <h2 className="ffh-section">Your line</h2>
            <TrackSketch
              track={snapshot.track}
              route={[]}
              position={snapshot.position}
              destination={
                snapshot.revealedDestination
                  ? { lat: snapshot.revealedDestination.lat, lng: snapshot.revealedDestination.lng }
                  : null
              }
            />
          </section>

          {/* The reveal */}
          {snapshot.revealedDestination && (
            <section style={{ padding: "22px 20px 0" }}>
              <div className="ffh-card" style={{ textAlign: "center", borderColor: "var(--ffh-yellow-dim)" }}>
                <div className="ffh-muted">You have arrived at</div>
                <div style={{ fontSize: 30, fontWeight: 800, letterSpacing: "-0.02em", marginTop: 8 }}>
                  {snapshot.revealedDestination.name}
                </div>
                <div className="ffh-muted" style={{ marginTop: 6, textTransform: "capitalize" }}>
                  {snapshot.revealedDestination.category}
                </div>
                <Link
                  to={`/trips/${snapshot.tripId}`}
                  className="ffh-btn ffh-btn--block"
                  style={{ marginTop: 18, textDecoration: "none" }}
                >
                  See the trip and the stamp
                </Link>
              </div>
            </section>
          )}

          {/* SOS, always reachable in one press-and-hold. */}
          <section style={{ padding: "22px 20px 0" }}>
            <Link
              to="/sos"
              className="ffh-btn ffh-btn--danger ffh-btn--block"
              style={{ textDecoration: "none" }}
            >
              <AlertTriangle size={18} /> Emergency
            </Link>
          </section>
        </>
      )}

      {/* ---------------------------------------------------------- nearby */} 
      <section style={{ padding: "24px 20px 0" }}>
        <h2 className="ffh-section">
          Nearby
          <Link to="/games" className="ffh-link">
            See all
          </Link>
        </h2>
        <div className="ffh-chips">
          {NEARBY_CATEGORIES.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              type="button"
              className={`ffh-chip ffh-haptic-tap${nearby === key ? " ffh-chip--active" : ""}`}
              onClick={() => {
                void tapFeedback();
                setNearby(nearby === key ? null : key);
                setPendingRequest(`${label} and a short walk, about an hour`);
              }}
            >
              <Icon size={16} /> {label}
            </button>
          ))}
        </div>
        {nearby && (
          <p className="ffh-muted" style={{ marginTop: 10 }}>
            Added to your next request. Open Chat to plan it.
          </p>
        )}
      </section>
    </div>
  );
};

const Stat: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="ffh-card" style={{ padding: 16 }}>
    <div className="ffh-muted" style={{ fontSize: 13 }}>
      {label}
    </div>
    <div className="ffh-mono" style={{ fontSize: 20, fontWeight: 700, marginTop: 4 }}>
      {value}
    </div>
  </div>
);

const EmptyState: React.FC<{ onUseTemplate: () => void }> = ({ onUseTemplate }) => (
  <section style={{ padding: "18px 20px 0" }}>
    <div className="ffh-card">
      <div className="ffh-row" style={{ gap: 14 }}>
        <span className="ffh-game-card__icon" style={{ flex: "none" }}>
          <BedDouble size={22} />
        </span>
        <div className="ffh-grow">
          <div style={{ fontWeight: 700, fontSize: 17 }}>No trip yet</div>
          <div className="ffh-muted" style={{ marginTop: 3 }}>
            Tell me the mood, the company and how long you have. I will pick somewhere and keep it a secret.
          </div>
        </div>
      </div>
      <Link to="/" className="ffh-btn ffh-btn--block" style={{ marginTop: 16, textDecoration: "none" }}>
        Plan a trip in Chat
      </Link>
      <button type="button" className="ffh-btn ffh-btn--ghost ffh-btn--block" style={{ marginTop: 10 }} onClick={onUseTemplate}>
        Use a quick template instead
      </button>
    </div>
  </section>
);
