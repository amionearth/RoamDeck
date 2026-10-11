/**
 * Geodesy, ported from `phone-app/farfromhome_device/engine/geo.py`.
 *
 * Same formulas, same constants, same test values, so the web app and the handheld
 * agree on where north is. Pure functions only: no state, no DOM, no network.
 */

export const EARTH_RADIUS_M = 6371008.8;

export function toRadians(degrees: number): number {
  return (degrees * Math.PI) / 180;
}

export function toDegrees(radians: number): number {
  return (radians * 180) / Math.PI;
}

/** Fold any angle into [0, 360). */
export function normaliseDeg(angle: number): number {
  return ((angle % 360) + 360) % 360;
}

/** Fold any angle into (-180, 180]. */
export function normaliseSignedDeg(angle: number): number {
  const value = ((angle + 180) % 360) - 180;
  return value === -180 ? 180 : value;
}

/** Great-circle distance in metres. */
export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const phi1 = toRadians(lat1);
  const phi2 = toRadians(lat2);
  const dPhi = phi2 - phi1;
  const dLambda = toRadians(lon2 - lon1);
  const a =
    Math.sin(dPhi / 2) ** 2 +
    Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Initial great-circle bearing, clockwise from north. */
export function bearingDeg(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const phi1 = toRadians(lat1);
  const phi2 = toRadians(lat2);
  const dLambda = toRadians(lon2 - lon1);
  const y = Math.sin(dLambda) * Math.cos(phi2);
  const x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLambda);
  if (x === 0 && y === 0) return 0;
  return normaliseDeg(toDegrees(Math.atan2(y, x)));
}

/** `(bearing - heading) mod 360`: the rotation the compass needle draws. */
export function arrowAngle(bearing: number, heading: number): number {
  return normaliseDeg(bearing - heading);
}

const COMPASS_POINTS = [
  "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
] as const;

export function compassPoint(angle: number): string {
  return COMPASS_POINTS[Math.round(normaliseDeg(angle) / 22.5) % 16];
}

/** Is the target to the left, right, straight ahead, or behind? */
export function turnDirection(bearing: number, heading: number): "left" | "right" | "straight" | "turn around" {
  const delta = normaliseSignedDeg(bearing - heading);
  const magnitude = Math.abs(delta);
  if (magnitude <= 12) return "straight";
  if (magnitude >= 168) return "turn around";
  return delta > 0 ? "right" : "left";
}

/** Project a point `distanceM` along `bearing`. */
export function moveTowards(
  lat: number,
  lon: number,
  bearing: number,
  distanceM: number
): { lat: number; lng: number } {
  const angular = distanceM / EARTH_RADIUS_M;
  const phi1 = toRadians(lat);
  const lambda1 = toRadians(lon);
  const theta = toRadians(bearing);

  const sinPhi2 = Math.sin(phi1) * Math.cos(angular) + Math.cos(phi1) * Math.sin(angular) * Math.cos(theta);
  const phi2 = Math.asin(Math.max(-1, Math.min(1, sinPhi2)));
  const lambda2 =
    lambda1 +
    Math.atan2(
      Math.sin(theta) * Math.sin(angular) * Math.cos(phi1),
      Math.cos(angular) - Math.sin(phi1) * Math.sin(phi2)
    );

  const lng = ((toDegrees(lambda2) + 540) % 360) - 180;
  return { lat: toDegrees(phi2), lng };
}

export function interpolate(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
  fraction: number
): { lat: number; lng: number } {
  const t = Math.max(0, Math.min(1, fraction));
  return { lat: lat1 + (lat2 - lat1) * t, lng: lon1 + (lon2 - lon1) * t };
}

/** Distance from a point to a line segment, in metres, in a local flat frame. */
export function distanceToSegmentM(
  lat: number,
  lon: number,
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number
): number {
  const mPerDegLat = 111132;
  const mPerDegLon = Math.max(1e-6, 111320 * Math.cos(toRadians(lat)));

  const project = (pLat: number, pLon: number) => ({
    x: (pLon - lon) * mPerDegLon,
    y: (pLat - lat) * mPerDegLat,
  });

  const a = project(lat1, lon1);
  const b = project(lat2, lon2);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq < 1e-9) return Math.hypot(a.x, a.y);
  const t = Math.max(0, Math.min(1, (-a.x * dx + -a.y * dy) / lengthSq));
  const closestX = a.x + t * dx;
  const closestY = a.y + t * dy;
  return Math.hypot(closestX, closestY);
}

/** Shortest distance from a point to a polyline. */
export function distanceToPathM(lat: number, lon: number, path: Array<[number, number]>): number {
  if (!path.length) return Number.POSITIVE_INFINITY;
  if (path.length === 1) return haversineM(lat, lon, path[0][0], path[0][1]);
  let best = Number.POSITIVE_INFINITY;
  for (let index = 0; index < path.length - 1; index += 1) {
    const [lat1, lon1] = path[index];
    const [lat2, lon2] = path[index + 1];
    best = Math.min(best, distanceToSegmentM(lat, lon, lat1, lon1, lat2, lon2));
  }
  return best;
}

export function pathLengthM(path: Array<[number, number]>): number {
  let total = 0;
  for (let index = 0; index < path.length - 1; index += 1) {
    total += haversineM(path[index][0], path[index][1], path[index + 1][0], path[index + 1][1]);
  }
  return total;
}

/** Ramer-Douglas-Peucker, for drawing the track without thousands of points. */
export function simplifyPath(
  path: Array<[number, number]>,
  toleranceM = 8
): Array<[number, number]> {
  if (path.length < 3) return [...path];

  const rdp = (points: Array<[number, number]>): Array<[number, number]> => {
    const start = points[0];
    const end = points[points.length - 1];
    let worstDistance = -1;
    let worstIndex = 0;
    for (let index = 1; index < points.length - 1; index += 1) {
      const distance = distanceToSegmentM(
        points[index][0],
        points[index][1],
        start[0],
        start[1],
        end[0],
        end[1]
      );
      if (distance > worstDistance) {
        worstDistance = distance;
        worstIndex = index;
      }
    }
    if (worstDistance > toleranceM) {
      const left = rdp(points.slice(0, worstIndex + 1));
      const right = rdp(points.slice(worstIndex));
      return [...left.slice(0, -1), ...right];
    }
    return [start, end];
  };

  return rdp(path);
}
