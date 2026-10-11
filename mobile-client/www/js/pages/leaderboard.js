/**
 * leaderboard.js – Leaderboard page.
 */

import { getLeaderboard, getBaseUrl } from "../api.js";

export async function renderLeaderboard() {
  const list    = document.getElementById("leaderboard-list");
  const scopeSel  = document.getElementById("lb-scope");
  const metricSel = document.getElementById("lb-metric");
  const base = getBaseUrl();

  async function load() {
    list.innerHTML = skeletons();
    try {
      const rows = await getLeaderboard(scopeSel.value, metricSel.value);
      if (!rows.length) {
        list.innerHTML = `<p class="hint-text" style="padding:40px 0">No data yet.</p>`;
        return;
      }
      list.innerHTML = rows.map(r => {
        const rankClass = r.rank === 1 ? "top1" : r.rank === 2 ? "top2" : r.rank === 3 ? "top3" : "";
        const rankEmoji = r.rank === 1 ? "🥇" : r.rank === 2 ? "🥈" : r.rank === 3 ? "🥉" : r.rank;
        const avatarSrc = r.avatar_path
          ? `${base}${r.avatar_path}`
          : "img/default-avatar.svg";
        return `
          <div class="lb-row${r.is_current_user ? " is-me" : ""}">
            <div class="lb-rank ${rankClass}">${rankEmoji}</div>
            <img class="lb-avatar" src="${avatarSrc}" alt="" onerror="this.src='img/default-avatar.svg'" />
            <div class="lb-info">
              <div class="lb-name">${escHtml(r.display_name)}</div>
              <div class="lb-rank-title">${escHtml(r.rank_title || "")}</div>
            </div>
            <div class="lb-val">${r.value.toLocaleString()}</div>
          </div>`;
      }).join("");
    } catch (err) {
      list.innerHTML = `<p class="hint-text" style="color:var(--accent2)">⚠️ ${err.message}</p>`;
    }
  }

  scopeSel.onchange  = load;
  metricSel.onchange = load;
  load();
}

function skeletons() {
  return Array(8).fill(`<div class="skeleton-card" style="height:58px;border-radius:var(--radius-sm)"></div>`).join("");
}

function escHtml(s) {
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
