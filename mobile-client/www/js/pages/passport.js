/**
 * passport.js – Passport / stamps page.
 */

import { getPassport, getBaseUrl } from "../api.js";

export async function renderPassport() {
  const statsEl  = document.getElementById("passport-stats");
  const stampsEl = document.getElementById("passport-stamps");
  const base = getBaseUrl();

  statsEl.innerHTML  = `<div class="passport-stats-grid"><div class="pstat"><strong>…</strong><span>Stamps</span></div></div>`;
  stampsEl.innerHTML = skeletons();

  try {
    const data = await getPassport();

    statsEl.innerHTML = `
      <div class="passport-stats-grid">
        <div class="pstat"><strong>${data.total_stamps}</strong><span>Stamps</span></div>
        <div class="pstat"><strong>${data.categories_collected}/${data.categories_total}</strong><span>Categories</span></div>
        <div class="pstat"><strong>${data.rarities.legendary}</strong><span>Legendary</span></div>
      </div>`;

    if (!data.stamps || data.stamps.length === 0) {
      stampsEl.innerHTML = `<p class="hint-text" style="grid-column:1/-1;padding:40px 0">No stamps yet. Start a BlindTrip to earn your first!</p>`;
      return;
    }

    stampsEl.innerHTML = data.stamps.map(s => `
      <div class="stamp-card">
        <img src="${base}${s.image_url || "/img/stamp-placeholder.svg"}" alt="${escHtml(s.place_name)}" loading="lazy"
             onerror="this.src='img/stamp-placeholder.svg'" />
        <div class="stamp-card-body">
          <div class="stamp-name">${escHtml(s.place_name)}</div>
          <div class="stamp-rarity ${s.rarity}">${s.rarity}</div>
        </div>
      </div>`).join("");

  } catch (err) {
    stampsEl.innerHTML = `<p class="hint-text" style="grid-column:1/-1;color:var(--accent2)">⚠️ ${err.message}</p>`;
  }
}

function skeletons() {
  return Array(6).fill(`<div class="skeleton-card" style="height:140px;border-radius:var(--radius-sm)"></div>`).join("");
}

function escHtml(s) {
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
