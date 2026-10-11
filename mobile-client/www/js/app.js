/**
 * app.js – Main application entry point.
 *
 * Handles:
 *   • Startup / splash
 *   • Auth flow (login / register)
 *   • Page routing (bottom nav)
 *   • Page renderers delegated to page modules
 */

import {
  isLoggedIn, login, register, logout, getMe,
  getBaseUrl, getServerHost, setServerHost, checkHealth,
} from "./api.js";
import { connect as wsConnect, disconnect as wsDisconnect } from "./ws.js";

import "./pages/trip.js";                          // registers window.openTrip
import { renderFeed }        from "./pages/feed.js";
import { renderPassport }    from "./pages/passport.js";
import { renderGame, cleanupGame } from "./pages/game.js";
import { renderLeaderboard } from "./pages/leaderboard.js";
import { renderFriends }     from "./pages/friends.js";
import { renderProfile }     from "./pages/profile.js";

// ── Toast utility ─────────────────────────────────────────────────────────────

let toastTimer = null;
export function showToast(msg, duration = 2500) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.classList.add("hidden"), 300);
  }, duration);
}
window.showToast = showToast; // allow page modules to call it

// ── Page routing ──────────────────────────────────────────────────────────────

const PAGE_TITLES = {
  feed: "Explore",
  passport: "Passport",
  game: "Games",
  "active-game": "Game",
  leaderboard: "Leaderboard",
  friends: "Friends",
  profile: "Me",
  trip: "Trip",
};

let currentPage = "feed";
const pageHistory = [];

export function navigateTo(pageId, opts = {}) {
  if (pageId !== currentPage) {
    pageHistory.push(currentPage);
  }

  // Hide all pages
  document.querySelectorAll(".page").forEach((p) => {
    p.classList.remove("active");
    p.classList.add("hidden");
  });

  // Show target
  const target = document.getElementById(`page-${pageId}`);
  if (!target) return;
  target.classList.remove("hidden");
  target.classList.add("active", "page-enter");
  setTimeout(() => target.classList.remove("page-enter"), 300);

  currentPage = pageId;
  document.getElementById("page-title").textContent = PAGE_TITLES[pageId] || pageId;

  // Back button visibility
  const backBtn = document.getElementById("back-btn");
  backBtn.style.display = pageHistory.length > 0 && !["feed","passport","game","leaderboard","friends","profile"].includes(pageId)
    ? "flex" : "none";

  // Highlight active nav button
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.page === pageId);
  });

  // Trigger page renderer
  if (pageId === "feed")         renderFeed();
  if (pageId === "passport")     renderPassport();
  if (pageId === "game")         renderGame();
  if (pageId === "leaderboard")  renderLeaderboard();
  if (pageId === "friends")      renderFriends();
  if (pageId === "profile")      renderProfile();
}
window.navigateTo = navigateTo;

// ── Auth UI ───────────────────────────────────────────────────────────────────

function showAuthScreen() {
  document.getElementById("splash").classList.add("hidden");
  document.getElementById("main-app").classList.add("hidden");
  document.getElementById("auth-screen").classList.remove("hidden");
  document.getElementById("server-addr-display").textContent = getServerHost();
}

function showMainApp() {
  document.getElementById("splash").classList.add("hidden");
  document.getElementById("auth-screen").classList.add("hidden");
  document.getElementById("main-app").classList.remove("hidden");
  wsConnect();
  navigateTo("feed");
}

// Auth tab switching
document.querySelectorAll(".auth-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".auth-tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    document.getElementById("login-form").classList.toggle("hidden", tab !== "login");
    document.getElementById("register-form").classList.toggle("hidden", tab !== "register");
  });
});

// Login submit
document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  const errEl = document.getElementById("login-error");
  errEl.classList.add("hidden");

  try {
    await login(username, password);
    showMainApp();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

// Register submit
document.getElementById("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username     = document.getElementById("reg-username").value.trim();
  const display_name = document.getElementById("reg-display").value.trim();
  const password     = document.getElementById("reg-password").value;
  const home_city    = document.getElementById("reg-city").value.trim() || "Kozhikode";
  const errEl = document.getElementById("reg-error");
  errEl.classList.add("hidden");

  try {
    await register(username, display_name, password, home_city);
    showMainApp();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

// Server config modal
document.getElementById("change-server-btn").addEventListener("click", () => {
  document.getElementById("server-host-input").value = getServerHost();
  document.getElementById("server-modal").classList.remove("hidden");
});
document.getElementById("server-modal-cancel").addEventListener("click", () => {
  document.getElementById("server-modal").classList.add("hidden");
});
document.getElementById("server-modal-save").addEventListener("click", async () => {
  const host = document.getElementById("server-host-input").value.trim();
  if (!host) return;
  setServerHost(host);
  document.getElementById("server-addr-display").textContent = host;
  document.getElementById("server-modal").classList.add("hidden");
  // Quick health check
  try {
    await checkHealth();
    showToast("✅ Server reachable");
  } catch {
    showToast("⚠️ Server not reachable at " + host);
  }
});

// Bottom nav
document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    cleanupGame();
    navigateTo(btn.dataset.page);
  });
});

// Back button
document.getElementById("back-btn").addEventListener("click", () => {
  const prev = pageHistory.pop();
  if (prev) navigateTo(prev);
});

// ── Startup ───────────────────────────────────────────────────────────────────

async function boot() {
  // Small delay so splash renders first
  await new Promise((r) => setTimeout(r, 600));

  if (!isLoggedIn()) {
    // Try to reach server; non-blocking
    checkHealth().catch(() => {});
    showAuthScreen();
    return;
  }

  // Verify token is still valid
  try {
    await getMe();
    showMainApp();
  } catch {
    showAuthScreen();
  }
}

boot();
