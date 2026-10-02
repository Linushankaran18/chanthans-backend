"""Upload validation: size-limited chunked read and real-content check via Pillow."""
import io
import logging
import warnings
from dataclasses import dataclass

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.exceptions import FileTooLargeError, InvalidImageError

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
# Pillow format -> (extension, content type)
ALLOWED_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}
ALLOWED_CONTENT_TYPES = {ct for _, ct in ALLOWED_FORMATS.values()}


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    extension: str
    content_type: str


async def read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Read in chunks, aborting as soon as the limit is exceeded."""
    buffer = bytearray()
    while chunk := await file.read(CHUNK_SIZE):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise FileTooLargeError(f"File exceeds the maximum size of {max_bytes // (1024 * 1024)} MB")
    return bytes(buffer)


def verify_image(data: bytes) -> tuple[str, str]:
    """Decode the image for real; return (extension, content_type) of the actual format."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                fmt = probe.format
                probe.verify()
            # verify() does not decode pixel data; fully decode a fresh handle.
            with Image.open(io.BytesIO(data)) as img:
                img.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise InvalidImageError("File is not a valid image")
    if fmt not in ALLOWED_FORMATS:
        raise InvalidImageError("Only JPEG, PNG and WebP images are allowed")
    return ALLOWED_FORMATS[fmt]


async def validate_upload(file: UploadFile, max_bytes: int) -> ValidatedImage:
    if (file.content_type or "").lower() not in ALLOWED_CONTENT_TYPES:
        raise InvalidImageError("Only JPEG, PNG and WebP images are allowed")
    data = await read_limited(file, max_bytes)
    if not data:
        raise InvalidImageError("Uploaded file is empty")
    extension, content_type = verify_image(data)
    return ValidatedImage(data=data, extension=extension, content_type=content_type)
