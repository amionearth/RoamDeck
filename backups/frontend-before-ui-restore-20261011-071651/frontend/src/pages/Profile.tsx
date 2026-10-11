import React, { useEffect, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import {
  Shield,
  MapPin,
  Camera,
  Footprints,
  Compass,
  Trophy,
  Edit3,
  Award,
  UserPlus,
  UserCheck,
  Clock,
  Sparkles,
} from "lucide-react";
import { RingGauge } from "../components/RingGauge";
import { StyleRadar } from "../components/StyleRadar";
import { StampCase } from "../components/StampCase";
import { StampModal } from "../components/StampModal";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

export const Profile: React.FC = () => {
  const { username } = useParams<{ username: string }>();
  const [searchParams] = useSearchParams();
  const fromQr = searchParams.get("from_qr") === "1";
  const { user: currentUser } = useAuth();
  const [profile, setProfile] = useState<any>(null);
  const [summary, setSummary] = useState<any>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "stamps">("overview");
  const [showEditModal, setShowEditModal] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [isPrivate, setIsPrivate] = useState(false);
  const [selectedStampForModal, setSelectedStampForModal] = useState<any | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [requestStatus, setRequestStatus] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const targetUsername = username || currentUser?.username || "explorer";
  const isOwnProfile = currentUser && currentUser.username === targetUsername;

  const fetchProfileData = async () => {
    try {
      const [profData, sumData] = await Promise.all([
        api.get(`/api/users/${targetUsername}`),
        api.get(`/api/users/${targetUsername}/summary`),
      ]);
      setProfile(profData);
      setSummary(sumData);
      setIsPrivate(profData.is_private || false);
      setRequestStatus(profData.friendship_status || (profData.is_friend ? "accepted" : "none"));
      setEditTitle(profData.playercard?.title || "Blind Compass Voyager");
    } catch (err) {
      console.error("Profile fetch error:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSendFriendRequest = async () => {
    try {
      const res = await api.post("/api/friends/request", {
        username_or_code: profile.friend_code || profile.username,
        source: fromQr ? "meetup" : "search",
      });
      if (res.status === "already_friends" || res.status === "accepted") {
        setRequestStatus("accepted");
        setActionMessage("Connected as explorer companions!");
      } else {
        setRequestStatus("pending_outgoing");
        setActionMessage("Friend request sent!");
      }
      setTimeout(() => setActionMessage(null), 4000);
    } catch (err: any) {
      setActionMessage(err.message || "Could not send friend request");
    }
  };

  useEffect(() => {
    fetchProfileData();
  }, [targetUsername]);

  const handleTogglePrivate = async () => {
    try {
      const newPrivate = !isPrivate;
      setIsPrivate(newPrivate);
      await api.patch("/api/me", { is_private: newPrivate });
    } catch (err) {
      console.error("Privacy toggle error:", err);
    }
  };

  const handleSavePlayercard = async () => {
    try {
      await api.patch("/api/me/playercard", { title: editTitle });
      setShowEditModal(false);
      fetchProfileData();
    } catch (err) {
      console.error("Save playercard error:", err);
    }
  };

  if (isLoading || !profile) {
    return (
      <div className="glass rounded-card p-12 text-center text-ink-muted border border-white/40">
        <Compass className="w-8 h-8 animate-spin-slow mx-auto mb-3 text-teal-800" />
        <span className="text-sm font-sans">Opening explorer profile...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6 sm:space-y-8 animate-fade-in">
      {/* 1. Identity Banner Panel */}
      <div className="glass rounded-card p-6 sm:p-8 border border-white/40 shadow-sm relative overflow-hidden">
        <div className="flex flex-col md:flex-row items-center md:items-start gap-6 relative z-10">
          {/* Rank Shield */}
          <div className="relative w-24 h-24 sm:w-28 sm:h-28 flex flex-col items-center justify-center bg-gradient-to-br from-teal-700 to-teal-950 text-white rounded-3xl shadow-md shrink-0 border border-white/30">
            <Shield className="w-16 h-16 opacity-20 absolute" />
            <span className="text-[10px] uppercase font-sans font-semibold tracking-wider opacity-80 z-10">
              Level
            </span>
            <span className="font-serif text-3xl sm:text-4xl font-normal leading-none z-10 my-0.5">
              {summary?.level || 1}
            </span>
            <span className="text-[10px] font-sans font-medium text-amber-300 z-10">
              Rank Shield
            </span>
          </div>

          {/* User Details (NON-TRUNCATED USERNAME) */}
          <div className="flex-1 text-center md:text-left">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
              <div>
                <h1 className="font-serif text-3xl sm:text-4xl text-ink font-normal leading-tight">
                  {profile.display_name}
                </h1>
                <div className="flex flex-wrap items-center justify-center md:justify-start gap-2.5 text-xs text-ink-muted font-sans mt-1">
                  <span className="text-teal-950 font-semibold">@{profile.username}</span>
                  <span>•</span>
                  <span className="text-teal-800 font-medium">
                    {summary?.rank_title || "Coastal Wanderer"}
                  </span>
                  <span>•</span>
                  <span className="font-mono text-ink-muted">Code #{profile.friend_code}</span>
                </div>
              </div>

              {/* Action buttons for owner vs visitor */}
              {isOwnProfile ? (
                <div className="flex items-center gap-3 self-center md:self-auto">
                  <label className="flex items-center gap-2 cursor-pointer text-xs font-sans text-ink-muted hover:text-ink">
                    <input
                      type="checkbox"
                      checked={isPrivate}
                      onChange={handleTogglePrivate}
                      className="rounded text-teal-700 focus:ring-teal-700"
                    />
                    <span>Private Profile</span>
                  </label>
                  <button
                    onClick={() => setShowEditModal(true)}
                    className="px-4 py-1.5 rounded-full glass-strong border border-white/40 text-xs font-sans font-medium text-teal-950 hover:bg-white flex items-center gap-1.5 shadow-sm"
                  >
                    <Edit3 className="w-3.5 h-3.5" />
                    <span>Edit playercard</span>
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2.5 self-center md:self-auto">
                  {requestStatus === "accepted" ? (
                    <span className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-emerald-800 text-xs font-sans font-semibold">
                      <UserCheck className="w-4 h-4" />
                      <span>Companions</span>
                    </span>
                  ) : requestStatus === "pending_outgoing" ? (
                    <span className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-800 text-xs font-sans font-semibold">
                      <Clock className="w-4 h-4" />
                      <span>Request Sent</span>
                    </span>
                  ) : requestStatus === "pending_incoming" ? (
                    <Link
                      to="/friends"
                      className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-teal-800 text-white text-xs font-sans font-semibold hover:bg-teal-900 shadow-sm"
                    >
                      <UserPlus className="w-4 h-4" />
                      <span>Respond to Request</span>
                    </Link>
                  ) : (
                    <button
                      onClick={handleSendFriendRequest}
                      className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-teal-800 text-white text-xs font-sans font-semibold hover:bg-teal-900 shadow-sm transition"
                    >
                      <UserPlus className="w-4 h-4" />
                      <span>Add Companion</span>
                    </button>
                  )}
                </div>
              )}
            </div>

            {fromQr && !isOwnProfile && (
              <div className="mt-3 p-3 rounded-inner bg-teal-800/10 border border-teal-800/20 flex items-center justify-between text-xs text-teal-950">
                <span className="flex items-center gap-1.5">
                  <Sparkles className="w-4 h-4 text-teal-700" />
                  <strong>Scanned Explorer QR!</strong> You're viewing {profile.display_name}'s passport.
                </span>
                {requestStatus === "none" && (
                  <button
                    onClick={handleSendFriendRequest}
                    className="px-3 py-1 rounded-full bg-teal-800 text-white font-semibold text-[11px] hover:bg-teal-900"
                  >
                    Connect now
                  </button>
                )}
              </div>
            )}

            {actionMessage && (
              <div className="mt-2 text-xs font-sans font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-inner p-2 text-center">
                {actionMessage}
              </div>
            )}

            <p className="text-sm text-ink-muted font-sans mt-3 max-w-xl">
              {profile.bio ||
                "Exploring unmapped shores, fishing villages, and secret cliff viewpoints with FarFromHome."}
            </p>

            {/* Level XP Bar */}
            <div className="mt-4 pt-4 border-t border-white/30 max-w-lg">
              <div className="flex justify-between text-xs text-ink-muted font-sans mb-1.5">
                <span>Experience Progress</span>
                <span className="font-mono text-teal-950 font-medium">
                  {summary?.xp || 420} / {summary?.next_level_xp || 500} XP
                </span>
              </div>
              <div className="w-full h-2 rounded-full bg-white/40 overflow-hidden">
                <div
                  className="h-full bg-teal-800 rounded-full transition-all duration-700"
                  style={{ width: `${summary?.level_pct || 84}%` }}
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 2. Four Stat Tiles Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="glass rounded-card p-4 sm:p-5 border border-white/40 text-center shadow-sm">
          <Compass className="w-5 h-5 text-teal-700 mx-auto mb-2" />
          <span className="font-serif text-3xl text-ink font-normal block">
            {summary?.career_totals?.trips || 8}
          </span>
          <span className="text-xs text-ink-muted font-sans font-medium">Blind Trips</span>
        </div>

        <div className="glass rounded-card p-4 sm:p-5 border border-white/40 text-center shadow-sm">
          <Footprints className="w-5 h-5 text-amber-600 mx-auto mb-2" />
          <span className="font-serif text-3xl text-ink font-normal block font-mono">
            {summary?.career_totals?.distance_km || 94.6}
          </span>
          <span className="text-xs text-ink-muted font-sans font-medium">Distance (km)</span>
        </div>

        <div className="glass rounded-card p-4 sm:p-5 border border-white/40 text-center shadow-sm">
          <MapPin className="w-5 h-5 text-teal-800 mx-auto mb-2" />
          <span className="font-serif text-3xl text-ink font-normal block">
            {summary?.career_totals?.places || 18}
          </span>
          <span className="text-xs text-ink-muted font-sans font-medium">Places Discovered</span>
        </div>

        <div className="glass rounded-card p-4 sm:p-5 border border-white/40 text-center shadow-sm">
          <Camera className="w-5 h-5 text-purple-700 mx-auto mb-2" />
          <span className="font-serif text-3xl text-ink font-normal block">
            {summary?.career_totals?.photos || 36}
          </span>
          <span className="text-xs text-ink-muted font-sans font-medium">Photos Taken</span>
        </div>
      </div>

      {/* 3. Featured Playercard Banner with Featured Stamp */}
      <div className="glass rounded-card p-6 sm:p-8 border border-white/40 shadow-sm relative overflow-hidden">
        <div className="flex flex-col sm:flex-row items-center justify-between gap-6 relative z-10">
          <div>
            <span className="text-xs font-sans font-medium text-teal-900 block">
              Featured Playercard
            </span>
            <h2 className="font-serif text-2xl sm:text-3xl text-ink font-normal mt-1">
              {profile.playercard?.title || "Blind Compass Voyager"}
            </h2>
            <p className="text-xs text-ink-muted font-sans mt-1">
              Explorer serial certification #{profile.friend_code}
            </p>
          </div>

          {/* Top Badges */}
          <div className="flex items-center gap-2">
            {summary?.top_badges?.map((b: any, idx: number) => (
              <div
                key={idx}
                className="w-11 h-11 rounded-inner glass-strong border border-white/40 flex items-center justify-center shadow-sm text-teal-800"
                title={b.name}
              >
                <Trophy className="w-5 h-5 text-amber-500" />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 4. Small Ring Gauges (Collection Progress) & Travel Style Radar */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
        {/* Ring Gauges (5 cols) */}
        <div className="lg:col-span-5 glass rounded-card p-6 border border-white/40 shadow-sm flex flex-col justify-around">
          <h3 className="font-serif text-xl text-ink font-normal text-center mb-3">
            Collection Progress
          </h3>
          <div className="grid grid-cols-2 gap-4">
            <RingGauge
              current={summary?.rings?.badges_earned || 6}
              total={summary?.rings?.badges_total || 12}
              label="Badges Unlocked"
              color="#1B6C72"
            />
            <RingGauge
              current={summary?.rings?.stamps_collected || 8}
              total={summary?.rings?.stamps_target || 16}
              label="Passport Stamps"
              color="#D97706"
            />
          </div>
        </div>

        {/* Travel Style Radar (7 cols) */}
        <div className="lg:col-span-7 glass rounded-card p-6 border border-white/40 shadow-sm">
          <h3 className="font-serif text-xl text-ink font-normal mb-1">
            Travel Style Radar
          </h3>
          <p className="text-xs text-ink-muted font-sans mb-3">
            Exploration tendencies derived from blind journeys, tasks, and stamps.
          </p>
          <div className="h-64 sm:h-72">
            <StyleRadar data={summary?.radar || []} />
          </div>
        </div>
      </div>

      {/* 5. Career Bests & Mood Breakdown */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="glass rounded-card p-6 border border-white/40 shadow-sm">
          <h3 className="font-serif text-xl text-ink font-normal mb-4">Career Bests</h3>
          <div className="space-y-3">
            {summary?.career_bests?.map((cb: any, idx: number) => (
              <div
                key={idx}
                className="flex justify-between items-center py-2 border-b border-white/30 text-xs font-sans"
              >
                <span className="text-ink-muted">{cb.label}</span>
                <span className="font-semibold text-teal-950 font-mono">{cb.value}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="glass rounded-card p-6 border border-white/40 shadow-sm">
          <h3 className="font-serif text-xl text-ink font-normal mb-4">Exploration Moods</h3>
          <div className="space-y-3">
            {summary?.most_played_moods?.map((m: any, idx: number) => (
              <div key={idx} className="space-y-1">
                <div className="flex justify-between text-xs font-sans">
                  <span className="font-medium text-ink capitalize">{m.name}</span>
                  <span className="text-ink-muted font-mono">
                    {m.count} trips ({m.percentage}%)
                  </span>
                </div>
                <div className="w-full h-2 rounded-full bg-white/40 overflow-hidden">
                  <div
                    className="h-full bg-teal-800 rounded-full"
                    style={{ width: `${m.percentage}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Edit Playercard Modal */}
      {showEditModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="glass rounded-card max-w-sm w-full p-6 border border-white/40 shadow-2xl">
            <h3 className="font-serif text-2xl text-ink font-normal mb-2">Edit Playercard</h3>
            <div className="space-y-4">
              <div>
                <label className="text-xs font-sans text-ink-muted block mb-1">
                  Custom Title
                </label>
                <input
                  type="text"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  placeholder="e.g. Sunset Seeker"
                  className="w-full px-4 py-2 glass-strong rounded-full border border-white/40 text-xs font-sans focus:outline-none focus:ring-1 focus:ring-teal-700"
                />
              </div>
              <div className="flex space-x-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowEditModal(false)}
                  className="flex-1 py-2 rounded-full glass-strong border border-white/40 text-xs font-sans text-ink-muted"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleSavePlayercard}
                  className="flex-1 py-2 rounded-full bg-teal-800 text-white text-xs font-sans font-semibold shadow-sm"
                >
                  Save playercard
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Stamp Inspection Modal */}
      {selectedStampForModal && (
        <StampModal
          stamp={selectedStampForModal}
          onClose={() => setSelectedStampForModal(null)}
        />
      )}
    </div>
  );
};
