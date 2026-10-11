/**
 * api.js – All HTTP calls to the RoamDeck FastAPI backend (port 8000).
 *
 * Communication model:
 *   • Auth token is a signed blob returned by POST /api/auth/login (or /register).
 *   • Token is stored in localStorage and sent as  Authorization: Bearer <token>
 *     on every request.  The httpOnly cookie the server also sets is not usable
 *     from a Capacitor native webview (different origin), so we rely on the header.
 *   • The server URL is configurable so users can point the app at any LAN host.
 */

// ── Server address ────────────────────────────────────────────────────────────

const DEFAULT_HOST = "192.168.1.100";   // change on first launch via Settings
const SERVER_PORT  = 8000;

export function getServerHost() {
  return localStorage.getItem("rdServerHost") || DEFAULT_HOST;
}
export function setServerHost(host) {
  localStorage.setItem("rdServerHost", host.trim());
}
export function getBaseUrl() {
  const h = getServerHost();
  // Support bare IPs, hostnames and full URLs
  if (h.startsWith("http://") || h.startsWith("https://")) return h.replace(/\/$/, "");
  return `http://${h}:${SERVER_PORT}`;
}
export function getWsUrl() {
  const base = getBaseUrl();
  return base.replace(/^http/, "ws") + "/ws";
}

// ── Token management ─────────────────────────────────────────────────────────

export function getToken()          { return localStorage.getItem("rdToken"); }
export function setToken(t)         { localStorage.setItem("rdToken", t); }
export function clearToken()        { localStorage.removeItem("rdToken"); }
export function isLoggedIn()        { return !!getToken(); }

// ── Core fetch wrapper ────────────────────────────────────────────────────────

/**
 * Low-level fetch with:
 *   - Base URL prepended automatically
 *   - Authorization header injected when a token exists
 *   - JSON body serialisation for objects
 *   - Throws a descriptive Error on non-2xx responses
 */
export async function apiFetch(path, options = {}) {
  const url = `${getBaseUrl()}${path}`;
  const headers = new Headers(options.headers || {});

  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  // Default content-type for JSON bodies
  if (options.body && typeof options.body === "object" && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }

  const res = await fetch(url, { ...options, headers });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {}
    throw new Error(detail);
  }

  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function login(username, password) {
  const data = await apiFetch("/api/auth/login", {
    method: "POST",
    body: { username, password },
  });
  if (data.token) setToken(data.token);
  return data;
}

export async function register(username, display_name, password, home_city = "Kozhikode") {
  const data = await apiFetch("/api/auth/register", {
    method: "POST",
    body: { username, display_name, password, home_city },
  });
  if (data.token) setToken(data.token);
  return data;
}

export async function logout() {
  try { await apiFetch("/api/auth/logout", { method: "POST" }); } catch {}
  clearToken();
}

// ── Current user ──────────────────────────────────────────────────────────────

export function getMe()                     { return apiFetch("/api/me"); }
export function updateMe(body)              { return apiFetch("/api/me", { method: "PATCH", body }); }
export function uploadAvatar(formData) {
  return apiFetch("/api/me/avatar", { method: "POST", body: formData });
}
export function getUserProfile(username)    { return apiFetch(`/api/users/${username}`); }
export function getUserSummary(username)    { return apiFetch(`/api/users/${username}/summary`); }
export function searchUsers(q)              { return apiFetch(`/api/users/search?q=${encodeURIComponent(q)}`); }

// ── Feed ──────────────────────────────────────────────────────────────────────

export function getFeed(cursor = null) {
  const qs = cursor ? `?cursor=${cursor}` : "";
  return apiFetch(`/api/feed${qs}`);
}

// ── Trips ─────────────────────────────────────────────────────────────────────

export function getTrip(id)          { return apiFetch(`/api/trips/${id}`); }
export function getTripPhotos(id)    { return apiFetch(`/api/trips/${id}/photos`); }
export function likeTrip(id)         { return apiFetch(`/api/trips/${id}/like`, { method: "POST" }); }
export function getTripComments(id)  { return apiFetch(`/api/trips/${id}/comments`); }
export function postComment(id, body, parent_id = null) {
  return apiFetch(`/api/trips/${id}/comments`, { method: "POST", body: { body, parent_id } });
}
export function reviewTrip(id, rating, body = "") {
  return apiFetch(`/api/trips/${id}/review`, { method: "POST", body: { rating, body } });
}

// ── Passport & Badges ─────────────────────────────────────────────────────────

export function getPassport()  { return apiFetch("/api/passport"); }
export function getBadges()    { return apiFetch("/api/badges"); }

// ── Leaderboard ───────────────────────────────────────────────────────────────

export function getLeaderboard(scope = "global", metric = "points") {
  return apiFetch(`/api/leaderboard?scope=${scope}&metric=${metric}`);
}

// ── Friends ───────────────────────────────────────────────────────────────────

export function getFriends()                    { return apiFetch("/api/friends"); }
export function sendFriendRequest(username_or_code) {
  return apiFetch("/api/friends/request", { method: "POST", body: { username_or_code } });
}
export function acceptFriend(friendship_id)     {
  return apiFetch(`/api/friends/${friendship_id}/accept`, { method: "POST" });
}
export function removeFriend(friendship_id)     {
  return apiFetch(`/api/friends/${friendship_id}`, { method: "DELETE" });
}

// ── Games (phone_games router prefix /api/g) ─────────────────────────────────

export function getGameInfo(code) {
  return apiFetch(`/api/g/${code}/info`);
}

export async function joinGame(code, display_name, player_id = null) {
  const body = player_id ? { player_id, display_name } : { display_name };
  const data = await apiFetch(`/api/g/${code}/join`, { method: "POST", body });
  if (data.token) localStorage.setItem("rdPlayerToken", data.token);
  return data;
}

export function getPlayerStatus(code) {
  const pt = localStorage.getItem("rdPlayerToken");
  return apiFetch(`/api/g/${code}/me`, {
    headers: { "X-Player-Token": pt || "" },
  });
}

export function getGameScoreboard(code) {
  return apiFetch(`/api/g/${code}/scoreboard`);
}

export function getGallery(code) {
  const pt = localStorage.getItem("rdPlayerToken");
  return apiFetch(`/api/g/${code}/gallery`, {
    headers: { "X-Player-Token": pt || "" },
  });
}

export function votePhoto(code, photo_id, vote) {
  const pt = localStorage.getItem("rdPlayerToken");
  return apiFetch(`/api/g/${code}/vote`, {
    method: "POST",
    body: { photo_id, vote },
    headers: { "X-Player-Token": pt || "" },
  });
}

/**
 * Submit a task photo. imageBlob is a Blob (from camera or file input).
 */
export async function submitTaskPhoto(code, task_id, imageBlob, caption = "") {
  const pt = localStorage.getItem("rdPlayerToken");
  const form = new FormData();
  form.append("task_id", task_id);
  form.append("photo", imageBlob, "photo.jpg");
  form.append("caption", caption);

  const url = `${getBaseUrl()}/api/g/${code}/submissions`;
  const headers = new Headers({ "X-Player-Token": pt || "" });
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(url, { method: "POST", body: form, headers });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export function startGame(code) {
  return apiFetch(`/api/g/${code}/start`, { method: "POST" });
}

// ── SOS ───────────────────────────────────────────────────────────────────────

export function getSosEvents() { return apiFetch("/api/sos/events"); }

// ── Health ────────────────────────────────────────────────────────────────────

export function checkHealth() { return apiFetch("/api/health"); }
