/**
 * The offline outbox, in IndexedDB.
 *
 * This is the browser/native twin of `phone-app/farfromhome_device/net/outbox.py`, and
 * it implements the same rules, because two clients that behave differently is worse
 * than one:
 *
 * * **one queue, strict order** — rows go out in `id` order, so a trip is always
 *   created before its points and a photo is never uploaded to a trip that does not
 *   exist yet. If the head row is backing off, everything behind it waits.
 * * **backoff** 2, 4, 8, 16, 32, then 60 seconds.
 * * **photos** are stored as blobs and uploaded last, after the metadata rows.
 * * **SOS bypasses the queue** — sent immediately with a short timeout, *and* queued.
 * * **idempotency** — every write carries a client-generated UUID, and the server now
 *   honours `photo_id` and `game_id`, so a retry cannot duplicate.
 * * **status handling** follows brief 03 §4: 2xx and 409 are final; 400/422 are
 *   dropped as unfixable; 401 stops the queue and asks for re-pairing; 404/408/425/429
 *   and 5xx retry.
 *
 * IndexedDB rather than localStorage because a photo is up to 2 MB and localStorage
 * is a synchronous 5 MB string store; storing images there would freeze the UI thread.
 */

import { apiUrl, serverBase } from "./server";
import { useAuthStore } from "./authToken";

const DB_NAME = "ffh-outbox";
const DB_VERSION = 1;
const STORE = "outbox";

/** 2, 4, 8, 16, 32, then 60 seconds. Same ladder as the Python client. */
export const BACKOFF_SEQUENCE = [2, 4, 8, 16, 32, 60];

export function backoffSeconds(attempts: number): number {
  return BACKOFF_SEQUENCE[Math.min(attempts, BACKOFF_SEQUENCE.length - 1)];
}

export type RowKind = "trip" | "points" | "reveal" | "photo" | "arrive" | "finish" | "sos" | "game";

export interface OutboxRow {
  id?: number;
  /** ISO timestamp, for the UI. */
  created_at: string;
  method: "POST" | "PUT" | "PATCH";
  path: string;
  kind: RowKind;
  ref_id?: string;
  body?: unknown;
  /** A photo blob. Stored inline; IndexedDB handles binary efficiently. */
  blob?: Blob;
  file_name?: string;
  form?: Record<string, string>;
  attempts: number;
  next_try: number;
  last_status?: number | null;
  last_error?: string | null;
}

export interface DrainResult {
  sent: number;
  remaining: number;
  stoppedFor?: "auth" | "offline" | "backoff" | "empty";
  lastError?: string;
}

// --------------------------------------------------------------------------- //
// the store
// --------------------------------------------------------------------------- //
let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("This browser has no IndexedDB, so the offline queue is unavailable."));
      return;
    }
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
        store.createIndex("next_try", "next_try", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Could not open the offline queue."));
  });
  return dbPromise;
}

async function withStore<T>(
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => IDBRequest
): Promise<T> {
  const db = await openDb();
  return new Promise<T>((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const store = transaction.objectStore(STORE);
    const request = work(store);
    request.onsuccess = () => resolve(request.result as T);
    request.onerror = () => reject(request.error ?? new Error("Offline queue error"));
  });
}

// --------------------------------------------------------------------------- //
// queue operations
// --------------------------------------------------------------------------- //
export async function enqueue(row: Omit<OutboxRow, "id" | "attempts" | "next_try" | "created_at"> & {
  created_at?: string;
}): Promise<number> {
  const record: OutboxRow = {
    ...row,
    created_at: row.created_at ?? new Date().toISOString(),
    attempts: 0,
    next_try: 0,
  };
  return withStore<number>("readwrite", (store) => store.add(record));
}

export async function peekAll(): Promise<OutboxRow[]> {
  const rows = await withStore<OutboxRow[]>("readonly", (store) => store.getAll());
  return rows.sort((a, b) => (a.id ?? 0) - (b.id ?? 0));
}

export async function count(): Promise<number> {
  return withStore<number>("readonly", (store) => store.count());
}

/** The oldest row, and only if it is due. Strict ordering: never skip ahead. */
export async function head(now = Date.now() / 1000): Promise<OutboxRow | null> {
  const rows = await peekAll();
  if (!rows.length) return null;
  const first = rows[0];
  return first.next_try <= now ? first : null;
}

