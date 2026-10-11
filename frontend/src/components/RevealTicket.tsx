import React, { useState, useEffect } from "react";
import { Sparkles, MapPin, Award } from "lucide-react";

interface RevealTicketProps {
  destinationName: string;
  isRevealed: boolean;
  stampData?: {
    place_name: string;
    rarity: string;
    serial: string;
  };
  onRevealComplete?: () => void;
}

export const RevealTicket: React.FC<RevealTicketProps> = ({
  destinationName,
  isRevealed,
  stampData,
  onRevealComplete,
}) => {
  const [stage, setStage] = useState<"hidden" | "tearing" | "revealed">(
    isRevealed ? "revealed" : "hidden"
  );

  useEffect(() => {
    if (isRevealed && stage === "hidden") {
      setStage("tearing");
      const timer = setTimeout(() => {
        setStage("revealed");
        if (onRevealComplete) onRevealComplete();
      }, 800);
      return () => clearTimeout(timer);
    }
  }, [isRevealed, stage, onRevealComplete]);

  return (
    <div className="relative bg-paper rounded-card p-6 md:p-8 shadow-clay border border-mist/20 overflow-hidden">
      <div className="flex flex-col md:flex-row items-center justify-between gap-6">
        {/* Left Side: Destination Reveal */}
        <div className="flex-1">
          <span className="text-xs uppercase font-mono tracking-widest text-mist block mb-1">
            Destination Reveal
          </span>

          {stage === "revealed" ? (
            <div className="transition-all duration-700">
              <div className="flex items-center gap-2 text-lagoon mb-1 font-semibold text-sm">
                <Sparkles className="w-4 h-4 text-signal" />
                <span>Arrived & Unveiled</span>
              </div>
              <h2 className="font-serif text-3xl md:text-5xl text-ink font-normal leading-tight">
                {destinationName}
              </h2>
            </div>
          ) : (
            <div>
              <div className="flex items-center gap-2 text-mist mb-1 text-sm">
                <MapPin className="w-4 h-4 text-signal animate-bounce" />
                <span>Destination Hidden in Fog</span>
              </div>
              <div className="font-mono text-3xl md:text-5xl tracking-widest font-bold text-signal/80 bg-fog/80 px-4 py-2 rounded-tile inline-block">
                ???
              </div>
              <p className="text-xs text-mist mt-2">
                Keep walking toward the compass needle. Revealed upon device arrival.
              </p>
            </div>
          )}
        </div>

        {/* Right Side: Perforated Tear Stub or Stamp Landing */}
        <div className="relative w-full md:w-64 flex flex-col items-center justify-center p-4 rounded-tile bg-fog/60 border border-dashed border-mist/30">
          {stage === "tearing" && (
            <div className="animate-tear flex flex-col items-center text-center p-4 bg-paper rounded-tile shadow-md">
              <span className="text-xs font-mono font-bold text-mist uppercase">Tearing pass stub...</span>
            </div>
          )}

          {stage === "revealed" && (
            <div className="animate-thunk flex flex-col items-center text-center p-4 bg-paper rounded-tile shadow-clay border border-lagoon/40 w-full">
              <div className="w-10 h-10 rounded-full bg-signal/15 text-signal flex items-center justify-center mb-2">
                <Award className="w-6 h-6" />
              </div>
              <span className="text-[10px] uppercase font-bold tracking-wider text-signal">
                {stampData?.rarity || "Rare"} Stamp Awarded
              </span>
              <h4 className="font-serif text-lg font-bold text-ink mt-0.5">
                {stampData?.place_name || destinationName}
              </h4>
              <span className="text-[10px] font-mono text-mist mt-1">
                {stampData?.serial || "FFH-STAMP-OK"}
              </span>
            </div>
          )}

          {stage === "hidden" && (
            <div className="text-center py-4">
              <span className="text-xs font-mono text-mist block mb-1">Pass Stub Sealed</span>
              <span className="text-[11px] text-mist/70">Tears automatically on arrival</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
