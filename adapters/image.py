"""
Image adapter — Download and render image files.

Handles:
- Raster images (PNG, JPEG, GIF, WEBP, BMP, TIFF): deposit as-is
- SVG: deposit raw SVG + render to PNG via rsvg-convert (fallback: sips)

SVG rendering enables Claude to "see" the image content since Claude can
view PNGs but not SVGs directly.
"""

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from adapters.drive import download_file, get_file_size, download_file_to_temp, STREAMING_THRESHOLD_BYTES


# MIME types we handle
RASTER_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
}

SVG_MIME = "image/svg+xml"

ALL_IMAGE_MIMES = RASTER_IMAGE_MIMES | {SVG_MIME}


# Extension mapping for MIME types
MIME_TO_EXTENSION = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "image/svg+xml": "svg",
}


@dataclass
class ImageResult:
    """Result of image fetch."""
    image_bytes: bytes
    filename: str
    mime_type: str

    # For SVG: rendered PNG (if rendering succeeded)
    rendered_png_bytes: bytes | None = None
    render_method: Literal["rsvg-convert", "sips"] | None = None

    # Warnings during processing
    warnings: list[str] = field(default_factory=list)


def is_image_file(mime_type: str) -> bool:
    """Check if MIME type is an image we handle."""
    return mime_type in ALL_IMAGE_MIMES


def is_svg(mime_type: str) -> bool:
    """Check if MIME type is SVG."""
    return mime_type == SVG_MIME


def get_extension(mime_type: str) -> str:
    """Get file extension for MIME type."""
    return MIME_TO_EXTENSION.get(mime_type, "bin")


def fetch_image(file_id: str, filename: str, mime_type: str) -> ImageResult:
    """
    Fetch image from Drive.

    For raster images: just download.
    For SVG: download + render to PNG.
    Large files (>50MB) stream to temp file to avoid OOM.

    Args:
        file_id: Drive file ID
        filename: Original filename (for constructing output name)
        mime_type: MIME type of the image

    Returns:
        ImageResult with image bytes and optional PNG render for SVG
    """
    # Check file size for streaming decision
    file_size = get_file_size(file_id)
    warnings: list[str] = []

    if file_size > STREAMING_THRESHOLD_BYTES:
        # Large file: stream to temp, read back
        warnings.append(f"Large image ({file_size / (1024*1024):.1f}MB): using streaming download")
        ext = get_extension(mime_type)
        tmp_path = download_file_to_temp(file_id, suffix=f".{ext}")
        try:
            image_bytes = tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        # Normal download
        image_bytes = download_file(file_id)

    # Build output filename with correct extension
    ext = get_extension(mime_type)
    output_filename = f"image.{ext}"

    result = ImageResult(
        image_bytes=image_bytes,
        filename=output_filename,
        mime_type=mime_type,
        warnings=warnings,
    )

    # For SVG, also render to PNG
    if is_svg(mime_type):
        png_bytes, method, warning = _render_svg_to_png(image_bytes)
        if png_bytes:
            result.rendered_png_bytes = png_bytes
            result.render_method = method
        if warning:
            result.warnings.append(warning)

    return result


def _render_svg_to_png(svg_bytes: bytes) -> tuple[bytes | None, Literal["rsvg-convert", "sips"] | None, str | None]:
    """
    Render SVG to PNG.

    Strategy:
    1. Try rsvg-convert (librsvg) - best quality, cross-platform
    2. Fall back to sips (macOS built-in) - always available on Mac
    3. If both fail, return None with warning

    Args:
        svg_bytes: Raw SVG content

    Returns:
        Tuple of (png_bytes, method, warning)
        png_bytes is None if rendering failed
    """
    # Write SVG to temp file
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as svg_tmp:
        svg_tmp.write(svg_bytes)
        svg_path = Path(svg_tmp.name)

    png_path = svg_path.with_suffix(".png")

    try:
        # Try rsvg-convert first
        try:
            subprocess.run(
                [
                    "rsvg-convert",
                    "--width=1280",
                    "--keep-aspect-ratio",
                    "-o", str(png_path),
                    str(svg_path),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            if png_path.exists():
                return png_path.read_bytes(), "rsvg-convert", None
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass  # Try sips fallback

        # Try sips (macOS)
        try:
            subprocess.run(
                [
                    "sips",
                    "-s", "format", "png",
                    str(svg_path),
                    "--out", str(png_path),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            if png_path.exists():
                return png_path.read_bytes(), "sips", None
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

        # Both failed
        return None, None, "SVG render failed: neither rsvg-convert nor sips available"

    finally:
        # Clean up temp files
        svg_path.unlink(missing_ok=True)
        png_path.unlink(missing_ok=True)
