"""Pure image validation utilities."""

import io
from dataclasses import dataclass

from PIL import Image


# Claude API hard limits (per-axis px; raw bytes). Tool constants may add headroom.
MAX_IMAGE_DIMENSION_PX = 8_000


@dataclass
class ImageValidation:
    valid: bool
    dimensions: str | None = None   # "W×H" — set for both valid and oversized results
    skip_reason: str | None = None  # set when valid=False


def validate_image_bytes(content_bytes: bytes, max_dimension_px: int = MAX_IMAGE_DIMENSION_PX) -> ImageValidation:
    """
    Validate image bytes using PIL.

    Returns ImageValidation with valid=True and dimensions if the bytes are
    a readable raster image within the dimension limit.

    Returns valid=False with skip_reason if:
    - PIL cannot open the bytes (content doesn't match declared MIME type)
    - Image dimensions exceed max_dimension_px on either axis

    Does NOT validate SVG — callers should skip validation for image/svg+xml.
    """
    try:
        img = Image.open(io.BytesIO(content_bytes))
        w, h = img.size
    except Exception:
        return ImageValidation(
            valid=False,
            skip_reason="bytes are not a valid image (content doesn't match declared MIME type)",
        )

    if w > max_dimension_px or h > max_dimension_px:
        return ImageValidation(
            valid=False,
            dimensions=f"{w}×{h}",
            skip_reason=f"dimensions {w}×{h} exceed API limit of {max_dimension_px}px per axis",
        )

    return ImageValidation(valid=True, dimensions=f"{w}×{h}")
