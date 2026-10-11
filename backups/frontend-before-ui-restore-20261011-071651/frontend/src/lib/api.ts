// FarFromHome API client.
//
// Every request goes to an **absolute** URL built by `lib/server.ts`, because a
// Capacitor app is served from a local origin (`http://localhost` on Android,
// `capacitor://localhost` on iOS): a relative `fetch("/api/...")` would ask the phone
// itself rather than the laptop.
//
// The client also heals its own address: a network-level failure triggers one
// re-discovery and one retry, which is what makes moving between WiFi networks work
// without restarting the app.

import {
  apiUrl,
  getServerUrl,
  revalidateServer,
  serverBase,
} from "./server";
import { useAuthStore } from "./authToken";

export class ApiError extends Error {
  status: number;
  data: any;
  constructor(status: number, message: string, data?: any) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export class NetworkError extends Error {
  constructor(message = "Cannot reach the server") {
    super(message);
  }
}

interface RequestOptions extends RequestInit {
  /** Skip the re-discovery + retry pass. Used by the health probe itself. */
  noRetry?: boolean;
}

async function rawRequest<T>(endpoint: string, options: RequestOptions): Promise<T> {
  const isFormData = options.body instanceof FormData;
  const headers = new Headers(options.headers || {});
  if (!isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  // Bearer token, for the handheld/device and player flows. The website uses
  // httpOnly cookies, which a native shell cannot rely on surviving a restart.
  const token = useAuthStore.getState().token;
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(apiUrl(endpoint), {
    ...options,
    headers,
    credentials: "include",
  });

  if (!response.ok) {
    let errData: any = {};
    try {
      errData = await response.json();
    } catch {
      errData = { message: response.statusText };
    }
    const detail = errData.detail;
    const message =
      errData.message ||
      (typeof detail === "string" ? detail : null) ||
      (detail && typeof detail === "object" ? detail.message : null) ||
      "Request failed";
    throw new ApiError(response.status, message, errData);
  }

  const contentType = response.headers.get("content-type");
  if (contentType && contentType.includes("application/json")) {
    return response.json();
  }
  return (await response.text()) as unknown as T;
}

/**
 * One request, with the self-healing retry.
 *
 * A `NetworkError` (the phone could not reach anything) is different from a 5xx (the
 * server answered and was unhappy). Only the first is worth re-discovering for.
 */
export async function apiRequest<T = any>(
  endpoint: string,
  options: RequestOptions = {}
): Promise<T> {
  try {
    return await rawRequest<T>(endpoint, options);
  } catch (error) {
    if (options.noRetry || !(error instanceof NetworkError)) {
      // `fetch` throws a TypeError on a network failure; normalise it first.
      if (error instanceof TypeError && !options.noRetry) {
        const found = await revalidateServer();
        if (found) {
          return rawRequest<T>(endpoint, { ...options, noRetry: true });
        }
        throw new NetworkError(
          `Could not reach the server at ${serverBase() || "any known address"}. Check that the laptop is running and that this phone is on the same WiFi.`
        );
      }
      throw error;
    }
    throw error;
  }
}

export const api = {
  get: <T = any>(url: string, options?: RequestOptions) =>
    apiRequest<T>(url, { ...options, method: "GET" }),
  post: <T = any>(url: string, body?: any, options?: RequestOptions) =>
    apiRequest<T>(url, {
      ...options,
      method: "POST",
      body: body instanceof FormData ? body : JSON.stringify(body ?? {}),
    }),
  patch: <T = any>(url: string, body?: any, options?: RequestOptions) =>
    apiRequest<T>(url, {
      ...options,
      method: "PATCH",
      body: body instanceof FormData ? body : JSON.stringify(body ?? {}),
    }),
  put: <T = any>(url: string, body?: any, options?: RequestOptions) =>
    apiRequest<T>(url, {
      ...options,
      method: "PUT",
      body: body instanceof FormData ? body : JSON.stringify(body ?? {}),
    }),
  delete: <T = any>(url: string, options?: RequestOptions) =>
    apiRequest<T>(url, { ...options, method: "DELETE" }),
};

// --------------------------------------------------------------------------- //
// the device (handheld) API, as documented in phone-app/docs/SERVER_CONTRACT.md
// --------------------------------------------------------------------------- //
export interface DeviceHealth {
  ok: boolean;
  app: string;
  server_time?: string;
}

export async function getHealth(): Promise<DeviceHealth | null> {
  try {
    return await apiRequest<DeviceHealth>("/api/health", { noRetry: true });
  } catch {
    return null;
  }
}

export interface NetworkInfo {
  lan_ip: string;
  port: number;
  base_url: string;
  candidates: string[];
}

export async function getNetworkInfo(): Promise<NetworkInfo | null> {
  try {
    return await apiRequest<NetworkInfo>("/api/network-info", { noRetry: true });
  } catch {
    return null;
  }
}

export function currentServer(): string {
  return getServerUrl() ?? "";
}
