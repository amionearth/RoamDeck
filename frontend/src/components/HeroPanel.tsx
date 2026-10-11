import React from "react";
import { Link } from "react-router-dom";
import { Compass, Radio, Users, ChevronRight, Navigation } from "lucide-react";
import { BlobImage } from "./BlobImage";
import { DEFAULT_TRIP_IMAGE } from "../lib/assets";

interface HeroPanelProps {
  trip: {
    id: string;
    title: string;
    revealed: boolean;
    destination?: string | null;
    status: string;
    cover_url?: string | null;
    distance_km?: number;
    participants_count?: number;
    stamps_count?: number;
  } | null;
}

export const HeroPanel: React.FC<HeroPanelProps> = ({ trip }) => {
  if (!trip) {
    return (
      <div className="glass rounded-card p-8 lg:p-10 border border-white/40 min-h-[360px] flex flex-col justify-between relative overflow-hidden">
        <div className="max-w-md">
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-medium glass-strong text-teal-900 border border-white/40 mb-4">
            <Compass className="w-3.5 h-3.5 text-teal-700" />
            Device ready for exploration
          </span>
          <h1 className="font-serif text-3xl sm:text-4xl lg:text-5xl font-normal text-ink leading-tight break-words">
            No blind expedition currently active.
          </h1>
          <p className="mt-3 text-sm sm:text-base text-ink-muted font-sans leading-relaxed">
            Pick up your FarFromHome device, dial your travel mood, and set off. The destination remains secret until you arrive.
          </p>
        </div>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Link
            to="/passport"
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full glass-strong hover:bg-white text-teal-950 text-sm font-sans font-medium transition-all shadow-sm border border-white/50"
          >
            <span>View passport stamps</span>
            <ChevronRight className="w-4 h-4" />
          </Link>
          <Link
            to="/games"
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full bg-teal-800 hover:bg-teal-900 text-white text-sm font-sans font-medium transition-all shadow-sm"
          >
            <span>Explore game arena</span>
          </Link>
        </div>
      </div>
    );
  }

  const isLive = trip.status === "in_progress";
  const displayTitle = trip.revealed ? trip.destination || trip.title : trip.title || "Blind coastal adventure";
  const coverImage = trip.cover_url || DEFAULT_TRIP_IMAGE;

  return (
    <div className="glass rounded-card p-6 sm:p-8 lg:p-10 border border-white/40 relative overflow-hidden min-h-[380px] lg:min-h-[420px] flex flex-col justify-between">
      {/* Background scenic photo with organic blob mask or ambient blur */}
      <div className="absolute right-0 top-0 bottom-0 w-full sm:w-3/5 lg:w-1/2 pointer-events-none opacity-40 sm:opacity-85 transition-opacity">
        <BlobImage
          src={coverImage}
          alt={displayTitle}
          blobIndex={1}
          className="w-full h-full object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-r from-[rgba(247,249,248,0.95)] sm:from-[rgba(247,249,248,0.7)] to-transparent pointer-events-none" />
      </div>

      {/* Top status chips */}
      <div className="relative z-10 flex flex-wrap items-center gap-2.5 mb-6">
        {isLive ? (
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-semibold tracking-wide bg-teal-700 text-white shadow-sm">
            <span className="w-2 h-2 rounded-full bg-teal-300 animate-pulse" />
            Live journey in progress
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-medium glass-strong text-teal-950 border border-white/40">
            Completed journey
          </span>
        )}

        {!trip.revealed ? (
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-semibold bg-amber-500/90 text-white shadow-sm border border-amber-300/40">
            <Compass className="w-3.5 h-3.5" />
            Destination: ???
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-medium bg-emerald-600/90 text-white shadow-sm">
            Revealed upon arrival
          </span>
        )}
      </div>

      {/* Hero Headline & Intro */}
      <div className="relative z-10 max-w-xl">
        <h1 className="font-serif text-3xl sm:text-4xl lg:text-5xl font-normal text-ink leading-tight break-words">
          {displayTitle}
        </h1>
        <p className="mt-3 text-sm sm:text-base text-ink-muted font-sans max-w-md leading-relaxed">
          Follow the compass needle. Watch for mystery checkpoints, snap requested photos, and unlock the final stamp together.
        </p>

        {/* Quick stats chips */}
        <div className="mt-5 flex flex-wrap items-center gap-2 sm:gap-3 text-xs font-sans text-teal-950 font-medium">
          {trip.distance_km ? (
            <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full glass-strong border border-white/40">
              <Navigation className="w-3.5 h-3.5 text-teal-700" />
              <span>{trip.distance_km} km distance</span>
            </div>
          ) : null}

          {trip.participants_count ? (
            <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full glass-strong border border-white/40">
              <Users className="w-3.5 h-3.5 text-teal-700" />
              <span>{trip.participants_count} explorers</span>
            </div>
          ) : null}
        </div>
      </div>

      {/* Bottom CTA Row */}
      <div className="relative z-10 mt-8 pt-4 flex flex-wrap items-center gap-3">
        <Link
          to={`/trips/${trip.id}`}
          className="inline-flex items-center gap-2 px-6 py-3 rounded-full bg-teal-800 hover:bg-teal-900 text-white text-sm font-sans font-semibold transition-all shadow-md group"
        >
          <span>Watch live journey</span>
          <ChevronRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
        </Link>
        <Link
          to="/passport"
          className="inline-flex items-center gap-2 px-5 py-3 rounded-full glass-strong hover:bg-white text-teal-950 text-sm font-sans font-medium transition-all shadow-sm border border-white/50"
        >
          <span>Passport collection</span>
        </Link>
      </div>
    </div>
  );
};
