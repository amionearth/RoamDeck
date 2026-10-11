import React, { useEffect, useState } from "react";
import { DEFAULT_TRIP_IMAGE } from "../lib/assets";

interface FallbackImageProps extends Omit<React.ImgHTMLAttributes<HTMLImageElement>, "src"> {
  src?: string | null;
  fallbackSrc?: string;
}

export const FallbackImage: React.FC<FallbackImageProps> = ({
  src,
  fallbackSrc = DEFAULT_TRIP_IMAGE,
  onError,
  ...props
}) => {
  const [usingFallback, setUsingFallback] = useState(!src);

  useEffect(() => {
    setUsingFallback(!src);
  }, [src, fallbackSrc]);

  return (
    <img
      {...props}
      src={usingFallback ? fallbackSrc : src || fallbackSrc}
      onError={(event) => {
        const fallbackUrl = new URL(fallbackSrc, window.location.href).href;
        if (event.currentTarget.src !== fallbackUrl) {
          setUsingFallback(true);
          return;
        }

        onError?.(event);
        event.currentTarget.style.visibility = "hidden";
      }}
    />
  );
};
