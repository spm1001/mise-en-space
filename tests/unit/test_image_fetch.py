"""Tests for image file fetch functionality."""

import io
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from PIL import Image as PILImage

from adapters.image import (
    is_image_file,
    is_svg,
    get_extension,
    fetch_image,
    RASTER_IMAGE_MIMES,
    SVG_MIME,
    ALL_IMAGE_MIMES,
    ImageResult,
    _render_svg_to_png,
)
from tools.fetch import fetch_image_file


def _make_valid_png(width: int = 10, height: int = 10) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height)).save(buf, format="PNG")
    return buf.getvalue()


_VALID_PNG = _make_valid_png()


class TestIsImageFile:
    """Tests for is_image_file() helper."""

    def test_raster_images(self):
        """Recognizes raster image MIME types."""
        for mime_type in RASTER_IMAGE_MIMES:
            assert is_image_file(mime_type), f"Should recognize {mime_type}"

    def test_svg(self):
        """Recognizes SVG MIME type."""
        assert is_image_file(SVG_MIME)
        assert is_image_file("image/svg+xml")

    def test_non_image_types(self):
        """Rejects non-image MIME types."""
        assert not is_image_file("application/pdf")
        assert not is_image_file("text/plain")
        assert not is_image_file("video/mp4")
        assert not is_image_file("application/vnd.google-apps.document")


class TestIsSvg:
    """Tests for is_svg() helper."""

    def test_svg_mime(self):
        """Recognizes SVG MIME type."""
        assert is_svg("image/svg+xml")
        assert is_svg(SVG_MIME)

    def test_non_svg(self):
        """Rejects non-SVG MIME types."""
        assert not is_svg("image/png")
        assert not is_svg("image/jpeg")


class TestGetExtension:
    """Tests for get_extension() helper."""

    def test_known_types(self):
        """Returns correct extensions for known types."""
        assert get_extension("image/png") == "png"
        assert get_extension("image/jpeg") == "jpg"
        assert get_extension("image/gif") == "gif"
        assert get_extension("image/webp") == "webp"
        assert get_extension("image/svg+xml") == "svg"

    def test_unknown_type(self):
        """Returns 'bin' for unknown types."""
        assert get_extension("image/unknown") == "bin"


class TestFetchImageAdapter:
    """Tests for the image adapter fetch_image()."""

    @patch("adapters.image.get_file_size")
    @patch("adapters.image.download_file")
    def test_fetch_raster_image(self, mock_download, mock_size):
        """Fetches raster image and returns bytes."""
        png_bytes = b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
        mock_download.return_value = png_bytes
        mock_size.return_value = 100  # Small file

        result = fetch_image("abc123", "diagram.png", "image/png")

        assert result.image_bytes == png_bytes
        assert result.filename == "image.png"
        assert result.mime_type == "image/png"
        assert result.rendered_png_bytes is None  # No rendering for raster
        assert result.render_method is None

    @patch("adapters.image.get_file_size")
    @patch("adapters.image._render_svg_to_png")
    @patch("adapters.image.download_file")
    def test_fetch_svg_with_successful_render(self, mock_download, mock_render, mock_size):
        """Fetches SVG and renders to PNG."""
        svg_bytes = b"<svg>...</svg>"
        png_bytes = b"\x89PNG\r\n\x1a\n"
        mock_download.return_value = svg_bytes
        mock_render.return_value = (png_bytes, "rsvg-convert", None)
        mock_size.return_value = 100  # Small file

        result = fetch_image("svg123", "diagram.svg", "image/svg+xml")

        assert result.image_bytes == svg_bytes
        assert result.filename == "image.svg"
        assert result.mime_type == "image/svg+xml"
        assert result.rendered_png_bytes == png_bytes
        assert result.render_method == "rsvg-convert"

    @patch("adapters.image.get_file_size")
    @patch("adapters.image._render_svg_to_png")
    @patch("adapters.image.download_file")
    def test_fetch_svg_with_failed_render(self, mock_download, mock_render, mock_size):
        """Handles SVG render failure gracefully."""
        svg_bytes = b"<svg>...</svg>"
        mock_download.return_value = svg_bytes
        mock_render.return_value = (None, None, "SVG render failed: neither rsvg-convert nor sips available")
        mock_size.return_value = 100  # Small file

        result = fetch_image("svg456", "diagram.svg", "image/svg+xml")

        assert result.image_bytes == svg_bytes
        assert result.filename == "image.svg"
        assert result.rendered_png_bytes is None
        assert result.render_method is None
        assert "SVG render failed" in result.warnings[0]


