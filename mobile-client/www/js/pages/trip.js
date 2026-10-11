/**
 * trip.js – Trip detail page.
 * Called by feed/passport pages via window.openTrip(id).
 */

import { getTrip, getTripPhotos, getTripComments, likeTrip, postComment, getBaseUrl } from "../api.js";

export async function openTrip(tripId) {
  window.navigateTo("trip");
  const content = document.getElementById("trip-detail-content");
  const base = getBaseUrl();
  content.innerHTML = `<div style="padding:40px;text-align:center"><div class="splash-spinner" style="margin:0 auto"></div></div>`;

  try {
    const [trip, photos, comments] = await Promise.all([
      getTrip(tripId),
      getTripPhotos(tripId).catch(() => []),
      getTripComments(tripId).catch(() => []),
    ]);

    const heroHtml = trip.cover_photo_id
      ? `<img src="${base}/api/trips/${tripId}/photos/${trip.cover_photo_id}/file" alt="cover" style="width:100%;height:100%;object-fit:cover;display:block" />`
      : `<div class="trip-detail-hero-placeholder">🗺️</div>`;

    const spoilerOverlay = !trip.destination_revealed
      ? `<div class="trip-detail-spoiler"><span>🔒</span><p>Destination revealed on arrival</p></div>` : "";

    const photoGrid = photos.length
      ? `<div class="photos-grid">${photos.slice(0,9).map(p =>
          `<img src="${base}${p.thumb_url}" alt="" loading="lazy" />`).join("")}</div>`
      : "";

    const memberList = (trip.members || []).map(m =>
      `<span class="player-chip"><span class="player-dot" style="background:#0f9b8e"></span>${escHtml(m.display_name || m.username)}</span>`
    ).join("");

    const commentsHtml = comments.map(c => `
      <div class="comment-item">
        <div class="comment-name">${escHtml(c.display_name || c.username)}</div>
        <div class="comment-body">${escHtml(c.body)}</div>
      </div>`).join("");

    content.innerHTML = `
      <div class="trip-detail-hero">
        ${heroHtml}
        ${spoilerOverlay}
      </div>
      <div class="trip-detail-body">
        <div class="trip-detail-title">${escHtml(trip.title || "Unnamed Trip")}</div>
        <div class="trip-detail-meta">${statusLabel(trip.status)} · ${trip.created_at ? fmtDate(trip.created_at) : ""}</div>

        ${trip.destination_revealed && trip.destination
          ? `<p style="color:var(--accent3);font-weight:700;margin-bottom:12px">📍 ${escHtml(trip.destination)}</p>` : ""}

        <div class="trip-stats-row">
          <div class="trip-stat"><strong>${trip.likes_count || 0}</strong><span>Likes</span></div>
          <div class="trip-stat"><strong>${photos.length}</strong><span>Photos</span></div>
          <div class="trip-stat"><strong>${comments.length}</strong><span>Comments</span></div>
        </div>

        ${memberList ? `<div style="margin-bottom:16px">${memberList}</div>` : ""}

        <button id="trip-like-btn" class="btn ${trip.liked ? 'btn-danger' : 'btn-outline'} btn-sm" data-id="${tripId}" data-liked="${trip.liked}">
          ${trip.liked ? "❤️ Liked" : "🤍 Like"} (${trip.likes_count || 0})
        </button>

        ${photoGrid ? `<h3 class="section-title" style="padding:16px 0 8px">Photos</h3>${photoGrid}` : ""}

        <h3 class="section-title" style="padding:16px 0 8px">Comments</h3>
        <div id="trip-comments">${commentsHtml || `<p class="hint-text">No comments yet</p>`}</div>

        <div class="comment-form">
          <input type="text" id="trip-comment-input" placeholder="Add a comment…" />
          <button class="btn btn-primary btn-sm" id="trip-comment-submit">Send</button>
        </div>
      </div>
    `;

    // Like
    document.getElementById("trip-like-btn").addEventListener("click", async (btn) => {
      const el = document.getElementById("trip-like-btn");
      try {
        const res = await likeTrip(tripId);
        el.className = `btn ${res.liked ? "btn-danger" : "btn-outline"} btn-sm`;
        el.innerHTML = `${res.liked ? "❤️ Liked" : "🤍 Like"} (${res.likes_count})`;
      } catch (e) { window.showToast("⚠️ " + e.message); }
    });

    // Comment
    document.getElementById("trip-comment-submit").addEventListener("click", async () => {
      const inp = document.getElementById("trip-comment-input");
      const body = inp.value.trim();
      if (!body) return;
      try {
        const c = await postComment(tripId, body);
        const div = document.createElement("div");
        div.className = "comment-item";
        div.innerHTML = `<div class="comment-name">${escHtml(c.display_name)}</div><div class="comment-body">${escHtml(c.body)}</div>`;
        const container = document.getElementById("trip-comments");
        const empty = container.querySelector(".hint-text");
        if (empty) empty.remove();
        container.appendChild(div);
        inp.value = "";
      } catch (e) { window.showToast("⚠️ " + e.message); }
    });

  } catch (err) {
    content.innerHTML = `<p style="padding:40px;text-align:center;color:var(--accent2)">⚠️ ${err.message}</p>`;
  }
}

window.openTrip = openTrip;

function escHtml(s) {
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function statusLabel(s) {
  const m = { planning:"📋 Planning", running:"🚀 In Progress", arrived:"🏁 Arrived", finished:"✅ Finished" };
  return m[s] || s || "–";
}

function fmtDate(iso) {
  try { return new Date(iso).toLocaleDateString(undefined, { year:"numeric", month:"short", day:"numeric" }); }
  catch { return iso; }
}
