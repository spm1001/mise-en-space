"""Pure image validation and resize utilities."""

import io
from dataclasses import dataclass, field

from PIL import Image


# Claude API hard limits (per-axis px; raw bytes). Tool constants may add headroom.
MAX_IMAGE_DIMENSION_PX = 8_000

# Anthropic's optimal long-edge threshold — API downscales internally above this
# anyway, so resizing to this value costs nothing in quality and saves tokens.
MAX_LONG_EDGE_PX = 1_568

# API byte limit (hard limit: 5MB; 10% headroom)
MAX_IMAGE_BYTES = 4_500_000

# PIL format strings keyed by MIME type
_MIME_TO_FORMAT: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
}


@dataclass
class ImageValidation:
    valid: bool
    dimensions: str | None = None   # "W×H" — set for both valid and oversized results
    skip_reason: str | None = None  # set when valid=False


@dataclass
class ImageResizeResult:
    content_bytes: bytes
    mime_type: str              # may change to image/jpeg if JPEG fallback applied
    dimensions: str             # "W×H" final dimensions
    original_dimensions: str | None = None   # set when resize occurred
    scale_factor: float | None = None        # set when resize occurred
    jpeg_fallback: bool = False              # True if PNG→JPEG conversion applied


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


def resize_image_bytes(
    content_bytes: bytes,
    mime_type: str,
    max_long_edge: int = MAX_LONG_EDGE_PX,
    max_size_bytes: int = MAX_IMAGE_BYTES,
) -> ImageResizeResult:
    """
    Open image bytes and resize if the long edge exceeds max_long_edge.

    Raises ValueError if PIL cannot open the bytes (genuine MIME mismatch —
    cannot be fixed by resizing; caller should skip the image).

    Resize rules:
    - If max(w, h) ≤ max_long_edge: return bytes and mime_type unchanged.
    - If max(w, h) > max_long_edge: scale to max_long_edge on the long edge
      (LANCZOS resampling), preserving aspect ratio.
    - Keep original format (JPEG stays JPEG, PNG stays PNG).
    - If PNG is still > max_size_bytes after resize (rare): convert to JPEG
      as a last resort (jpeg_fallback=True in result).

    Does NOT handle SVG — callers should skip validation for image/svg+xml.
    """
    try:
        img = Image.open(io.BytesIO(content_bytes))
        img.load()
    except Exception:
        raise ValueError(
            "bytes are not a valid image (content doesn't match declared MIME type)"
        )

    w, h = img.size
    original_dimensions = f"{w}×{h}"

    if max(w, h) <= max_long_edge:
        return ImageResizeResult(
            content_bytes=content_bytes,
            mime_type=mime_type,
            dimensions=original_dimensions,
        )

    # Scale so the long edge equals max_long_edge exactly.
    scale = max_long_edge / max(w, h)
    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)

    fmt = _MIME_TO_FORMAT.get(mime_type, "PNG")
    out_mime = mime_type
    jpeg_fallback = False

    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, format="JPEG", quality=85)
    else:
        img.save(buf, format=fmt)

    resized_bytes = buf.getvalue()

    # PNG fallback: convert to JPEG if still over the byte limit.
    if fmt == "PNG" and len(resized_bytes) > max_size_bytes:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        resized_bytes = buf.getvalue()
        out_mime = "image/jpeg"
        jpeg_fallback = True

    return ImageResizeResult(
        content_bytes=resized_bytes,
        mime_type=out_mime,
        dimensions=f"{new_w}×{new_h}",
        original_dimensions=original_dimensions,
        scale_factor=round(scale, 4),
        jpeg_fallback=jpeg_fallback,
    )