class TestRenderSvgToPng:
    """Tests for SVG to PNG rendering."""

    @patch("adapters.image.subprocess.run")
    def test_rsvg_convert_success(self, mock_run):
        """Uses rsvg-convert when available."""
        svg_bytes = b"<svg></svg>"
        png_bytes = b"\x89PNG\r\n\x1a\n"

        # Mock subprocess to succeed
        mock_run.return_value = MagicMock(returncode=0)

        # We need to mock the file writing/reading
        with patch("adapters.image.Path.exists", return_value=True):
            with patch("adapters.image.Path.read_bytes", return_value=png_bytes):
                with patch("adapters.image.Path.unlink"):
                    result_bytes, method, warning = _render_svg_to_png(svg_bytes)

        assert method == "rsvg-convert"
        assert warning is None

    @patch("adapters.image.subprocess.run")
    def test_sips_fallback(self, mock_run):
        """Falls back to sips when rsvg-convert unavailable."""
        svg_bytes = b"<svg></svg>"
        png_bytes = b"\x89PNG\r\n\x1a\n"

        # First call (rsvg-convert) fails, second call (sips) succeeds
        mock_run.side_effect = [
            FileNotFoundError("rsvg-convert not found"),
            MagicMock(returncode=0),
        ]

        with patch("adapters.image.Path.exists", return_value=True):
            with patch("adapters.image.Path.read_bytes", return_value=png_bytes):
                with patch("adapters.image.Path.unlink"):
                    result_bytes, method, warning = _render_svg_to_png(svg_bytes)

        assert method == "sips"
        assert warning is None

    @patch("adapters.image.subprocess.run")
    def test_both_renderers_fail(self, mock_run):
        """Returns warning when both renderers fail."""
        svg_bytes = b"<svg></svg>"

        # Both calls fail
        mock_run.side_effect = [
            FileNotFoundError("rsvg-convert not found"),
            FileNotFoundError("sips not found"),
        ]

        with patch("adapters.image.Path.unlink"):
            result_bytes, method, warning = _render_svg_to_png(svg_bytes)

        assert result_bytes is None
        assert method is None
        assert "neither rsvg-convert nor sips available" in warning


