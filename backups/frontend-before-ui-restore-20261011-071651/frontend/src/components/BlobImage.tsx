import React, { useId } from "react";
import { NORMALIZED_BLOBS } from "../styles/blobs";

interface BlobImageProps {
  src: string;
  alt: string;
  blobIndex?: 1 | 2 | 3;
  className?: string;
  imgClassName?: string;
}

export const BlobImage: React.FC<BlobImageProps> = ({
  src,
  alt,
  blobIndex = 1,
  className = "w-48 h-48",
  imgClassName = "w-full h-full object-cover",
}) => {
  const uniqueId = useId().replace(/:/g, "");
  const clipId = `blob-clip-${blobIndex}-${uniqueId}`;
  const pathD = blobIndex === 2 ? NORMALIZED_BLOBS.blob2 : blobIndex === 3 ? NORMALIZED_BLOBS.blob3 : NORMALIZED_BLOBS.blob1;

  return (
    <div className={`relative overflow-hidden shrink-0 ${className}`}>
      <svg className="w-0 h-0 absolute pointer-events-none" aria-hidden="true">
        <defs>
          <clipPath id={clipId} clipPathUnits="objectBoundingBox">
            <path d={pathD} />
          </clipPath>
        </defs>
      </svg>
      <img
        src={src}
        alt={alt}
        style={{ clipPath: `url(#${clipId})` }}
        className={`${imgClassName} transition-transform duration-700 hover:scale-105`}
        loading="lazy"
      />
    </div>
  );
};
