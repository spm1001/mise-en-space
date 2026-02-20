"""
Tests for PIL image validation — extractors/image.py and the two tool paths
that apply it (fetch_attachment, fetch_image_file).
"""

import io
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from PIL import Image

from extractors.image import validate_image_bytes, ImageValidation, MAX_IMAGE_DIMENSION_PX
from models import FetchError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png_bytes(width: int = 100, height: int = 100) -> bytes:
    """Create minimal valid PNG bytes at the given dimensions."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


DOCX_MAGIC = b"PK\x03\x04"  # ZIP/DOCX header — not a valid image


# ---------------------------------------------------------------------------
# validate_image_bytes unit tests
# ---------------------------------------------------------------------------

class TestValidateImageBytes:
    """Unit tests for the pure validate_image_bytes helper."""

    def test_valid_image_returns_true(self):
        """Valid PNG bytes → valid=True with dimensions."""
        result = validate_image_bytes(_make_png_bytes(200, 150))
        assert result.valid is True
        assert result.dimensions == "200×150"
        assert result.skip_reason is None

    def test_non_image_bytes_returns_invalid(self):
        """DOCX bytes under image MIME → valid=False with reason."""
        result = validate_image_bytes(DOCX_MAGIC)
        assert result.valid is False
        assert result.skip_reason is not None
        assert "not a valid image" in result.skip_reason
        assert result.dimensions is None

    def test_oversized_image_returns_invalid(self):
        """Image exceeding max_dimension_px → valid=False with dimensions."""
        big = _make_png_bytes(8001, 100)
        result = validate_image_bytes(big, max_dimension_px=8_000)
        assert result.valid is False
        assert result.dimensions == "8001×100"
        assert "8001×100" in result.skip_reason
        assert "8000px" in result.skip_reason

    def test_exactly_at_limit_is_valid(self):
        """Image exactly at the dimension limit is accepted."""
        edge = _make_png_bytes(8000, 8000)
        result = validate_image_bytes(edge, max_dimension_px=8_000)
        assert result.valid is True
        assert result.dimensions == "8000×8000"

    def test_empty_bytes_returns_invalid(self):
        """Empty bytes → not a valid image."""
        result = validate_image_bytes(b"")
        assert result.valid is False
        assert "not a valid image" in result.skip_reason

    def test_custom_max_dimension(self):
        """Custom max_dimension_px is honoured."""
        small_limit = _make_png_bytes(101, 50)
        result = validate_image_bytes(small_limit, max_dimension_px=100)
        assert result.valid is False
        assert "101×50" in result.skip_reason


# ---------------------------------------------------------------------------
# fetch_attachment — image path (tools/fetch/gmail.py)
# ---------------------------------------------------------------------------

class TestFetchAttachmentImageValidation:
    """
    fetch_attachment with an image attachment applies PIL validation.
    Invalid bytes return FetchError; valid bytes deposit normally.
    """

    def _make_thread_data(self, filename: str, mime_type: str, att_bytes: bytes):
        """Build the mock objects fetch_attachment needs."""
        from models import EmailAttachment, EmailMessage, GmailThreadData

        att = EmailAttachment(
            attachment_id="att001",
            filename=filename,
            mime_type=mime_type,
            size=len(att_bytes),
        )
        msg = MagicMock()
        msg.message_id = "msg001"
        msg.attachments = [att]
        thread = MagicMock()
        thread.messages = [msg]
        return thread, msg, att

    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.write_image")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail._download_attachment_bytes")
    def test_invalid_image_bytes_returns_fetch_error(
        self,
        mock_download_bytes,
        mock_fetch_thread,
        mock_lookup_exfil,
        mock_write_image,
        mock_write_manifest,
        mock_get_folder,
    ):
        """DOCX bytes declared as image/png → FetchError, nothing deposited."""
        from tools.fetch.gmail import fetch_attachment

        thread, msg, att = self._make_thread_data("photo.png", "image/png", DOCX_MAGIC)
        mock_fetch_thread.return_value = thread
        mock_lookup_exfil.return_value = {}
        mock_download_bytes.return_value = DOCX_MAGIC

        result = fetch_attachment("thread001", "photo.png")

        assert isinstance(result, FetchError)
        assert result.kind == "extraction_failed"
        assert "validation failed" in result.message.lower()
        mock_write_image.assert_not_called()

    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.write_image")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail._download_attachment_bytes")
    def test_valid_image_bytes_deposits_normally(
        self,
        mock_download_bytes,
        mock_fetch_thread,
        mock_lookup_exfil,
        mock_write_image,
        mock_write_manifest,
        mock_get_folder,
    ):
        """Valid PNG bytes → deposited, FetchResult returned."""
        from tools.fetch.gmail import fetch_attachment
        from models import FetchResult

        png = _make_png_bytes(200, 150)
        thread, msg, att = self._make_thread_data("photo.png", "image/png", png)
        mock_fetch_thread.return_value = thread
        mock_lookup_exfil.return_value = {}
        mock_download_bytes.return_value = png

        folder = Path("/tmp/mise/image--photo--thread001")
        mock_get_folder.return_value = folder
        mock_write_image.return_value = folder / "photo.png"

        result = fetch_attachment("thread001", "photo.png")

        assert isinstance(result, FetchResult)
        mock_write_image.assert_called_once()


# ---------------------------------------------------------------------------
# fetch_image_file — Drive image path (tools/fetch/drive.py)
# ---------------------------------------------------------------------------

class TestFetchImageFileValidation:
    """
    fetch_image_file with raster images applies PIL validation.
    Invalid bytes return FetchError; SVG bypasses validation.
    """

    @patch("tools.fetch.drive.write_manifest")
    @patch("tools.fetch.drive.write_image")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.adapter_fetch_image")
    def test_invalid_raster_bytes_returns_fetch_error(
        self, mock_adapter, mock_folder, mock_write, mock_manifest
    ):
        """DOCX bytes returned by adapter as image/png → FetchError."""
        from adapters.image import ImageResult
        from tools.fetch.drive import fetch_image_file

        mock_adapter.return_value = ImageResult(
            image_bytes=DOCX_MAGIC,
            filename="image.png",
            mime_type="image/png",
        )

        metadata = {"mimeType": "image/png", "name": "corrupt.png"}
        result = fetch_image_file("file123", "corrupt.png", metadata)

        assert isinstance(result, FetchError)
        assert result.kind == "extraction_failed"
        assert "validation failed" in result.message.lower()
        mock_write.assert_not_called()

    @patch("tools.fetch.drive.write_manifest")
    @patch("tools.fetch.drive.write_image")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.adapter_fetch_image")
    def test_valid_raster_bytes_deposits_normally(
        self, mock_adapter, mock_folder, mock_write, mock_manifest
    ):
        """Valid PNG bytes → deposited, FetchResult returned."""
        from adapters.image import ImageResult
        from tools.fetch.drive import fetch_image_file
        from models import FetchResult

        png = _make_png_bytes(300, 200)
        mock_adapter.return_value = ImageResult(
            image_bytes=png,
            filename="image.png",
            mime_type="image/png",
        )
        mock_folder.return_value = Path("/tmp/mise/image--photo--file123")
        mock_write.return_value = Path("/tmp/mise/image--photo--file123/image.png")

        metadata = {"mimeType": "image/png", "name": "photo.png"}
        result = fetch_image_file("file123", "photo.png", metadata)

        assert isinstance(result, FetchResult)
        mock_write.assert_called()

    @patch("tools.fetch.drive.write_manifest")
    @patch("tools.fetch.drive.write_image")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.adapter_fetch_image")
    def test_svg_bypasses_pil_validation(
        self, mock_adapter, mock_folder, mock_write, mock_manifest
    ):
        """SVG bytes are not raster — PIL validation is skipped entirely."""
        from adapters.image import ImageResult
        from tools.fetch.drive import fetch_image_file
        from models import FetchResult

        svg_bytes = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
        mock_adapter.return_value = ImageResult(
            image_bytes=svg_bytes,
            filename="image.svg",
            mime_type="image/svg+xml",
        )
        mock_folder.return_value = Path("/tmp/mise/image--diagram--svg123")
        mock_write.return_value = Path("/tmp/mise/image--diagram--svg123/image.svg")

        metadata = {"mimeType": "image/svg+xml", "name": "diagram.svg"}
        result = fetch_image_file("svg123", "diagram.svg", metadata)

        # Should succeed — SVG is not validated by PIL
        assert isinstance(result, FetchResult)
        assert result.metadata["is_svg"] is True
