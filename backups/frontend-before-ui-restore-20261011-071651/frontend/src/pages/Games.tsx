import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Gamepad2, Trophy, Users, ChevronRight, Globe, MapPin, Camera, Sparkles, QrCode } from "lucide-react";
import { api } from "../lib/api";

export const Games: React.FC = () => {
  const [activeTab, setActiveTab] = useState<"games" | "leaderboard">("games");

  // Games state
  const [games, setGames] = useState<any[]>([]);
  const [isLoadingGames, setIsLoadingGames] = useState(true);

  // Leaderboard state
  const [scope, setScope] = useState<"global" | "friends">("global");
  const [metric, setMetric] = useState<"points" | "places" | "photos">("points");
  const [rankings, setRankings] = useState<any[]>([]);
  const [isLoadingLeaderboard, setIsLoadingLeaderboard] = useState(false);

  const fetchGames = async () => {
    try {
      setIsLoadingGames(true);
      const data = await api.get("/api/games");
      setGames(data);
    } catch (err) {
      console.error("Games fetch error:", err);
    } finally {
      setIsLoadingGames(false);
    }
  };

  const fetchRankings = async () => {
    try {
      setIsLoadingLeaderboard(true);
      const data = await api.get(`/api/leaderboard?scope=${scope}&metric=${metric}`);
      setRankings(data);
    } catch (err) {
      console.error("Leaderboard fetch error:", err);
    } finally {
      setIsLoadingLeaderboard(false);
    }
  };

  useEffect(() => {
    fetchGames();
  }, []);

  useEffect(() => {
    if (activeTab === "leaderboard") {
      fetchRankings();
    }
  }, [activeTab, scope, metric]);

  const topThree = rankings.slice(0, 3);
  const remainingRankings = rankings.slice(3);

  return (
    <div className="space-y-6 sm:space-y-8 animate-fade-in">
      {/* 1. Header with Tab Toggle (Games | Leaderboard) */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl sm:text-4xl text-ink font-normal">
            {activeTab === "games" ? "Blind Trip Games" : "Explorer Leaderboard"}
          </h1>
          <p className="mt-1 text-sm text-ink-muted font-sans">
            {activeTab === "games"
              ? "Group photo tasks, bingo hunts, and per-member challenges initiated by the device."
              : "Rankings across companions and global explorers tracked by points, discoveries, and shots."}
          </p>
        </div>

        {/* Tab Toggle Pill */}
        <div className="inline-flex p-1 rounded-full glass border border-white/40 self-start sm:self-auto">
          <button
            onClick={() => setActiveTab("games")}
            className={`px-5 py-2 rounded-full text-xs font-sans font-medium transition-all ${
              activeTab === "games"
                ? "bg-teal-800 text-white shadow-sm"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            Game Sessions
          </button>
          <button
            onClick={() => setActiveTab("leaderboard")}
            className={`px-5 py-2 rounded-full text-xs font-sans font-medium transition-all ${
              activeTab === "leaderboard"
                ? "bg-teal-800 text-white shadow-sm"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            Leaderboard
          </button>
        </div>
      </div>

      {/* 2. TAB: GAMES */}
      {activeTab === "games" && (
        <div className="space-y-6">
          {isLoadingGames ? (
            <div className="glass rounded-card p-12 text-center text-ink-muted border border-white/40">
              <Gamepad2 className="w-8 h-8 animate-spin-slow mx-auto mb-3 text-teal-800" />
              <span className="text-sm font-sans">Loading game arena...</span>
            </div>
          ) : games.length === 0 ? (
            <div className="glass rounded-card p-12 text-center text-ink-muted border border-white/40">
              <Gamepad2 className="w-10 h-10 mx-auto mb-3 text-teal-800 opacity-60" />
              <h3 className="font-serif text-2xl text-ink font-normal mb-2">No Active Games</h3>
              <p className="text-sm text-ink-muted max-w-sm mx-auto">
                Start a photo hunt or group bingo session on your handheld device or trigger one from the demo console.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {games.map((g) => {
                const isRunning = g.status === "running";
                return (
                  <Link
                    key={g.id}
                    to={`/games/${g.id}`}
                    className="group block rounded-card glass p-6 border border-white/40 transition-all duration-300 hover:shadow-card hover:-translate-y-1 hover:border-white/60 flex flex-col justify-between"
                  >
                    <div>
                      {/* Top Chips */}
                      <div className="flex items-center justify-between gap-2 mb-3">
                        <div className="flex items-center gap-2">
                          <span className="px-3 py-1 rounded-full text-xs font-sans font-semibold capitalize bg-teal-800 text-white shadow-sm">
                            {g.type.replace("_", " ")}
                          </span>
                          <span className="px-2.5 py-1 rounded-full text-xs font-mono font-bold bg-amber-500/20 text-amber-900 border border-amber-500/30">
                            CODE {g.code}
                          </span>
                        </div>

                        {isRunning ? (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-sans font-semibold bg-emerald-600/90 text-white shadow-sm">
                            <span className="w-2 h-2 rounded-full bg-white animate-pulse" />
                            Live
                          </span>
                        ) : (
                          <span className="text-xs text-ink-muted font-sans">Finished</span>
                        )}
                      </div>

                      {/* Title */}
                      <h3 className="font-serif text-2xl text-ink font-normal group-hover:text-teal-800 transition-colors mt-2">
                        {g.winner_name ? `Winner: ${g.winner_name}` : "Live Game In Progress"}
                      </h3>

                      {g.penalty && (
                        <p className="text-xs text-ink-muted font-sans mt-2">
                          Penalty: <span className="font-medium text-teal-950">{g.penalty}</span>
                        </p>
                      )}
                      {g.qr_url && isRunning && (
                        <div className="mt-4 p-3 rounded-inner glass-strong border border-white/40 flex items-center justify-between gap-3">
                          <div className="text-left">
                            <span className="text-[11px] font-sans font-semibold text-teal-950 block">
                              Scan to Join from Phone
                            </span>
                            <span className="text-[10px] text-ink-muted font-sans">
                              Point camera at QR code
                            </span>
                          </div>
                          <img
                            src={g.qr_url}
                            alt={`Join game ${g.code}`}
                            className="w-16 h-16 rounded-md bg-white p-1 border border-white/40 object-contain shrink-0"
                          />
                        </div>
                      )}
                    </div>

                    {/* Bottom stats row */}
                    <div className="mt-6 pt-4 border-t border-white/30 flex items-center justify-between text-xs font-sans text-ink-muted">
                      <div className="flex items-center gap-1.5">
                        <Users className="w-4 h-4 text-teal-700" />
                        <span>{g.players_count || 3} Players participating</span>
                      </div>
                      <div className="flex items-center gap-1 text-teal-800 font-semibold group-hover:translate-x-1 transition-transform">
                        <span>Enter room</span>
                        <ChevronRight className="w-4 h-4" />
                      </div>
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* 3. TAB: LEADERBOARD */}
      {activeTab === "leaderboard" && (
        <div className="space-y-6">
          {/* Filter Bar: Scope (Global / Friends) & Metric (Points, Places, Photos) */}
          <div className="glass rounded-card p-4 border border-white/40 shadow-sm flex flex-col sm:flex-row items-center justify-between gap-4">
            {/* Scope tabs */}
            <div className="inline-flex p-1 rounded-full glass-strong border border-white/30">
              <button
                onClick={() => setScope("global")}
                className={`px-4 py-1.5 rounded-full text-xs font-sans font-medium flex items-center gap-1.5 transition-all ${
                  scope === "global"
                    ? "bg-teal-800 text-white shadow-sm"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                <Globe className="w-3.5 h-3.5" />
                <span>Global</span>
              </button>
              <button
                onClick={() => setScope("friends")}
                className={`px-4 py-1.5 rounded-full text-xs font-sans font-medium flex items-center gap-1.5 transition-all ${
                  scope === "friends"
                    ? "bg-teal-800 text-white shadow-sm"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                <Users className="w-3.5 h-3.5" />
                <span>Friends</span>
              </button>
            </div>

            {/* Metric buttons */}
            <div className="flex items-center gap-2">
              {[
                { id: "points", label: "Points", icon: Trophy },
                { id: "places", label: "Places", icon: MapPin },
                { id: "photos", label: "Photos", icon: Camera },
              ].map((m) => {
                const Icon = m.icon;
                return (
                  <button
                    key={m.id}
                    onClick={() => setMetric(m.id as any)}
                    className={`px-3.5 py-1.5 rounded-full text-xs font-sans font-medium flex items-center gap-1.5 transition-all ${
                      metric === m.id
                        ? "bg-teal-800 text-white shadow-sm"
                        : "glass-strong border border-white/30 text-ink-muted hover:text-ink"
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" />
                    <span>{m.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Podium for Top 3 */}
          {topThree.length >= 3 && (
            <div className="grid grid-cols-3 gap-3 sm:gap-6 items-end pt-8 pb-4">
              {/* 2nd Place */}
              <div className="glass rounded-card p-4 sm:p-5 border border-white/40 text-center flex flex-col items-center justify-end h-56 sm:h-64 shadow-sm">
                <span className="text-2xl sm:text-3xl mb-1">🥈</span>
                <div className="w-12 h-12 sm:w-14 sm:h-14 rounded-full bg-slate-300 text-slate-800 font-serif font-bold text-lg flex items-center justify-center border-2 border-white/50 mb-2">
                  {(topThree[1].display_name || "A")[0]}
                </div>
                <h4 className="font-serif text-sm sm:text-base text-ink font-normal line-clamp-1">
                  {topThree[1].display_name}
                </h4>
                <span className="font-mono text-xs sm:text-sm font-semibold text-teal-800 mt-1">
                  {topThree[1].score} {metric}
                </span>
                <span className="text-[10px] text-ink-muted font-sans mt-0.5">Rank #2</span>
              </div>

              {/* 1st Place */}
              <div className="glass rounded-card p-4 sm:p-6 border border-amber-300/60 bg-amber-500/10 text-center flex flex-col items-center justify-end h-64 sm:h-76 shadow-md relative">
                <div className="absolute -top-4 w-8 h-8 rounded-full bg-amber-400 text-zinc-950 flex items-center justify-center shadow-md">
                  <Sparkles className="w-4 h-4" />
                </div>
                <span className="text-3xl sm:text-4xl mb-1">🥇</span>
                <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-full bg-amber-400 text-amber-950 font-serif font-bold text-xl flex items-center justify-center border-2 border-white mb-2 shadow-inner">
                  {(topThree[0].display_name || "A")[0]}
                </div>
                <h4 className="font-serif text-base sm:text-lg text-ink font-semibold line-clamp-1">
                  {topThree[0].display_name}
                </h4>
                <span className="font-mono text-sm sm:text-base font-bold text-amber-700 mt-1">
                  {topThree[0].score} {metric}
                </span>
                <span className="text-xs text-ink-muted font-sans mt-0.5">Champion</span>
              </div>

              {/* 3rd Place */}
              <div className="glass rounded-card p-4 sm:p-5 border border-white/40 text-center flex flex-col items-center justify-end h-52 sm:h-60 shadow-sm">
                <span className="text-2xl sm:text-3xl mb-1">🥉</span>
                <div className="w-12 h-12 sm:w-14 sm:h-14 rounded-full bg-amber-700/30 text-amber-900 font-serif font-bold text-lg flex items-center justify-center border-2 border-white/50 mb-2">
                  {(topThree[2].display_name || "A")[0]}
                </div>
                <h4 className="font-serif text-sm sm:text-base text-ink font-normal line-clamp-1">
                  {topThree[2].display_name}
                </h4>
                <span className="font-mono text-xs sm:text-sm font-semibold text-teal-800 mt-1">
                  {topThree[2].score} {metric}
                </span>
                <span className="text-[10px] text-ink-muted font-sans mt-0.5">Rank #3</span>
              </div>
            </div>
          )}

          {/* Ranked List Table */}
          <div className="glass rounded-card overflow-hidden border border-white/40 shadow-sm divide-y divide-white/20">
            {rankings.map((row) => (
              <div
                key={row.user_id}
                className={`flex items-center justify-between p-4 transition-colors ${
                  row.is_current_user ? "bg-teal-700/10 font-semibold" : "hover:bg-white/20"
                }`}
              >
                <div className="flex items-center gap-4">
                  <span className="font-mono font-bold text-sm text-teal-950 w-7 text-center">
                    #{row.rank}
                  </span>
                  <div className="w-9 h-9 rounded-full bg-teal-800 text-white flex items-center justify-center text-xs font-serif font-semibold">
                    {(row.display_name || "A")[0].toUpperCase()}
                  </div>
                  <div>
                    <h4 className="text-sm font-sans font-medium text-ink">
                      {row.display_name}
                      {row.is_current_user && (
                        <span className="ml-2 text-[10px] px-2 py-0.5 rounded-full bg-teal-800 text-white">
                          You
                        </span>
                      )}
                    </h4>
                    <span className="text-[11px] text-ink-muted font-sans">
                      @{row.username || "explorer"}
                    </span>
                  </div>
                </div>

                <div className="text-right">
                  <span className="font-mono text-sm font-semibold text-teal-950 block">
                    {row.score}
                  </span>
                  <span className="text-[10px] text-ink-muted font-sans capitalize">{metric}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
