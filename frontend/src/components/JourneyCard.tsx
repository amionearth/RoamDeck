import React from "react";
import { Link } from "react-router-dom";
import { ChevronRight, Star, Compass } from "lucide-react";
import { FallbackImage } from "./FallbackImage";
import { DEFAULT_TRIP_IMAGE } from "../lib/assets";

interface JourneyCardProps {
  id: string;
  title: string;
  revealed: boolean;
  destination?: string | null;
  coverUrl?: string | null;
  rating?: number;
  distanceKm?: number;
  durationMinutes?: number;
  status?: string;
}

export const JourneyCard: React.FC<JourneyCardProps> = ({
  id,
  title,
  revealed,
  destination,
  coverUrl,
  rating,
  distanceKm,
  durationMinutes,
  status,
}) => {
  // If not revealed, NEVER show destination name (anti-spoiler)
  const displayTitle = revealed ? destination || title : title || "Blind expedition";
  const imageSrc = coverUrl || DEFAULT_TRIP_IMAGE;

  return (
    <Link
      to={`/trips/${id}`}
      className="group block rounded-card overflow-hidden glass p-2 transition-all duration-300 hover:shadow-card hover:-translate-y-1 hover:border-white/60"
    >
      <div className="relative aspect-[4/3] rounded-inner overflow-hidden bg-white/20">
        <FallbackImage
          src={imageSrc}
          alt={displayTitle}
          className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
        />
        {/* Soft bottom vignette */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent pointer-events-none" />

        {/* Status / Spoiler chip */}
        <div className="absolute top-2.5 left-2.5 z-10">
          {!revealed ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-sans font-semibold tracking-wide bg-amber-500/90 text-white backdrop-blur-md shadow-sm border border-amber-300/40">
              <Compass className="w-3 h-3 animate-spin-slow" />
              Secret destination
            </span>
          ) : status === "completed" ? (
            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-sans font-medium bg-white/80 text-teal-950 backdrop-blur-md shadow-sm border border-white/40">
              Completed
            </span>
          ) : null}
        </div>

        {/* Rating chip if finished */}
        {revealed && rating ? (
          <div className="absolute top-2.5 right-2.5 z-10 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-sans font-semibold bg-white/90 text-teal-950 backdrop-blur-md shadow-sm border border-white/40">
            <Star className="w-3 h-3 fill-amber-400 text-amber-400" />
            <span>{rating.toFixed(1)}</span>
          </div>
        ) : null}

        {/* Floating Round Glass Chevron Button */}
        <div className="absolute bottom-2.5 right-2.5 z-10 w-8 h-8 rounded-full glass-strong flex items-center justify-center text-teal-950 transition-all duration-300 group-hover:bg-white group-hover:scale-110 shadow-sm border border-white/50">
          <ChevronRight className="w-4 h-4 transition-transform group-hover:translate-x-0.5" />
        </div>
      </div>

      {/* Card Body */}
      <div className="p-3">
        <h3 className="font-serif text-lg text-ink font-normal line-clamp-1 group-hover:text-teal-800 transition-colors">
          {displayTitle}
        </h3>
        <div className="flex items-center gap-2 mt-1 text-xs text-ink-muted font-sans font-medium">
          {distanceKm ? <span>{distanceKm} km</span> : null}
          {distanceKm && durationMinutes ? <span>•</span> : null}
          {durationMinutes ? <span>{Math.round(durationMinutes)} mins</span> : null}
          {!distanceKm && !durationMinutes && (
            <span>{revealed ? "Expedition archive" : "Active route"}</span>
          )}
        </div>
      </div>
    </Link>
  );
};
