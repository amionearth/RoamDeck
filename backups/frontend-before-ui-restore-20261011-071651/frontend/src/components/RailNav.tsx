import React from "react";
import { NavLink } from "react-router-dom";
import { Compass, Sparkles, MapPin, Gamepad2, Award, User, Settings2 } from "lucide-react";
import { useAuth } from "../lib/auth";

export const RailNav: React.FC = () => {
  const { user } = useAuth();

  const navItems = [
    { to: "/", label: "Home", icon: Compass },
    { to: "/feed", label: "Feed", icon: Sparkles },
    { to: "/passport", label: "Passport", icon: Award },
    { to: "/games", label: "Games", icon: Gamepad2 },
    { to: "/friends", label: "Friends", icon: MapPin },
    { to: user ? `/u/${user.username}` : "/login", label: "Profile", icon: User },
    { to: "/demo", label: "Demo", icon: Settings2 },
  ];

  return (
    <aside className="hidden lg:flex flex-col items-center w-24 py-8 bg-paper border-r border-mist/15 min-h-screen fixed left-0 top-0 z-30 shadow-sm">
      {/* Brand Icon */}
      <div className="w-12 h-12 rounded-2xl bg-lagoon flex items-center justify-center text-white shadow-clay mb-10">
        <Compass className="w-7 h-7" />
      </div>

      {/* Navigation Icons with labels underneath */}
      <nav className="flex flex-col space-y-6 w-full items-center">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex flex-col items-center justify-center w-16 py-2.5 rounded-2xl transition-all ${
                isActive
                  ? "bg-lagoon text-white shadow-clay font-medium"
                  : "text-mist hover:text-ink hover:bg-fog/60"
              }`
            }
          >
            <item.icon className="w-6 h-6 mb-1" />
            <span className="text-[11px] tracking-tight">{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  );
};
