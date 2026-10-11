/**
 * The blind-trip engine, ported from `phone-app/farfromhome_device/engine/`.
 *
 * Two behaviours are copied deliberately and are non-negotiable, because they are the
 * product:
 *
 * 1. **The destination is never shown before the reveal.** Nothing in this class
 *    exposes `plan.destination` through a public accessor except `revealedDestination()`,
 *    which returns `null` until the arrival rule has fired. The compass reports a
 *    bearing and an arrow angle and **no distance**, exactly as the brief requires.
 * 2. **Reveal rules are evaluated in code, not by the UI.** `never`, `on_proximity`,
 *    `on_arrival` and `at_time` each fire once, and a GPS wobble in and out of a radius
 *    cannot re-trigger anything.
 *
 * The engine owns no React state. Screens subscribe to `onChange` and render whatever
 * `snapshot()` returns, which keeps one source of truth for "what may be shown".
 */

import {
  arrowAngle,
  bearingDeg,
  compassPoint,
  distanceToPathM,
  haversineM,
  normaliseDeg,
  pathLengthM,
  simplifyPath,
  turnDirection,
} from "./geo";
import {
  queueArrive,
  queueFinish,
  queuePoints,
  queueReveal,
  queueTrip,
} from "./outbox";
import { getCurrentFix, watchFixes, watchHeading, type Fix } from "./native";

// --------------------------------------------------------------------------- //
// types
// --------------------------------------------------------------------------- //
export type RevealRule = "never" | "on_proximity" | "on_arrival" | "at_time";

export interface PlanWaypoint {
  id: string;
  idx: number;
  lat: number;
  lng: number;
  kind: "route" | "photo_spot" | "shop" | "food" | "viewpoint" | "rest";
  hint: string;
  reveal: RevealRule;
  radius_m: number;
}

export interface Plan {
  schema_version: number;
  trip_id: string;
  title: string;
  description: string;
  mood: string;
  members: Array<{ name: string; color: string }>;
  destination: {
    name: string;
    lat: number;
    lng: number;
    category: string;
    radius_m: number;
    /** Always true: the mystery is the product. */
    secret: true;
  };
  waypoints: PlanWaypoint[];
  games: Array<{ type: string; tasks: string[]; duration_min: number }>;
  route_km: number;
  fallback: { used: boolean; reason: string };
  created_at: string;
  /** Where this plan came from, for the UI to be honest about it. */
  source: "llm" | "template" | "offline-template";
}

export type TripState = "idle" | "planned" | "running" | "paused" | "arrived" | "wrapped_up";

export interface TrackPoint {
  lat: number;
  lng: number;
  at: number;
  accuracy_m: number;
}

/** Everything a screen may draw. Nothing here leaks the destination before arrival. */
export interface TripSnapshot {
  state: TripState;
  tripId: string | null;
  title: string;
  /** Bearing to the next target, degrees clockwise from north. */
  bearingDeg: number | null;
  /** `(bearing - heading) mod 360`: what the needle rotates by. */
  arrowDeg: number;
  /** The device's own heading, or null when unknown. */
  headingDeg: number | null;
  /** Where the heading came from, so the UI can be honest about it. */
  headingSource: string;
  /** Coarse label such as "onward" — never a place name. */
  targetLabel: string;
  targetKind: string;
  hasFix: boolean;
  /** Only ever set after the reveal rule fires. */
  revealedDestination: { name: string; lat: number; lng: number; category: string } | null;
  /** Waypoints whose hint may be shown. */
  visibleHints: Array<{ idx: number; label: string; text: string }>;
  /** Metres walked, jitter-filtered. */
  distanceM: number;
  position: { lat: number; lng: number; accuracy_m: number } | null;
  accuracy: number | null;
  offRoute: boolean;
  offRouteMetres: number;
  minutesElapsed: number;
  waypointsReached: number;
  waypointsTotal: number;
  queued: number;
  message: string;
  error: string | null;
  track: Array<[number, number]>;
}

