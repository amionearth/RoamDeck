import React, { useEffect, useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { Heart, MessageSquare, Star, MapPin, Compass, Navigation } from "lucide-react";
import { TopBar } from "../components/TopBar";
import { api } from "../lib/api";

export const Feed: React.FC = () => {
  const [trips, setTrips] = useState<any[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedMood, setSelectedMood] = useState<string>("all");
  const [isLoading, setIsLoading] = useState(true);

  const fetchFeed = async () => {
    try {
      const data = await api.get("/api/feed?limit=30");
      setTrips(data.items || []);
    } catch (err) {
      console.error("Feed fetch error:", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchFeed();
  }, []);

  const handleLike = async (e: React.MouseEvent, tripId: string) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      const res = await api.post(`/api/trips/${tripId}/like`);
      setTrips((prev) =>
        prev.map((t) =>
          t.id === tripId
            ? { ...t, likes_count: res.likes_count, user_has_liked: res.liked }
            : t
        )
      );
    } catch (err) {
      console.error("Like error:", err);
    }
  };

  // Filtered journeys based on search & mood pill
  const filteredTrips = useMemo(() => {
    return trips.filter((t: any) => {
      const matchesMood =
        selectedMood === "all" ||
        t.mood?.toLowerCase() === selectedMood.toLowerCase();

      if (!matchesMood) return false;
      if (!searchQuery.trim()) return true;

      const q = searchQuery.toLowerCase();
      const matchTitle = t.title?.toLowerCase().includes(q);
      const matchDest =
        t.destination_revealed && t.destination_name?.toLowerCase().includes(q);
      const matchOwner = t.owner_name?.toLowerCase().includes(q);
      return matchTitle || matchDest || matchOwner;
    });
  }, [trips, searchQuery, selectedMood]);

  return (
    <div className="space-y-6 sm:space-y-8 animate-fade-in">
      {/* 1. Working Search Bar on Feed */}
      <TopBar
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="Search blind adventures, coastal destinations, owners..."
      />

      {/* 2. Header and Mood Filter Pills */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl sm:text-4xl text-ink font-normal">
            Explorer Feed
          </h1>
          <p className="mt-1 text-sm text-ink-muted font-sans">
            Recent blind journeys taken by travelers and their FarFromHome devices.
          </p>
        </div>

        {/* Mood Filter Pills */}
        <div className="inline-flex p-1 rounded-full glass border border-white/40 self-start sm:self-auto overflow-x-auto max-w-full">
          {["all", "calm", "curious", "chaotic", "romantic", "social"].map((m) => (
            <button
              key={m}
              onClick={() => setSelectedMood(m)}
              className={`px-3.5 py-1.5 rounded-full text-xs font-sans font-medium capitalize transition-all whitespace-nowrap ${
                selectedMood === m
                  ? "bg-teal-800 text-white shadow-sm"
                  : "text-ink-muted hover:text-ink"
              }`}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      {/* 3. Image-Led Cards Grid (2-3 columns on desktop) */}
      {isLoading ? (
        <div className="glass rounded-card p-12 text-center text-ink-muted border border-white/40">
          <Compass className="w-8 h-8 animate-spin-slow mx-auto mb-3 text-teal-800" />
          <span className="text-sm font-sans">Loading explorer journeys...</span>
        </div>
      ) : filteredTrips.length === 0 ? (
        <div className="glass rounded-card p-12 text-center text-sm text-ink-muted border border-white/40">
          No journeys found matching your filters.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredTrips.map((trip) => {
            const displayTitle = trip.destination_revealed
              ? trip.destination_name || trip.title
              : trip.title || "Blind coastal adventure";
            const coverImage = trip.cover_photo_id
              ? `/api/trips/${trip.id}/photos/${trip.cover_photo_id}/thumb`
              : "/seed/photos/beach_sunset.jpg";

            return (
              <Link
                key={trip.id}
                to={`/trips/${trip.id}`}
                className="group block rounded-card overflow-hidden glass p-3 transition-all duration-300 hover:shadow-card hover:-translate-y-1 hover:border-white/60 flex flex-col justify-between"
              >
                <div>
                  {/* Image container */}
                  <div className="relative aspect-[16/10] rounded-inner overflow-hidden bg-white/20">
                    <img
                      src={coverImage}
                      alt={displayTitle}
                      className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
                    />

                    {/* Gradient top & bottom vignettes */}
                    <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-black/20 pointer-events-none" />

                    {/* Top status badges */}
                    <div className="absolute top-2.5 left-2.5 right-2.5 z-10 flex items-center justify-between">
                      {!trip.destination_revealed ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-sans font-semibold bg-amber-500/90 text-white shadow-sm border border-amber-300/40">
                          <Compass className="w-3 h-3" />
                          Destination: ???
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-sans font-medium bg-emerald-600/90 text-white shadow-sm">
                          Arrived
                        </span>
                      )}

                      <span className="inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-sans font-medium capitalize bg-white/80 text-teal-950 backdrop-blur-md shadow-sm border border-white/40">
                        {trip.mood || "coastal"} wander
                      </span>
                    </div>

                    {/* Rating badge */}
                    {trip.rating && (
                      <div className="absolute bottom-2.5 right-2.5 z-10 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-sans font-semibold bg-white/90 text-teal-950 backdrop-blur-md shadow-sm">
                        <Star className="w-3 h-3 fill-amber-400 text-amber-400" />
                        <span>{trip.rating}</span>
                      </div>
                    )}
                  </div>

                  {/* Card Body */}
                  <div className="p-3">
                    <h3 className="font-serif text-xl text-ink font-normal leading-snug line-clamp-1 group-hover:text-teal-800 transition-colors">
                      {displayTitle}
                    </h3>
                    <p className="mt-1 text-xs text-ink-muted font-sans line-clamp-2">
                      {trip.description ||
                        "A spontaneous blind journey guided by compass bearings and local sensory clues."}
                    </p>
                  </div>
                </div>

                {/* Glass Bottom Bar: Author, stats & like button */}
                <div className="mt-2 pt-3 border-t border-white/30 flex items-center justify-between text-xs font-sans">
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 rounded-full bg-teal-800 text-white text-[10px] flex items-center justify-center font-serif">
                      {(trip.owner_name || "A")[0].toUpperCase()}
                    </div>
                    <span className="text-teal-950 font-medium">{trip.owner_name || "Explorer"}</span>
                  </div>

                  <div className="flex items-center gap-3">
                    <button
                      onClick={(e) => handleLike(e, trip.id)}
                      className={`flex items-center gap-1 transition-colors ${
                        trip.user_has_liked ? "text-rose-600 font-semibold" : "text-ink-muted hover:text-rose-600"
                      }`}
                    >
                      <Heart className={`w-3.5 h-3.5 ${trip.user_has_liked ? "fill-rose-500" : ""}`} />
                      <span>{trip.likes_count || 0}</span>
                    </button>

                    <div className="flex items-center gap-1 text-ink-muted">
                      <MessageSquare className="w-3.5 h-3.5" />
                      <span>{trip.comments_count || 0}</span>
                    </div>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
};
