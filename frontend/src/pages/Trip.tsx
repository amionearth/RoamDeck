import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  Heart,
  MessageSquare,
  Star,
  MapPin,
  Compass,
  UtensilsCrossed,
  Image as ImageIcon,
  Share2,
  Users,
  Send,
  Sparkles,
} from "lucide-react";
import { PassTicket } from "../components/PassTicket";
import { MapCard } from "../components/MapCard";
import { StampCase } from "../components/StampCase";
import { StampModal } from "../components/StampModal";
import { BlobImage } from "../components/BlobImage";
import { FallbackImage } from "../components/FallbackImage";
import { DEFAULT_TRIP_IMAGE } from "../lib/assets";
import { api } from "../lib/api";
import { realtime } from "../lib/ws";
import { useAuth } from "../lib/auth";

export const TripPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const [trip, setTrip] = useState<any>(null);
  const [photos, setPhotos] = useState<any[]>([]);
  const [comments, setComments] = useState<any[]>([]);
  const [stamp, setStamp] = useState<any>(null);
  const [newComment, setNewComment] = useState("");
  const [selectedMember, setSelectedMember] = useState<string | null>(null);
  const [starRating, setStarRating] = useState(5);
  const [reviewBody, setReviewBody] = useState("");
  const [reviewSubmitted, setReviewSubmitted] = useState(false);
  const [selectedStampForModal, setSelectedStampForModal] = useState<any | null>(null);
  const [activeTab, setActiveTab] = useState<"discussion" | "review">("discussion");
  const [isLoading, setIsLoading] = useState(true);
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [copiedInvite, setCopiedInvite] = useState(false);

  const fetchTripData = async () => {
    if (!id) return;
    try {
      const [tripRes, photosRes, commentsRes] = await Promise.all([
        api.get(`/api/trips/${id}`),
        api.get(`/api/trips/${id}/photos`),
        api.get(`/api/trips/${id}/comments`),
      ]);
      setTrip(tripRes);
      setPhotos(photosRes);
      setComments(commentsRes);

      // If arrived or finished, fetch the minted stamp if available
      if (tripRes.destination_revealed) {
        try {
          const stampRes = await api.get(`/api/trips/${id}/stamp`);
          if (stampRes && stampRes.id) {
            setStamp(stampRes);
          }
        } catch {
          // Fallback stamp object constructed from trip data
          setStamp({
            id: `stamp-${id.slice(0, 8)}`,
            trip_id: id,
            destination: tripRes.destination_name || "Secret Shoreline",
            rarity: tripRes.rarity || "rare",
            distance_m: tripRes.distance_m || 12400,
            duration_minutes: tripRes.duration_minutes || 75,
            rating: tripRes.rating || 4.9,
            created_at: tripRes.created_at,
            cover_url: tripRes.cover_photo_id
              ? `/api/trips/${id}/photos/${tripRes.cover_photo_id}/thumb`
              : DEFAULT_TRIP_IMAGE,
          });
        }
      }
    } catch (err) {
      console.error("Trip fetch error:", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchTripData();

    if (id) {
      realtime.connect();
      realtime.subscribe(`trip:${id}`);

      const unsubPos = realtime.on("trip.position", (data) => {
        if (data.trip_id === id) {
          setTrip((prev: any) =>
            prev ? { ...prev, points: [...(prev.points || []), data] } : prev
          );
        }
      });

      const unsubArrived = realtime.on("trip.arrived", (data) => {
        if (data.trip_id === id) {
          fetchTripData();
        }
      });

      const unsubWp = realtime.on("trip.waypoint_revealed", (data) => {
        if (data.trip_id === id) {
          fetchTripData();
        }
      });

      return () => {
        realtime.unsubscribe(`trip:${id}`);
        unsubPos();
        unsubArrived();
        unsubWp();
      };
    }
  }, [id]);

  const handleLike = async () => {
    if (!id) return;
    try {
      const res = await api.post(`/api/trips/${id}/like`);
      setTrip((prev: any) => ({
        ...prev,
        likes_count: res.likes_count,
        user_has_liked: res.liked,
      }));
    } catch (err) {
      console.error("Like error:", err);
    }
  };

  const handleCommentSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id || !newComment.trim()) return;
    try {
      const res = await api.post(`/api/trips/${id}/comments`, { body: newComment.trim() });
      setComments((prev) => [...prev, res]);
      setNewComment("");
    } catch (err) {
      console.error("Comment error:", err);
    }
  };

  const handleReviewSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id) return;
    try {
      await api.post(`/api/trips/${id}/review`, { rating: starRating, body: reviewBody });
      setReviewSubmitted(true);
      fetchTripData();
    } catch (err) {
      console.error("Review error:", err);
    }
  };

  if (isLoading || !trip) {
    return (
      <div className="glass rounded-card p-12 text-center text-ink-muted border border-white/40">
        <Compass className="w-8 h-8 animate-spin-slow mx-auto mb-3 text-teal-800" />
        <span className="text-sm font-sans">Connecting to blind journey telemetry...</span>
      </div>
    );
  }

  const filteredPhotos = selectedMember
    ? photos.filter((p) => p.author_name === selectedMember)
    : photos;

  const membersList = trip.members || [];
  const recipe = trip.plan?.recipe;
  const isFinished = trip.status === "finished" || trip.status === "arrived";
  const displayTitle = trip.destination_revealed
    ? trip.destination_name || trip.title
    : trip.title || "Blind coastal adventure";

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Top Breadcrumb / Status Line */}
      <div className="flex items-center justify-between pb-2 border-b border-white/30">
        <div className="flex items-center gap-2 text-xs font-sans text-ink-muted">
          <Link to="/" className="hover:text-teal-800">Home</Link>
          <span>/</span>
          <Link to="/feed" className="hover:text-teal-800">Journeys</Link>
          <span>/</span>
          <span className="text-teal-950 font-medium truncate max-w-[200px]">{displayTitle}</span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowInviteModal(true)}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-sans font-semibold bg-teal-800 hover:bg-teal-900 text-white transition-all shadow-sm"
          >
            <Users className="w-3.5 h-3.5 text-teal-200" />
            <span>Invite Friend / Stranger</span>
          </button>

          <button
            onClick={handleLike}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-xs font-sans font-medium transition-all border ${
              trip.user_has_liked
                ? "bg-rose-500/10 border-rose-400/40 text-rose-600"
                : "glass-strong border-white/40 text-ink-muted hover:text-ink"
            }`}
          >
            <Heart className={`w-3.5 h-3.5 ${trip.user_has_liked ? "fill-rose-500" : ""}`} />
            <span>{trip.likes_count || 0}</span>
          </button>
        </div>
      </div>

      {/* Main 2-Column Responsive Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* ================= LEFT COLUMN (7 COLS): Blob cover, Map, Photo Timeline ================= */}
        <div className="lg:col-span-7 space-y-6">
          {/* 1. Hero Scenic Header with Blob Mask */}
          <div className="glass rounded-card p-6 sm:p-8 border border-white/40 relative overflow-hidden">
            <div className="relative z-10">
              <div className="flex items-center gap-2 mb-3">
                {trip.status === "in_progress" ? (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-semibold bg-teal-800 text-white shadow-sm">
                    <span className="w-2 h-2 rounded-full bg-teal-300 animate-pulse" />
                    Live expedition
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-medium glass-strong text-teal-950 border border-white/40">
                    Completed expedition
                  </span>
                )}

                {!trip.destination_revealed ? (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-semibold bg-amber-500/90 text-white shadow-sm border border-amber-300/40">
                    Destination: ???
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-semibold bg-emerald-600 text-white shadow-sm">
                    Revealed destination
                  </span>
                )}
              </div>

              <h1 className="font-serif text-3xl sm:text-4xl text-ink font-normal leading-tight">
                {displayTitle}
              </h1>

              <p className="mt-2 text-sm text-ink-muted font-sans leading-relaxed">
                {trip.description ||
                  "A blind journey planned purely from intuition. The compass pointed forward until the coastline unveiled itself."}
              </p>
            </div>

            {/* Blob Image Mask Display */}
            <div className="mt-6 rounded-inner overflow-hidden aspect-[16/9] bg-white/20 relative shadow-sm">
              <BlobImage
                src={
                  trip.cover_photo_id
                    ? `/api/trips/${trip.id}/photos/${trip.cover_photo_id}/thumb`
                    : DEFAULT_TRIP_IMAGE
                }
                alt={displayTitle}
                blobIndex={1}
                className="w-full h-full object-cover"
              />
            </div>
          </div>

          {/* 2. Interactive Route & Waypoints Map */}
          <div className="glass rounded-card p-4 sm:p-6 border border-white/40 shadow-sm">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="font-serif text-xl sm:text-2xl text-ink font-normal">
                  Route & Waypoints
                </h3>
                <p className="text-xs text-ink-muted font-sans">
                  {trip.points?.length || 0} GPS breadcrumb pulses
                </p>
              </div>
              {trip.status === "in_progress" && (
                <span className="text-xs font-sans font-medium text-teal-800 bg-teal-100/60 px-2.5 py-1 rounded-full">
                  Receiving telemetry
                </span>
              )}
            </div>
            <div className="rounded-inner overflow-hidden border border-white/30 h-80 sm:h-96">
              <MapCard
                points={trip.points || []}
                waypoints={trip.waypoints || []}
                destination={
                  trip.destination_revealed
                    ? { lat: trip.destination_lat, lng: trip.destination_lng, name: trip.destination_name }
                    : null
                }
                isRevealed={trip.destination_revealed}
                isLive={trip.status === "in_progress"}
                className="w-full h-full"
              />
            </div>
          </div>

          {/* 3. Photo Masonry Timeline with Glass Bottom Bar */}
          <div className="glass rounded-card p-4 sm:p-6 border border-white/40 shadow-sm space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <h3 className="font-serif text-xl sm:text-2xl text-ink font-normal">
                  Photo Timeline
                </h3>
                <p className="text-xs text-ink-muted font-sans">
                  Moments captured along the blind path
                </p>
              </div>

              {/* Member filter buttons */}
              {membersList.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <button
                    onClick={() => setSelectedMember(null)}
                    className={`px-3 py-1 rounded-full text-xs font-sans font-medium transition-all ${
                      selectedMember === null
                        ? "bg-teal-800 text-white shadow-sm"
                        : "glass-strong text-ink-muted hover:text-ink border border-white/40"
                    }`}
                  >
                    All ({photos.length})
                  </button>
                  {membersList.map((m: any) => (
                    <button
                      key={m.display_name}
                      onClick={() => setSelectedMember(m.display_name)}
                      className={`px-3 py-1 rounded-full text-xs font-sans font-medium transition-all ${
                        selectedMember === m.display_name
                          ? "bg-teal-800 text-white shadow-sm"
                          : "glass-strong text-ink-muted hover:text-ink border border-white/40"
                      }`}
                    >
                      {m.display_name}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {filteredPhotos.length === 0 ? (
              <div className="p-8 text-center text-xs text-ink-muted font-sans rounded-inner bg-white/20">
                No moments captured along this segment yet.
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {filteredPhotos.map((photo) => (
                  <div
                    key={photo.id}
                    className="group relative rounded-inner overflow-hidden border border-white/30 bg-white/10 aspect-[4/3] flex flex-col justify-end"
                  >
                    <FallbackImage
                      src={photo.thumb_url || photo.url}
                      alt={photo.caption || "Journey photo"}
                      className="absolute inset-0 w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
                    />
                    {/* Glass caption bar along bottom */}
                    <div className="relative z-10 glass-strong p-3 border-t border-white/40 backdrop-blur-md">
                      <div className="flex items-center justify-between text-[11px] font-sans font-semibold text-teal-950">
                        <span>{photo.author_name || "Explorer"}</span>
                        <span className="text-ink-muted font-normal text-[10px]">
                          {photo.category || "Discovery"}
                        </span>
                      </div>
                      {photo.caption && (
                        <p className="text-xs text-ink font-sans mt-0.5 line-clamp-1 font-medium">
                          {photo.caption}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ================= RIGHT COLUMN (5 COLS STICKY): PassTicket, Stamp, Recipe, Tabs ================= */}
        <div className="lg:col-span-5 space-y-6 lg:sticky lg:top-20">
          {/* 1. Boarding Pass Ticket */}
          <div className="glass rounded-card p-4 sm:p-5 border border-white/40 shadow-sm">
            <h4 className="text-xs font-sans font-medium text-teal-950 pb-3 mb-3 border-b border-white/30">
              Trip Boarding Pass
            </h4>
            <PassTicket
              id={trip.id}
              title={trip.title}
              destination={trip.destination_name}
              isRevealed={trip.destination_revealed}
              mood={trip.mood || "coastal"}
              status={trip.status}
              date={
                trip.created_at
                  ? new Date(trip.created_at).toLocaleDateString("en-US", {
                      month: "short",
                      day: "numeric",
                    })
                  : "Oct 11"
              }
            />
          </div>

          {/* 2. Destination Stamp Case on Arrival */}
          {trip.destination_revealed && stamp && (
            <div className="space-y-2">
              <div className="flex items-center justify-between px-1">
                <span className="text-xs font-sans font-medium text-teal-950 flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-amber-500" />
                  Unlocked Destination Stamp
                </span>
                <span className="text-[11px] text-ink-muted font-sans">Tap to inspect</span>
              </div>
              <StampCase
                stamp={stamp}
                orientation="landscape"
                onOpen={() => setSelectedStampForModal(stamp)}
              />
            </div>
          )}

          {/* 3. Surprise Cooking Card (if trip plan included a recipe) */}
          {recipe && (
            <div className="glass rounded-card p-5 border border-white/40 shadow-sm space-y-3">
              <div className="flex items-center gap-2 text-teal-900 font-sans font-semibold text-xs">
                <UtensilsCrossed className="w-4 h-4 text-amber-600" />
                <span>Local Coastal Recipe Challenge</span>
              </div>
              <h4 className="font-serif text-lg text-ink font-normal">
                {recipe.dish_name || "Regional Specialty"}
              </h4>
              <ul className="space-y-1.5 text-xs text-ink-muted font-sans">
                {recipe.steps?.map((step: string, idx: number) => (
                  <li key={idx} className="flex items-start gap-2">
                    <span className="font-mono text-[11px] text-teal-800 font-bold">{idx + 1}.</span>
                    <span>{step}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 4. Tabbed Discussion / Review Widget */}
          <div className="glass rounded-card p-5 border border-white/40 shadow-sm space-y-4">
            <div className="flex items-center gap-2 border-b border-white/30 pb-3">
              <button
                onClick={() => setActiveTab("discussion")}
                className={`px-3 py-1.5 rounded-full text-xs font-sans font-medium transition-all ${
                  activeTab === "discussion"
                    ? "bg-teal-800 text-white shadow-sm"
                    : "glass-strong text-ink-muted hover:text-ink"
                }`}
              >
                Discussion ({comments.length})
              </button>
              {isFinished && (
                <button
                  onClick={() => setActiveTab("review")}
                  className={`px-3 py-1.5 rounded-full text-xs font-sans font-medium transition-all ${
                    activeTab === "review"
                      ? "bg-teal-800 text-white shadow-sm"
                      : "glass-strong text-ink-muted hover:text-ink"
                  }`}
                >
                  Trip Review
                </button>
              )}
            </div>

            {activeTab === "discussion" ? (
              <div className="space-y-3">
                <div className="max-h-60 overflow-y-auto space-y-2 pr-1">
                  {comments.length === 0 ? (
                    <p className="text-xs text-ink-muted font-sans py-2">
                      No notes yet. Be the first to share an observation.
                    </p>
                  ) : (
                    comments.map((c) => (
                      <div
                        key={c.id}
                        className="p-3 rounded-inner glass-strong border border-white/30 text-xs font-sans space-y-1"
                      >
                        <div className="flex items-center justify-between text-teal-950 font-semibold">
                          <span>{c.display_name}</span>
                          <span className="text-[10px] text-ink-muted font-normal">
                            {c.created_at
                              ? new Date(c.created_at).toLocaleTimeString([], {
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })
                              : ""}
                          </span>
                        </div>
                        <p className="text-ink font-normal">{c.body}</p>
                      </div>
                    ))
                  )}
                </div>

                <form onSubmit={handleCommentSubmit} className="flex gap-2 pt-2">
                  <input
                    type="text"
                    value={newComment}
                    onChange={(e) => setNewComment(e.target.value)}
                    placeholder="Write a message..."
                    className="flex-1 px-4 py-2 rounded-full glass-strong border border-white/40 text-xs font-sans focus:outline-none focus:ring-1 focus:ring-teal-700"
                  />
                  <button
                    type="submit"
                    disabled={!newComment.trim()}
                    className="w-8 h-8 rounded-full bg-teal-800 text-white flex items-center justify-center disabled:opacity-50 hover:bg-teal-900 transition-colors"
                  >
                    <Send className="w-3.5 h-3.5" />
                  </button>
                </form>
              </div>
            ) : (
              <div className="space-y-3">
                {reviewSubmitted ? (
                  <div className="p-4 rounded-inner bg-emerald-500/10 text-emerald-800 text-xs font-sans font-medium text-center border border-emerald-500/20">
                    Review submitted! Thank you for rating this journey.
                  </div>
                ) : (
                  <form onSubmit={handleReviewSubmit} className="space-y-3">
                    <div className="flex items-center gap-1.5">
                      {[1, 2, 3, 4, 5].map((s) => (
                        <button
                          type="button"
                          key={s}
                          onClick={() => setStarRating(s)}
                          className="p-0.5 hover:scale-110 transition-transform"
                        >
                          <Star
                            className={`w-5 h-5 ${
                              s <= starRating
                                ? "text-amber-400 fill-amber-400"
                                : "text-stone-300"
                            }`}
                          />
                        </button>
                      ))}
                    </div>
                    <textarea
                      rows={3}
                      value={reviewBody}
                      onChange={(e) => setReviewBody(e.target.value)}
                      placeholder="What surprised you most about this destination?"
                      className="w-full p-3 rounded-inner glass-strong border border-white/40 text-xs font-sans focus:outline-none focus:ring-1 focus:ring-teal-700"
                    />
                    <button
                      type="submit"
                      className="w-full py-2.5 rounded-full bg-teal-800 hover:bg-teal-900 text-white text-xs font-sans font-semibold transition-all shadow-sm"
                    >
                      Publish review
                    </button>
                  </form>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Stamp Inspection Modal */}
      {selectedStampForModal && (
        <StampModal
          stamp={selectedStampForModal}
          onClose={() => setSelectedStampForModal(null)}
        />
      )}

      {/* Blind Trip Invite Modal (No Destination Spoilers) */}
      {showInviteModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-fade-in">
          <div className="glass-strong rounded-[28px] max-w-md w-full p-6 sm:p-8 border border-white/40 shadow-2xl space-y-5">
            <div className="flex items-center justify-between border-b border-white/20 pb-3">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-full bg-teal-800 text-teal-200 flex items-center justify-center">
                  <Compass className="w-4 h-4" />
                </div>
                <h3 className="font-serif text-2xl text-ink font-normal">
                  Invite to Blind Trip
                </h3>
              </div>
              <button
                onClick={() => setShowInviteModal(false)}
                className="w-8 h-8 rounded-full bg-white/40 hover:bg-white/60 flex items-center justify-center text-ink text-sm font-semibold transition"
              >
                ✕
              </button>
            </div>

            <div className="bg-amber-500/10 border border-amber-500/20 rounded-inner p-3.5 flex items-start gap-3">
              <Sparkles className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
              <p className="text-[11px] font-sans text-amber-900 leading-relaxed">
                <strong>Anti-Spoiler Guarantee:</strong> Destination name and exact coordinates stay completely hidden from invitees until arrival is logged on the compass device!
              </p>
            </div>

            <div className="space-y-2.5 text-xs font-sans text-ink">
              <div className="flex justify-between py-1 border-b border-white/10">
                <span className="text-ink-muted">Expedition</span>
                <span className="font-medium text-teal-950">{trip.title || "Blind coastal adventure"}</span>
              </div>
              <div className="flex justify-between py-1 border-b border-white/10">
                <span className="text-ink-muted">Destination</span>
                <span className="font-mono font-bold text-amber-700 bg-amber-100/60 px-2 py-0.5 rounded">??? [SECRET]</span>
              </div>
              <div className="flex justify-between py-1 border-b border-white/10">
                <span className="text-ink-muted">Distance</span>
                <span className="font-medium">{trip.distance_m ? `${(trip.distance_m / 1000).toFixed(1)} km` : "2.8 km"}</span>
              </div>
              <div className="flex justify-between py-1 border-b border-white/10">
                <span className="text-ink-muted">Mood</span>
                <span className="capitalize font-medium">{trip.mood || "Adventure"}</span>
              </div>
              <div className="flex justify-between py-1">
                <span className="text-ink-muted">Host</span>
                <span className="font-medium text-teal-800">{trip.owner?.display_name || "Alex Explorer"}</span>
              </div>
            </div>

            <div className="pt-2 flex flex-col items-center justify-center gap-3">
              <div className="p-3 bg-white rounded-2xl shadow-sm border border-black/5">
                <img
                  src={`/api/friends/qr.png?code=TRIP-${trip.id.slice(0, 8).toUpperCase()}`}
                  alt="Invite QR"
                  className="w-36 h-36 object-contain"
                />
              </div>
              <p className="text-[11px] font-sans text-ink-muted text-center">
                Scan with any phone camera on the Wi-Fi network to join this blind expedition.
              </p>
            </div>

            <div className="space-y-2">
              <button
                onClick={() => {
                  const shareUrl = `${window.location.origin}/trips/${trip.id}`;
                  navigator.clipboard.writeText(shareUrl);
                  setCopiedInvite(true);
                  setTimeout(() => setCopiedInvite(false), 2500);
                }}
                className="w-full py-2.5 rounded-full bg-teal-800 hover:bg-teal-900 text-white text-xs font-sans font-semibold transition-all shadow-sm flex items-center justify-center gap-2"
              >
                <Share2 className="w-3.5 h-3.5" />
                <span>{copiedInvite ? "Copied Invite Link!" : "Copy Anti-Spoiler Invite Link"}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
