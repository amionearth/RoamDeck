import React, { useEffect, useRef } from "react";
import JsBarcode from "jsbarcode";
import { Link } from "react-router-dom";
import { Compass, ChevronRight, MapPin } from "lucide-react";

interface PassTicketProps {
  id: string;
  title: string;
  destination: string;
  isRevealed: boolean;
  mood: string;
  date?: string;
  distanceKm?: number;
  status: string;
  showLink?: boolean;
}

export const PassTicket: React.FC<PassTicketProps> = ({
  id,
  title,
  destination,
  isRevealed,
  mood,
  date = "Today",
  distanceKm,
  status,
  showLink = true,
}) => {
  const barcodeRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (barcodeRef.current) {
      try {
        JsBarcode(barcodeRef.current, id.slice(0, 10).toUpperCase(), {
          format: "CODE128",
          width: 1.1,
          height: 28,
          displayValue: false,
          margin: 0,
          background: "transparent",
          lineColor: "#17242B",
        });
      } catch {}
    }
  }, [id]);

  const inner = (
    <div className="ticket-mask-horizontal glass-strong rounded-[24px] p-5 sm:p-6 flex flex-col sm:flex-row items-stretch justify-between gap-4 border border-white/70 shadow-lg relative overflow-hidden group">
      {/* Main pass content */}
      <div className="flex-1 flex flex-col justify-between">
        <div>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[11px] font-semibold text-lagoon bg-white/60 px-2.5 py-0.5 rounded-full capitalize">
              {mood} wander
            </span>
            {status === "in_progress" && (
              <span className="text-[11px] font-bold text-signal bg-signal/15 px-2.5 py-0.5 rounded-full flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-signal animate-ping" />
                Live trip
              </span>
            )}
          </div>
          <h3 className="font-serif text-xl sm:text-2xl text-ink font-normal leading-snug">
            {title}
          </h3>
          <div className="flex items-center gap-1.5 text-xs text-mist mt-1.5">
            <MapPin className="w-3.5 h-3.5 text-lagoon shrink-0" />
            <span className="font-medium">
              Destination:{" "}
              {isRevealed ? (
                <span className="font-serif font-semibold text-ink">{destination}</span>
              ) : (
                <span className="font-mono font-bold text-signal bg-signal/15 px-1.5 py-0.5 rounded">???</span>
              )}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-4 text-xs text-mist pt-3 mt-3 border-t border-dashed border-ink/15 num-tabular">
          <span>{date}</span>
          <span>•</span>
          <span>{distanceKm ? `${distanceKm} km` : "Tracking distance"}</span>
        </div>
      </div>

      {/* Dashed divider line for tear stub */}
      <div className="sm:border-l sm:border-dashed sm:border-ink/20 sm:pl-4 flex flex-row sm:flex-col items-center justify-between sm:justify-center gap-2 pt-2 sm:pt-0 border-t sm:border-t-0 border-dashed border-ink/15">
        <div className="text-left sm:text-center">
          <span className="text-[9px] font-mono tracking-wider text-mist uppercase block">Pass Serial</span>
          <span className="text-xs font-mono font-bold text-ink">{id.slice(0, 8).toUpperCase()}</span>
          <svg ref={barcodeRef} className="w-24 h-7 my-1 opacity-80" />
        </div>
        {showLink && (
          <div className="flex items-center gap-0.5 text-xs font-semibold text-lagoon group-hover:text-deep-sea transition">
            <span>Pass</span>
            <ChevronRight className="w-4 h-4" />
          </div>
        )}
      </div>
    </div>
  );

  if (showLink) {
    return <Link to={`/trips/${id}`} className="block">{inner}</Link>;
  }
  return inner;
};
