"""Validate local images once and keep small previews; no network or AI calls."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.generation.models import DraftImage

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


class CaseImageError(ValueError):
    code = "CASE_IMAGE_INVALID"


@dataclass(frozen=True)
class PreparedImage:
    metadata: DraftImage
    data: bytes
    preview: bytes


def prepare_images(files, *, existing_count=0):
    """Validate the whole batch before the caller changes a draft."""
    if existing_count + len(files) > MAX_IMAGES:
        raise CaseImageError("Choose up to four images in total.")
    return [prepare_image(data, filename) for filename, data in files]


def prepare_image(data, filename):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise CaseImageError("Each image must be a PNG or JPEG file no larger than 10 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.format not in {"PNG", "JPEG"} or original.width * original.height > MAX_IMAGE_PIXELS:
                    raise CaseImageError("Use PNG or JPEG images with no more than 20 million pixels.")
                image_format = original.format
                original.load()
                image = ImageOps.exif_transpose(original).convert("RGBA" if image_format == "PNG" else "RGB")
            normalized = BytesIO()
            image.save(normalized, format=image_format, **({"quality": 92} if image_format == "JPEG" else {}))
            image_data = normalized.getvalue()
            if len(image_data) > MAX_IMAGE_BYTES:
                raise CaseImageError("This image is too large after processing. Please upload a smaller copy.")
            metadata = DraftImage(sha256=sha256(image_data).hexdigest(),
                                  filename=str(filename).replace("\\", "/").rsplit("/", 1)[-1][:200] or "Image",
                                  format=image_format, width=image.width, height=image.height)
            image.thumbnail((320, 200))
            preview = BytesIO()
            image.save(preview, format="PNG")
            return PreparedImage(metadata, image_data, preview.getvalue())
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        if isinstance(exc, CaseImageError):
            raise
        raise CaseImageError("This file could not be read as a valid PNG or JPEG image.") from None


def images_hash(images):
    return sha256(json.dumps([image.sha256 for image in images]).encode()).hexdigest()