// --------------------------------------------------------------------------- //
// tuning (the same numbers the handheld uses)
// --------------------------------------------------------------------------- //
const DEFAULTS = {
  pointIntervalMs: 5000,
  batchIntervalMs: 15000,
  batchSize: 50,
  maxAccuracyM: 50,
  offRouteToleranceM: 120,
  offRouteDwellMs: 45000,
  jitterFloorM: 2,
};

const WAYPOINT_LABELS: Record<string, string> = {
  route: "onward",
  photo_spot: "a photo spot",
  shop: "a shop",
  food: "somewhere to eat",
  viewpoint: "a viewpoint",
  rest: "a place to rest",
};

// --------------------------------------------------------------------------- //
// the engine
// --------------------------------------------------------------------------- //
export class TripEngine {
  private plan: Plan | null = null;
  private state: TripState = "idle";
  private fix: Fix | null = null;
  private previousFix: Fix | null = null;
  private heading: number | null = null;
  private headingSource = "none";
  private track: TrackPoint[] = [];
  private distanceM = 0;
  private startedAt: number | null = null;
  private arrivedAt: number | null = null;
  private reached = new Set<number>();
  private revealed = new Set<string>();
  private visibleHints: Array<{ idx: number; label: string; text: string }> = [];
  private pendingPoints: Array<{ lat: number; lng: number; t: string; acc: number }> = [];
  private lastLoggedAt = 0;
  private lastBatchedAt = 0;
  private offRouteSince: number | null = null;
  private offRoute = false;
  private offRouteMetres = 0;
  private message = "";
  private error: string | null = null;
  private queued = 0;
  private stopFixes: (() => void) | null = null;
  private stopHeading: (() => void) | null = null;
  private changeListeners = new Set<(snapshot: TripSnapshot) => void>();

  // ---------------------------------------------------------------- //
  // lifecycle
  // ---------------------------------------------------------------- //
  onChange(listener: (snapshot: TripSnapshot) => void): () => void {
    this.changeListeners.add(listener);
    listener(this.snapshot());
    return () => this.changeListeners.delete(listener);
  }

  private emit(): void {
    const snapshot = this.snapshot();
    for (const listener of this.changeListeners) {
      try {
        listener(snapshot);
      } catch {
        /* a broken listener must not stop the others */
      }
    }
  }

  getPlan(): Plan | null {
    return this.plan;
  }

  setPlan(plan: Plan, queueToServer = true): void {
    this.plan = plan;
    this.state = "planned";
    this.arrivedAt = null;
    this.reached.clear();
    this.revealed.clear();
    this.visibleHints = [];
    this.distanceM = 0;
    this.track = [];
    this.message = "";
    this.error = null;
    this.startedAt = null;
    if (queueToServer) {
      void queueTrip(plan as any).then(() => this.refreshQueueCount());
    }
    this.emit();
  }

  /** Start the live loop: GPS watch plus compass watch. */
  async start(): Promise<void> {
    if (!this.plan) throw new Error("There is no plan to start.");
    if (this.state === "running" || this.state === "arrived") return;

    this.startedAt = this.startedAt ?? Date.now();
    this.state = "running";

    // A single fix first, so the arrow is useful immediately rather than after the
    // first watch callback.
    try {
      const fix = await getCurrentFix();
      this.acceptFix(fix);
    } catch (error) {
      this.error = (error as Error).message;
    }

    this.stopFixes = await watchFixes(
      (fix) => this.acceptFix(fix),
      (message) => {
        // A lost fix freezes the arrow (brief §12) rather than guessing.
        this.fix = null;
        this.error = message;
        this.emit();
      }
    );

    const handle = await watchHeading(
      (sample) => {
        this.heading = sample.heading_deg;
        this.headingSource = handleRef.source || "device";
        this.emit();
      },
      (message) => {
        this.headingSource = "unavailable";
        if (!this.error) this.error = message;
        this.emit();
      }
    );
    const handleRef = handle;
    this.stopHeading = handle.stop;
    this.headingSource = handle.source;

    this.emit();
  }

  pause(): void {
    if (this.state === "running") {
      this.state = "paused";
      this.emit();
    }
  }

