/**
 * game.js – Game lobby + active game page.
 *
 * Flow:
 *   1. User enters a 6-letter code → look up game info (GET /api/g/{code}/info)
 *   2. User enters display name → join (POST /api/g/{code}/join) → receives player token
 *   3. Active game page shows tasks, gallery, scoreboard
 *   4. Task photo submission uses Capacitor Camera
 *   5. Real-time updates via WebSocket subscription to game:<id>
 */

import {
  getGameInfo, joinGame, getPlayerStatus,
  getGameScoreboard, getGallery, votePhoto, submitTaskPhoto,
  getBaseUrl,
} from "../api.js";
import { takePhoto } from "../camera.js";
import { subscribe, unsubscribe, on, off } from "../ws.js";

let activeGameCode = null;    // currently joined game code
let activeGameId   = null;    // game UUID for WS subscription
let timerInterval  = null;    // countdown interval
let pendingTask    = null;    // task waiting for photo { task_id, text, points }

// ── Cleanup (called when leaving game pages) ─────────────────────────────────

export function cleanupGame() {
  clearInterval(timerInterval);
  timerInterval = null;
  if (activeGameId) {
    unsubscribe(`game:${activeGameId}`);
    off("game.started",     onGameStarted);
    off("game.submission",  onSubmission);
    off("game.player_joined", onPlayerJoined);
  }
  activeGameCode = null;
  activeGameId   = null;
}

// ── Lobby page ────────────────────────────────────────────────────────────────

export function renderGame() {
  // Show lobby panel
  document.getElementById("game-join-panel").classList.remove("hidden");
  document.getElementById("game-info-panel").classList.add("hidden");
  document.getElementById("game-code-input").value = "";
  document.getElementById("game-join-error").classList.add("hidden");
}

