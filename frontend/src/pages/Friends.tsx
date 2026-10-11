import React, { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import QRCode from "qrcode";
import { Check, X, QrCode, Search, Users, Copy, Sparkles, UserPlus } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

export const Friends: React.FC = () => {
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const [friendsData, setFriendsData] = useState<any>({ friends: [], pending_requests: [] });
  const [addCode, setAddCode] = useState(searchParams.get("add") || "");
  const [qrDataUrl, setQrDataUrl] = useState<string>("");
  const [copied, setCopied] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  const fetchFriends = async () => {
    try {
      const data = await api.get("/api/friends");
      setFriendsData(data);
    } catch (err) {
      console.error("Friends fetch error:", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchFriends();
    const queryAdd = searchParams.get("add");
    if (queryAdd) {
      setAddCode(queryAdd.trim());
    }
  }, [searchParams]);

  const friendCode = friendsData?.friend_code || user?.friend_code || "FFH-7492";

  const handleCopyCode = () => {
    navigator.clipboard.writeText(friendCode);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleAddByCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!addCode.trim()) return;
    try {
      const res = await api.post("/api/friends/request", { username_or_code: addCode.trim() });
      setActionMessage(res.status === "already_friends" ? "Already connected as friends!" : "Companion request sent!");
      setAddCode("");
      fetchFriends();
      setTimeout(() => setActionMessage(""), 4000);
    } catch (err: any) {
      setActionMessage(err.message || "Failed to add user");
    }
  };

  const handleAccept = async (id: string) => {
    try {
      await api.post(`/api/friends/${id}/accept`);
      fetchFriends();
    } catch (err) {
      console.error("Accept error:", err);
    }
  };

  const handleRemove = async (id: string) => {
    try {
      await api.delete(`/api/friends/${id}`);
      fetchFriends();
    } catch (err) {
      console.error("Remove error:", err);
    }
  };

  return (
    <div className="space-y-6 sm:space-y-8 animate-fade-in">
      {/* 1. Page Header */}
      <div>
        <h1 className="font-serif text-3xl sm:text-4xl text-ink font-normal">
          Explorer Companions
        </h1>
        <p className="mt-1 text-sm text-ink-muted font-sans">
          Manage fellow adventurers, accept requests, and connect devices.
        </p>
      </div>

      {actionMessage && (
        <div className="p-3 glass rounded-inner border border-teal-800/30 text-teal-950 text-xs text-center font-sans font-medium">
          {actionMessage}
        </div>
      )}

      {/* 2. Responsive 2-Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Left Column (5 cols): Friend Code Card + QR + Add Input */}
        <div className="lg:col-span-5 space-y-6">
          <div className="glass rounded-card p-6 border border-white/40 shadow-sm space-y-5">
            <div>
              <span className="text-xs font-sans font-medium text-teal-950 block">
                Your Explorer Code
              </span>
              <div className="flex items-center justify-between mt-1">
                <span className="font-mono text-3xl font-bold text-teal-800 tracking-wider">
                  {friendCode}
                </span>
                <button
                  onClick={handleCopyCode}
                  className="px-3 py-1.5 rounded-full glass-strong border border-white/40 text-xs font-sans font-medium text-teal-950 hover:bg-white flex items-center gap-1.5 shadow-sm"
                >
                  <Copy className="w-3.5 h-3.5" />
                  <span>{copied ? "Copied" : "Copy"}</span>
                </button>
              </div>
              <p className="text-xs text-ink-muted font-sans mt-2">
                Share this serial code with friends so they can add you to blind trips.
              </p>
            </div>

            {/* QR Code Container (Server-provided QR using LAN address) */}
            <div className="flex flex-col items-center justify-center p-4 rounded-inner bg-white/40 border border-white/50">
              <img
                src={`/api/friends/qr.png?code=${encodeURIComponent(friendCode)}`}
                alt="Explorer Friend Invite QR"
                className="w-40 h-40 object-contain rounded-lg bg-white p-1 shadow-sm"
              />
              <span className="text-[11px] text-ink-muted font-sans mt-2">
                Scan with any phone camera on LAN to connect
              </span>
            </div>

            {/* Add Friend Form */}
            <form onSubmit={handleAddByCode} className="space-y-2 pt-2 border-t border-white/30">
              <label className="text-xs font-sans font-medium text-teal-950 block">
                Connect with Explorer Code
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={addCode}
                  onChange={(e) => setAddCode(e.target.value)}
                  placeholder="Enter code (e.g. FFH-2914)"
                  className="flex-1 px-4 py-2.5 rounded-full glass-strong border border-white/40 text-xs font-mono uppercase tracking-wider focus:outline-none focus:ring-1 focus:ring-teal-700"
                />
                <button
                  type="submit"
                  disabled={!addCode.trim()}
                  className="px-5 py-2.5 rounded-full bg-teal-800 text-white text-xs font-sans font-semibold disabled:opacity-50 hover:bg-teal-900 transition-colors shadow-sm"
                >
                  Connect
                </button>
              </div>
            </form>
          </div>
        </div>

        {/* Right Column (7 cols): Pending Requests & Friends Grid */}
        <div className="lg:col-span-7 space-y-6">
          {/* Pending Requests */}
          {friendsData.pending_requests?.length > 0 && (
            <div className="glass rounded-card p-6 border border-white/40 shadow-sm space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-serif text-xl text-ink font-normal">Pending Requests</h3>
                <span className="text-xs font-sans font-medium text-amber-700 bg-amber-500/10 px-2.5 py-0.5 rounded-full">
                  {friendsData.pending_requests.length} new
                </span>
              </div>

              <div className="space-y-3">
                {friendsData.pending_requests.map((req: any) => (
                  <div
                    key={req.friendship_id}
                    className="p-3.5 rounded-inner glass-strong border border-white/30 flex items-center justify-between"
                  >
                    <div>
                      <span className="font-sans font-semibold text-sm text-ink block">
                        {req.display_name}
                      </span>
                      <span className="text-xs text-ink-muted font-sans">@{req.username}</span>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleAccept(req.friendship_id)}
                        className="w-8 h-8 rounded-full bg-teal-800 text-white flex items-center justify-center hover:bg-teal-900 shadow-sm"
                        title="Accept connection"
                      >
                        <Check className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleRemove(req.friendship_id)}
                        className="w-8 h-8 rounded-full glass-strong border border-white/40 text-ink-muted hover:text-ink flex items-center justify-center"
                        title="Decline"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Friends List */}
          <div className="glass rounded-card p-6 border border-white/40 shadow-sm space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-white/30">
              <h3 className="font-serif text-2xl text-ink font-normal">
                Companions ({friendsData.friends?.length || 0})
              </h3>
            </div>

            {friendsData.friends?.length === 0 ? (
              <p className="text-xs text-ink-muted font-sans text-center py-8">
                No companions added yet. Share your code to connect with friends!
              </p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {friendsData.friends.map((f: any) => (
                  <div
                    key={f.friendship_id}
                    className="p-3.5 rounded-inner glass-strong border border-white/30 flex items-center justify-between"
                  >
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-full bg-teal-800 text-white font-serif text-xs flex items-center justify-center font-bold">
                        {f.display_name.slice(0, 2).toUpperCase()}
                      </div>
                      <div>
                        <span className="font-sans font-semibold text-sm text-ink block line-clamp-1">
                          {f.display_name}
                        </span>
                        <span className="text-xs text-ink-muted font-sans">@{f.username}</span>
                      </div>
                    </div>

                    <button
                      onClick={() => handleRemove(f.friendship_id)}
                      className="text-xs text-ink-muted hover:text-rose-600 transition-colors px-2 py-1"
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
