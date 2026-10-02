"""One-time import of the website's original static hero images.

Run: python -m scripts.import_hero_images [path/to/website/public]

Copies the files into the configured storage backend (local or R2) and inserts
active ``hero_images`` rows in the original slide order. Refuses to run if the
table already has rows, so it cannot create duplicates.
"""
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.models.hero_image import HeroImage
from app.services.storage_service import get_storage_service

DEFAULT_PUBLIC_DIR = Path(__file__).resolve().parents[2] / "Chanthans-website" / "public"

# (path relative to the website's public/ folder, alt text) in original slide order.
SLIDES = [
    ("logo/slide1.jpeg", "Chanthans — a wedding moment"),
    ("Photos/c18.jpg", "Chanthans — elegant reception"),
    ("Photos/P7.jpg", "Chanthans — natural light portrait"),
    ("Photos/c19.jpg", "Chanthans — creative portrait"),
    ("Photos/P5.jpg", "Chanthans — romantic garden wedding"),
]
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_BYTES = get_settings().max_upload_size_bytes


async def import_images(public_dir: Path) -> None:
    files = [(public_dir / rel, alt) for rel, alt in SLIDES]
    missing = [str(p) for p, _ in files if not p.is_file()]
    if missing:
        sys.exit("Missing files:\n" + "\n".join(missing))
    storage = get_storage_service()
    async with get_sessionmaker()() as session:
        existing = await session.scalar(select(func.count()).select_from(HeroImage))
        if existing:
            sys.exit(f"hero_images already has {existing} row(s); nothing imported")
        for order, (path, alt) in enumerate(files, start=1):
            data = path.read_bytes()
            if len(data) > MAX_BYTES:
                sys.exit(f"{path.name} exceeds MAX_UPLOAD_SIZE_MB")
            ext = ".jpg" if path.suffix.lower() == ".jpeg" else path.suffix.lower()
            key = f"hero/{uuid4()}{ext}"
            url = storage.upload_image(key, data, CONTENT_TYPES[path.suffix.lower()])
            session.add(HeroImage(
                image_key=key, image_url=url, alt_text=alt, display_order=order, is_active=True,
            ))
            print(f"{order}. {path.name} -> {url}")
        await session.commit()
    print("Imported", len(files), "hero images")


if __name__ == "__main__":
    asyncio.run(import_images(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PUBLIC_DIR))
