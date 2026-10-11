/**
 * Auth and device-token state that survives a native app restart.
 *
 * The website authenticates with an httpOnly cookie (`ffh_session`). In a Capacitor
 * shell the WebView's cookie jar is not guaranteed to survive a reinstall, an OS
 * update or a "clear data", so the app also keeps the **Bearer token** the server
 * already supports (`get_current_user_optional` reads
 * `Authorization: Bearer …` as a fallback). That is the same header the handheld
 * uses, so nothing new is needed on the server.
 *
 * Persisted with Capacitor Preferences when running natively (a real key/value store
 * on the device) and with localStorage in a browser.
 */

import { create } from "zustand";

export interface StoredAuth {
  /** Website session token, sent as `Authorization: Bearer …`. */
  token: string | null;
  /** The device token, when this phone has been paired as a handheld. */
  deviceToken: string | null;
  /** The device id the token belongs to, for the pairing screen. */
  deviceId: string | null;
  /** Player token for one game session, keyed by join code. */
  playerTokens: Record<string, string>;
}

const STORAGE_KEY = "ffh.auth.v1";

function isNative(): boolean {
  const capacitor = (globalThis as any)?.Capacitor;
  return Boolean(capacitor?.isNativePlatform?.() ?? capacitor?.isNative);
}

async function loadPersisted(): Promise<Partial<StoredAuth>> {
  if (isNative()) {
    try {
      const { Preferences } = await import("@capacitor/preferences");
      const { value } = await Preferences.get({ key: STORAGE_KEY });
      return value ? (JSON.parse(value) as Partial<StoredAuth>) : {};
    } catch {
      return {};
    }
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Partial<StoredAuth>) : {};
  } catch {
    return {};
  }
}

async function savePersisted(state: StoredAuth): Promise<void> {
  const payload = JSON.stringify(state);
  if (isNative()) {
    try {
      const { Preferences } = await import("@capacitor/preferences");
      await Preferences.set({ key: STORAGE_KEY, value: payload });
      return;
    } catch {
      /* fall through to localStorage */
    }
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, payload);
  } catch {
    /* private mode */
  }
}

interface AuthState extends StoredAuth {
  hydrated: boolean;
  hydrate: () => Promise<void>;
  setToken: (token: string | null) => void;
  setDevice: (deviceId: string, deviceToken: string) => void;
  clearDevice: () => void;
  setPlayerToken: (code: string, token: string) => void;
  getPlayerToken: (code: string) => string | null;
  clear: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token: null,
  deviceToken: null,
  deviceId: null,
  playerTokens: {},
  hydrated: false,

  hydrate: async () => {
    const persisted = await loadPersisted();
    set({
      token: persisted.token ?? null,
      deviceToken: persisted.deviceToken ?? null,
      deviceId: persisted.deviceId ?? null,
      playerTokens: persisted.playerTokens ?? {},
      hydrated: true,
    });
  },

  setToken: (token) => {
    set({ token });
    void savePersisted({ ...pick(get()), token });
  },

  setDevice: (deviceId, deviceToken) => {
    set({ deviceId, deviceToken });
    void savePersisted({ ...pick(get()), deviceId, deviceToken });
  },

  clearDevice: () => {
    set({ deviceId: null, deviceToken: null });
    void savePersisted({ ...pick(get()), deviceId: null, deviceToken: null });
  },

  setPlayerToken: (code, token) => {
    const playerTokens = { ...get().playerTokens, [code.toUpperCase()]: token };
    set({ playerTokens });
    void savePersisted({ ...pick(get()), playerTokens });
  },

  getPlayerToken: (code) => get().playerTokens[code.toUpperCase()] ?? null,

  clear: () => {
    set({ token: null, playerTokens: {} });
    void savePersisted({ ...pick(get()), token: null, playerTokens: {} });
  },
}));

function pick(state: AuthState): StoredAuth {
  return {
    token: state.token,
    deviceToken: state.deviceToken,
    deviceId: state.deviceId,
    playerTokens: state.playerTokens,
  };
}

/** Convenience for the API layer, which runs outside React. */
export const authToken = {
  get: () => useAuthStore.getState().token,
  device: () => useAuthStore.getState().deviceToken,
};
