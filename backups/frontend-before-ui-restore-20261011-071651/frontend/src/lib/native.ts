/**
 * Native capabilities, behind one small interface that degrades to the web API.
 *
 * The point of this module: the app must behave identically in three places.
 *
 * | Where | Geolocation | Compass | Camera | Storage |
 * |---|---|---|---|---|
 * | Android/iOS (Capacitor) | `@capacitor/geolocation` | `@capacitor/motion` | `@capacitor/camera` | Preferences |
 * | Phone browser (same WiFi) | `navigator.geolocation` | `deviceorientation` | `<input capture>` | localStorage |
 * | Laptop browser (`vite dev`) | `navigator.geolocation` | `deviceorientation` | `<input capture>` | localStorage |
 *
 * Every plugin is imported **dynamically**, so the web build never pulls in native
 * code and a missing plugin degrades to the web path instead of throwing at import
 * time. That is also what lets the whole app run in a plain desktop browser for
 * testing, which is how the screens were developed.
 */

export type Platform = "web" | "android" | "ios";

interface CapacitorGlobal {
  isNativePlatform?: () => boolean;
  getPlatform?: () => string;
}

const capacitor = (globalThis as any)?.Capacitor as CapacitorGlobal | undefined;

export function isNative(): boolean {
  return Boolean(capacitor?.isNativePlatform?.() ?? false);
}

export function platform(): Platform {
  if (!isNative()) return "web";
  const value = capacitor?.getPlatform?.() ?? "web";
  if (value === "android") return "android";
  if (value === "ios") return "ios";
  return "web";
}

export function platformLabel(): string {
  switch (platform()) {
    case "android":
      return "Android";
    case "ios":
      return "iOS";
    default:
      return "Web";
  }
}

/** True when the app is running inside a wrapped native shell. */
export const native = { isNative, platform, platformLabel };

// --------------------------------------------------------------------------- //
// geolocation
// --------------------------------------------------------------------------- //
export interface Fix {
  lat: number;
  lng: number;
  accuracy_m: number;
  speed_mps?: number | null;
  /** Degrees clockwise from north, when the device reports it. */
  heading_deg?: number | null;
  altitude_m?: number | null;
  at: number;
}

export interface GeoOptions {
  /** Default is high accuracy: this is a walking app and 100 m is useless. */
  enableHighAccuracy?: boolean;
  timeoutMs?: number;
  maximumAgeMs?: number;
}

export async function requestLocationPermission(): Promise<boolean> {
  if (isNative()) {
    try {
      const { Geolocation } = await import("@capacitor/geolocation");
      const status = await Geolocation.requestPermissions();
      return status.location === "granted" || status.coarseLocation === "granted";
    } catch {
      /* fall through to the web path */
    }
  }
  if (typeof navigator === "undefined" || !navigator.geolocation) return false;
  // The web API has no permission query that works everywhere; a single fix is the
  // reliable test, and it is what actually matters.
  try {
    await getCurrentFix({ timeoutMs: 8000 });
    return true;
  } catch {
    return false;
  }
}

function toFix(position: {
  coords: {
    latitude: number;
    longitude: number;
    accuracy: number;
    speed?: number | null;
    heading?: number | null;
    altitude?: number | null;
  };
  timestamp: number;
}): Fix {
  return {
    lat: position.coords.latitude,
    lng: position.coords.longitude,
    accuracy_m: position.coords.accuracy ?? 999,
    speed_mps: position.coords.speed ?? null,
    heading_deg: position.coords.heading ?? null,
    altitude_m: position.coords.altitude ?? null,
    at: position.timestamp || Date.now(),
  };
}

