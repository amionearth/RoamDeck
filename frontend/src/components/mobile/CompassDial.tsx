import React, { useEffect, useRef, useState } from "react";

/**
 * The compass dial, drawn with SVG.
 *
 * Three things it deliberately cannot do:
 *
 * * show a distance. There is no prop for one. The brief's rule is that the compass is
 *   direction only, and a component that cannot be handed a distance cannot leak it.
 * * show a place name. `label` is a coarse word ("onward", "a photo spot") that the
 *   engine only produces from a revealed waypoint.
 * * guess. With no heading it shows the needle in its last known orientation and the
 *   dial says "finding north", rather than snapping to zero and lying.
 */

export interface CompassDialProps {
  /** `(bearing - heading) mod 360`: the rotation of the needle. */
  arrowDeg: number;
  /** The device's heading, for the cardinals to rotate against. */
  headingDeg: number | null;
  /** Coarse target description. Never a place name. */
  label: string;
  /** False while there is no GPS fix: the needle is hidden and the dial pulses. */
  hasFix: boolean;
  /** True when the heading is a real magnetic north rather than a guess. */
  headingIsAbsolute?: boolean;
  size?: number;
}

export const CompassDial: React.FC<CompassDialProps> = ({
  arrowDeg,
  headingDeg,
  label,
  hasFix,
  headingIsAbsolute = true,
  size,
}) => {
  const style = size ? { width: size, height: size } : undefined;
  const heading = headingDeg ?? 0;

  return (
    <div className="ffh-compass" style={style}>
      <div className="ffh-compass__ring" />
      <div className="ffh-compass__inner" />

      {/* The cardinals rotate with the device, so "N" is always really north. */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          transform: `rotate(${-heading}deg)`,
          transition: "transform 0.28s cubic-bezier(0.34, 1.2, 0.64, 1)",
        }}
      >
        <span className="ffh-compass__cardinal ffh-compass__cardinal--n">N</span>
        <span className="ffh-compass__cardinal ffh-compass__cardinal--e">E</span>
        <span className="ffh-compass__cardinal ffh-compass__cardinal--s">S</span>
        <span className="ffh-compass__cardinal ffh-compass__cardinal--w">W</span>
      </div>

      {hasFix ? (
        <div className="ffh-compass__needle" style={{ transform: `rotate(${arrowDeg}deg)` }}>
          <svg viewBox="0 0 100 100" width="62%" height="62%" aria-hidden="true">
            <defs>
              <linearGradient id="ffh-needle" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#FFE45C" />
                <stop offset="100%" stopColor="#FFC400" />
              </linearGradient>
              <filter id="ffh-glow" x="-60%" y="-60%" width="220%" height="220%">
                <feGaussianBlur stdDeviation="4" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>
            {/* A kite-shaped needle, wide at the base, like the reference design. */}
            <g filter="url(#ffh-glow)">
              <path d="M50 6 L64 46 L50 62 L36 46 Z" fill="url(#ffh-needle)" />
              <circle cx="50" cy="52" r="9" fill="#FFD400" />
            </g>
          </svg>
        </div>
      ) : (
        <div className="ffh-searching" />
      )}

      {/* The label sits inside the dial, at the bottom, like the reference. */}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: "13%",
          textAlign: "center",
          fontSize: 14,
          fontWeight: 700,
          color: hasFix ? "var(--ffh-yellow)" : "var(--ffh-warning)",
          letterSpacing: "0.02em",
        }}
      >
        {hasFix ? label : "finding north"}
      </div>

      {hasFix && !headingIsAbsolute && (
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: "6%",
            textAlign: "center",
            fontSize: 11,
            color: "var(--ffh-text-faint)",
          }}
        >
          relative to where you started
        </div>
      )}
    </div>
  );
};

/**
 * A small "you are here" marker for the trip map.
 *
 * Deliberately not a map library: on a phone, one offline-safe SVG of the track and the
 * route is more useful than a tile layer that will not load outdoors. Leaflet is still
 * used on the website for the full view.
 */
