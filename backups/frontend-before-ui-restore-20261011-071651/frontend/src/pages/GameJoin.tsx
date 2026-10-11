import React, { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Camera, Check, X, Trophy, Users, Sparkles, Gamepad2, WifiOff, Clock } from "lucide-react";
import { api } from "../lib/api";

export const GameJoin: React.FC = () => {
  const { code } = useParams<{ code: string }>();
  const [gameInfo, setGameInfo] = useState<any>(null);
  const [playerToken, setPlayerToken] = useState<string | null>(
    localStorage.getItem(`ffh_player_token_${code}`)
  );
  const [myStatus, setMyStatus] = useState<any>(null);
  const [scoreboard, setScoreboard] = useState<any[]>([]);
  const [gallery, setGallery] = useState<any[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [customName, setCustomName] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [activeTab, setActiveTab] = useState<"task" | "vote" | "scores">("task");
  const [connectionError, setConnectionError] = useState(false);

  const gameCode = (code || "").toUpperCase();

  const fetchGameInfo = async () => {
    try {
      const info = await api.get(`/api/g/${gameCode}/info`);
      setGameInfo(info);
      setConnectionError(false);
    } catch (err: any) {
      console.error("Game fetch error:", err);
      setConnectionError(true);
    }
  };

  const fetchMyStatus = async (token: string) => {
    try {
      const data = await api.get(`/api/g/${gameCode}/me`, {
        headers: { "X-Player-Token": token },
      });
      setMyStatus(data);
      setConnectionError(false);
      if (data.tasks?.length > 0 && !selectedTaskId) {
        setSelectedTaskId(data.tasks[0].id);
      }
    } catch (err: any) {
      console.error(err);
      if (err.status === 401 || err.status === 404) {
        // Player token might be revoked because game ended
        fetchGameInfo();
      } else {
        setConnectionError(true);
      }
    }
  };

  const fetchGalleryAndScores = async (token: string) => {
    try {
      const [scoresData, galleryData] = await Promise.all([
        api.get(`/api/g/${gameCode}/scoreboard`),
        api.get(`/api/g/${gameCode}/gallery`, { headers: { "X-Player-Token": token } }),
      ]);
      setScoreboard(scoresData.scoreboard || []);
      setGallery(galleryData || []);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    fetchGameInfo();
    if (playerToken) {
      fetchMyStatus(playerToken);
      fetchGalleryAndScores(playerToken);
      const interval = setInterval(() => {
        fetchGameInfo();
        fetchMyStatus(playerToken);
        fetchGalleryAndScores(playerToken);
      }, 3000);
      return () => clearInterval(interval);
    }
  }, [gameCode, playerToken]);

  const handleJoin = async (playerId?: string, name?: string) => {
    try {
      const res = await api.post(`/api/g/${gameCode}/join`, {
        player_id: playerId,
        display_name: name,
      });
      setPlayerToken(res.token);
      localStorage.setItem(`ffh_player_token_${gameCode}`, res.token);
      fetchMyStatus(res.token);
      fetchGameInfo();
    } catch (err: any) {
      setStatusMessage(err.message || "Failed to join game");
    }
  };

  const handlePhotoUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !playerToken || !selectedTaskId) return;

    setUploading(true);
    setStatusMessage("Uploading photo task...");
    const formData = new FormData();
    formData.append("task_id", selectedTaskId);
    formData.append("photo", file);

    try {
      await api.post(`/api/g/${gameCode}/submissions`, formData, {
        headers: { "X-Player-Token": playerToken },
      });
      setStatusMessage("Photo submitted for peer voting!");
      fetchMyStatus(playerToken);
      setTimeout(() => setStatusMessage(""), 3500);
    } catch (err: any) {
      setStatusMessage(err.message || "Submission failed");
    } finally {
      setUploading(false);
    }
  };

  const handleVote = async (photoId: string, vote: "approve" | "reject") => {
    if (!playerToken) return;
    try {
      await api.post(
        `/api/g/${gameCode}/vote`,
        { photo_id: photoId, vote },
        { headers: { "X-Player-Token": playerToken } }
      );
      setGallery((prev) => prev.filter((p) => p.photo_id !== photoId));
    } catch (err: any) {
      console.error(err);
    }
  };

  // C4 Case 3: Connection failure screen
  if (connectionError && !gameInfo) {
    return (
      <div className="min-h-screen flex flex-col justify-center items-center p-4 sm:p-6 text-center animate-fade-in">
        <div className="glass rounded-3xl p-8 max-w-sm w-full border border-white/40 shadow-2xl space-y-4">
          <div className="w-14 h-14 rounded-2xl bg-amber-500/20 text-amber-700 flex items-center justify-center mx-auto border border-amber-500/40">
            <WifiOff className="w-7 h-7" />
          </div>
          <h2 className="font-serif text-2xl text-ink font-normal">
            Cannot reach the game
          </h2>
          <p className="text-xs text-ink-muted font-sans leading-relaxed">
            Check that your phone is connected to the same Wi-Fi network as the device.
          </p>
          <button
            onClick={() => {
              setConnectionError(false);
              fetchGameInfo();
            }}
            className="w-full py-2.5 rounded-full bg-teal-800 text-white font-sans text-xs font-semibold shadow-sm hover:bg-teal-900 transition-colors"
          >
            Retry Connection
          </button>
        </div>
      </div>
    );
  }

  // C4 Case 2: Game is closed (show final result and friendly message)
  const isGameClosed = gameInfo?.status === "closed" || myStatus?.game_status === "closed";
  if (isGameClosed) {
    return (
      <div className="min-h-screen flex flex-col justify-center items-center p-4 sm:p-6 text-center animate-fade-in">
        <div className="glass rounded-3xl p-8 max-w-sm w-full border border-white/40 shadow-2xl space-y-5">
          <div className="w-14 h-14 rounded-2xl bg-amber-500/20 text-amber-900 flex items-center justify-center mx-auto border border-amber-500/40">
            <Trophy className="w-7 h-7 text-amber-600" />
          </div>
          <div>
            <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-amber-500/20 text-amber-900 border border-amber-500/30 uppercase">
              ROOM #{gameCode}
            </span>
            <h1 className="font-serif text-3xl text-ink font-normal mt-2">
              This game has ended
            </h1>
            <p className="text-xs text-ink-muted font-sans mt-1">
              Thank you for playing! Final scores and achievements have been certified.
            </p>
          </div>

          {scoreboard.length > 0 && (
            <div className="space-y-2 pt-2 border-t border-white/30 text-left">
              <span className="text-xs font-sans font-semibold text-teal-950 block text-center mb-1">
                Final Leaderboard
              </span>
              {scoreboard.map((p, idx) => (
                <div
                  key={p.player_id}
                  className={`flex items-center justify-between p-2.5 rounded-xl border text-xs ${
                    idx === 0
                      ? "glass-strong border-amber-400/60 font-semibold text-amber-950"
                      : "glass border-white/30 text-ink"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono">{idx === 0 ? "🥇" : `#${idx + 1}`}</span>
                    <span>{p.display_name}</span>
                  </div>
                  <span className="font-mono font-bold">{p.points} pts</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  // 1. Join Screen: If player hasn't claimed a slot yet
  if (!playerToken) {
    return (
      <div className="min-h-screen flex flex-col justify-center items-center p-4 sm:p-6 text-center animate-fade-in">
        <div className="glass rounded-3xl p-6 sm:p-8 max-w-sm w-full border border-white/40 shadow-2xl space-y-5">
          <div>
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-mono font-bold bg-amber-500/20 text-amber-900 border border-amber-500/30">
              ROOM #{gameCode}
            </span>
            <h1 className="font-serif text-3xl text-ink font-normal mt-3">
              Join Challenge
            </h1>
            <p className="text-xs text-ink-muted font-sans mt-1">
              Select your name or enter a moniker to join this blind expedition challenge.
            </p>
          </div>

          {/* Assigned players buttons */}
          <div className="space-y-2">
            {gameInfo?.players?.map((p: any) => (
              <button
                key={p.id}
                onClick={() => handleJoin(p.id)}
                className="w-full py-3.5 px-4 rounded-2xl glass-strong border border-white/40 font-sans font-medium text-ink hover:bg-white transition-all flex items-center justify-between text-xs shadow-sm"
              >
                <span>{p.display_name}</span>
                <span className="text-teal-800 font-semibold">Join as player →</span>
              </button>
            ))}
          </div>

          {/* Custom name fallback */}
          <div className="pt-4 border-t border-white/30 space-y-2">
            <input
              type="text"
              value={customName}
              onChange={(e) => setCustomName(e.target.value)}
              placeholder="Or enter custom name..."
              className="w-full px-4 py-3 rounded-full glass-strong border border-white/40 text-xs font-sans text-center focus:outline-none focus:ring-1 focus:ring-teal-700"
            />
            <button
              onClick={() => handleJoin(undefined, customName)}
              disabled={!customName.trim()}
              className="w-full py-3 rounded-full bg-teal-800 text-white font-sans font-semibold text-xs shadow-md disabled:opacity-50 hover:bg-teal-900 transition-colors"
            >
              Enter Game Room
            </button>
          </div>
        </div>
      </div>
    );
  }

  // C4 Case 1: Player has joined, but game is still open/lobby (not started by host)
  const isGameInLobby = gameInfo?.status === "open" && myStatus?.game_status === "open";
  if (isGameInLobby) {
    return (
      <div className="min-h-screen flex flex-col justify-center items-center p-4 sm:p-6 text-center animate-fade-in">
        <div className="glass rounded-3xl p-6 sm:p-8 max-w-sm w-full border border-white/40 shadow-2xl space-y-5">
          <div className="w-14 h-14 rounded-2xl bg-teal-800 text-white flex items-center justify-center mx-auto shadow-md">
            <Clock className="w-7 h-7 text-teal-200 animate-pulse" />
          </div>

          <div>
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-mono font-bold bg-amber-500/20 text-amber-900 border border-amber-500/30">
              LOBBY #{gameCode}
            </span>
            <h2 className="font-serif text-2xl text-ink font-normal mt-3">
              Waiting for the host to start
            </h2>
            <p className="text-xs text-ink-muted font-sans mt-1">
              You are signed in as{" "}
              <strong className="text-teal-950 font-semibold">
                {myStatus?.player?.display_name || "Explorer"}
              </strong>
              . Once the device host presses Start, your photo tasks will appear.
            </p>
          </div>

            {/* Player List in Lobby */}
            <div className="space-y-2 pt-2 border-t border-white/30 text-left">
              <span className="text-xs font-sans font-semibold text-teal-950 block text-center mb-1">
                Connected Players ({gameInfo?.players?.filter((p: any) => p.claimed).length || 1})
              </span>
              {gameInfo?.players?.map((p: any) => (
                <div
                  key={p.id}
                  className="flex items-center justify-between p-2.5 rounded-xl glass-strong border border-white/40 text-xs"
                >
                  <div className="flex items-center gap-2">
                    <div
                      className="w-3 h-3 rounded-full"
                      style={{ backgroundColor: p.color || "#1E7F85" }}
                    />
                    <span className="font-sans font-medium text-ink">{p.display_name}</span>
                  </div>
                  {p.claimed ? (
                    <span className="text-[10px] text-emerald-700 font-semibold bg-emerald-500/20 px-2 py-0.5 rounded-full">
                      Ready
                    </span>
                  ) : (
                    <span className="text-[10px] text-ink-muted">Waiting...</span>
                  )}
                </div>
              ))}
            </div>

            {/* Test Action: Start Game Button */}
            <div className="pt-2">
              <button
                type="button"
                onClick={async () => {
                  try {
                    await api.post(`/api/g/${gameCode}/start`);
                    fetchGameInfo();
                    if (playerToken) fetchMyStatus(playerToken);
                  } catch (err: any) {
                    setStatusMessage(err.message || "Failed to start game");
                  }
                }}
                className="w-full py-3 rounded-full bg-emerald-700 hover:bg-emerald-800 text-white font-sans font-semibold text-xs shadow-md transition-all flex items-center justify-center gap-2"
              >
                <Sparkles className="w-4 h-4 text-emerald-200" />
                <span>Start Game (Host & Test)</span>
              </button>
            </div>
          </div>
        </div>
      );
    }

  // 2. In-Game Active Screen
  const currentTask =
    myStatus?.tasks?.find((t: any) => t.id === selectedTaskId) || myStatus?.tasks?.[0];

  return (
    <div className="min-h-screen pb-16 flex flex-col justify-between max-w-lg mx-auto p-4 sm:p-6 animate-fade-in">
      {/* Top Glass Header */}
      <div className="glass rounded-2xl p-4 border border-white/40 shadow-sm flex items-center justify-between">
        <div>
          <span className="text-[10px] font-sans text-ink-muted uppercase tracking-wider block">
            Explorer
          </span>
          <span className="font-serif text-lg font-normal text-ink">
            {myStatus?.player?.display_name || "Explorer"}
          </span>
        </div>
        <div className="text-right">
          <span className="text-[10px] font-sans text-ink-muted uppercase tracking-wider block">
            Score
          </span>
          <span className="font-mono font-bold text-teal-800 text-lg">
            {myStatus?.player?.score || 0} pts
          </span>
        </div>
      </div>

      {statusMessage && (
        <div className="my-3 p-3 glass rounded-inner border border-teal-800/40 text-teal-950 text-xs font-sans font-medium text-center shadow-sm">
          {statusMessage}
        </div>
      )}

      {/* Main Tab Controls */}
      <div className="inline-flex p-1 rounded-full glass border border-white/40 my-4 w-full">
        {(["task", "vote", "scores"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setActiveTab(t)}
            className={`flex-1 py-2 text-xs font-sans font-medium rounded-full capitalize transition-all ${
              activeTab === t
                ? "bg-teal-800 text-white shadow-sm"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            {t} {t === "vote" && gallery.length > 0 && `(${gallery.length})`}
          </button>
        ))}
      </div>

      {/* TAB: TASK CAMERA */}
      {activeTab === "task" && (
        <div className="flex-1 flex flex-col justify-center">
          {currentTask ? (
            <div className="glass rounded-3xl p-6 sm:p-8 border border-white/40 text-center space-y-5 shadow-lg">
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-semibold bg-amber-500/20 text-amber-900 border border-amber-500/30">
                <Sparkles className="w-3.5 h-3.5" />
                Task #{currentTask.idx + 1} • {currentTask.points} Points
              </span>

              <h2 className="font-serif text-2xl sm:text-3xl text-ink font-normal leading-tight">
                {currentTask.text}
              </h2>

              {currentTask.status === "completed" ? (
                <div className="py-4 text-emerald-800 font-sans font-semibold flex items-center justify-center gap-2">
                  <Check className="w-5 h-5" />
                  <span>Task Completed & Approved!</span>
                </div>
              ) : (
                <div className="pt-2">
                  {/* Large Touch-friendly Take Photo Button */}
                  <label className="flex flex-col items-center justify-center w-full py-8 px-4 rounded-2xl bg-teal-800 hover:bg-teal-900 text-white shadow-lg cursor-pointer transform active:scale-95 transition-all">
                    <Camera className="w-10 h-10 mb-2 text-teal-200" />
                    <span className="text-sm font-sans font-semibold">Take & Submit Photo</span>
                    <span className="text-[11px] text-teal-200/80 mt-1">
                      Tap to activate phone camera
                    </span>
                    <input
                      type="file"
                      accept="image/*"
                      capture="environment"
                      onChange={handlePhotoUpload}
                      disabled={uploading}
                      className="hidden"
                    />
                  </label>
                </div>
              )}
            </div>
          ) : (
            <div className="glass rounded-2xl p-8 text-center text-xs text-ink-muted font-sans border border-white/40">
              No active tasks assigned.
            </div>
          )}
        </div>
      )}

      {/* TAB: PEER VOTING */}
      {activeTab === "vote" && (
        <div className="flex-1 space-y-4">
          <h3 className="font-serif text-xl text-ink text-center">Vote on Companion Photos</h3>
          {gallery.length === 0 ? (
            <div className="glass rounded-2xl p-8 text-center text-xs text-ink-muted font-sans border border-white/40">
              No submissions currently waiting for your peer review.
            </div>
          ) : (
            gallery.map((item) => (
              <div
                key={item.photo_id}
                className="glass rounded-2xl overflow-hidden border border-white/40 shadow-md"
              >
                <img
                  src={item.file_url || item.thumb_url}
                  alt="Submission"
                  className="w-full h-56 object-cover"
                />
                <div className="p-4 text-center">
                  <span className="text-xs font-sans font-semibold text-ink block">
                    {item.player_name}
                  </span>
                  <p className="text-xs text-ink-muted font-sans mt-0.5">{item.task_text}</p>
                  <div className="flex gap-3 mt-4">
                    <button
                      onClick={() => handleVote(item.photo_id, "reject")}
                      className="flex-1 py-2.5 rounded-full border border-rose-400 text-rose-600 font-sans font-semibold text-xs flex items-center justify-center gap-1.5 active:bg-rose-50"
                    >
                      <X className="w-4 h-4" /> Reject
                    </button>
                    <button
                      onClick={() => handleVote(item.photo_id, "approve")}
                      className="flex-1 py-2.5 rounded-full bg-emerald-700 text-white font-sans font-semibold text-xs flex items-center justify-center gap-1.5 active:bg-emerald-800 shadow-sm"
                    >
                      <Check className="w-4 h-4" /> Approve
                    </button>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* TAB: SCOREBOARD */}
      {activeTab === "scores" && (
        <div className="flex-1 space-y-3">
          <h3 className="font-serif text-xl text-ink text-center">Live Scoreboard</h3>
          {scoreboard.map((p, idx) => (
            <div
              key={p.player_id}
              className={`flex items-center justify-between p-4 rounded-2xl border transition-all ${
                idx === 0
                  ? "glass bg-amber-500/15 border-amber-300/50 font-semibold"
                  : "glass border-white/40"
              }`}
            >
              <div className="flex items-center gap-3">
                <span className="font-mono text-sm font-bold text-ink-muted w-5">
                  #{idx + 1}
                </span>
                <span className="text-ink text-sm font-sans font-medium">{p.display_name}</span>
              </div>
              <span className="font-mono font-bold text-teal-800 text-sm">{p.points} pts</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