export async function getCurrentFix(options: GeoOptions = {}): Promise<Fix> {
  const highAccuracy = options.enableHighAccuracy ?? true;
  const timeout = options.timeoutMs ?? 12000;

  if (isNative()) {
    try {
      const { Geolocation } = await import("@capacitor/geolocation");
      const position = await Geolocation.getCurrentPosition({
        enableHighAccuracy: highAccuracy,
        timeout,
        maximumAge: options.maximumAgeMs ?? 0,
      });
      return toFix(position as any);
    } catch (error) {
      // A denied permission is a real answer, not something to retry on the web path.
      throw new Error(describeGeoError(error));
    }
  }

  if (typeof navigator === "undefined" || !navigator.geolocation) {
    throw new Error("This device has no location service.");
  }
  return new Promise<Fix>((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (position) => resolve(toFix(position as any)),
      (error) => reject(new Error(describeGeoError(error))),
      {
        enableHighAccuracy: highAccuracy,
        timeout,
        maximumAge: options.maximumAgeMs ?? 0,
      }
    );
  });
}

/**
 * Watch the position. Returns a stop function.
 *
 * `onError` is called with a human sentence rather than an exception, because a lost
 * fix mid-walk is normal (brief §12) and the compass must freeze, not crash.
 */
export async function watchFixes(
  onFix: (fix: Fix) => void,
  onError?: (message: string) => void,
  options: GeoOptions = {}
): Promise<() => void> {
  const highAccuracy = options.enableHighAccuracy ?? true;

  if (isNative()) {
    try {
      const { Geolocation } = await import("@capacitor/geolocation");
      const id = await Geolocation.watchPosition(
        {
          enableHighAccuracy: highAccuracy,
          timeout: options.timeoutMs ?? 15000,
          maximumAge: options.maximumAgeMs ?? 0,
        },
        (position, err) => {
          if (err) {
            onError?.(describeGeoError(err));
            return;
          }
          if (position) onFix(toFix(position as any));
        }
      );
      return () => {
        void Geolocation.clearWatch({ id });
      };
    } catch (error) {
      onError?.(describeGeoError(error));
      // Fall through to the web implementation rather than giving up.
    }
  }

  if (typeof navigator === "undefined" || !navigator.geolocation) {
    onError?.("This device has no location service.");
    return () => undefined;
  }

  const id = navigator.geolocation.watchPosition(
    (position) => onFix(toFix(position as any)),
    (error) => onError?.(describeGeoError(error)),
    { enableHighAccuracy: highAccuracy, timeout: 15000, maximumAge: 0 }
  );
  return () => navigator.geolocation.clearWatch(id);
}

function describeGeoError(error: unknown): string {
  const message = String((error as any)?.message ?? error ?? "");
  const code = (error as any)?.code;
  if (/denied|permission/i.test(message) || code === 1) {
    return "Location permission was refused. Turn it on for FarFromHome in the phone's settings.";
  }
  if (/unavailable|position/i.test(message) || code === 2) {
    return "No location yet. Step outside or wait a moment for the satellites.";
  }
  if (/timeout/i.test(message) || code === 3) {
    return "The location fix is taking too long. Keep the phone still.";
  }
  return message || "The location service is not answering.";
}

// --------------------------------------------------------------------------- //
// compass / motion
// --------------------------------------------------------------------------- //
export interface HeadingSample {
  /** Degrees clockwise from north, or null when the device cannot tell. */
  heading_deg: number | null;
  /** Raw rotation rates in degrees per second, when available. */
  gyro_z?: number | null;
  at: number;
}

export interface CompassHandle {
  stop: () => void;
  /** True when the device actually produced an orientation. */
  supported: boolean;
  source: string;
}

/**
 * Watch the device compass.
 *
 * Preferred source is the OS fused heading (Capacitor Motion on native, and
 * `deviceorientationabsolute` on the web, which iOS and Android both provide as a
 * proper magnetic heading). The plain `deviceorientation` event on iOS gives
 * `webkitCompassHeading`, which is also a real heading, so it is the second choice.
 * A raw `alpha` value is the last resort and is relative, not absolute — the UI says
 * so rather than pretending it is a compass.
 */
