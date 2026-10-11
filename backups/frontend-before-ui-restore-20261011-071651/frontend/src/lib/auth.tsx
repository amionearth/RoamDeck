/**
 * Website authentication, plus the device (handheld) pairing flow.
 *
 * Two identities live side by side and they are genuinely different things:
 *
 * * a **user** signs in with a username and password, and browses the social side;
 * * a **device** (the handheld) is claimed with a short pairing code and has its own
 *   bearer token, which is what the blind-trip endpoints accept.
 *
 * Both tokens are persisted through `lib/authToken.ts`, so a native app restart does
 * not lose either.
 */

import React, { createContext, useContext, useEffect, useState } from "react";
import { api, ApiError, NetworkError } from "./api";
import { useAuthStore } from "./authToken";

export interface UserProfile {
  id: string;
  username: string;
  display_name: string;
  bio?: string;
  avatar_path?: string;
  home_city?: string;
  is_private?: boolean;
  xp?: number;
  level: number;
  rank_title: string;
  friend_code: string;
  playercard?: any;
}

interface AuthContextType {
  user: UserProfile | null;
  isLoading: boolean;
  /** Set when the last attempt failed for a reason worth showing the user. */
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, displayName: string, password: string) => Promise<void>;
  demoLogin: () => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshUser = async () => {
    try {
      const data = await api.get<UserProfile>("/api/me");
      setUser(data);
      setError(null);
    } catch (err) {
      // A 401 simply means "not signed in", which is not an error worth shouting about.
      // A network failure is, because the user needs to fix the WiFi.
      if (err instanceof NetworkError) {
        setError(err.message);
      }
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void (async () => {
      await useAuthStore.getState().hydrate();
      await refreshUser();
    })();
  }, []);

  const login = async (username: string, password: string) => {
    setError(null);
    const result = await api.post<{ token?: string }>("/api/auth/login", { username, password });
    // The server sets the cookie; it also returns a token when asked, which is what
    // keeps a native shell signed in across restarts.
    if (result?.token) useAuthStore.getState().setToken(result.token);
    await refreshUser();
  };

  const register = async (username: string, displayName: string, password: string) => {
    setError(null);
    const result = await api.post<{ token?: string }>("/api/auth/register", {
      username,
      display_name: displayName,
      password,
    });
    if (result?.token) useAuthStore.getState().setToken(result.token);
    await refreshUser();
  };

  const demoLogin = async () => {
    await login("explorer", "explorer");
  };

  const logout = async () => {
    try {
      await api.post("/api/auth/logout");
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
    } finally {
      useAuthStore.getState().clear();
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider
      value={{ user, isLoading, error, login, register, demoLogin, logout, refreshUser }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
};

// --------------------------------------------------------------------------- //
// device pairing
// --------------------------------------------------------------------------- //
export interface PairingStart {
  device_id: string;
  pairing_code: string;
  poll_secret: string;
}

/**
 * Register this phone as a handheld and start polling for the claim.
 *
 * Mirrors `phone-app/farfromhome_device/net/pairing.py` exactly:
 * `POST /api/device/register` with no auth, then
 * `GET /api/device/pairing/{id}?secret=…` every 3 s until `paired`.
 */
export async function startDevicePairing(name: string): Promise<PairingStart> {
  return api.post<PairingStart>("/api/device/register", { name });
}

export type PairingStatus =
  | { status: "pending" }
  | { status: "paired"; token: string; owner_user_id?: string }
  | { status: "expired" | "error" };

export async function pollDevicePairing(
  deviceId: string,
  secret: string
): Promise<PairingStatus> {
  try {
    return await api.get<PairingStatus>(
      `/api/device/pairing/${encodeURIComponent(deviceId)}?secret=${encodeURIComponent(secret)}`
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return { status: "expired" };
    if (err instanceof ApiError && err.status === 404) return { status: "expired" };
    return { status: "error" };
  }
}
