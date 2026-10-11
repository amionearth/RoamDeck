import os
from pathlib import Path
from PIL import Image, ImageOps
import io
from typing import Tuple
from backend.app.config import settings

def process_and_save_image(image_bytes: bytes, destination_path: Path, thumb_path: Path, max_size: Tuple[int, int] = (1920, 1080), thumb_size: Tuple[int, int] = (400, 400)) -> Tuple[str, str]:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(io.BytesIO(image_bytes))
    # Correct orientation if EXIF contains orientation tag before stripping
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Resize main image if larger than max_size
    img_copy = img.copy()
    img_copy.thumbnail(max_size, Image.Resampling.LANCZOS)
    
    # Save main image without EXIF (Pillow save without exif parameter strips EXIF)
    img_copy.save(str(destination_path), format="JPEG", quality=85, optimize=True)

    # Generate thumbnail
    thumb = img.copy()
    thumb.thumbnail(thumb_size, Image.Resampling.LANCZOS)
    thumb.save(str(thumb_path), format="JPEG", quality=80, optimize=True)

    return str(destination_path), str(thumb_path)
