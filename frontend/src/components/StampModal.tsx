import React, { useState, useEffect } from "react";
import { generateStampSvg, exportStampPng, downloadFile } from "../lib/ticket";
import type { StampOptions } from "../lib/ticket";
import { X, Download, Share2, Sparkles, Check } from "lucide-react";

interface StampModalProps {
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
  } | null;
  onClose: () => void;
}

export const StampModal: React.FC<StampModalProps> = ({ stamp, onClose }) => {
  const [orientation, setOrientation] = useState<"portrait" | "landscape">("landscape");
  const [svgContent, setSvgContent] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [copied, setCopied] = useState<boolean>(false);

  useEffect(() => {
    if (!stamp) return;
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
        console.error("Failed to render modal SVG:", e);
        if (isMounted) setLoading(false);
      }
    };
    fetchSvg();
    return () => {
      isMounted = false;
    };
  }, [stamp, orientation]);

  if (!stamp) return null;

  const handleDownloadSvg = () => {
    if (!svgContent) return;
    downloadFile(svgContent, `stamp-${stamp.id}-${orientation}.svg`, "svg");
  };

  const handleDownloadPng = async () => {
    if (!svgContent) return;
    try {
      const pngDataUrl = await exportStampPng(svgContent, 3);
      downloadFile(pngDataUrl, `stamp-${stamp.id}-${orientation}.png`, "png");
    } catch (e) {
      console.error("Failed to export PNG:", e);
    }
  };

  const handleShare = () => {
    const url = `${window.location.origin}/trips/${stamp.trip_id || stamp.id}`;
    if (navigator.share) {
      navigator.share({
        title: `FarFromHome Stamp: ${stamp.destination || "Blind Expedition"}`,
        url,
      });
    } else {
      navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-md animate-fade-in">
      <div className="relative w-full max-w-3xl rounded-3xl bg-[#171717] border border-white/20 p-6 sm:p-8 text-white shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        {/* Top Header */}
        <div className="flex items-center justify-between pb-4 border-b border-white/10">
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-sans font-medium capitalize bg-white/10 border border-white/20">
              <Sparkles className="w-3.5 h-3.5 text-amber-400" />
              {stamp.rarity} Stamp
            </span>
            <h2 className="font-serif text-2xl font-normal tracking-wide">
              {stamp.destination || "Secret Shore"}
            </h2>
          </div>

          <button
            onClick={onClose}
            className="w-9 h-9 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center transition-colors text-white/80"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Orientation Toggle */}
        <div className="flex items-center justify-center gap-2 my-4">
          <div className="inline-flex p-1 rounded-full bg-white/10 border border-white/15">
            <button
              onClick={() => setOrientation("landscape")}
              className={`px-4 py-1.5 rounded-full text-xs font-sans font-medium transition-all ${
                orientation === "landscape"
                  ? "bg-white text-zinc-900 shadow-sm"
                  : "text-white/70 hover:text-white"
              }`}
            >
              Landscape Ticket
            </button>
            <button
              onClick={() => setOrientation("portrait")}
              className={`px-4 py-1.5 rounded-full text-xs font-sans font-medium transition-all ${
                orientation === "portrait"
                  ? "bg-white text-zinc-900 shadow-sm"
                  : "text-white/70 hover:text-white"
              }`}
            >
              Portrait Ticket
            </button>
          </div>
        </div>

        {/* Preview Viewport */}
        <div className="flex-1 flex items-center justify-center overflow-auto p-4 rounded-2xl bg-black/40 border border-white/5 my-2">
          {loading ? (
            <div className="flex flex-col items-center gap-3 py-12 text-white/50 animate-pulse">
              <span className="text-sm">Rendering high-res SVG ticket...</span>
            </div>
          ) : svgContent ? (
            <div
              className={`w-full flex items-center justify-center ${
                orientation === "portrait" ? "max-w-xs" : "max-w-2xl"
              }`}
              dangerouslySetInnerHTML={{ __html: svgContent }}
            />
          ) : (
            <div className="py-12 text-white/40">Failed to render ticket</div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="mt-4 pt-4 border-t border-white/10 flex flex-wrap items-center justify-between gap-3">
          <button
            onClick={handleShare}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-full bg-white/10 hover:bg-white/20 text-white text-xs font-sans font-medium transition-all border border-white/15"
          >
            {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Share2 className="w-4 h-4" />}
            <span>{copied ? "Link Copied!" : "Share Stamp"}</span>
          </button>

          <div className="flex items-center gap-2">
            <button
              onClick={handleDownloadSvg}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-full bg-white/10 hover:bg-white/20 text-white text-xs font-sans font-medium transition-all border border-white/15"
            >
              <Download className="w-4 h-4" />
              <span>Download SVG</span>
            </button>

            <button
              onClick={handleDownloadPng}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full bg-amber-500 hover:bg-amber-600 text-zinc-950 text-xs font-sans font-semibold transition-all shadow-md"
            >
              <Download className="w-4 h-4" />
              <span>Export HD PNG (3x)</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
