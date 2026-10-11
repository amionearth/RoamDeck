import React, { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

export const AmbientBackdrop: React.FC<{ tripCoverUrl?: string | null }> = ({ tripCoverUrl }) => {
  const location = useLocation();
  const isTripPage = location.pathname.startsWith("/trips/");
  const defaultBackdrop = "/seed/backdrops/coastline_golden.jpg";

  const targetImage = isTripPage && tripCoverUrl ? tripCoverUrl : defaultBackdrop;
  const [activeImage, setActiveImage] = useState(targetImage);
  const [fadingImage, setFadingImage] = useState<string | null>(null);

  useEffect(() => {
    if (targetImage !== activeImage) {
      setFadingImage(activeImage);
      setActiveImage(targetImage);
      const timer = setTimeout(() => {
        setFadingImage(null);
      }, 450);
      return () => clearTimeout(timer);
    }
  }, [targetImage, activeImage]);

  return (
    <div
      className="fixed inset-0 pointer-events-none select-none overflow-hidden"
      style={{ zIndex: -1 }}
      aria-hidden="true"
    >
      {/* 1. Underlying Rich Gradient Fallback */}
      <div
        className="absolute inset-0"
        style={{
          background: `
            radial-gradient(circle at 15% 20%, rgba(141, 201, 223, 0.5) 0%, transparent 45%),
            radial-gradient(circle at 85% 75%, rgba(242, 163, 58, 0.35) 0%, transparent 50%),
            radial-gradient(circle at 50% 50%, rgba(30, 127, 133, 0.4) 0%, transparent 65%),
            linear-gradient(135deg, #1E7F85 0%, #1F4B8F 100%)
          `,
        }}
      />

      {/* 2. Previous Fading Image Layer */}
      {fadingImage && (
        <img
          src={fadingImage}
          alt=""
          className={`absolute inset-0 w-full h-full object-cover transition-opacity duration-400 ease-out opacity-0 ${
            isTripPage ? "scale-110 blur-[36px]" : "blur-[6px]"
          }`}
          onError={(e) => {
            (e.target as HTMLElement).style.display = "none";
          }}
        />
      )}

      {/* 3. Active Image Layer */}
      <img
        src={activeImage}
        alt=""
        className={`absolute inset-0 w-full h-full object-cover transition-all duration-400 ease-in opacity-85 ${
          isTripPage ? "scale-110 blur-[36px]" : "blur-[6px]"
        }`}
        onError={(e) => {
          (e.target as HTMLElement).style.display = "none";
        }}
      />

      {/* 4. Atmospheric Gradient Veil (Teal to Sea Blue with Amber glow) */}
      <div
        className="absolute inset-0"
        style={{
          background: `
            radial-gradient(circle at 80% 85%, rgba(242, 163, 58, 0.28) 0%, transparent 40%),
            linear-gradient(135deg, rgba(30, 127, 133, 0.52) 0%, rgba(31, 75, 143, 0.62) 100%)
          `,
          mixBlendMode: "multiply",
        }}
      />

      {/* 5. Lightening Wash to maintain high text contrast */}
      <div className="absolute inset-0 bg-white/20 backdrop-brightness-105" />
    </div>
  );
};