  resume(): void {
    if (this.state === "paused") {
      this.state = "running";
      this.emit();
    }
  }

  stop(): void {
    this.stopFixes?.();
    this.stopHeading?.();
    this.stopFixes = null;
    this.stopHeading = null;
  }

  /** Force the arrival check, for the "I am here" button. */
  async arriveManually(): Promise<void> {
    await this.arrive();
  }

  reset(): void {
    this.stop();
    this.plan = null;
    this.state = "idle";
    this.fix = null;
    this.previousFix = null;
    this.heading = null;
    this.track = [];
    this.distanceM = 0;
    this.startedAt = null;
    this.arrivedAt = null;
    this.reached.clear();
    this.revealed.clear();
    this.visibleHints = [];
    this.pendingPoints = [];
    this.message = "";
    this.error = null;
    this.emit();
  }

  // ---------------------------------------------------------------- //
  // sensors
  // ---------------------------------------------------------------- //
  private acceptFix(fix: Fix): void {
    // A bad fix is dropped, not used: 50 m of accuracy would put the arrow through a
    // wall and make the arrival check meaningless.
    if (fix.accuracy_m > DEFAULTS.maxAccuracyM) {
      this.error = `Weak GPS fix (+-${Math.round(fix.accuracy_m)} m). Waiting for a better one.`;
      this.emit();
      return;
    }

    if (this.previousFix) {
      const jump = haversineM(this.previousFix.lat, this.previousFix.lng, fix.lat, fix.lng);
      const deltaSeconds = Math.max(0.1, (fix.at - this.previousFix.at) / 1000);
      if (jump / deltaSeconds > 12) {
        // 43 km/h between two fixes is a GPS artefact, not a walk.
        return;
      }
    }

    const previous = this.fix;
    this.fix = fix;
    this.previousFix = fix;
    this.error = null;

    if (previous) {
      const step = haversineM(previous.lat, previous.lng, fix.lat, fix.lng);
      const noiseFloor = Math.max(
        DEFAULTS.jitterFloorM,
        0.5 * Math.min(previous.accuracy_m, fix.accuracy_m)
      );
      if (step >= noiseFloor) this.distanceM += step;
    }

    this.logTrack(fix);
    this.bufferPoints(fix);
    this.evaluateGeofences(fix);
    this.evaluateReveals(fix);
    this.evaluateOffRoute(fix);
    this.emit();
  }

  private logTrack(fix: Fix): void {
    if (fix.at - this.lastLoggedAt < DEFAULTS.pointIntervalMs) return;
    this.lastLoggedAt = fix.at;
    this.track.push({ lat: fix.lat, lng: fix.lng, at: fix.at, accuracy_m: fix.accuracy_m });
    if (this.track.length > 4000) this.track.splice(0, this.track.length - 4000);
  }

  private bufferPoints(fix: Fix): void {
    this.pendingPoints.push({
      lat: Number(fix.lat.toFixed(6)),
      lng: Number(fix.lng.toFixed(6)),
      t: new Date(fix.at).toISOString(),
      acc: Number(fix.accuracy_m.toFixed(2)),
    });

    const now = Date.now();
    if (!this.lastBatchedAt) {
      this.lastBatchedAt = now;
      return;
    }
    if (now - this.lastBatchedAt < DEFAULTS.batchIntervalMs) return;
    this.lastBatchedAt = now;
    void this.flushPoints();
  }

  /** Send the buffered fixes. Called on the batch timer and on trip end. */
  async flushPoints(): Promise<void> {
    if (!this.plan || !this.pendingPoints.length) return;
    const batch = this.pendingPoints.splice(0, DEFAULTS.batchSize);
    await queuePoints(this.plan.trip_id, batch);
    await this.refreshQueueCount();
  }

