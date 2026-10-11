import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { Trophy, QrCode, Clock, Users, ThumbsUp, ThumbsDown, CheckCircle, AlertCircle } from "lucide-react";
import { Header } from "../components/Header";
import { api } from "../lib/api";
import { realtime } from "../lib/ws";

export const GameDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [game, setGame] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchGame = async () => {
    if (!id) return;
    try {
      const data = await api.get(`/api/games/${id}`);
      setGame(data);
    } catch (err) {
      console.error("Game detail fetch error:", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchGame();

    if (id) {
      realtime.connect();
      realtime.subscribe(`game:${id}`);

      const unsubSub = realtime.on("game.submission", () => fetchGame());
      const unsubStatus = realtime.on("game.submission_status", () => fetchGame());
      const unsubEnd = realtime.on("game.ended", () => fetchGame());

      return () => {
        realtime.unsubscribe(`game:${id}`);
        unsubSub();
        unsubStatus();
        unsubEnd();
      };
    }
  }, [id]);

  if (isLoading || !game) {
    return <div className="max-w-4xl mx-auto px-4 py-12 text-center text-mist">Loading Game Arena...</div>;
  }

  const isLive = game.status === "running" || game.status === "open";

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6 pb-24 lg:pb-12 space-y-8">
      {/* Header Banner & Live Status */}
      <div className="bg-paper rounded-card p-6 md:p-8 shadow-clay border border-mist/20 flex flex-col md:flex-row items-center justify-between gap-6">
        <div>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs font-semibold px-3 py-1 rounded-full uppercase bg-fog text-lagoon">
              {game.type.replace("_", " ")}
            </span>
            {isLive ? (
              <span className="text-xs font-bold text-signal bg-signal/15 px-3 py-1 rounded-full animate-pulse flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-signal" />
                Live Arena
              </span>
            ) : (
              <span className="text-xs font-semibold text-mist bg-fog px-3 py-1 rounded-full">
                Completed
              </span>
            )}
          </div>

          <h2 className="font-serif text-3xl md:text-4xl text-ink font-normal">
            Game Room: <span className="font-mono font-bold text-lagoon">{game.code}</span>
          </h2>
          <p className="text-sm text-mist mt-1">
            Penalty: <span className="text-ink font-semibold">{game.penalty}</span>
          </p>
        </div>

        {/* QR Code Stub */}
        <div className="flex flex-col items-center p-3 bg-fog rounded-tile border border-mist/20 shrink-0 text-center">
          <img src={game.qr_url} alt="Join QR Code" className="w-28 h-28 object-contain" />
          <span className="text-[10px] font-mono font-bold text-mist mt-1 uppercase">Scan to Join on Phone</span>
          <a
            href={game.join_url}
            target="_blank"
            rel="noreferrer"
            className="text-[11px] text-lagoon font-bold underline mt-0.5"
          >
            Open player link
          </a>
        </div>
      </div>

      {/* Live Scoreboard */}
      <div className="bg-paper rounded-card p-6 md:p-8 shadow-clay border border-mist/20">
        <h3 className="font-serif text-2xl text-ink font-normal mb-4 flex items-center gap-2">
          <Trophy className="w-6 h-6 text-signal" />
          <span>Scoreboard</span>
        </h3>

        <div className="space-y-3">
          {game.players.map((p: any, idx: number) => (
            <div
              key={p.id}
              className={`flex items-center justify-between p-4 rounded-tile border transition ${
                idx === 0
                  ? "bg-signal/10 border-signal/30 font-bold"
                  : "bg-fog/40 border-mist/15"
              }`}
            >
              <div className="flex items-center gap-3">
                <span className="font-mono text-base font-bold text-mist w-6">{idx + 1}.</span>
                <div
                  className="w-4 h-4 rounded-full"
                  style={{ backgroundColor: p.color || "#1E7F85" }}
                />
                <span className="text-ink text-sm md:text-base">{p.display_name}</span>
              </div>
              <span className="font-mono text-lg font-bold text-lagoon">{p.points} pts</span>
            </div>
          ))}
        </div>
      </div>

      {/* Submissions & Peer-Votes */}
      <div className="bg-paper rounded-card p-6 md:p-8 shadow-clay border border-mist/20">
        <h3 className="font-serif text-2xl text-ink font-normal mb-4">Player Submissions</h3>
        {game.submissions?.length === 0 ? (
          <p className="text-sm text-mist text-center py-8">
            Waiting for players to submit photos for their tasks...
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
            {game.submissions.map((sub: any) => (
              <div
                key={sub.photo_id}
                className="bg-fog/50 rounded-tile overflow-hidden border border-mist/20 flex flex-col justify-between"
              >
                <div className="w-full h-44 bg-fog overflow-hidden">
                  <img
                    src={sub.thumb_url || sub.url}
                    alt={sub.task_text}
                    className="w-full h-full object-cover"
                  />
                </div>
                <div className="p-3">
                  <span className="text-xs font-bold text-ink block">{sub.player_name}</span>
                  <p className="text-xs text-mist line-clamp-1">{sub.task_text}</p>
                  <div className="flex items-center justify-between mt-2 pt-2 border-t border-mist/15 text-[11px]">
                    <span
                      className={`font-semibold capitalize ${
                        sub.approval_status === "approved"
                          ? "text-green-600"
                          : sub.approval_status === "rejected"
                          ? "text-red-500"
                          : "text-signal"
                      }`}
                    >
                      {sub.approval_status}
                    </span>
                    <div className="flex items-center gap-2 text-mist">
                      <span className="flex items-center gap-1">
                        <ThumbsUp className="w-3 h-3 text-green-600" />
                        {sub.approvals}
                      </span>
                      <span className="flex items-center gap-1">
                        <ThumbsDown className="w-3 h-3 text-red-500" />
                        {sub.rejections}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
