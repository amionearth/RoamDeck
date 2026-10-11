import React from "react";
import { NavLink } from "react-router-dom";
import { Compass, Sparkles, Award, Gamepad2, Users, User, Radio } from "lucide-react";
import { useAuth } from "../lib/auth";

export const GlassRail: React.FC = () => {
  const { user } = useAuth();

  const navItems = [
    { to: "/", label: "Home", icon: Compass },
    { to: "/feed", label: "Feed", icon: Sparkles },
    { to: "/passport", label: "Passport", icon: Award },
    { to: "/games", label: "Games", icon: Gamepad2 },
    { to: "/radio", label: "Radio", icon: Radio },
    { to: "/friends", label: "Friends", icon: Users },
    { to: user ? `/u/${user.username}` : "/login", label: "Profile", icon: User },
  ];

  return (
    <aside
      className="hidden md:flex flex-col items-center justify-between fixed left-4 top-4 bottom-4 z-40 w-20 lg:w-[88px] glass-strong rounded-[32px] py-6 shadow-2xl transition-all"
      aria-label="Sidebar Navigation"
    >
      {/* Top Logo Mark */}
      <NavLink
        to="/"
        className="w-12 h-12 rounded-2xl bg-gradient-to-br from-lagoon to-deep-sea flex items-center justify-center text-white shadow-md transform hover:scale-105 active:scale-95 transition"
        title="FarFromHome"
      >
        <Compass className="w-6 h-6 stroke-[1.8]" />
      </NavLink>

      {/* Nav items */}
      <nav className="flex flex-col space-y-3.5 w-full items-center my-auto">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex flex-col items-center justify-center w-14 lg:w-16 py-2.5 rounded-2xl transition-all duration-200 group ${
                isActive
                  ? "bg-lagoon text-white shadow-md font-semibold"
                  : "text-ink/65 hover:text-ink hover:bg-white/40"
              }`
            }
          >
            <item.icon className="w-5 h-5 lg:w-[22px] lg:h-[22px] stroke-[1.8] group-hover:scale-105 transition-transform" />
            <span className="text-[10px] lg:text-[11px] font-sans mt-1 tracking-tight hidden lg:inline">
              {item.label}
            </span>
          </NavLink>
        ))}
      </nav>

      {/* Bottom Compass Indicator */}
      <div className="w-8 h-8 rounded-full bg-white/40 flex items-center justify-center text-mist">
        <span className="text-[10px] font-mono font-bold text-lagoon">N</span>
      </div>
    </aside>
  );
};