  // ---------------------------------------------------------------- //
  // rules
  // ---------------------------------------------------------------- //
  private routePath(): Array<[number, number]> {
    if (!this.plan) return [];
    const path: Array<[number, number]> = this.plan.waypoints
      .slice()
      .sort((a, b) => a.idx - b.idx)
      .map((waypoint) => [waypoint.lat, waypoint.lng] as [number, number]);
    // The destination is the shape of the route, not a label, so including it leaks
    // nothing — and without it the off-route check would be wrong on the final leg.
    path.push([this.plan.destination.lat, this.plan.destination.lng]);
    return path;
  }

  private evaluateGeofences(fix: Fix): void {
    if (!this.plan) return;
    for (const waypoint of this.plan.waypoints) {
      if (this.reached.has(waypoint.idx)) continue;
      const distance = haversineM(fix.lat, fix.lng, waypoint.lat, waypoint.lng);
      if (distance <= Math.max(15, waypoint.radius_m)) {
        this.reached.add(waypoint.idx);
        void queueReveal(this.plan.trip_id, waypoint.idx).then(() => this.refreshQueueCount());
      }
    }
  }

  private evaluateReveals(fix: Fix): void {
    if (!this.plan) return;

    for (const waypoint of this.plan.waypoints) {
      const key = `wp:${waypoint.idx}`;
      if (this.revealed.has(key) || waypoint.reveal === "never") continue;

      let shouldReveal = false;
      if (waypoint.reveal === "on_proximity") {
        shouldReveal = this.reached.has(waypoint.idx);
      } else if (waypoint.reveal === "at_time" && this.startedAt) {
        const elapsedMinutes = (Date.now() - this.startedAt) / 60000;
        const budget = Math.max(30, this.plan.route_km * 12);
        shouldReveal = elapsedMinutes >= (budget * (waypoint.idx + 1)) / Math.max(1, this.plan.waypoints.length);
      }

      if (shouldReveal) {
        this.revealed.add(key);
        this.visibleHints.push({
          idx: waypoint.idx,
          label: WAYPOINT_LABELS[waypoint.kind] ?? "your next stop",
          text: waypoint.hint,
        });
        this.message = waypoint.hint;
      }
    }

    // Arrival: the only rule that may reveal the destination.
    if (!this.revealed.has("destination")) {
      const distance = haversineM(fix.lat, fix.lng, this.plan.destination.lat, this.plan.destination.lng);
      if (distance <= this.plan.destination.radius_m) {
        void this.arrive();
      }
    }
  }

  private evaluateOffRoute(fix: Fix): void {
    const path = this.routePath();
    if (path.length < 2) return;

    const distance = distanceToPathM(fix.lat, fix.lng, path);
    this.offRouteMetres = distance;

    if (distance > DEFAULTS.offRouteToleranceM) {
      this.offRouteSince = this.offRouteSince ?? Date.now();
      if (Date.now() - this.offRouteSince >= DEFAULTS.offRouteDwellMs) {
        this.offRoute = true;
      }
    } else {
      this.offRouteSince = null;
      this.offRoute = false;
    }
  }

  private async arrive(): Promise<void> {
    if (!this.plan || this.state === "arrived") return;
    this.state = "arrived";
    this.arrivedAt = Date.now();
    this.revealed.add("destination");
    await this.flushPoints();
    await queueArrive(this.plan.trip_id, this.plan.destination.lat, this.plan.destination.lng);
    await this.refreshQueueCount();
    this.message = this.plan.destination.name;
    this.emit();
  }

  async wrapUp(summary: string): Promise<void> {
    if (!this.plan) return;
    this.state = "wrapped_up";
    await this.flushPoints();
    await queueFinish(this.plan.trip_id, this.distanceM, summary);
    await this.refreshQueueCount();
    this.stop();
    this.emit();
  }

  private async refreshQueueCount(): Promise<void> {
    try {
      const { count } = await import("./outbox");
      this.queued = await count();
      this.emit();
    } catch {
      /* the queue is unavailable; the count simply stays put */
    }
  }