export async function remove(id: number): Promise<void> {
  await withStore("readwrite", (store) => store.delete(id));
}

export async function reschedule(id: number, attempts: number, status?: number | null, error?: string, delayOverride?: number): Promise<number> {
  const delay = delayOverride ?? backoffSeconds(attempts);
  const rows = await peekAll();
  const row = rows.find((item) => item.id === id);
  if (!row) return delay;
  const updated: OutboxRow = {
    ...row,
    attempts: attempts + 1,
    next_try: Date.now() / 1000 + delay,
    last_status: status ?? null,
    last_error: (error ?? "").slice(0, 400),
  };
  await withStore("readwrite", (store) => store.put(updated));
  return delay;
}

export async function clearAll(): Promise<void> {
  await withStore("readwrite", (store) => store.clear());
}

/** Make everything due now: the "send the queue now" button. */
export async function resetBackoff(): Promise<void> {
  const rows = await peekAll();
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(STORE, "readwrite");
    const store = transaction.objectStore(STORE);
    for (const row of rows) store.put({ ...row, next_try: 0 });
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

// --------------------------------------------------------------------------- //
// classification (brief 03 §4, the same table the Python client uses)
// --------------------------------------------------------------------------- //
export type Outcome = "done" | "dropped" | "retry" | "unauthorised";

export function classify(status: number): Outcome {
  if (status >= 200 && status < 300) return "done";
  if (status === 401) return "unauthorised";
  if (status === 409) return "done"; // duplicate, or game_closed: final either way
  if (status === 404) return "retry"; // the trip may not exist yet
  if ([400, 403, 405, 410, 413, 415, 422].includes(status)) return "dropped";
  if ([408, 425, 429].includes(status) || status >= 500) return "retry";
  return "dropped";
}

// --------------------------------------------------------------------------- //
// sending
// --------------------------------------------------------------------------- //
async function sendRow(row: OutboxRow): Promise<{ outcome: Outcome; status?: number; message?: string; payload?: any }> {
  const token = useAuthStore.getState().deviceToken;
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (row.blob) {
    const form = new FormData();
    for (const [key, value] of Object.entries(row.form ?? {})) form.append(key, value);
    form.append("file", row.blob, row.file_name ?? "photo.jpg");
    body = form;
  } else if (row.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(row.body);
  }

  try {
    const response = await fetch(apiUrl(row.path), { method: row.method, headers, body });
    if (response.status === 401) {
      return { outcome: "unauthorised", status: 401, message: "the server rejected the device token" };
    }
    let payload: any = null;
    const text = await response.text();
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = { raw: text.slice(0, 300) };
      }
    }
    const outcome = classify(response.status);
    return {
      outcome,
      status: response.status,
      message: payload?.detail?.message ?? payload?.detail ?? payload?.message ?? "",
      payload,
    };
  } catch (error) {
    // A network failure is a retry, and it also means we are offline.
    return { outcome: "retry", message: String((error as any)?.message ?? error) };
  }
}

/**
 * Send rows until the queue is empty, the head is backing off, or a limit is hit.
 *
 * ``onProgress`` fires after each attempt so the UI can show the queue moving.
 */
export async function drain(
  options: { maxRows?: number; onProgress?: (result: DrainResult) => void } = {}
): Promise<DrainResult> {
  const maxRows = options.maxRows ?? 50;
  let sent = 0;
  let lastError: string | undefined;
  let stoppedFor: DrainResult["stoppedFor"] = "empty";

  for (let index = 0; index < maxRows; index += 1) {
    const row = await head();
    if (!row || row.id === undefined) {
      stoppedFor = index === 0 ? "empty" : "backoff";
      break;
    }

    const result = await sendRow(row);

    if (result.outcome === "done") {
      await remove(row.id);
      sent += 1;
      continue;
    }
    if (result.outcome === "dropped") {
      // Log it and move on: this request can never succeed, and blocking the queue
      // behind it would lose everything else.
      console.warn("[outbox] dropping unfixable row", row.path, result.status, result.message);
      await remove(row.id);
      continue;
    }
    if (result.outcome === "unauthorised") {
      stoppedFor = "auth";
      lastError = result.message;
      break;
    }

    const sosRow = row.kind === "sos";
    await reschedule(row.id, row.attempts, result.status ?? null, result.message, sosRow ? 15 : undefined);
    lastError = result.message;
    stoppedFor = "offline";
    break;
  }

  const remaining = await count();
  const summary: DrainResult = { sent, remaining, stoppedFor, lastError };
  options.onProgress?.(summary);
  return summary;
}