class TestFetchImageFile:
    """Tests for fetch_image_file() in tools/fetch.py."""

    @patch("tools.fetch.drive.write_manifest")
    @patch("tools.fetch.drive.write_image")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.adapter_fetch_image")
    def test_fetch_png(self, mock_adapter, mock_folder, mock_write, mock_manifest):
        """Fetches PNG image and deposits correctly."""
        png_bytes = _VALID_PNG
        mock_adapter.return_value = ImageResult(
            image_bytes=png_bytes,
            filename="image.png",
            mime_type="image/png",
        )
        mock_folder.return_value = Path("/tmp/mise/image--diagram--abc123")
        mock_write.return_value = Path("/tmp/mise/image--diagram--abc123/image.png")

        metadata = {"mimeType": "image/png", "name": "diagram.png"}
        result = fetch_image_file("abc123", "diagram.png", metadata)

        assert result.type == "image"
        assert result.format == "image"
        assert result.metadata["mime_type"] == "image/png"
        assert result.metadata["size_bytes"] == len(png_bytes)
        # Original image should be the content_file for raster
        assert "image.png" in result.content_file

    @patch("tools.fetch.drive.write_manifest")
    @patch("tools.fetch.drive.write_image")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.adapter_fetch_image")
    def test_fetch_svg_with_render(self, mock_adapter, mock_folder, mock_write, mock_manifest):
        """Fetches SVG with rendered PNG and deposits both."""
        svg_bytes = b"<svg>...</svg>"
        png_bytes = b"\x89PNG\r\n\x1a\n"
        mock_adapter.return_value = ImageResult(
            image_bytes=svg_bytes,
            filename="image.svg",
            mime_type="image/svg+xml",
            rendered_png_bytes=png_bytes,
            render_method="rsvg-convert",
        )
        mock_folder.return_value = Path("/tmp/mise/image--diagram--svg123")
        mock_write.side_effect = [
            Path("/tmp/mise/image--diagram--svg123/image.svg"),
            Path("/tmp/mise/image--diagram--svg123/image_rendered.png"),
        ]

        metadata = {"mimeType": "image/svg+xml", "name": "diagram.svg"}
        result = fetch_image_file("svg123", "diagram.svg", metadata)

        assert result.type == "image"
        assert result.metadata["is_svg"] is True
        assert result.metadata["has_rendered_png"] is True
        assert result.metadata["render_method"] == "rsvg-convert"
        # Rendered PNG should be the content_file for SVG
        assert "image_rendered.png" in result.content_file
        # Should have written both files
        assert mock_write.call_count == 2

    @patch("tools.fetch.drive.write_manifest")
    @patch("tools.fetch.drive.write_image")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.adapter_fetch_image")
    def test_fetch_svg_without_render(self, mock_adapter, mock_folder, mock_write, mock_manifest):
        """Fetches SVG without render (fallback to raw SVG)."""
        svg_bytes = b"<svg>...</svg>"
        mock_adapter.return_value = ImageResult(
            image_bytes=svg_bytes,
            filename="image.svg",
            mime_type="image/svg+xml",
            warnings=["SVG render failed: neither rsvg-convert nor sips available"],
        )
        mock_folder.return_value = Path("/tmp/mise/image--diagram--svg456")
        mock_write.return_value = Path("/tmp/mise/image--diagram--svg456/image.svg")

        metadata = {"mimeType": "image/svg+xml", "name": "diagram.svg"}
        result = fetch_image_file("svg456", "diagram.svg", metadata)

        assert result.type == "image"
        assert result.metadata["is_svg"] is True
        assert result.metadata["has_rendered_png"] is False
        # Raw SVG should be the content_file when no render
        assert "image.svg" in result.content_file
        # Should have written only one file
        assert mock_write.call_count == 1

    @patch("tools.fetch.drive.write_manifest")
    @patch("tools.fetch.drive.write_image")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.adapter_fetch_image")
    def test_fetch_with_email_context(self, mock_adapter, mock_folder, mock_write, mock_manifest):
        """Includes email_context when provided."""
        from models import EmailContext

        mock_adapter.return_value = ImageResult(
            image_bytes=_VALID_PNG,
            filename="image.png",
            mime_type="image/png",
        )
        mock_folder.return_value = Path("/tmp/test")
        mock_write.return_value = Path("/tmp/test/image.png")

        email_ctx = EmailContext(
            message_id="msg123",
            from_address="test@example.com",
            subject="Test email",
        )
        metadata = {"mimeType": "image/png", "name": "attachment.png"}
        result = fetch_image_file("file123", "attachment.png", metadata, email_context=email_ctx)

        assert "email_context" in result.metadata
        assert result.metadata["email_context"]["message_id"] == "msg123"


class TestFetchDriveRouting:
    """Tests for image routing in fetch_drive()."""

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_image_file")
    def test_routes_png_to_image_handler(self, mock_fetch_image, mock_metadata):
        """Routes PNG files to image handler."""
        from tools.fetch import fetch_drive

        mock_metadata.return_value = {
            "mimeType": "image/png",
            "name": "screenshot.png",
        }
        mock_fetch_image.return_value = MagicMock()

        fetch_drive("png123")

        mock_fetch_image.assert_called_once()

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_image_file")
    def test_routes_svg_to_image_handler(self, mock_fetch_image, mock_metadata):
        """Routes SVG files to image handler."""
        from tools.fetch import fetch_drive

        mock_metadata.return_value = {
            "mimeType": "image/svg+xml",
            "name": "diagram.svg",
        }
        mock_fetch_image.return_value = MagicMock()

        fetch_drive("svg123")

        mock_fetch_image.assert_called_once()

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_image_file")
    def test_routes_jpeg_to_image_handler(self, mock_fetch_image, mock_metadata):
        """Routes JPEG files to image handler."""
        from tools.fetch import fetch_drive

        mock_metadata.return_value = {
            "mimeType": "image/jpeg",
            "name": "photo.jpg",
        }
        mock_fetch_image.return_value = MagicMock()

        fetch_drive("jpg123")

        mock_fetch_image.assert_called_once()