export async function watchHeading(
  onSample: (sample: HeadingSample) => void,
  onError?: (message: string) => void
): Promise<CompassHandle> {
  if (isNative()) {
    try {
      const { Motion } = await import("@capacitor/motion");
      let sawHeading = false;
      const handler = (event: any) => {
        const rotation = event?.rotationRate ?? event?.accelerationIncludingGravity;
        const absolute = (globalThis as any)?.webkitCompassHeading as number | undefined;
        const alpha = event?.alpha;
        let heading: number | null = null;
        if (typeof absolute === "number") {
          heading = absolute;
        } else if (typeof alpha === "number") {
          heading = (360 - alpha) % 360;
        }
        if (heading !== null) sawHeading = true;
        onSample({
          heading_deg: heading,
          gyro_z: typeof rotation?.alpha === "number" ? rotation.alpha : null,
          at: Date.now(),
        });
      };
      await Motion.addListener("orientation", handler);
      return {
        supported: true,
        source: "device",
        stop: () => {
          void Motion.removeAllListeners();
          if (!sawHeading) {
            onError?.("This phone did not report a compass heading.");
          }
        },
      };
    } catch (error) {
      onError?.(String((error as any)?.message ?? error));
      /* fall through to the web path */
    }
  }

  if (typeof window === "undefined" || !("DeviceOrientationEvent" in window)) {
    onError?.("This device has no compass.");
    return { supported: false, source: "none", stop: () => undefined };
  }

  // iOS 13+ requires an explicit, user-gesture-initiated permission request.
  const requestPermission = (DeviceOrientationEvent as any)?.requestPermission;
  if (typeof requestPermission === "function") {
    try {
      const answer = await requestPermission();
      if (answer !== "granted") {
        onError?.("Compass permission was refused. Turn on Motion & Fitness for FarFromHome.");
        return { supported: false, source: "none", stop: () => undefined };
      }
    } catch {
      /* the browser will simply not fire events */
    }
  }

  let source = "relative";
  const handler = (event: DeviceOrientationEvent) => {
    const webkit = (event as any).webkitCompassHeading as number | undefined;
    let heading: number | null = null;
    if (typeof webkit === "number") {
      heading = webkit;
      source = "magnetic";
    } else if (typeof event.alpha === "number") {
      // `alpha` is counter-clockwise from north; the app wants clockwise.
      heading = (360 - event.alpha) % 360;
    }
    onSample({ heading_deg: heading, gyro_z: null, at: Date.now() });
  };

  // Prefer the absolute event when the browser offers it.
  const useAbsolute = "ondeviceorientationabsolute" in window;
  const eventName = useAbsolute ? "deviceorientationabsolute" : "deviceorientation";
  if (useAbsolute) source = "absolute";
  window.addEventListener(eventName as any, handler as any, true);

  return {
    supported: true,
    source,
    stop: () => window.removeEventListener(eventName as any, handler as any, true),
  };
}

// --------------------------------------------------------------------------- //
// camera
// --------------------------------------------------------------------------- //
export interface CapturedPhoto {
  /** Full-quality data URL, ready to compress and upload. */
  dataUrl: string;
  /** A smaller version for the thumbnail strip. */
  thumbnailDataUrl?: string;
  format: string;
}

/**
 * Take a photo.
 *
 * Native uses the OS camera (which is faster, uses the real flash, and works when the
 * WebView has no `capture` support); the web path uses a file input with
 * `capture="environment"`, which is what the phone game page already does and which
 * opens the camera directly on both mobile platforms.
 */
