import React from "react";
import { Link } from "react-router-dom";
import { Gamepad2, Award, Trophy, Compass, ArrowUpRight } from "lucide-react";

export const QuickActionTiles: React.FC = () => {
  const actions = [
    {
      title: "Join Game",
      desc: "Enter party code",
      icon: Gamepad2,
      href: "/games",
      color: "text-amber-600",
      bgColor: "bg-amber-100/60",
    },
    {
      title: "Passport",
      desc: "Stamps & ranks",
      icon: Award,
      href: "/passport",
      color: "text-teal-700",
      bgColor: "bg-teal-100/60",
    },
    {
      title: "Leaderboard",
      desc: "Top explorers",
      icon: Trophy,
      href: "/leaderboard",
      color: "text-amber-700",
      bgColor: "bg-amber-100/60",
    },
    {
      title: "Pair Device",
      desc: "Pi 5 status",
      icon: Compass,
      href: "/device",
      color: "text-teal-800",
      bgColor: "bg-teal-100/60",
    },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4">
      {actions.map((act) => {
        const Icon = act.icon;
        return (
          <Link
            key={act.title}
            to={act.href}
            className="glass rounded-card p-4 sm:p-5 border border-white/40 flex items-center justify-between group transition-all duration-300 hover:shadow-card hover:-translate-y-1 hover:border-white/60"
          >
            <div className="flex items-center gap-3">
              <div
                className={`w-10 h-10 sm:w-11 sm:h-11 rounded-full ${act.bgColor} flex items-center justify-center transition-transform group-hover:scale-110 shadow-inner`}
              >
                <Icon className={`w-5 h-5 ${act.color}`} />
              </div>
              <div>
                <h4 className="text-sm font-sans font-semibold text-ink group-hover:text-teal-800 transition-colors">
                  {act.title}
                </h4>
                <p className="text-xs text-ink-muted font-sans">{act.desc}</p>
              </div>
            </div>
            <div className="w-6 h-6 rounded-full glass-strong opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity text-teal-900">
              <ArrowUpRight className="w-3.5 h-3.5" />
            </div>
          </Link>
        );
      })}
    </div>
  );
};
