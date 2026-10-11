import React, { useEffect, useRef, useState } from "react";
import { Compass, MessageCircle, QrCode } from "lucide-react";

/**
 * The floating pill tab bar from the reference design: three tabs, one glowing yellow
 * "active" dot.
 *
 * There is no Capacitor version of this file on purpose. The tab bar is pure layout and
 * has nothing device-specific in it, so branching it per platform would be two things to
 * keep in step for no benefit. The native boundary lives in `src/lib/native.ts`, which is
 * where hardware lives; everything visual is shared.
 */

export type TabKey = "compass" | "chat" | "hub";

const TABS: Array<{
  key: TabKey;
  label: string;
  icon: React.ComponentType<{ size?: number; strokeWidth?: number }>;
}> = [
  { key: "compass", label: "Compass", icon: Compass },
  { key: "chat", label: "Chat", icon: MessageCircle },
  { key: "hub", label: "Hub", icon: QrCode },
];

export interface TabBarProps {
  active: TabKey;
  onChange: (tab: TabKey) => void;
  /** A small dot on this tab when it is not the active one. */
  alertOn?: TabKey;
}

export const TabBar: React.FC<TabBarProps> = ({ active, onChange, alertOn }) => (
  <nav className="ffh-tabbar" role="tablist" aria-label="Main">
    {TABS.map(({ key, label, icon: Icon }) => {
      const isActive = key === active;
      return (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={isActive}
          aria-label={label}
          className={`ffh-tab ffh-haptic-tap${isActive ? " ffh-tab--active" : ""}`}
          onClick={() => onChange(key)}
        >
          <span className="ffh-tab__dot" style={{ position: "relative" }}>
            <Icon size={19} strokeWidth={2.4} />
            {alertOn === key && !isActive && (
              <span
                style={{
                  position: "absolute",
                  top: 0,
                  right: 0,
                  width: 9,
                  height: 9,
                  borderRadius: 999,
                  background: "var(--ffh-yellow)",
                }}
              />
            )}
          </span>
          {label}
        </button>
      );
    })}
  </nav>
);