export interface TrackSketchProps {
  track: Array<[number, number]>;
  route?: Array<[number, number]>;
  position?: { lat: number; lng: number } | null;
  /** Only drawn when the engine has revealed it. */
  destination?: { lat: number; lng: number } | null;
  height?: number;
}

export const TrackSketch: React.FC<TrackSketchProps> = ({
  track,
  route = [],
  position = null,
  destination = null,
  height = 160,
}) => {
  const points = [...route, ...track, ...(position ? [[position.lat, position.lng] as [number, number]] : [])];
  if (points.length < 2) {
    return (
      <div
        className="ffh-card"
        style={{ height, display: "flex", alignItems: "center", justifyContent: "center" }}
      >
        <span className="ffh-muted">Your route will draw itself as you walk</span>
      </div>
    );
  }

  const lats = points.map((point) => point[0]);
  const lngs = points.map((point) => point[1]);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs);
  const maxLng = Math.max(...lngs);
  const spanLat = Math.max(1e-6, maxLat - minLat);
  const spanLng = Math.max(1e-6, maxLng - minLng);

  const width = 320;
  const pad = 14;
  const project = (lat: number, lng: number) => {
    const x = pad + ((lng - minLng) / spanLng) * (width - pad * 2);
    // Latitude grows northwards, SVG y grows downwards.
    const y = pad + ((maxLat - lat) / spanLat) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  };

  return (
    <div className="ffh-card" style={{ padding: 8 }}>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} aria-hidden="true">
        {route.length > 1 && (
          <polyline
            points={route.map(([lat, lng]) => project(lat, lng)).join(" ")}
            fill="none"
            stroke="var(--ffh-border)"
            strokeWidth={3}
            strokeDasharray="6 7"
            strokeLinecap="round"
          />
        )}
        {track.length > 1 && (
          <polyline
            points={track.map(([lat, lng]) => project(lat, lng)).join(" ")}
            fill="none"
            stroke="var(--ffh-yellow)"
            strokeWidth={4}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )}
        {destination && (
          <g>
            <circle
              cx={project(destination.lat, destination.lng).split(",")[0]}
              cy={project(destination.lat, destination.lng).split(",")[1]}
              r={9}
              fill="var(--ffh-yellow)"
            />
            <text
              x={Number(project(destination.lat, destination.lng).split(",")[0]) + 14}
              y={Number(project(destination.lat, destination.lng).split(",")[1]) + 5}
              fill="var(--ffh-yellow)"
              fontSize={13}
              fontWeight={700}
            >
              here
            </text>
          </g>
        )}
        {position && (
          <circle
            cx={project(position.lat, position.lng).split(",")[0]}
            cy={project(position.lat, position.lng).split(",")[1]}
            r={7}
            fill="#fff"
            stroke="var(--ffh-black)"
            strokeWidth={3}
          />
        )}
      </svg>
    </div>
  );
};

/** A thin animated line, used while planning. */
export const ProgressLine: React.FC<{ value: number }> = ({ value }) => (
  <div
    style={{
      height: 6,
      borderRadius: 999,
      background: "var(--ffh-surface-2)",
      overflow: "hidden",
    }}
  >
    <div
      style={{
        width: `${Math.max(4, Math.min(100, value * 100))}%`,
        height: "100%",
        background: "var(--ffh-yellow)",
        borderRadius: 999,
        transition: "width 0.35s ease",
      }}
    />
  </div>
);

/** Counting up, for the trip clock. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

export function formatDistance(metres: number): string {
  if (metres < 1000) return `${Math.round(metres)} m`;
  return `${(metres / 1000).toFixed(metres < 10000 ? 1 : 0)} km`;
}

/** Scroll a container to the bottom, for chat. */
export function useAutoScroll<T extends HTMLElement>(dependency: unknown): React.RefObject<T | null> {
  const ref = useRef<T | null>(null);
  useEffect(() => {
    const node = ref.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [dependency]);
  return ref;
}
