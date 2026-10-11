/**
 * feed.js – Social feed page.
 * Renders trip cards from GET /api/feed with like/comment.
 */

import { getFeed, likeTrip, getBaseUrl } from "../api.js";

let nextCursor = null;
let loading = false;

export async function renderFeed(reset = true) {
  const list = document.getElementById("feed-list");
  const loadMoreBtn = document.getElementById("feed-load-more");

  if (reset) {
    nextCursor = null;
    list.innerHTML = skeletons();
  }

  if (loading) return;
  loading = true;

  try {
    const data = await getFeed(reset ? null : nextCursor);
    if (reset) list.innerHTML = "";

    if (!data.items || data.items.length === 0) {
      if (reset) list.innerHTML = `<p style="text-align:center;color:var(--text2);padding:40px 0">No trips yet. Be the first explorer!</p>`;
      loadMoreBtn.style.display = "none";
      return;
    }

    data.items.forEach((trip) => {
      list.appendChild(buildTripCard(trip));
    });

    nextCursor = data.next_cursor;
    loadMoreBtn.style.display = nextCursor ? "block" : "none";
  } catch (err) {
    list.innerHTML = `<p style="text-align:center;color:var(--accent2);padding:40px 0">⚠️ ${err.message}</p>`;
  } finally {
    loading = false;
  }
}

function buildTripCard(trip) {
  const base = getBaseUrl();
  const card = document.createElement("div");
  card.className = "trip-card";

  const cover = trip.cover_photo_id
    ? `<img class="trip-card-img" src="${base}/api/trips/${trip.id}/photos/${trip.cover_photo_id}/thumb" alt="cover" loading="lazy" />`
    : `<div class="trip-card-img-placeholder">🗺️</div>`;

  const destText = trip.destination_revealed
    ? `<span class="trip-card-destination revealed">📍 ${trip.destination || "Revealed!"}</span>`
    : `<span class="trip-card-destination">🔒 Destination hidden</span>`;

  const members = Array.isArray(trip.members)
    ? trip.members.map((m) => m.display_name || m.username).join(", ")
    : "";

  card.innerHTML = `
    ${cover}
    <div class="trip-card-body">
      <div class="trip-card-title">${escHtml(trip.title || "Unnamed Trip")}</div>
      <div class="trip-card-meta">${members ? `👥 ${escHtml(members)}` : ""}${trip.status ? ` · ${badge(trip.status)}` : ""}</div>
      ${destText}
      <div class="trip-card-actions">
        <button class="like-btn${trip.liked ? " liked" : ""}" data-id="${trip.id}" data-count="${trip.likes_count || 0}">
          ${trip.liked ? "❤️" : "🤍"} <span>${trip.likes_count || 0}</span>
        </button>
        <button class="btn btn-ghost btn-sm" data-open-trip="${trip.id}">💬 View</button>
      </div>
    </div>
  `;

  // Like button
  card.querySelector(".like-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const id = btn.dataset.id;
    try {
      const res = await likeTrip(id);
      btn.classList.toggle("liked", res.liked);
      btn.innerHTML = `${res.liked ? "❤️" : "🤍"} <span>${res.likes_count}</span>`;
    } catch {}
  });

  // Open trip detail
  card.querySelector("[data-open-trip]").addEventListener("click", () => {
    window.openTrip(trip.id);
  });

  return card;
}

function badge(status) {
  const map = { planning: "📋", running: "🚀", arrived: "🏁", finished: "✅" };
  return map[status] || status;
}

function skeletons() {
  return `<div class="skeleton-list">${"<div class='skeleton-card'></div>".repeat(3)}</div>`;
}

function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// Refresh button
document.getElementById("feed-refresh-btn").addEventListener("click", () => renderFeed(true));
document.getElementById("feed-load-more").addEventListener("click", () => renderFeed(false));
