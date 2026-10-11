import React, { useEffect, useState, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import { toPng } from "html-to-image";
import { ArrowLeft, Download, MapPin, Compass, Sparkles } from "lucide-react";
import { api } from "../lib/api";

export const TripPoster: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [trip, setTrip] = useState<any>(null);
  const posterRef = useRef<HTMLDivElement>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (id) {
      api.get(`/api/trips/${id}`).then(setTrip).catch(console.error);
    }
  }, [id]);

  const handleDownload = async () => {
    if (!posterRef.current) return;
    setDownloading(true);
    try {
      const dataUrl = await toPng(posterRef.current, { quality: 0.95, pixelRatio: 2 });
      const link = document.createElement("a");
      link.download = `FarFromHome-${trip?.title || "Poster"}.png`;
      link.href = dataUrl;
      link.click();
    } catch (err) {
      console.error("Poster download error:", err);
    } finally {
      setDownloading(false);
    }
  };

  if (!trip) {
    return <div className="p-8 text-center text-mist">Loading poster canvas...</div>;
  }

  // Calculate simple SVG path for the route silhouette
  const points = trip.points || [];
  let svgPath = "M 50 150 Q 150 50 250 150 T 450 150";
  if (points.length > 2) {
    const minLat = Math.min(...points.map((p: any) => p.lat));
    const maxLat = Math.max(...points.map((p: any) => p.lat));
    const minLng = Math.min(...points.map((p: any) => p.lng));
    const maxLng = Math.max(...points.map((p: any) => p.lng));
    const spanLat = maxLat - minLat || 0.01;
    const spanLng = maxLng - minLng || 0.01;

    const coords = points.map((p: any) => {
      const x = 50 + ((p.lng - minLng) / spanLng) * 380;
      const y = 50 + (1 - (p.lat - minLat) / spanLat) * 380;
      return `${Math.round(x)},${Math.round(y)}`;
    });
    svgPath = `M ${coords.join(" L ")}`;
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 pb-24 space-y-6">
      <div className="flex items-center justify-between">
        <Link to={`/trips/${trip.id}`} className="flex items-center gap-2 text-xs font-semibold text-mist hover:text-ink">
          <ArrowLeft className="w-4 h-4" />
          <span>Back to trip pass</span>
        </Link>
        <button
          onClick={handleDownload}
          disabled={downloading}
          className="px-6 py-2.5 rounded-full bg-lagoon text-white font-semibold text-xs shadow-clay hover:opacity-95 flex items-center gap-2"
        >
          <Download className="w-4 h-4" />
          <span>{downloading ? "Rendering..." : "Download image (1080×1920)"}</span>
        </button>
      </div>

      {/* The 1080x1920 Scaled Aspect Poster Canvas */}
      <div className="flex justify-center">
        <div
          ref={posterRef}
          style={{ width: "450px", height: "800px" }}
          className="relative bg-dark-bg rounded-card overflow-hidden shadow-2xl flex flex-col justify-between p-8 text-white select-none"
        >
          {/* Photo Background (darkened slightly) */}
          <div className="absolute inset-0 z-0">
            <img
              src={
                trip.cover_photo_id
                  ? `/api/trips/${trip.id}/photos/${trip.cover_photo_id}/file`
                  : "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=800&q=80"
              }
              alt="Poster background"
              className="w-full h-full object-cover brightness-[0.55]"
            />
          </div>

          {/* Amber Torn-Paper Strips at Two Corners */}
          <div className="absolute -top-3 -left-6 w-28 h-9 bg-signal rotate-[-25deg] shadow-md z-20 opacity-90" />
          <div className="absolute -bottom-3 -right-6 w-28 h-9 bg-signal rotate-[-25deg] shadow-md z-20 opacity-90" />

          {/* Top Header */}
          <div className="relative z-10 space-y-3">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs uppercase tracking-widest text-signal bg-black/40 backdrop-blur px-3 py-1 rounded-full">
                FarFromHome Expedition
              </span>
              <span className="text-xs font-mono bg-white/20 backdrop-blur px-3 py-1 rounded-full">
                @{trip.owner?.username || "explorer"}
              </span>
            </div>

            {/* Handwritten Title (Caveat Brush) */}
            <h1 className="font-hand text-5xl md:text-6xl text-white font-normal leading-tight drop-shadow-lg pt-2">
              {trip.title}
            </h1>

            <div className="flex items-center gap-2 text-xs">
              <span className="bg-lagoon px-3 py-1 rounded-full font-semibold">
                {trip.mood} wander
              </span>
              <span className="bg-white/20 backdrop-blur px-3 py-1 rounded-full font-mono">
                {trip.start_at ? new Date(trip.start_at).toLocaleDateString() : "October 2026"}
              </span>
            </div>
          </div>

          {/* Middle: Route Silhouette Drawn as a White-Outlined Sticker */}
          <div className="relative z-10 flex flex-col items-center justify-center my-auto">
            <div className="relative p-6 bg-white/10 backdrop-blur-md rounded-card border-4 border-white shadow-2xl">
              <svg width="340" height="340" viewBox="0 0 480 480" className="overflow-visible">
                {/* Route outline glow */}
                <path
                  d={svgPath}
                  fill="none"
                  stroke="white"
                  strokeWidth="10"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d={svgPath}
                  fill="none"
                  stroke="#1E7F85"
                  strokeWidth="6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>

              {/* Duration Sticker on Route */}
              <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-signal text-ink font-mono font-bold text-xs px-3 py-1 rounded-full shadow-lg border-2 border-white">
                {Math.round((trip.distance_m || 2400) / 100) / 10} km walked
              </div>
            </div>
          </div>

          {/* Bottom Destination Pin & Stamp Serial */}
          <div className="relative z-10 bg-black/50 backdrop-blur-md p-4 rounded-tile border border-white/20 flex items-center justify-between">
            <div>
              <span className="text-[10px] font-mono uppercase tracking-wider text-mist block">Destination</span>
              <span className="font-serif text-lg font-bold text-white">
                {trip.destination_revealed ? trip.destination_name : "Secret Revealed upon Arrival"}
              </span>
            </div>
            <div className="text-right font-mono text-[10px] text-signal font-bold">
              ★ FARFROMHOME ★
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
