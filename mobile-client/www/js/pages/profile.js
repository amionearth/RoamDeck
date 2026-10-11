/**
 * profile.js – Profile / settings page.
 */

import { getMe, updateMe, uploadAvatar, getUserSummary, logout, getBaseUrl } from "../api.js";
import { takePhoto } from "../camera.js";

let profileData = null;

export async function renderProfile() {
  try {
    const user = await getMe();
    profileData = user;
    const base  = getBaseUrl();

    // Avatar
    const avatarEl = document.getElementById("profile-avatar");
    if (user.avatar_path) {
      avatarEl.src = `${base}${user.avatar_path}`;
      avatarEl.onerror = () => { avatarEl.src = "img/default-avatar.svg"; };
    }

    document.getElementById("profile-display-name").textContent = user.display_name || user.username;
    document.getElementById("profile-rank-title").textContent   = user.rank_title || `Level ${user.level}`;
    document.getElementById("profile-xp").textContent           = `${(user.xp || 0).toLocaleString()} XP`;

    // Edit form prefill
    document.getElementById("edit-display-name").value = user.display_name || "";
    document.getElementById("edit-bio").value           = user.bio || "";
    document.getElementById("edit-home-city").value     = user.home_city || "";

    // Summary stats (non-blocking)
    if (user.username) {
      getUserSummary(user.username).then(s => {
        document.getElementById("pstat-stamps").textContent  = s.stamps_count ?? "—";
        document.getElementById("pstat-trips").textContent   = s.trips_count ?? "—";
        document.getElementById("pstat-friends").textContent = s.friends_count ?? "—";
      }).catch(() => {});
    }

  } catch (err) {
    window.showToast("⚠️ " + err.message);
  }

  bindProfileActions();
}

function bindProfileActions() {
  // Edit form
  const form = document.getElementById("edit-profile-form");
  const newForm = form.cloneNode(true);
  form.parentNode.replaceChild(newForm, form);

  newForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const display_name = document.getElementById("edit-display-name").value.trim();
    const bio          = document.getElementById("edit-bio").value.trim();
    const home_city    = document.getElementById("edit-home-city").value.trim();
    try {
      await updateMe({ display_name, bio, home_city });
      window.showToast("✅ Profile updated");
      document.getElementById("profile-display-name").textContent = display_name;
    } catch (e) { window.showToast("⚠️ " + e.message); }
  });

  // Avatar upload
  const avatarBtn = document.getElementById("avatar-edit-btn");
  const newBtn = avatarBtn.cloneNode(true);
  avatarBtn.parentNode.replaceChild(newBtn, avatarBtn);

  newBtn.addEventListener("click", async () => {
    try {
      const blob = await takePhoto();
      if (!blob) return;
      const form = new FormData();
      form.append("file", blob, "avatar.jpg");
      const res = await uploadAvatar(form);
      const base = getBaseUrl();
      document.getElementById("profile-avatar").src = `${base}${res.avatar_path}?t=${Date.now()}`;
      window.showToast("✅ Avatar updated");
    } catch (e) { window.showToast("⚠️ " + e.message); }
  });

  // Logout
  const logoutBtn = document.getElementById("logout-btn");
  const newLogout = logoutBtn.cloneNode(true);
  logoutBtn.parentNode.replaceChild(newLogout, logoutBtn);

  newLogout.addEventListener("click", async () => {
    if (!confirm("Sign out?")) return;
    await logout();
    location.reload();
  });
}
