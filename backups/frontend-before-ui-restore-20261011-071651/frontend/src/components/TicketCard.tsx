import React, { useEffect, useRef } from "react";
import JsBarcode from "jsbarcode";
import { Compass, Calendar, Clock, MapPin, ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";

interface TicketCardProps {
  id: string;
  title: string;
  destination: string;
  isRevealed: boolean;
  mood: string;
  date?: string;
  duration?: string;
  distanceKm?: number;
  status: string;
  barcodeValue?: string;
  onClick?: () => void;
  showLink?: boolean;
}

export const TicketCard: React.FC<TicketCardProps> = ({
  id,
  title,
  destination,
  isRevealed,
  mood,
  date,
  duration,
  distanceKm,
  status,
  barcodeValue,
  onClick,
  showLink = true,
}) => {
  const barcodeRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (barcodeRef.current) {
      try {
        JsBarcode(barcodeRef.current, barcodeValue || id.slice(0, 10).toUpperCase(), {
          format: "CODE128",
          width: 1.2,
          height: 32,
          displayValue: false,
          margin: 0,
          background: "transparent",
          lineColor: "#17242B",
        });
      } catch (e) {
        // Fallback gracefully
      }
    }
  }, [id, barcodeValue]);

  const CardContent = (
    <div
      onClick={onClick}
      className="relative flex flex-col md:flex-row bg-paper rounded-card shadow-clay overflow-hidden border border-mist/20 transition hover:border-mist/40 cursor-pointer"
    >
      {/* Left/Main pass area */}
      <div className="flex-1 p-6 md:p-8 flex flex-col justify-between">
        <div>
          <div className="flex items-center justify-between gap-2 mb-4">
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-fog text-lagoon capitalize">
              <Compass className="w-3.5 h-3.5" />
              {mood} blind trip
            </span>
            {status === "in_progress" && (
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-signal/15 text-signal animate-pulse">
                <span className="w-2 h-2 rounded-full bg-signal" />
                LIVE
              </span>
            )}
          </div>

          <h3 className="font-serif text-2xl md:text-3xl text-ink font-normal leading-tight mb-2">
            {title}
          </h3>

          <div className="flex items-center gap-2 text-mist text-sm mb-6">
            <MapPin className="w-4 h-4 text-lagoon" />
            <span className="font-medium">
              Destination:{" "}
              {isRevealed ? (
                <span className="font-serif text-ink text-base font-semibold">{destination}</span>
              ) : (
                <span className="tracking-widest font-mono text-signal bg-signal/10 px-2 py-0.5 rounded">???</span>
              )}
            </span>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4 pt-4 border-t border-mist/15 text-xs">
          <div>
            <span className="text-mist block mb-0.5">Departed</span>
            <span className="font-semibold text-ink">{date || "Today"}</span>
          </div>
          <div>
            <span className="text-mist block mb-0.5">Distance</span>
            <span className="font-semibold text-ink font-mono">{distanceKm ? `${distanceKm} km` : "Tracking"}</span>
          </div>
          <div>
            <span className="text-mist block mb-0.5">Status</span>
            <span className="font-semibold capitalize text-lagoon">{status.replace("_", " ")}</span>
          </div>
        </div>
      </div>

      {/* Dashed tear line with top and bottom notches */}
      <div className="relative flex md:flex-col items-center justify-between border-t md:border-t-0 md:border-l border-dashed border-mist/30 py-4 md:py-8 px-6 md:px-8 bg-paper/60 md:w-56">
        {/* Notches */}
        <div className="hidden md:block absolute -top-3.5 -left-3.5 w-7 h-7 rounded-full bg-fog border-b border-mist/20" />
        <div className="hidden md:block absolute -bottom-3.5 -left-3.5 w-7 h-7 rounded-full bg-fog border-t border-mist/20" />

        <div className="flex flex-col items-start md:items-center text-left md:text-center w-full">
          <span className="text-[10px] font-mono uppercase tracking-widest text-mist mb-1">Pass No.</span>
          <span className="text-xs font-mono font-bold text-ink mb-3">{id.slice(0, 8).toUpperCase()}</span>
          <svg ref={barcodeRef} className="w-full max-w-[120px] h-8 my-1" />
        </div>

        {showLink && (
          <div className="flex items-center gap-1 text-xs font-semibold text-lagoon hover:text-deep-sea transition mt-2 md:mt-4">
            <span>View pass</span>
            <ChevronRight className="w-4 h-4" />
          </div>
        )}
      </div>
    </div>
  );

  if (showLink) {
    return <Link to={`/trips/${id}`}>{CardContent}</Link>;
  }
  return CardContent;
};
