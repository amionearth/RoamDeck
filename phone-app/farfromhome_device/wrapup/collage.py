"""Photo collage generation with Pillow.

Brief §9 item 9: *"at the end, auto-generate a photo collage (Pillow) from the
trip's photos."*

Constraints from the hardware section (§3): a 4 GB Pi and a small screen. So the
collage is built at a fixed, modest size (1400x1050 by default) with a bounded
number of images (at most 9), and every source image is opened with ``draft()``
first so JPEGs decode at a reduced scale rather than at full resolution.

The layout is deterministic but not boring: one hero image plus a grid, with
rounded corners, a title band and the trip's numbers. It never writes EXIF, and it
never includes a destination photo when the destination has not been revealed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

logger = logging.getLogger("ffh.wrapup.collage")

DEFAULT_SIZE = (1400, 1050)
MAX_IMAGES = 9
BACKGROUND = (18, 32, 38)
PAPER = (245, 241, 234)
ACCENT = (242, 163, 58)
INK = (24, 34, 38)


@dataclass
class CollageResult:
    path: Optional[Path]
    width: int = 0
    height: int = 0
    images_used: int = 0
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None

    def to_payload(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "path": str(self.path) if self.path else None,
            "width": self.width,
            "height": self.height,
            "images_used": self.images_used,
            "reason": self.reason,
        }


def _load_square(path: Path, size: int) -> Optional[Any]:
    """Open an image, crop it to a centred square and scale it to ``size``."""
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as source:
            # draft() lets libjpeg decode at a fraction of full size: a big win on a Pi.
            source.draft("RGB", (size * 2, size * 2))
            image = ImageOps.exif_transpose(source).convert("RGB")
            image = ImageOps.fit(image, (size, size), method=Image.LANCZOS, centering=(0.5, 0.42))
            return image
    except Exception as exc:
        logger.warning("could not use %s in the collage: %s", path, exc)
        return None


def _rounded(image: Any, radius: int) -> Any:
    from PIL import Image, ImageDraw

    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, image.size[0] - 1, image.size[1] - 1], radius=radius, fill=255)
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out


def build_collage(
    photo_paths: Sequence[Path | str],
    output_path: Path | str,
    title: str = "A blind trip",
    subtitle: str = "",
    stats: Optional[Dict[str, Any]] = None,
    size: Tuple[int, int] = DEFAULT_SIZE,
    max_images: int = MAX_IMAGES,
    destination_name: Optional[str] = None,
) -> CollageResult:
    """Compose a collage and write it as a JPEG.

    ``destination_name`` is only passed once the destination has been revealed, so
    a collage built mid-trip cannot leak the secret through its caption.
    """
    from PIL import Image, ImageDraw, ImageFont

    width, height = size
    usable = [Path(path) for path in photo_paths if Path(path).is_file()]
    if not usable:
        return CollageResult(None, reason="there are no photos to build a collage from")

    selected = usable[:max_images]
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    canvas = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    margin = int(width * 0.035)
    header_height = int(height * 0.13)
    footer_height = int(height * 0.09)

    # --- layout: hero + grid ------------------------------------------
    grid_top = header_height + margin
    grid_bottom = height - footer_height - margin
    grid_height = max(10, grid_bottom - grid_top)
    grid_width = width - margin * 2

    count = len(selected)
    if count == 1:
        columns = rows = 1
    elif count <= 4:
        columns = rows = 2
    else:
        columns = 3
        rows = 3

    gap = max(6, int(min(grid_width, grid_height) * 0.012))
    cell_width = (grid_width - gap * (columns - 1)) // columns
    cell_height = (grid_height - gap * (rows - 1)) // rows
    cell = max(48, min(cell_width, cell_height))

    placed = 0
    for path in selected:
        if placed >= columns * rows:
            break
        column = placed % columns
        row = placed // columns
        tile = _load_square(path, cell)
        if tile is None:
            continue
        tile = _rounded(tile, radius=max(6, cell // 22))
        x = margin + column * (cell + gap)
        y = grid_top + row * (cell + gap)
        canvas.paste(tile, (x, y), tile)
        placed += 1

    # --- header --------------------------------------------------------
    def font(size_px: int, bold: bool = False) -> Any:
        candidates = (
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "arialbd.ttf" if bold else "arial.ttf",
            "segoeuib.ttf" if bold else "segoeui.ttf",
        )
        for name in candidates:
            try:
                return ImageFont.truetype(name, size_px)
            except Exception:
                continue
        return ImageFont.load_default()

    draw.rectangle([0, 0, width, header_height], fill=PAPER)
    draw.rectangle([0, header_height - 4, width, header_height], fill=ACCENT)
    draw.text((margin, int(header_height * 0.22)), title[:60], fill=INK, font=font(int(header_height * 0.38), True))

    heading = subtitle
    if destination_name:
        heading = f"{subtitle}  ·  {destination_name}" if subtitle else destination_name
    if heading:
        draw.text((margin, int(header_height * 0.66)), heading[:90], fill=(90, 104, 108), font=font(int(header_height * 0.22)))

    # --- footer --------------------------------------------------------
    footer_top = height - footer_height
    draw.rectangle([0, footer_top, width, height], fill=PAPER)
    if stats:
        line = "   ".join(
            f"{key}: {value}" for key, value in list(stats.items())[:5] if value not in (None, "")
        )
        draw.text((margin, footer_top + int(footer_height * 0.3)), line[:110], fill=INK,
                  font=font(int(footer_height * 0.3), True))
    draw.text(
        (width - margin - int(width * 0.22), footer_top + int(footer_height * 0.32)),
        "FarFromHome",
        fill=(140, 152, 156),
        font=font(int(footer_height * 0.26)),
    )

    quality = 86
    canvas.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)

    # A 4 GB Pi should not be holding a 1400x1050 canvas any longer than needed.
    canvas.close()

    logger.info("collage written to %s (%d photos)", output, placed)
    return CollageResult(output, width=width, height=height, images_used=placed)


def collage_from_storage(
    storage: Any,
    trip_id: str,
    output_path: Path | str,
    title: str = "A blind trip",
    subtitle: str = "",
    stats: Optional[Dict[str, Any]] = None,
    destination_name: Optional[str] = None,
) -> CollageResult:
    """Convenience wrapper: pull this trip's photos out of SQLite and compose."""
    rows = storage.photos_for_trip(trip_id)
    paths = [Path(row["local_path"]) for row in rows]
    merged_stats = {"photos": len(paths)}
    if stats:
        merged_stats.update(stats)
    return build_collage(
        paths,
        output_path,
        title=title,
        subtitle=subtitle,
        stats=merged_stats,
        destination_name=destination_name,
    )
