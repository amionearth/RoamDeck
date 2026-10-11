import React, { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AmbientBackdrop } from "./AmbientBackdrop";
import { GlassRail } from "./GlassRail";
import { BottomPill } from "./BottomPill";

interface AppShellProps {
  children: React.ReactNode;
  tripCoverUrl?: string | null;
}

export const AppShell: React.FC<AppShellProps> = ({ children, tripCoverUrl }) => {
  const location = useLocation();
  const navigate = useNavigate();

  // Standalone pages: phone game join page, login, register
  const isStandalone =
    location.pathname.startsWith("/g/") ||
    location.pathname === "/login" ||
    location.pathname === "/register";

  // Shortcut Ctrl+Shift+D to open /demo (Section 2 cut list)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key.toUpperCase() === "D") {
        e.preventDefault();
        navigate("/demo");
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);

  if (isStandalone) {
    return (
      <div className="min-h-[100dvh] relative text-ink antialiased">
        <AmbientBackdrop tripCoverUrl={null} />
        <main className="w-full">{children}</main>
      </div>
    );
  }

  return (
    <div className="min-h-[100dvh] relative text-ink antialiased flex flex-col">
      {/* 1. Global Ambient Coastline Glass Backdrop */}
      <AmbientBackdrop tripCoverUrl={tripCoverUrl} />

      {/* 2. Floating Left Rail (Desktop >= 1024px & Tablet >= 768px) */}
      <GlassRail />

      {/* 3. Main Content Container - EXACT SAME X-POSITION AND WIDTH FOR ALL PAGES */}
      <div className="flex-1 w-full md:pl-28 lg:pl-32 transition-all">
        <div className="max-w-[1360px] mx-auto px-4 sm:px-6 lg:px-8 py-4 sm:py-6 pb-28 md:pb-12">
          {children}
        </div>
      </div>

      {/* 4. Floating Mobile Bottom Pill Navigation */}
      <BottomPill />
    </div>
  );
};
