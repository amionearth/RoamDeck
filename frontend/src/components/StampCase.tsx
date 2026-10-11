import React, { useEffect, useState } from "react";
import { generateStampSvg } from "../lib/ticket";
import type { StampOptions } from "../lib/ticket";
import { Eye, Sparkles } from "lucide-react";

interface StampCaseProps {
  stamp: {
    id: string;
    trip_id?: string;
    destination?: string;
    rarity: "common" | "rare" | "epic" | "legendary" | string;
    distance_m?: number;
    duration_minutes?: number;
    rating?: number | null;
    created_at?: string;
    cover_url?: string;
    image_url?: string;
  };
  orientation?: "portrait" | "landscape";
  onOpen?: () => void;
  className?: string;
}

const RARITY_GRADIENTS: Record<string, string> = {
  common: "from-slate-400/20 to-slate-500/10 text-slate-200 border-slate-400/40",
  rare: "from-sky-400/20 to-teal-500/10 text-sky-200 border-sky-400/40",
  epic: "from-purple-400/20 to-indigo-500/10 text-purple-200 border-purple-400/40",
  legendary: "from-amber-400/20 to-amber-600/10 text-amber-200 border-amber-400/40",
};

export const StampCase: React.FC<StampCaseProps> = ({
  stamp,
  orientation = "landscape",
  onOpen,
  className = "",
}) => {
  const [svgContent, setSvgContent] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let isMounted = true;
    const fetchSvg = async () => {
      try {
        setLoading(true);
        const options: StampOptions = {
          orientation,
          destination: stamp.destination || "Secret Shore",
          distance_m: stamp.distance_m || 12400,
          duration_minutes: stamp.duration_minutes || 74,
          rating: stamp.rating || 4.9,
          dateStr: stamp.created_at
            ? new Date(stamp.created_at).toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
                year: "numeric",
              })
            : "Oct 11, 2026",
          subtitle: "Coastal Blind Expedition",
          imageUrl: stamp.image_url || stamp.cover_url || "/seed/photos/kappad_beach_sunset.jpg",
          tripUrl: `${window.location.origin}/trips/${stamp.trip_id || stamp.id}`,
          rarity: stamp.rarity || "rare",
          serial: stamp.id.slice(0, 10).toUpperCase(),
        };
        const svg = await generateStampSvg(options);
        if (isMounted) {
          setSvgContent(svg);
          setLoading(false);
        }
      } catch (e) {
        console.error("Failed to render stamp SVG:", e);
        if (isMounted) setLoading(false);
      }
    };
    fetchSvg();
    return () => {
      isMounted = false;
    };
  }, [stamp, orientation]);

  const rarityClass = RARITY_GRADIENTS[stamp.rarity] || RARITY_GRADIENTS.common;

  return (
    <div
      onClick={onOpen}
      className={`group relative rounded-2xl bg-[#171717]/90 backdrop-blur-xl border border-white/10 p-3 sm:p-4 cursor-pointer transition-all duration-300 hover:scale-[1.02] hover:border-white/30 hover:shadow-2xl overflow-hidden ${className}`}
    >
      {/* Specular sheen on display case */}
      <div className="absolute inset-0 bg-gradient-to-tr from-transparent via-white/5 to-white/10 pointer-events-none" />

      {/* Rarity badge ribbon */}
      <div className="flex items-center justify-between mb-2 relative z-10">
        <span
          className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-sans font-medium capitalize border bg-gradient-to-r ${rarityClass}`}
        >
          <Sparkles className="w-3 h-3" />
          {stamp.rarity}
        </span>
        <span className="text-[10px] text-white/40 font-mono tracking-wider">
          #{stamp.id.slice(0, 6).toUpperCase()}
        </span>
      </div>

      {/* Rendered Stamp Preview */}
      <div className="relative z-10 w-full flex items-center justify-center min-h-[140px] sm:min-h-[160px] overflow-hidden rounded-lg bg-black/40 p-2">
        {loading ? (
          <div className="flex flex-col items-center gap-2 py-8 text-white/50 animate-pulse">
            <span className="text-xs">Minting stamp ticket...</span>
          </div>
        ) : svgContent ? (
          <div
            className="w-full flex items-center justify-center transform transition-transform group-hover:scale-[1.01]"
            dangerouslySetInnerHTML={{ __html: svgContent }}
          />
        ) : (
          <div className="py-8 text-white/40 text-xs">Failed to load preview</div>
        )}

        {/* Hover overlay hint */}
        <div className="absolute inset-0 bg-black/50 backdrop-blur-sm opacity-0 group-hover:opacity-100 flex items-center justify-center gap-2 text-white text-xs font-sans font-medium transition-opacity duration-200">
          <Eye className="w-4 h-4" />
          <span>Inspect & Export</span>
        </div>
      </div>
    </div>
  );
};
