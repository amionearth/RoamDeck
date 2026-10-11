/**
 * friends.js – Friends page.
 */

import { getFriends, searchUsers, sendFriendRequest, acceptFriend, removeFriend, getBaseUrl } from "../api.js";

let friendsData = null;

export async function renderFriends() {
  loadFriends();
  bindSearch();
}

async function loadFriends() {
  const reqList     = document.getElementById("friend-requests-list");
  const friendsList = document.getElementById("friends-list");
  const codeEl      = document.getElementById("my-friend-code");
  const base        = getBaseUrl();

  reqList.innerHTML     = `<p class="hint-text">Loading…</p>`;
  friendsList.innerHTML = `<p class="hint-text">Loading…</p>`;

  try {
    const data = await getFriends();
    friendsData = data;
    codeEl.textContent = data.friend_code || "—";

    // Pending requests
    if (!data.pending_requests || data.pending_requests.length === 0) {
      reqList.innerHTML = `<p class="hint-text">No pending requests.</p>`;
    } else {
      reqList.innerHTML = data.pending_requests.map(r => {
        const avatar = r.avatar_path ? `${base}${r.avatar_path}` : "img/default-avatar.svg";
        return `
          <div class="friend-card">
            <img class="friend-avatar" src="${avatar}" onerror="this.src='img/default-avatar.svg'" alt="" />
            <div class="friend-info">
              <div class="friend-name">${escHtml(r.display_name)}</div>
              <div class="friend-city">@${escHtml(r.username)}</div>
            </div>
            <button class="friend-action accept" data-accept="${r.friendship_id}">Accept</button>
          </div>`;
      }).join("");

      reqList.querySelectorAll("[data-accept]").forEach(btn => {
        btn.addEventListener("click", async () => {
          try {
            await acceptFriend(btn.dataset.accept);
            window.showToast("✅ Friend request accepted");
            loadFriends();
          } catch (e) { window.showToast("⚠️ " + e.message); }
        });
      });
    }

    // Friends list
    if (!data.friends || data.friends.length === 0) {
      friendsList.innerHTML = `<p class="hint-text">No friends yet. Search to add some!</p>`;
    } else {
      friendsList.innerHTML = data.friends.map(f => {
        const avatar = f.avatar_path ? `${base}${f.avatar_path}` : "img/default-avatar.svg";
        return `
          <div class="friend-card">
            <img class="friend-avatar" src="${avatar}" onerror="this.src='img/default-avatar.svg'" alt="" />
            <div class="friend-info">
              <div class="friend-name">${escHtml(f.display_name)}</div>
              <div class="friend-city">${escHtml(f.home_city || "")}</div>
            </div>
            <button class="friend-action" style="border-color:var(--accent2);color:var(--accent2)" data-remove="${f.friendship_id}">Remove</button>
          </div>`;
      }).join("");

      friendsList.querySelectorAll("[data-remove]").forEach(btn => {
        btn.addEventListener("click", async () => {
          if (!confirm("Remove this friend?")) return;
          try {
            await removeFriend(btn.dataset.remove);
            window.showToast("Removed");
            loadFriends();
          } catch (e) { window.showToast("⚠️ " + e.message); }
        });
      });
    }
  } catch (err) {
    reqList.innerHTML = "";
    friendsList.innerHTML = `<p class="hint-text" style="color:var(--accent2)">⚠️ ${err.message}</p>`;
  }
}

function bindSearch() {
  const input   = document.getElementById("friend-search-input");
  const btn     = document.getElementById("friend-search-btn");
  const results = document.getElementById("friend-search-results");
  const base    = getBaseUrl();

  // avoid double-binding on re-render
  const newBtn = btn.cloneNode(true);
  btn.parentNode.replaceChild(newBtn, btn);

  newBtn.addEventListener("click", async () => {
    const q = input.value.trim();
    if (!q) return;
    results.innerHTML = `<p class="hint-text">Searching…</p>`;
    results.classList.remove("hidden");

    try {
      const users = await searchUsers(q);
      if (!users.length) {
        results.innerHTML = `<p class="hint-text">No users found for "${escHtml(q)}"</p>`;
        return;
      }
      results.innerHTML = users.map(u => {
        const avatar = u.avatar_path ? `${base}${u.avatar_path}` : "img/default-avatar.svg";
        return `
          <div class="friend-card">
            <img class="friend-avatar" src="${avatar}" onerror="this.src='img/default-avatar.svg'" alt="" />
            <div class="friend-info">
              <div class="friend-name">${escHtml(u.display_name)}</div>
              <div class="friend-city">@${escHtml(u.username)} · Lvl ${u.level}</div>
            </div>
            <button class="friend-action" data-add="${escHtml(u.username)}">Add</button>
          </div>`;
      }).join("");

      results.querySelectorAll("[data-add]").forEach(btn => {
        btn.addEventListener("click", async () => {
          try {
            const res = await sendFriendRequest(btn.dataset.add);
            const msgs = {
              sent: "✅ Friend request sent",
              accepted: "✅ Now friends!",
              already_friends: "Already friends",
              request_already_pending: "Request already pending",
            };
            window.showToast(msgs[res.status] || res.status);
            btn.disabled = true;
          } catch (e) { window.showToast("⚠️ " + e.message); }
        });
      });
    } catch (err) {
      results.innerHTML = `<p class="hint-text" style="color:var(--accent2)">⚠️ ${err.message}</p>`;
    }
  });

  // Copy friend code
  document.getElementById("copy-friend-code-btn").onclick = () => {
    const code = document.getElementById("my-friend-code").textContent;
    navigator.clipboard?.writeText(code).then(() => window.showToast("Copied!")).catch(() => {});
  };
}

function escHtml(s) {
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
