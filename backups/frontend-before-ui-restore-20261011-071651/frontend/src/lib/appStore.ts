import { create } from "zustand";

/**
 * Small UI-level state that several screens share.
 *
 * Deliberately tiny. Everything that matters - the plan, the position, the queue -
 * lives in `lib/engine.ts` and `lib/outbox.ts`, which are plain modules with their own
 * subscriptions. This store only holds things that are genuinely about the shell:
 * which tab is open, a request handed from one screen to another, and the last
 * diagnostic summary.
 */

export type TabKey = "compass" | "chat" | "hub";

export interface ChatMessage {
  id: string;
  role: "agent" | "me" | "hint" | "system";
  text: string;
  at: number;
}

export interface AppState {
  tab: TabKey;
  setTab: (tab: TabKey) => void;

  /**
   * A request typed or tapped on one screen to be picked up by the Chat screen's
   * composer. Used by the "Nearby: Café" chips, which should feel like one tap.
   */
  pendingRequest: string | null;
  setPendingRequest: (text: string | null) => void;

  messages: ChatMessage[];
  addMessage: (message: Omit<ChatMessage, "id" | "at">) => void;
  clearMessages: () => void;

  /** The last "is the phone paired as a handheld" answer, for Settings. */
  deviceName: string;
  setDeviceName: (name: string) => void;

  /** Bumped whenever the queue changes, so screens can re-read it cheaply. */
  queueVersion: number;
  bumpQueue: () => void;
}

let counter = 0;

export const useAppStore = create<AppState>((set) => ({
  tab: "compass",
  setTab: (tab) => set({ tab }),

  pendingRequest: null,
  setPendingRequest: (pendingRequest) => set({ pendingRequest }),

  messages: [],
  addMessage: (message) =>
    set((state) => {
      counter += 1;
      return {
        messages: [
          ...state.messages,
          { ...message, id: `m${counter}`, at: Date.now() },
        ].slice(-80),
      };
    }),
  clearMessages: () => set({ messages: [] }),

  deviceName: "This phone",
  setDeviceName: (deviceName) => set({ deviceName }),

  queueVersion: 0,
  bumpQueue: () => set((state) => ({ queueVersion: state.queueVersion + 1 })),
}));
