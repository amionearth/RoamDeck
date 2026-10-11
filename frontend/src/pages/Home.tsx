import React, { useEffect, useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { TopBar } from "../components/TopBar";
import { HeroPanel } from "../components/HeroPanel";
import { WeatherCard } from "../components/WeatherCard";
import { PassTicket } from "../components/PassTicket";
import { JourneyCard } from "../components/JourneyCard";
import { QuickActionTiles } from "../components/QuickAction";
import { MapCard } from "../components/MapCard";
import { api } from "../lib/api";
import { DEFAULT_TRIP_IMAGE } from "../lib/assets";

export const Home: React.FC = () => {
  const [weather, setWeather] = useState<any>(null);
  const [feedTrips, setFeedTrips] = useState<any[]>([]);
  const [liveTrip, setLiveTrip] = useState<any>(null);
  const [latestFinishedTrip, setLatestFinishedTrip] = useState<any>(null);
  const [visitedPoints, setVisitedPoints] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [weatherRes, feedRes] = await Promise.all([
          api.get("/api/weather?lat=11.2588&lng=75.7804"),
          api.get("/api/feed?limit=12"),
        ]);
        setWeather(weatherRes);
        const trips = feedRes.items || [];
        setFeedTrips(trips);

        // Find in-progress trip
        const active = trips.find((t: any) => t.status === "in_progress");
        setLiveTrip(active);

        // Find latest finished trip
        const finished = trips.find((t: any) => t.status === "finished" || t.status === "arrived");
        setLatestFinishedTrip(finished);

        // Collect visited place points
        const points = trips.flatMap((t: any) => t.points || []).slice(0, 30);
        setVisitedPoints(points);
      } catch (err) {
        console.error("Home data error:", err);
      } finally {
        setIsLoading(false);
      }
    };
    fetchData();
  }, []);

  // Filtered recent journeys based on search query
  const filteredJourneys = useMemo(() => {
    if (!searchQuery.trim()) return feedTrips.slice(0, 4);
    const q = searchQuery.toLowerCase();
    return feedTrips.filter((t: any) => {
      const matchTitle = t.title?.toLowerCase().includes(q);
      const matchDest = t.destination_revealed && t.destination_name?.toLowerCase().includes(q);
      const matchMood = t.mood?.toLowerCase().includes(q);
      return matchTitle || matchDest || matchMood;
    });
  }, [feedTrips, searchQuery]);

  const featuredTrip = liveTrip || latestFinishedTrip;

  return (
    <div className="space-y-6 sm:space-y-8 animate-fade-in">
      {/* 1. Top Bar with Search (Home & Feed only) */}
      <TopBar
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="Search journeys, shorelines, moods..."
      />

      {/* 2. Desktop 12-Column Hero & Widget Section */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Left Hero (8 cols on desktop) */}
        <div className="lg:col-span-8">
          <HeroPanel
            trip={
              featuredTrip
                ? {
                    id: featuredTrip.id,
                    title: featuredTrip.title,
                    revealed: featuredTrip.destination_revealed,
                    destination: featuredTrip.destination_name,
                    status: featuredTrip.status,
                    cover_url: featuredTrip.cover_photo_id
                      ? `/api/trips/${featuredTrip.id}/photos/${featuredTrip.cover_photo_id}/thumb`
                      : DEFAULT_TRIP_IMAGE,
                    distance_km: featuredTrip.distance_m
                      ? Math.round(featuredTrip.distance_m / 100) / 10
                      : 12.4,
                    participants_count: featuredTrip.participants_count || 3,
                  }
                : null
            }
          />
        </div>

        {/* Right Stack (4 cols on desktop: Weather + Pass Ticket) */}
        <div className="lg:col-span-4 flex flex-col gap-6">
          <WeatherCard weather={weather} isLoading={isLoading} />

          {featuredTrip && (
            <div className="glass rounded-card p-4 border border-white/40 shadow-sm">
              <div className="flex items-center justify-between pb-3 mb-3 border-b border-white/30 text-xs font-sans text-ink-muted">
                <span className="font-medium text-teal-950">Journey boarding pass</span>
                <Link
                  to={`/trips/${featuredTrip.id}`}
                  className="text-teal-800 font-semibold hover:underline"
                >
                  Inspect →
                </Link>
              </div>
              <PassTicket
                id={featuredTrip.id}
                title={featuredTrip.title}
                destination={featuredTrip.destination_name}
                isRevealed={featuredTrip.destination_revealed}
                mood={featuredTrip.mood || "coastal"}
                status={featuredTrip.status}
                date={
                  featuredTrip.created_at
                    ? new Date(featuredTrip.created_at).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                      })
                    : "Oct 11"
                }
              />
            </div>
          )}
        </div>
      </div>

      {/* 3. Quick Action Buttons Row */}
      <div>
        <QuickActionTiles />
      </div>

      {/* 4. Recent Journeys Section */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="font-serif text-2xl sm:text-3xl text-ink font-normal break-words">
              Recent Journeys
            </h2>
            <p className="text-xs sm:text-sm text-ink-muted font-sans mt-0.5">
              Blind expeditions unrolled along the coast
            </p>
          </div>
          <Link
            to="/feed"
            className="text-xs sm:text-sm font-sans font-medium text-teal-800 flex items-center gap-1 hover:underline"
          >
            <span>View full feed</span>
            <ChevronRight className="w-4 h-4" />
          </Link>
        </div>

        {filteredJourneys.length === 0 ? (
          <div className="glass rounded-card p-8 text-center border border-white/40 text-sm text-ink-muted">
            No journeys match your search "{searchQuery}".
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {filteredJourneys.map((trip) => (
              <JourneyCard
                key={trip.id}
                id={trip.id}
                title={trip.title}
                revealed={trip.destination_revealed}
                destination={trip.destination_name}
                coverUrl={
                  trip.cover_photo_id
                    ? `/api/trips/${trip.id}/photos/${trip.cover_photo_id}/thumb`
                    : DEFAULT_TRIP_IMAGE
                }
                rating={trip.rating || 4.8}
                distanceKm={
                  trip.distance_m ? Math.round(trip.distance_m / 100) / 10 : 8.5
                }
                durationMinutes={trip.duration_minutes || 65}
                status={trip.status}
              />
            ))}
          </div>
        )}
      </div>

      {/* 5. Visited Exploration Map */}
      <div className="glass rounded-card p-4 sm:p-6 border border-white/40 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="font-serif text-xl sm:text-2xl text-ink font-normal">
              Explored Shoreline
            </h3>
            <p className="text-xs text-ink-muted font-sans mt-0.5">
              Recorded GPS footsteps and mystery waypoint stops
            </p>
          </div>
        </div>
        <div className="rounded-inner overflow-hidden border border-white/30 h-64 sm:h-80">
          <MapCard points={visitedPoints} className="w-full h-full" />
        </div>
      </div>
    </div>
  );
};
