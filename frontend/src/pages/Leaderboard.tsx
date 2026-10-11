import React, { useEffect, useState } from "react";
import { Trophy, Award, Camera, MapPin, Users, Globe } from "lucide-react";
import { Header } from "../components/Header";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

export const Leaderboard: React.FC = () => {
  const { user } = useAuth();
  const [scope, setScope] = useState<"global" | "friends">("global");
  const [metric, setMetric] = useState<"points" | "places" | "photos">("points");
  const [rankings, setRankings] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchRankings = async () => {
    try {
      const data = await api.get(`/api/leaderboard?scope=${scope}&metric=${metric}`);
      setRankings(data);
    } catch (err) {
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchRankings();
  }, [scope, metric]);

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6 pb-24 lg:pb-12 space-y-8">
      <Header
        title="Explorer Rankings"
        subtitle="Global and companion leaderboards tracking points, places, and photos."
      />

      {/* Scope and Metric Filters */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 bg-paper rounded-card p-4 shadow-clay border border-mist/20">
        {/* Scope Tabs */}
        <div className="flex bg-fog p-1 rounded-full border border-mist/20">
          <button
            onClick={() => setScope("global")}
            className={`px-5 py-1.5 rounded-full text-xs font-semibold flex items-center gap-1.5 transition ${
              scope === "global" ? "bg-lagoon text-white shadow-sm" : "text-mist hover:text-ink"
            }`}
          >
            <Globe className="w-3.5 h-3.5" /> Global
          </button>
          <button
            onClick={() => setScope("friends")}
            className={`px-5 py-1.5 rounded-full text-xs font-semibold flex items-center gap-1.5 transition ${
              scope === "friends" ? "bg-lagoon text-white shadow-sm" : "text-mist hover:text-ink"
            }`}
          >
            <Users className="w-3.5 h-3.5" /> Friends
          </button>
        </div>

        {/* Metric Tabs */}
        <div className="flex items-center gap-2">
          {[
            { id: "points", label: "Points", icon: Trophy },
            { id: "places", label: "Places", icon: MapPin },
            { id: "photos", label: "Photos", icon: Camera },
          ].map((m) => (
            <button
              key={m.id}
              onClick={() => setMetric(m.id as any)}
              className={`px-4 py-1.5 rounded-full text-xs font-semibold flex items-center gap-1.5 transition ${
                metric === m.id
                  ? "bg-paper text-ink border border-lagoon shadow-sm"
                  : "bg-fog text-mist border border-transparent hover:text-ink"
              }`}
            >
              <m.icon className="w-3.5 h-3.5 text-lagoon" />
              <span>{m.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Rankings List */}
      <div className="bg-paper rounded-card overflow-hidden shadow-clay border border-mist/20 divide-y divide-mist/15">
        {rankings.map((row) => (
          <div
            key={row.user_id}
            className={`flex items-center justify-between p-4 sm:p-5 transition ${
              row.is_current_user
                ? "bg-lagoon/10 border-l-4 border-l-lagoon"
                : "hover:bg-fog/30"
            }`}
          >
            <div className="flex items-center gap-4">
              <span
                className={`font-mono font-bold text-base w-7 text-center ${
                  row.rank === 1
                    ? "text-signal text-xl font-serif"
                    : row.rank === 2
                    ? "text-slate-400 font-serif"
                    : row.rank === 3
                    ? "text-amber-700 font-serif"
                    : "text-mist"
                }`}
              >
                {row.rank === 1 ? "🥇" : row.rank === 2 ? "🥈" : row.rank === 3 ? "🥉" : `${row.rank}.`}
              </span>

              <div className="w-10 h-10 rounded-full bg-lagoon text-white flex items-center justify-center font-bold text-xs shrink-0 overflow-hidden">
                {row.avatar_path ? (
                  <img src={row.avatar_path} alt={row.display_name} className="w-full h-full object-cover" />
                ) : (
                  row.display_name.slice(0, 2).toUpperCase()
                )}
              </div>

              <div>
                <span className="font-semibold text-sm text-ink block">
                  {row.display_name} {row.is_current_user && "(You)"}
                </span>
                <span className="text-xs text-mist font-mono">
                  Level {row.level} • {row.rank_title}
                </span>
              </div>
            </div>

            <div className="text-right">
              <span className="font-mono text-lg font-bold text-ink block">
                {row.value}
              </span>
              <span className="text-[10px] uppercase font-mono text-mist">
                {metric}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