export async function takePhoto(options: { source?: "camera" | "gallery" } = {}): Promise<CapturedPhoto> {
  const wantGallery = options.source === "gallery";

  if (isNative()) {
    try {
      const { Camera, CameraResultType, CameraSource } = await import("@capacitor/camera");
      const photo = await Camera.getPhoto({
        quality: 90,
        allowEditing: false,
        resultType: CameraResultType.DataUrl,
        source: wantGallery ? CameraSource.Photos : CameraSource.Camera,
        width: 1920,
        correctOrientation: true,
      });
      if (!photo.dataUrl) throw new Error("The camera returned no image.");
      return { dataUrl: photo.dataUrl, format: photo.format ?? "jpeg" };
    } catch (error) {
      const message = String((error as any)?.message ?? error);
      if (/cancel/i.test(message)) throw new Error("cancelled");
      if (/permission|denied/i.test(message)) {
        throw new Error("Camera permission was refused. Turn it on for FarFromHome in the phone's settings.");
      }
      throw new Error(message || "The camera is not available.");
    }
  }

  return new Promise<CapturedPhoto>((resolve, reject) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    if (!wantGallery) input.capture = "environment" as any;
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) {
        reject(new Error("cancelled"));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => resolve({ dataUrl: String(reader.result), format: file.type });
      reader.onerror = () => reject(new Error("Could not read the photo."));
      reader.readAsDataURL(file);
    };
    input.click();
  });
}

// --------------------------------------------------------------------------- //
// network state
// --------------------------------------------------------------------------- //
export interface NetworkState {
  connected: boolean;
  connectionType: string;
}

/** Current connectivity, and a subscription. Used by the offline banner. */
export async function getNetworkState(): Promise<NetworkState> {
  if (isNative()) {
    try {
      const { Network } = await import("@capacitor/network");
      const status = await Network.getStatus();
      return { connected: status.connected, connectionType: status.connectionType };
    } catch {
      /* fall through */
    }
  }
  return { connected: typeof navigator === "undefined" ? true : navigator.onLine, connectionType: "unknown" };
}

export async function watchNetwork(onChange: (state: NetworkState) => void): Promise<() => void> {
  if (isNative()) {
    try {
      const { Network } = await import("@capacitor/network");
      const handle = await Network.addListener("networkStatusChange", (status) =>
        onChange({ connected: status.connected, connectionType: status.connectionType })
      );
      return () => {
        void handle.remove();
      };
    } catch {
      /* fall through */
    }
  }
  if (typeof window === "undefined") return () => undefined;
  const online = () => onChange({ connected: true, connectionType: "unknown" });
  const offline = () => onChange({ connected: false, connectionType: "none" });
  window.addEventListener("online", online);
  window.addEventListener("offline", offline);
  return () => {
    window.removeEventListener("online", online);
    window.removeEventListener("offline", offline);
  };
}

// --------------------------------------------------------------------------- //
// small niceties
// --------------------------------------------------------------------------- //
export async function tapFeedback(style: "light" | "medium" | "heavy" = "light"): Promise<void> {
  if (!isNative()) {
    try {
      navigator.vibrate?.(style === "heavy" ? 60 : style === "medium" ? 35 : 15);
    } catch {
      /* not supported */
    }
    return;
  }
  try {
    const { Haptics, ImpactStyle } = await import("@capacitor/haptics");
    const map = { light: ImpactStyle.Light, medium: ImpactStyle.Medium, heavy: ImpactStyle.Heavy };
    await Haptics.impact({ style: map[style] });
  } catch {
    /* not fatal */
  }
}

export async function toast(message: string): Promise<void> {
  if (isNative()) {
    try {
      const { Toast } = await import("@capacitor/toast");
      await Toast.show({ text: message, duration: "short" });
      return;
    } catch {
      /* fall through */
    }
  }
  // The web has no toast; the UI's own banner shows the message instead.
}

export async function shareSomething(payload: {
  title: string;
  text: string;
  url?: string;
  dialogTitle?: string;
}): Promise<boolean> {
  if (isNative()) {
    try {
      const { Share } = await import("@capacitor/share");
      await Share.share({
        title: payload.title,
        text: payload.text,
        url: payload.url,
        dialogTitle: payload.dialogTitle ?? payload.title,
      });
      return true;
    } catch {
      /* the user cancelled, or the plugin is missing */
    }
  }
  try {
    if (navigator.share) {
      await navigator.share({ title: payload.title, text: payload.text, url: payload.url });
      return true;
    }
  } catch {
    /* cancelled */
  }
  return false;
}