document.getElementById("game-look-btn").addEventListener("click", async () => {
  const code = document.getElementById("game-code-input").value.trim().toUpperCase();
  if (code.length < 4) { window.showToast("Enter a valid game code"); return; }

  const btn = document.getElementById("game-look-btn");
  btn.disabled = true;
  btn.textContent = "Looking up…";

  try {
    const info = await getGameInfo(code);
    showGameInfoPanel(code, info);
  } catch (e) {
    window.showToast("⚠️ " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Look Up Game";
  }
});

function showGameInfoPanel(code, info) {
  document.getElementById("game-join-panel").classList.add("hidden");
  const panel = document.getElementById("game-info-panel");
  panel.classList.remove("hidden");

  const typeLabel = { scavenger_hunt: "🔍 Scavenger Hunt", photo_race: "📸 Photo Race", trivia: "🧠 Trivia" }[info.type] || info.type;
  const statusLabel = { open:"⏳ Waiting", running:"▶️ Running", closed:"✅ Closed" }[info.status] || info.status;

  document.getElementById("game-info-header").innerHTML = `
    <div class="game-join-icon">🎮</div>
    <div class="game-code-big">${info.code}</div>
    <p>${typeLabel} · ${statusLabel}</p>
    ${info.params?.duration_s ? `<p style="color:var(--text2);font-size:0.85rem">${Math.round(info.params.duration_s/60)} min game</p>` : ""}
  `;

  const playersHtml = (info.players || []).map(p =>
    `<span class="player-chip"><span class="player-dot" style="background:${p.color}"></span>${escHtml(p.display_name)}</span>`
  ).join("") || `<p class="hint-text">No players yet</p>`;
  document.getElementById("game-players-list").innerHTML = `<div style="margin-bottom:14px">${playersHtml}</div>`;

  // Store for join
  document.getElementById("game-join-btn").dataset.code = code;
  document.getElementById("game-join-btn").dataset.status = info.status;
  document.getElementById("game-join-btn").disabled = info.status === "closed";
  document.getElementById("game-join-btn").textContent = info.status === "closed" ? "Game Over" : "Join Game";
}

document.getElementById("game-join-btn").addEventListener("click", async (e) => {
  const code = e.currentTarget.dataset.code;
  const name = document.getElementById("game-player-name").value.trim();
  const errEl = document.getElementById("game-join-error");
  errEl.classList.add("hidden");

  if (!name) { window.showToast("Enter your display name"); return; }

  e.currentTarget.disabled = true;
  e.currentTarget.textContent = "Joining…";

  try {
    const res = await joinGame(code, name);
    activeGameCode = code;
    window.navigateTo("active-game");
    loadActiveGame(code, res);
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
    e.currentTarget.disabled = false;
    e.currentTarget.textContent = "Join Game";
  }
});

// ── Active game page ──────────────────────────────────────────────────────────

async function loadActiveGame(code, joinRes) {
  // Player badge
  document.getElementById("game-player-badge").innerHTML =
    `<span style="color:${joinRes.color}">${escHtml(joinRes.display_name)}</span>`;

  // Load full status
  try {
    const status = await getPlayerStatus(code);
    activeGameId = status.game_status ? joinRes.player_id : null;

    // Find game id from scoreboard (we need it for WS)
    const sb = await getGameScoreboard(code);
    // Extract game id by re-fetching game info
    const info = await getGameInfo(code);
    activeGameId = info.game_id;

    // WS subscription
    subscribe(`game:${activeGameId}`);
    on("game.started",      onGameStarted);
    on("game.submission",   onSubmission);
    on("game.player_joined", onPlayerJoined);

    renderGameStatus(status, sb);
    startTimer(status);
    renderTasks(status.tasks || [], code);
    loadScoreboard(code);
  } catch (e) {
    window.showToast("⚠️ " + e.message);
  }
}

function renderGameStatus(status, sb) {
  const label = { open:"Waiting to start", running:"Game running!", closed:"Game over" }[status.game_status] || status.game_status;
  document.getElementById("game-status-label").textContent = label;
  const myPts = (sb?.scoreboard || []).find(r => r.player_id === status.player?.id)?.points || status.player?.score || 0;
  document.getElementById("game-my-score").textContent = `${myPts} pts`;
}

function startTimer(status) {
  clearInterval(timerInterval);
  const timerEl = document.getElementById("game-timer");

  if (status.game_status === "closed") { timerEl.textContent = "Done"; return; }
  if (!status.ends_at) { timerEl.textContent = "--:--"; return; }

  const endsAt = new Date(status.ends_at).getTime();

  timerInterval = setInterval(() => {
    const rem = Math.max(0, endsAt - Date.now());
    const m = Math.floor(rem / 60000);
    const s = Math.floor((rem % 60000) / 1000);
    timerEl.textContent = `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
    if (rem <= 0) {
      clearInterval(timerInterval);
      document.getElementById("game-status-label").textContent = "Time's up!";
    }
  }, 1000);
}

function renderTasks(tasks, code) {
  const list = document.getElementById("task-list");
  if (!tasks.length) {
    list.innerHTML = `<p class="hint-text">No tasks available.</p>`;
    return;
  }

  const icons = { pending:"🔲", approved:"✅", rejected:"❌", submitted:"⏳" };

  list.innerHTML = tasks.map(t => {
    const icon = icons[t.status] || "🔲";
    const done = ["approved","submitted"].includes(t.status);
    return `
      <div class="task-card" data-task-id="${t.id}">
        <div class="task-status-icon">${icon}</div>
        <div class="task-body">
          <div class="task-text">${escHtml(t.text)}</div>
          <div class="task-meta">+${t.points} pts${t.time_limit_s ? ` · ${t.time_limit_s}s` : ""}</div>
        </div>
        <button class="task-submit-btn" data-task-id="${t.id}" data-task-text="${escHtml(t.text)}" data-task-pts="${t.points}"
          ${done ? "disabled" : ""}>${done ? "Done" : "📷"}</button>
      </div>`;
  }).join("");

  list.querySelectorAll(".task-submit-btn:not([disabled])").forEach(btn => {
    btn.addEventListener("click", () => openPhotoModal(code, {
      task_id:   btn.dataset.taskId,
      text:      btn.dataset.taskText,
      points:    btn.dataset.taskPts,
    }));
  });
}

async function loadScoreboard(code) {
  const list = document.getElementById("scoreboard-list");
  list.innerHTML = `<p class="hint-text">Loading…</p>`;
  try {
    const data = await getGameScoreboard(code);
    list.innerHTML = (data.scoreboard || []).map(r => {
      const rankClass = r.rank === 1 ? "top1" : r.rank === 2 ? "top2" : r.rank === 3 ? "top3" : "";
      return `
        <div class="score-row">
          <div class="score-rank ${rankClass}">${r.rank}</div>
          <span class="player-dot" style="background:${r.color};width:10px;height:10px;border-radius:50%;display:inline-block"></span>
          <div class="score-name">${escHtml(r.display_name)}</div>
          <div class="score-pts">${r.points} pts</div>
        </div>`;
    }).join("");
  } catch (e) {
    list.innerHTML = `<p class="hint-text" style="color:var(--accent2)">⚠️ ${e.message}</p>`;
  }
}

async function loadGallery(code) {
  const grid = document.getElementById("gallery-list");
  const base = getBaseUrl();
  grid.innerHTML = `<p class="hint-text">Loading…</p>`;
  try {
    const items = await getGallery(code);
    if (!items.length) { grid.innerHTML = `<p class="hint-text">Nothing to vote on right now.</p>`; return; }
    grid.innerHTML = items.map(item => `
      <div class="gallery-item" data-photo-id="${item.photo_id}">
        <img src="${base}${item.file_url}" alt="" loading="lazy" />
        <div class="gallery-item-body">
          <div class="gallery-item-meta">📷 ${escHtml(item.player_name)} · ${escHtml(item.task_text)}</div>
          ${item.caption ? `<p style="font-size:0.85rem;margin-bottom:8px">${escHtml(item.caption)}</p>` : ""}
          <div class="gallery-vote-btns">
            <button class="vote-approve" data-id="${item.photo_id}">✅ Approve</button>
            <button class="vote-reject"  data-id="${item.photo_id}">❌ Reject</button>
          </div>
        </div>
      </div>`).join("");

    grid.querySelectorAll(".vote-approve").forEach(btn =>
      btn.addEventListener("click", () => castVote(code, btn.dataset.id, "approve", btn)));
    grid.querySelectorAll(".vote-reject").forEach(btn =>
      btn.addEventListener("click", () => castVote(code, btn.dataset.id, "reject", btn)));
  } catch (e) {
    grid.innerHTML = `<p class="hint-text" style="color:var(--accent2)">⚠️ ${e.message}</p>`;
  }
}

async function castVote(code, photoId, vote, btn) {
  try {
    await votePhoto(code, photoId, vote);
    const card = btn.closest(".gallery-item");
    card.style.opacity = "0.4";
    card.querySelectorAll("button").forEach(b => b.disabled = true);
    window.showToast(vote === "approve" ? "✅ Approved" : "❌ Rejected");
  } catch (e) { window.showToast("⚠️ " + e.message); }
}

// ── Game tab switching ────────────────────────────────────────────────────────

document.querySelectorAll(".tab-btn[data-gtab]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn[data-gtab]").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.gtab;
    document.querySelectorAll(".gtab").forEach(g => {
      g.classList.toggle("hidden",  g.id !== `gtab-${tab}`);
      g.classList.toggle("active",  g.id === `gtab-${tab}`);
    });
    if (tab === "gallery" && activeGameCode) loadGallery(activeGameCode);
    if (tab === "scores"  && activeGameCode) loadScoreboard(activeGameCode);
  });
});

document.getElementById("gallery-refresh-btn").addEventListener("click", () => {
  if (activeGameCode) loadGallery(activeGameCode);
});
document.getElementById("scores-refresh-btn").addEventListener("click", () => {
  if (activeGameCode) loadScoreboard(activeGameCode);
});

// ── Photo submission modal ────────────────────────────────────────────────────

function openPhotoModal(code, task) {
  pendingTask = task;
  document.getElementById("photo-modal-task-title").textContent = `📷 ${task.text}`;
  document.getElementById("photo-preview").classList.add("hidden");
  document.getElementById("photo-preview").src = "";
  document.getElementById("photo-caption").value = "";
  document.getElementById("photo-submit-btn").disabled = true;
  document.getElementById("photo-submit-error").classList.add("hidden");
  document.getElementById("photo-modal").classList.remove("hidden");
  document.getElementById("photo-modal").dataset.code = code;
}

document.getElementById("take-photo-btn").addEventListener("click", async () => {
  try {
    const blob = await takePhoto();
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const prev = document.getElementById("photo-preview");
    prev.src = url;
    prev.classList.remove("hidden");
    document.getElementById("photo-submit-btn").disabled = false;
    // Store blob on button for submit handler
    document.getElementById("photo-submit-btn")._blob = blob;
  } catch (e) { window.showToast("⚠️ " + e.message); }
});

document.getElementById("photo-modal-cancel").addEventListener("click", () => {
  document.getElementById("photo-modal").classList.add("hidden");
  pendingTask = null;
});

document.getElementById("photo-submit-btn").addEventListener("click", async (e) => {
  const blob = e.currentTarget._blob;
  const code = document.getElementById("photo-modal").dataset.code;
  if (!blob || !pendingTask) return;

  const caption = document.getElementById("photo-caption").value.trim();
  const errEl = document.getElementById("photo-submit-error");
  errEl.classList.add("hidden");
  e.currentTarget.disabled = true;
  e.currentTarget.textContent = "Uploading…";

  try {
    await submitTaskPhoto(code, pendingTask.task_id, blob, caption);
    document.getElementById("photo-modal").classList.add("hidden");
    window.showToast("✅ Photo submitted!");

    // Mark task as submitted in UI
    const taskCard = document.querySelector(`[data-task-id="${pendingTask.task_id}"]`);
    if (taskCard) {
      taskCard.querySelector(".task-status-icon").textContent = "⏳";
      taskCard.querySelector(".task-submit-btn").disabled = true;
      taskCard.querySelector(".task-submit-btn").textContent = "Sent";
    }
    pendingTask = null;
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
    e.currentTarget.disabled = false;
    e.currentTarget.textContent = "Submit";
  }
});

// ── WebSocket event handlers ──────────────────────────────────────────────────

function onGameStarted(data) {
  document.getElementById("game-status-label").textContent = "▶️ Game running!";
  startTimer({ game_status: "running", ends_at: data.ends_at });
  window.showToast("🎮 Game started!");
}

function onSubmission(data) {
  // Update task icon if it's ours (player_name match is rough; server sends player_id)
  window.showToast(`📸 ${data.player_name} submitted a photo`);
  if (activeGameCode) loadScoreboard(activeGameCode);
}

function onPlayerJoined(data) {
  window.showToast(`👋 ${data.player.display_name} joined`);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
