import React, { useState } from "react";
import { Play, RotateCcw, Database, Compass, Flag, Gamepad2, StopCircle, Terminal, Trash2 } from "lucide-react";
import { api } from "../lib/api";

export const DemoPage: React.FC = () => {
  const [logs, setLogs] = useState<string[]>([
    "FarFromHome demo control console initialized. Ready for hackathon presentation.",
  ]);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  const addLog = (msg: string) => {
    const time = new Date().toLocaleTimeString();
    setLogs((prev) => [`[${time}] ${msg}`, ...prev.slice(0, 49)]);
  };

  const handleAction = async (name: string, endpoint: string, body: any = {}) => {
    setLoadingAction(name);
    try {
      const res = await api.post(endpoint, body);
      addLog(`${name} SUCCESS: ${JSON.stringify(res)}`);
    } catch (err: any) {
      addLog(`${name} FAILED: ${err.message}`);
    } finally {
      setLoadingAction(null);
    }
  };

  const actions = [
    {
      name: "Seed Rich Demo Data",
      desc: "6 users, 8 finished trips, 8 stamps, 6 badges, friends, active games",
      endpoint: "/api/demo/seed",
      icon: Database,
      color: "text-teal-700",
      bgColor: "bg-teal-100/60",
    },
    {
      name: "Start Fake Trip",
      desc: "Broadcast live blind expedition with destination: ???",
      endpoint: "/api/demo/start-fake-trip",
      icon: Compass,
      color: "text-amber-600",
      bgColor: "bg-amber-100/60",
    },
    {
      name: "Trigger Arrival",
      desc: "Report arrival, tear ticket, reveal destination, mint certified stamp",
      endpoint: "/api/demo/arrive-trip",
      icon: Flag,
      color: "text-emerald-700",
      bgColor: "bg-emerald-100/60",
    },
    {
      name: "Start Fake Game",
      desc: "Spin up live group challenge with 3 simulated players and photo tasks",
      endpoint: "/api/demo/start-fake-game",
      body: { players_count: 3 },
      icon: Gamepad2,
      color: "text-indigo-700",
      bgColor: "bg-indigo-100/60",
    },
    {
      name: "End Active Game",
      desc: "Freeze scores, select top winner, and award player XP",
      endpoint: "/api/demo/end-game",
      icon: StopCircle,
      color: "text-rose-600",
      bgColor: "bg-rose-100/60",
    },
    {
      name: "Reset Database",
      desc: "Wipe all database records, cached weather, and uploaded photos",
      endpoint: "/api/demo/reset",
      icon: RotateCcw,
      color: "text-rose-700",
      bgColor: "bg-rose-100/60",
    },
  ];

  return (
    <div className="space-y-6 sm:space-y-8 animate-fade-in">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-amber-500/20 text-amber-900 border border-amber-500/30 uppercase tracking-wider">
            Shortcut: Ctrl + Shift + D
          </span>
        </div>
        <h1 className="font-serif text-3xl sm:text-4xl text-ink font-normal">
          Demo Safety Control Panel
        </h1>
        <p className="mt-1 text-sm text-ink-muted font-sans">
          One-click stage fallbacks, simulators, and live presentation state triggers.
        </p>
      </div>

      {/* 6 Actions Grid in Glass Tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4 sm:gap-6">
        {actions.map((act) => {
          const Icon = act.icon;
          const isBusy = loadingAction === act.name;

          return (
            <button
              key={act.name}
              onClick={() => handleAction(act.name, act.endpoint, act.body || {})}
              disabled={loadingAction !== null}
              className="glass rounded-card p-6 border border-white/40 text-left transition-all duration-300 hover:shadow-card hover:-translate-y-1 hover:border-white/60 flex flex-col justify-between group disabled:opacity-50"
            >
              <div>
                <div
                  className={`w-11 h-11 rounded-full ${act.bgColor} flex items-center justify-center mb-4 transition-transform group-hover:scale-110 shadow-inner`}
                >
                  <Icon className={`w-5 h-5 ${act.color}`} />
                </div>
                <h3 className="font-serif text-xl text-ink font-normal group-hover:text-teal-800 transition-colors">
                  {act.name}
                </h3>
                <p className="text-xs text-ink-muted font-sans mt-1.5 leading-relaxed">
                  {act.desc}
                </p>
              </div>

              <div className="mt-6 pt-3 border-t border-white/30 text-[11px] font-sans font-semibold text-teal-800 flex items-center justify-between">
                <span>{isBusy ? "Triggering..." : "Execute trigger"}</span>
                <span className="opacity-0 group-hover:opacity-100 transition-opacity">→</span>
              </div>
            </button>
          );
        })}
      </div>

      {/* Terminal Telemetry Log Output (glass-dark acrylic styling) */}
      <div className="glass-dark rounded-card p-6 border border-white/10 shadow-2xl font-mono text-xs text-white/90 space-y-3">
        <div className="flex items-center justify-between pb-3 border-b border-white/10 text-white/60">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-amber-400" />
            <span className="text-xs font-sans font-medium text-white/80">
              Demo Event Telemetry Log
            </span>
          </div>
          <button
            onClick={() => setLogs([])}
            className="flex items-center gap-1 text-[11px] hover:text-white transition-colors"
          >
            <Trash2 className="w-3 h-3" />
            <span>Clear</span>
          </button>
        </div>

        <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1">
          {logs.map((log, i) => (
            <div key={i} className="text-white/80 leading-relaxed font-mono text-[11px]">
              {log}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
