/**
 * Realtime WebSocket client.
 *
 * The important change from the web-only version: the URL is built from the **server**
 * address, not `window.location`. A Capacitor app is served from a local origin, so
 * `window.location.host` would open a socket to the phone itself and fail.
 *
 * Also added: a keep-alive ping every 20 seconds and a catch-up callback after a
 * reconnect, because a walk in and out of WiFi range is normal and the client must
 * re-subscribe and then ask for the state it missed (brief 03 §8).
 */

import { wsUrl } from "./server";

type EventHandler = (data: any) => void;

class RealtimeSocket {
  private socket: WebSocket | null = null;
  private listeners: Map<string, Set<EventHandler>> = new Map();
  private subscribedChannels: Set<string> = new Set();
  private reconnectTimer: number | null = null;
  private pingTimer: number | null = null;
  private attempts = 0;
  private catchUp: (() => void) | null = null;
  private token: string | null = null;
  private closedByUs = false;

  /** Backoff 1, 2, 4, 8, capped at 15 seconds. */
  private static backoff(attempt: number): number {
    return Math.min(15_000, 1000 * 2 ** Math.min(attempt, 3));
  }

  connect(token?: string | null) {
    if (token !== undefined) this.token = token;
    if (
      this.socket &&
      (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    this.closedByUs = false;

    let url: string;
    try {
      url = wsUrl(this.token ?? undefined);
    } catch {
      return;
    }

    try {
      this.socket = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.socket.onopen = () => {
      this.attempts = 0;
      // Re-subscribe before anything else, then catch up on what was missed.
      this.subscribedChannels.forEach((channel) => {
        this.socket?.send(JSON.stringify({ subscribe: channel }));
      });
      this.startPing();
      this.catchUp?.();
    };

    this.socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        const { event: name, data } = payload;
        // The server also uses `type` for a few messages (meetup, ping).
        const eventName = name ?? payload.type;
        if (eventName && this.listeners.has(eventName)) {
          this.listeners.get(eventName)?.forEach((handler) => handler(data ?? payload));
        }
      } catch (error) {
        console.warn("[ws] could not read a frame", error);
      }
    };

    this.socket.onclose = () => {
      this.stopPing();
      if (!this.closedByUs) this.scheduleReconnect();
    };

    this.socket.onerror = () => {
      this.socket?.close();
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer !== null) return;
    const delay = RealtimeSocket.backoff(this.attempts);
    this.attempts += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private startPing() {
    this.stopPing();
    // The server closes idle sockets; a JSON ping is harmless if it ignores it.
    this.pingTimer = window.setInterval(() => {
      this.send({ type: "ping", t: new Date().toISOString() });
    }, 20_000);
  }

  private stopPing() {
    if (this.pingTimer !== null) window.clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  /** Called after every successful reconnect. */
  onReconnect(handler: () => void) {
    this.catchUp = handler;
  }

  subscribe(channel: string) {
    this.subscribedChannels.add(channel);
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ subscribe: channel }));
    }
  }

  unsubscribe(channel: string) {
    this.subscribedChannels.delete(channel);
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ unsubscribe: channel }));
    }
  }

  send(data: any) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(data));
    }
  }

  on(event: string, handler: EventHandler) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event)?.add(handler);
    return () => {
      this.listeners.get(event)?.delete(handler);
    };
  }

  get connected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }
}

export const realtime = new RealtimeSocket();
