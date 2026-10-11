import React, { useEffect, useRef } from "react";
import JsBarcode from "jsbarcode";
import { Award, Lock, MapPin } from "lucide-react";

interface StampTileProps {
  placeName?: string;
  category?: string;
  date?: string;
  rarity?: "common" | "rare" | "epic" | "legendary" | string;
  serial?: string;
  isLocked?: boolean;
}

const RARITY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  common: { bg: "bg-[#7C97A0]/10", text: "text-[#7C97A0]", border: "border-[#7C97A0]/30" },
  rare: { bg: "bg-[#1F4B8F]/10", text: "text-[#1F4B8F]", border: "border-[#1F4B8F]/30" },
  epic: { bg: "bg-[#7B4FC2]/10", text: "text-[#7B4FC2]", border: "border-[#7B4FC2]/30" },
  legendary: { bg: "bg-[#F2A33A]/15", text: "text-[#F2A33A]", border: "border-[#F2A33A]/40" },
};

export const StampTile: React.FC<StampTileProps> = ({
  placeName = "Mysterious Place",
  category = "viewpoint",
  date,
  rarity = "common",
  serial = "FFH-XXXX",
  isLocked = false,
}) => {
  const barcodeRef = useRef<SVGSVGElement>(null);
  const color = RARITY_COLORS[rarity.toLowerCase()] || RARITY_COLORS.common;

  useEffect(() => {
    if (!isLocked && barcodeRef.current) {
      try {
        JsBarcode(barcodeRef.current, serial, {
          format: "CODE128",
          width: 0.9,
          height: 18,
          displayValue: false,
          margin: 0,
          background: "transparent",
          lineColor: "#17242B",
        });
      } catch {}
    }
  }, [serial, isLocked]);

  if (isLocked) {
    return (
      <div className="relative flex flex-col justify-center items-center h-48 rounded-tile border-2 border-dashed border-mist/30 bg-paper/40 p-4 text-center">
        <Lock className="w-8 h-8 text-mist/40 mb-2" />
        <span className="text-xs font-mono text-mist/60 uppercase tracking-widest">Locked Stamp</span>
        <span className="text-[11px] text-mist/50 mt-1">Complete a trip to unlock</span>
      </div>
    );
  }

  return (
    <div className={`relative flex flex-col justify-between h-48 rounded-tile bg-paper shadow-clay border ${color.border} overflow-hidden p-4 transition hover:scale-[1.02]`}>
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className={`text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full ${color.bg} ${color.text}`}>
            {rarity}
          </span>
          <span className="text-[10px] font-mono text-mist">{date || "Earned"}</span>
        </div>

        <h4 className="font-serif text-lg text-ink font-semibold leading-tight line-clamp-2 mt-1">
          {placeName}
        </h4>
        <div className="flex items-center gap-1 text-xs text-mist mt-1 capitalize">
          <MapPin className="w-3 h-3 text-lagoon" />
          <span>{category}</span>
        </div>
      </div>

      {/* Stub with barcode & serial */}
      <div className="pt-3 border-t border-dashed border-mist/20 flex flex-col items-center">
        <svg ref={barcodeRef} className="w-24 h-5 opacity-80" />
        <span className="text-[9px] font-mono font-bold tracking-widest text-mist mt-0.5">{serial}</span>
      </div>
    </div>
  );
};
