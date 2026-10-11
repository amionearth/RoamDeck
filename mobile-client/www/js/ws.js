/**
 * ws.js – WebSocket manager for real-time game events.
 *
 * The server expects:
 *   subscribe:   { "subscribe": "game:<id>" }
 *   unsubscribe: { "unsubscribe": "game:<id>" }
 *
 * It emits events as: { "event": "<type>", "data": { … } }
 */

import { getWsUrl } from "./api.js";

let socket = null;
let reconnectTimer = null;
const listeners = {};          // { eventType: [ callback, … ] }
const pendingSubs = new Set(); // channels to subscribe on (re)connect

export function on(event, cb) {
  if (!listeners[event]) listeners[event] = [];
  listeners[event].push(cb);
}

export function off(event, cb) {
  if (!listeners[event]) return;
  listeners[event] = listeners[event].filter((f) => f !== cb);
}

function emit(event, data) {
  (listeners[event] || []).forEach((cb) => cb(data));
  (listeners["*"] || []).forEach((cb) => cb({ event, data }));
}

export function connect() {
  if (socket && socket.readyState <= 1) return; // already open/connecting

  const url = getWsUrl();
  socket = new WebSocket(url);

  socket.onopen = () => {
    console.log("[ws] connected");
    clearTimeout(reconnectTimer);
    // Resubscribe to all pending channels
    pendingSubs.forEach((ch) => socket.send(JSON.stringify({ subscribe: ch })));
    emit("connected", {});
  };

  socket.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.event) emit(msg.event, msg.data || {});
    } catch {}
  };

  socket.onerror = () => {};

  socket.onclose = () => {
    emit("disconnected", {});
    reconnectTimer = setTimeout(connect, 3000);
  };
}

export function disconnect() {
  clearTimeout(reconnectTimer);
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
}

export function subscribe(channel) {
  pendingSubs.add(channel);
  if (socket && socket.readyState === 1) {
    socket.send(JSON.stringify({ subscribe: channel }));
  }
}

export function unsubscribe(channel) {
  pendingSubs.delete(channel);
  if (socket && socket.readyState === 1) {
    socket.send(JSON.stringify({ unsubscribe: channel }));
  }
}
