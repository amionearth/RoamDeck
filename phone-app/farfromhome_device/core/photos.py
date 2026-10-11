"""Photo preparation: compress, resize, strip EXIF, and validate before queueing.

Brief 03 §6.4: *"Prepare photos before queueing: JPEG, long edge 1920 px or less,
quality about 85, target under 2 MB (the server rejects files over 10 MB)."*
Brief §5.3: *"Strip GPS EXIF before upload unless the user opts in."*

The server strips EXIF again on ingest, which is the right place for the guarantee;
doing it here too means a photo left in the outbox on a lost device has no location
in it either. Both, not either.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("ffh.core.photos")

#: The server rejects anything above this (``settings.MAX_UPLOAD_MB``, default 10).
SERVER_LIMIT_BYTES = 10 * 1024 * 1024

#: The brief's target for a phone-network upload.
TARGET_BYTES = 2 * 1024 * 1024


@dataclass
class PreparedPhoto:
    """The result of preparing one capture for upload."""

    path: Path
    bytes_size: int
    width: int
    height: int
    quality: int
    exif_stripped: bool
    resized: bool

    def to_payload(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "bytes": self.bytes_size,
            "width": self.width,
            "height": self.height,
            "quality": self.quality,
            "exif_stripped": self.exif_stripped,
            "resized": self.resized,
        }


def prepare_photo(
    raw: bytes,
    output_path: Path | str,
    max_edge: int = 1920,
    quality: int = 85,
    target_bytes: int = TARGET_BYTES,
    strip_exif: bool = True,
    min_quality: int = 60,
) -> Optional[PreparedPhoto]:
    """Turn camera bytes into a JPEG that is small enough to upload.

    Strategy: correct the orientation, strip metadata, resize the long edge, then
    step the quality down until the file fits the target. If it still does not fit,
    shrink the pixels and try again. Only after all of that does it give up — and
    giving up means returning ``None`` and logging why, never uploading something
    the server will reject.
    """
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except Exception as exc:
        logger.warning("could not read the captured image: %s", exc)
        return None

    original_size = image.size
    resized = False

    if max(image.size) > max_edge:
        ratio = max_edge / float(max(image.size))
        new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
        image = image.resize(new_size, Image.LANCZOS)
        resized = True

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def encode(quality_level: int) -> bytes:
        buffer = io.BytesIO()
        # Passing no `exif=` argument is what strips the metadata: Pillow only
        # writes EXIF when it is handed it explicitly.
        save_args: Dict[str, Any] = {
            "format": "JPEG",
            "quality": quality_level,
            "optimize": True,
            "progressive": True,
        }
        if not strip_exif:
            save_args["exif"] = getattr(image, "info", {}).get("exif", b"")
        image.save(buffer, **save_args)
        return buffer.getvalue()

    attempt_quality = max(min_quality, min(95, int(quality)))
    data = encode(attempt_quality)
    while len(data) > target_bytes and attempt_quality > min_quality:
        attempt_quality -= 8
        data = encode(attempt_quality)

    if len(data) > target_bytes:
        # Still too big: shrink the pixels and try once more at a moderate quality.
        shrink = 0.75
        while len(data) > target_bytes and shrink > 0.3:
            new_size = (max(1, int(image.width * shrink)), max(1, int(image.height * shrink)))
            reduced = image.resize(new_size, Image.LANCZOS)
            buffer = io.BytesIO()
            reduced.save(buffer, format="JPEG", quality=max(min_quality, 75), optimize=True)
            data = buffer.getvalue()
            shrink -= 0.15
        resized = True

    if len(data) > SERVER_LIMIT_BYTES:
        logger.error("prepared photo is %d bytes, above the server's limit; discarding", len(data))
        image.close()
        return None

    try:
        out_path.write_bytes(data)
    except OSError as exc:
        logger.error("could not write %s: %s", out_path, exc)
        image.close()
        return None

    size = image.size
    image.close()
    logger.debug(
        "prepared photo %s: %dx%d -> %dx%d, %d kB, q%d",
        out_path.name, original_size[0], original_size[1], size[0], size[1],
        len(data) // 1024, attempt_quality,
    )
    return PreparedPhoto(
        path=out_path,
        bytes_size=len(data),
        width=size[0],
        height=size[1],
        quality=attempt_quality,
        exif_stripped=strip_exif,
        resized=resized,
    )


def has_gps_exif(raw: bytes) -> bool:
    """Does this image carry location metadata? Used by the settings screen."""
    from PIL import Image
    from PIL.ExifTags import TAGS

    try:
        with Image.open(io.BytesIO(raw)) as image:
            exif = image.getexif()
            if not exif:
                return False
            for tag_id, value in exif.items():
                if TAGS.get(tag_id) == "GPSInfo" and value:
                    return True
            return False
    except Exception:
        return False


def dominant_colour(path: Path | str, sample: int = 64) -> Optional[Tuple[int, int, int]]:
    """The most common colour in an image, for the colour-hunt check.

    Cheap on purpose: the image is reduced to a ``sample x sample`` thumbnail first,
    so this costs a few milliseconds on a Pi. The brief (§8.5) puts the authoritative
    colour check on the server; this is for the local, offline game.
    """
    from PIL import Image

    try:
        with Image.open(path) as image:
            thumb = image.convert("RGB").resize((sample, sample), Image.NEAREST)
            counts: Dict[Tuple[int, int, int], int] = {}
            for pixel in thumb.getdata():
                # Quantise to 32-level buckets so near-identical colours merge.
                bucket = (pixel[0] // 32 * 32, pixel[1] // 32 * 32, pixel[2] // 32 * 32)
                counts[bucket] = counts.get(bucket, 0) + 1
    except Exception as exc:
        logger.debug("dominant_colour failed for %s: %s", path, exc)
        return None
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def colour_match(
    path: Path | str,
    target_hex: str,
    tolerance: float = 0.35,
) -> Tuple[bool, float]:
    """``(matches, distance)`` for a colour-hunt task.

    Distance is normalised Euclidean distance in RGB, so ``0`` is exact and ``1`` is
    as far apart as two colours get.
    """
    found = dominant_colour(path)
    if found is None:
        return (False, 1.0)
    try:
        target = tuple(int(target_hex.lstrip("#")[index : index + 2], 16) for index in (0, 2, 4))
    except (ValueError, IndexError):
        return (False, 1.0)
    distance = (sum((found[index] - target[index]) ** 2 for index in range(3)) ** 0.5) / (255 * 3 ** 0.5)
    return (distance <= tolerance, distance)
