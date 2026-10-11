/**
 * geo.js – GPS helpers wrapping Capacitor Geolocation with browser fallback.
 */

async function getCapacitorGeo() {
  try {
    const { Geolocation } = await import("https://cdn.jsdelivr.net/npm/@capacitor/geolocation@6/dist/esm/index.js");
    return Geolocation;
  } catch {
    return null;
  }
}

/**
 * Get current position once.
 * Returns { lat, lng, accuracy } or null on error.
 */
export async function getCurrentPosition() {
  const Geo = await getCapacitorGeo();

  if (Geo && window.Capacitor && !window.CapacitorStub) {
    try {
      await Geo.requestPermissions();
      const pos = await Geo.getCurrentPosition({ enableHighAccuracy: true, timeout: 15000 });
      return {
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
      };
    } catch (e) {
      console.warn("Capacitor GPS error:", e);
      return null;
    }
  }

  // Browser fallback
  return new Promise((resolve) => {
    if (!navigator.geolocation) { resolve(null); return; }
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 15000 }
    );
  });
}

/**
 * Watch position – calls callback({ lat, lng, accuracy }) on each update.
 * Returns a cleanup function.
 */
export async function watchPosition(callback) {
  const Geo = await getCapacitorGeo();

  if (Geo && window.Capacitor && !window.CapacitorStub) {
    try {
      await Geo.requestPermissions();
      const id = await Geo.watchPosition({ enableHighAccuracy: true }, (pos, err) => {
        if (err || !pos) return;
        callback({ lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy });
      });
      return () => Geo.clearWatch({ id });
    } catch {
      return () => {};
    }
  }

  if (!navigator.geolocation) return () => {};
  const id = navigator.geolocation.watchPosition(
    (p) => callback({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy }),
    null,
    { enableHighAccuracy: true }
  );
  return () => navigator.geolocation.clearWatch(id);
}