/** Queue one batch of GPS fixes. The body is a bare array, which the server accepts. */
export async function queuePoints(
  tripId: string,
  points: Array<{ lat: number; lng: number; t: string; acc: number }>
): Promise<number> {
  if (!points.length) return 0;
  return enqueue({
    method: "POST",
    path: `/api/device/trips/${tripId}/points`,
    kind: "points",
    ref_id: tripId,
    body: points,
  });
}

export async function queueTrip(plan: unknown & { trip_id: string }): Promise<number> {
  return enqueue({
    method: "POST",
    path: "/api/device/trips",
    kind: "trip",
    ref_id: plan.trip_id,
    body: plan,
  });
}

export async function queueReveal(tripId: string, idx: number): Promise<number> {
  return enqueue({
    method: "POST",
    path: `/api/device/trips/${tripId}/waypoints/${idx}/reveal`,
    kind: "reveal",
    ref_id: `${tripId}:${idx}`,
    body: { at: new Date().toISOString() },
  });
}

export async function queueArrive(tripId: string, lat: number, lng: number): Promise<number> {
  return enqueue({
    method: "POST",
    path: `/api/device/trips/${tripId}/arrive`,
    kind: "arrive",
    ref_id: tripId,
    body: { lat, lng, at: new Date().toISOString() },
  });
}

export async function queueFinish(tripId: string, distanceM: number, summary: string): Promise<number> {
  return enqueue({
    method: "POST",
    path: `/api/device/trips/${tripId}/finish`,
    kind: "finish",
    ref_id: tripId,
    body: { distance_m: Math.round(distanceM), summary },
  });
}

export async function queuePhoto(
  tripId: string,
  blob: Blob,
  meta: {
    photo_id: string;
    taken_at: string;
    lat?: number | null;
    lng?: number | null;
    member_name?: string;
    task_id?: string;
    game_id?: string;
    category?: string;
    caption?: string;
  },
  fileName = "photo.jpg"
): Promise<number> {
  const form: Record<string, string> = {};
  for (const [key, value] of Object.entries(meta)) {
    if (value === null || value === undefined || value === "") continue;
    form[key] = String(value);
  }
  return enqueue({
    method: "POST",
    path: `/api/device/trips/${tripId}/photos`,
    kind: "photo",
    ref_id: meta.photo_id,
    blob,
    file_name: fileName,
    form,
  });
}

/**
 * SOS: send immediately with a short timeout **and** queue a copy.
 *
 * Brief 03 §9 is explicit that SOS is the one thing that does not wait for the queue,
 * and that it repeats until the server confirms. Both happen here.
 */
export async function sendSos(
  body: { lat: number; lng: number; trip_id?: string | null; at: string }
): Promise<{ queued: boolean; delivered: boolean; message: string }> {
  await enqueue({ method: "POST", path: "/api/device/sos", kind: "sos", ref_id: "sos", body });

  const token = useAuthStore.getState().deviceToken;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(apiUrl("/api/device/sos"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (response.ok) {
      return { queued: true, delivered: true, message: "SOS sent to the server. Stay where you are if it is safe." };
    }
    return {
      queued: true,
      delivered: false,
      message: `The server answered ${response.status}. It is queued and will retry. Call for help directly.`,
    };
  } catch {
    return {
      queued: true,
      delivered: false,
      message: `Could not reach ${serverBase()}. It is queued and will retry every 15 seconds. Call for help directly.`,
    };
  } finally {
    clearTimeout(timer);
  }
}

export async function status(): Promise<{
  queued: number;
  head: OutboxRow | null;
  nextInSeconds: number | null;
  items: OutboxRow[];
}> {
  const rows = await peekAll();
  const first = rows[0] ?? null;
  return {
    queued: rows.length,
    head: first,
    nextInSeconds: first ? Math.max(0, Math.round(first.next_try - Date.now() / 1000)) : null,
    // The UI shows the first few; the blobs are not useful there.
    items: rows.slice(0, 12).map((row) => ({ ...row, blob: undefined })),
  };
}