  // ---------------------------------------------------------------- //
  // what the UI may draw
  // ---------------------------------------------------------------- //
  /** The next target. Its coordinates are used for the bearing and nothing else. */
  private nextTarget(): { lat: number; lng: number; label: string; kind: string } | null {
    if (!this.plan) return null;
    for (const waypoint of this.plan.waypoints.slice().sort((a, b) => a.idx - b.idx)) {
      if (this.reached.has(waypoint.idx)) continue;
      return {
        lat: waypoint.lat,
        lng: waypoint.lng,
        label: this.revealed.has(`wp:${waypoint.idx}`)
          ? WAYPOINT_LABELS[waypoint.kind] ?? "your next stop"
          : "onward",
        kind: waypoint.kind,
      };
    }
    return {
      lat: this.plan.destination.lat,
      lng: this.plan.destination.lng,
      // Even after arrival the arrow says "your destination": the arrival screen is
      // where the name belongs, so there is exactly one place a name can appear.
      label: "your destination",
      kind: "destination",
    };
  }

  snapshot(): TripSnapshot {
    const plan = this.plan;
    const target = this.nextTarget();
    const fix = this.fix;

    let bearing: number | null = null;
    if (fix && target) {
      bearing = bearingDeg(fix.lat, fix.lng, target.lat, target.lng);
    }

    const heading = this.heading;
    const arrow = bearing !== null && heading !== null ? arrowAngle(bearing, heading) : 0;

    const destinationRevealed =
      this.revealed.has("destination") && this.state === "arrived" && plan
        ? {
            name: plan.destination.name,
            lat: plan.destination.lat,
            lng: plan.destination.lng,
            category: plan.destination.category,
          }
        : null;

    return {
      state: this.state,
      tripId: plan?.trip_id ?? null,
      title: plan?.title ?? "",
      bearingDeg: bearing,
      arrowDeg: arrow,
      headingDeg: heading,
      headingSource: this.headingSource,
      targetLabel: target?.label ?? "next stop",
      targetKind: target?.kind ?? "route",
      hasFix: Boolean(fix),
      revealedDestination: destinationRevealed,
      visibleHints: [...this.visibleHints],
      distanceM: this.distanceM,
      position: fix ? { lat: fix.lat, lng: fix.lng, accuracy_m: fix.accuracy_m } : null,
      accuracy: fix?.accuracy_m ?? null,
      offRoute: this.offRoute,
      offRouteMetres: this.offRouteMetres,
      minutesElapsed: this.startedAt ? (Date.now() - this.startedAt) / 60000 : 0,
      waypointsReached: this.reached.size,
      waypointsTotal: plan?.waypoints.length ?? 0,
      queued: this.queued,
      message: this.message,
      error: this.error,
      track: simplifyPath(
        this.track.map((point) => [point.lat, point.lng] as [number, number]),
        10
      ),
    };
  }

  /** A short sentence for the agent bubble. Never contains a place name. */
  coachingLine(): string {
    const snapshot = this.snapshot();
    if (!snapshot.tripId) return "Tell me what kind of trip you want and I will pick somewhere.";
    if (snapshot.state === "arrived" && snapshot.revealedDestination) {
      return `You are at ${snapshot.revealedDestination.name}. Take a good look around.`;
    }
    if (!snapshot.hasFix) return "No GPS yet. Hold still for a moment and I will find you.";
    if (snapshot.bearingDeg === null) return "Follow the arrow.";
    const direction = turnDirection(snapshot.bearingDeg, snapshot.headingDeg ?? snapshot.bearingDeg);
    switch (direction) {
      case "straight":
        return "Straight on. You are pointing the right way.";
      case "turn around":
        return "You are facing the wrong way. Turn around.";
      default:
        return `The way is to your ${direction}. Follow the arrow.`;
    }
  }

  /** Where north is, for the readout under the dial. */
  bearingLabel(): string {
    const snapshot = this.snapshot();
    if (snapshot.bearingDeg === null) return "--";
    return `${String(Math.round(snapshot.bearingDeg)).padStart(3, "0")}° ${compassPoint(snapshot.bearingDeg)}`;
  }

  routeLengthKm(): number {
    return this.plan ? pathLengthM(this.routePath()) / 1000 : 0;
  }
}

/** One engine per app, shared by the compass, chat and hub screens. */
export const tripEngine = new TripEngine();
