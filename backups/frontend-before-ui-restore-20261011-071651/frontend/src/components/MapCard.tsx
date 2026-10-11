import React, { useEffect, useRef } from "react";
import L from "leaflet";

interface WaypointMarker {
  id?: string;
  idx: number;
  lat: number;
  lng: number;
  hint: string;
}

interface MapCardProps {
  points?: Array<{ lat: number; lng: number }>;
  waypoints?: WaypointMarker[];
  destination?: { lat: number; lng: number; name: string } | null;
  isRevealed?: boolean;
  isLive?: boolean;
  className?: string;
}

export const MapCard: React.FC<MapCardProps> = ({
  points = [],
  waypoints = [],
  destination = null,
  isRevealed = false,
  isLive = false,
  className = "h-80 w-full",
}) => {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<L.Map | null>(null);
  const routeLayerRef = useRef<L.Polyline | null>(null);
  const liveMarkerRef = useRef<L.CircleMarker | null>(null);

  useEffect(() => {
    if (!mapContainerRef.current) return;

    if (!mapInstanceRef.current) {
      const defaultCenter: [number, number] = points.length > 0 ? [points[0].lat, points[0].lng] : [11.2588, 75.7804];
      const map = L.map(mapContainerRef.current, {
        center: defaultCenter,
        zoom: 13,
        zoomControl: false,
      });

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '&copy; OpenStreetMap contributors',
      }).addTo(map);

      mapInstanceRef.current = map;
    }

    const map = mapInstanceRef.current;

    // Clear existing markers & lines except tile layer
    map.eachLayer((layer) => {
      if (layer instanceof L.Marker || layer instanceof L.Polyline || layer instanceof L.CircleMarker) {
        map.removeLayer(layer);
      }
    });

    const latLngs: [number, number][] = points.map((p) => [p.lat, p.lng]);

    // Dashed route line
    if (latLngs.length > 0) {
      const poly = L.polyline(latLngs, {
        color: "#1F4B8F",
        weight: 4,
        dashArray: "6, 8",
        lineCap: "round",
      }).addTo(map);
      routeLayerRef.current = poly;
      map.fitBounds(poly.getBounds(), { padding: [30, 30] });

      // Start Pin
      L.circleMarker(latLngs[0], {
        radius: 6,
        color: "#1E7F85",
        fillColor: "#1E7F85",
        fillOpacity: 1,
      }).bindPopup("Journey Start").addTo(map);
    }

    // Revealed waypoints only
    waypoints.forEach((wp) => {
      const marker = L.circleMarker([wp.lat, wp.lng], {
        radius: 8,
        color: "#F2A33A",
        fillColor: "#F2A33A",
        fillOpacity: 0.9,
      }).addTo(map);
      marker.bindPopup(`<b>Waypoint #${wp.idx + 1}</b><br/>${wp.hint}`);
    });

    // Destination Pin only once revealed
    if (isRevealed && destination) {
      const destMarker = L.circleMarker([destination.lat, destination.lng], {
        radius: 10,
        color: "#1E7F85",
        fillColor: "#1E7F85",
        fillOpacity: 1,
      }).addTo(map);
      destMarker.bindPopup(`<b>Destination Revealed!</b><br/>${destination.name}`).openPopup();
    }

    // Live pulsing dot if live
    if (isLive && latLngs.length > 0) {
      const latest = latLngs[latLngs.length - 1];
      const liveDot = L.circleMarker(latest, {
        radius: 7,
        color: "#F2A33A",
        fillColor: "#F2A33A",
        fillOpacity: 1,
      }).addTo(map);
      liveMarkerRef.current = liveDot;
    }

    return () => {};
  }, [points, waypoints, destination, isRevealed, isLive]);

  return (
    <div className={`relative rounded-card overflow-hidden shadow-clay border border-mist/20 ${className}`}>
      <div ref={mapContainerRef} className="w-full h-full z-0" />
      {isLive && (
        <div className="absolute top-3 right-3 z-10 flex items-center gap-1.5 px-3 py-1 bg-paper/90 backdrop-blur rounded-full text-xs font-bold text-signal shadow-sm">
          <span className="w-2 h-2 rounded-full bg-signal animate-ping" />
          Live GPS Feed
        </div>
      )}
    </div>
  );
};
