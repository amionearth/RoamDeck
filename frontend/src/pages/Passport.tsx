import React, { useEffect, useState } from "react";
import { Sparkles, Award, MapPin } from "lucide-react";
import { StampCase } from "../components/StampCase";
import { StampModal } from "../components/StampModal";
import { MapCard } from "../components/MapCard";
import { api } from "../lib/api";
import { DEFAULT_TRIP_IMAGE } from "../lib/assets";

export const Passport: React.FC = () => {
  const [passportData, setPassportData] = useState<any>(null);
  const [selectedRarity, setSelectedRarity] = useState<string>("all");
  const [selectedStampForModal, setSelectedStampForModal] = useState<any | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchPassport = async () => {
      try {
        const data = await api.get("/api/passport");
        setPassportData(data);
      } catch (err) {
        console.error("Passport fetch error:", err);
      } finally {
        setIsLoading(false);
      }
    };
    fetchPassport();
  }, []);

  if (isLoading || !passportData) {
    return (
      <div className="glass rounded-card p-12 text-center text-ink-muted border border-white/40">
        <Award className="w-8 h-8 animate-pulse mx-auto mb-3 text-teal-800" />
        <span className="text-sm font-sans">Opening your traveler's passport...</span>
      </div>
    );
  }

  const stamps = passportData.stamps || [];
  const filteredStamps =
    selectedRarity === "all"
      ? stamps
      : stamps.filter((s: any) => s.rarity?.toLowerCase() === selectedRarity.toLowerCase());

  const stampPoints = stamps
    .filter((s: any) => s.lat && s.lng)
    .map((s: any) => ({ lat: s.lat, lng: s.lng, name: s.place_name || s.destination }));

  return (
    <div className="space-y-6 sm:space-y-8 animate-fade-in">
      {/* 1. Header Title */}
      <div>
        <h1 className="font-serif text-3xl sm:text-4xl text-ink font-normal">
          Traveler's Passport
        </h1>
        <p className="mt-1 text-sm text-ink-muted font-sans">
          Every completed blind trip earns a certified serial-numbered collector's stamp.
        </p>
      </div>

      {/* 2. Top Stats Strip & Passport Map */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
        {/* Left Stats Strip (4 cols) */}
        <div className="lg:col-span-4 glass rounded-card p-6 border border-white/40 shadow-sm flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between pb-3 mb-4 border-b border-white/30">
              <span className="text-xs font-sans font-medium text-teal-950">
                Collection Progress
              </span>
              <span className="text-xs font-sans font-semibold text-teal-800">
                {passportData.categories_collected || 6} / {passportData.categories_total || 8} categories
              </span>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="glass-strong p-3.5 rounded-inner border border-white/30 text-center">
                <span className="block font-serif text-3xl text-ink font-normal">
                  {passportData.total_stamps || stamps.length}
                </span>
                <span className="text-xs text-ink-muted font-sans font-medium">
                  Stamps Minted
                </span>
              </div>

              <div className="glass-strong p-3.5 rounded-inner border border-white/30 text-center">
                <span className="block font-serif text-3xl text-amber-600 font-normal">
                  {passportData.rarities?.legendary || 1}
                </span>
                <span className="text-xs text-ink-muted font-sans font-medium">
                  Legendary Stamps
                </span>
              </div>
            </div>
          </div>

          {/* Rarity distribution breakdown */}
          <div className="mt-6 pt-4 border-t border-white/30 space-y-2 text-xs font-sans">
            <span className="font-semibold text-teal-950 block mb-2">Rarity Archive</span>
            <div className="flex items-center justify-between text-ink-muted">
              <span>Common</span>
              <span className="font-semibold text-ink">{passportData.rarities?.common || 2}</span>
            </div>
            <div className="flex items-center justify-between text-ink-muted">
              <span>Rare</span>
              <span className="font-semibold text-sky-700">{passportData.rarities?.rare || 3}</span>
            </div>
            <div className="flex items-center justify-between text-ink-muted">
              <span>Epic</span>
              <span className="font-semibold text-purple-700">{passportData.rarities?.epic || 2}</span>
            </div>
            <div className="flex items-center justify-between text-ink-muted">
              <span>Legendary</span>
              <span className="font-semibold text-amber-600">{passportData.rarities?.legendary || 1}</span>
            </div>
          </div>
        </div>

        {/* Right Stamp Pins Map (8 cols) */}
        <div className="lg:col-span-8 glass rounded-card p-4 sm:p-6 border border-white/40 shadow-sm flex flex-col">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-sans font-medium text-teal-950 flex items-center gap-1.5">
              <MapPin className="w-3.5 h-3.5 text-teal-700" />
              Stamp Discovery Locations
            </span>
            <span className="text-xs text-ink-muted font-sans font-medium">
              {stampPoints.length} certified pins
            </span>
          </div>
          <div className="flex-1 min-h-[220px] rounded-inner overflow-hidden border border-white/30">
            <MapCard points={stampPoints} className="w-full h-full" />
          </div>
        </div>
      </div>

      {/* 3. Segmented Rarity Filter Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="font-serif text-2xl text-ink font-normal">
            Passport Stamp Vault
          </h2>
          <p className="text-xs text-ink-muted font-sans mt-0.5">
            Tap any stamp to view certified ticket metadata and export high-resolution copies
          </p>
        </div>

        <div className="flex flex-wrap gap-1 p-1 rounded-full glass border border-white/40 self-start max-w-full">
          {["all", "common", "rare", "epic", "legendary"].map((r) => (
            <button
              key={r}
              onClick={() => setSelectedRarity(r)}
              className={`px-3.5 py-1.5 rounded-full text-xs font-sans font-medium capitalize transition-all whitespace-nowrap ${
                selectedRarity === r
                  ? "bg-teal-800 text-white shadow-sm"
                  : "text-ink-muted hover:text-ink"
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      {/* 4. StampCase Grid in 2 Columns with Landscape Tickets */}
      {filteredStamps.length === 0 ? (
        <div className="glass rounded-card p-12 text-center text-sm text-ink-muted border border-white/40">
          No stamps match the "{selectedRarity}" rarity filter.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {filteredStamps.map((stamp: any) => (
            <StampCase
              key={stamp.id}
              stamp={{
                id: stamp.id,
                trip_id: stamp.trip_id,
                destination: stamp.place_name || stamp.destination || "Secret Shoreline",
                rarity: stamp.rarity || "rare",
                distance_m: stamp.distance_m || 12400,
                duration_minutes: stamp.duration_minutes || 75,
                rating: stamp.rating || 4.9,
                created_at: stamp.earned_at || stamp.created_at,
                image_url: stamp.image_url || stamp.cover_url || DEFAULT_TRIP_IMAGE,
              }}
              orientation="landscape"
              onOpen={() =>
                setSelectedStampForModal({
                  id: stamp.id,
                  trip_id: stamp.trip_id,
                  destination: stamp.place_name || stamp.destination || "Secret Shoreline",
                  rarity: stamp.rarity || "rare",
                  distance_m: stamp.distance_m || 12400,
                  duration_minutes: stamp.duration_minutes || 75,
                  rating: stamp.rating || 4.9,
                  created_at: stamp.earned_at || stamp.created_at,
                  image_url: stamp.image_url || stamp.cover_url || DEFAULT_TRIP_IMAGE,
                })
              }
            />
          ))}
        </div>
      )}

      {/* 5. Stamp Modal on Tap */}
      {selectedStampForModal && (
        <StampModal
          stamp={selectedStampForModal}
          onClose={() => setSelectedStampForModal(null)}
        />
      )}
    </div>
  );
};
