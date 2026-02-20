"""
Tests for PIL image validation and resize — extractors/image.py and the two
tool paths that apply it (fetch_attachment, fetch_image_file).
"""

import io
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from PIL import Image

from extractors.image import (
    validate_image_bytes,
    ImageValidation,
    MAX_IMAGE_DIMENSION_PX,
    resize_image_bytes,
    ImageResizeResult,
    MAX_LONG_EDGE_PX,
)
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
# resize_image_bytes unit tests
# ---------------------------------------------------------------------------

def _make_jpeg_bytes(width: int = 100, height: int = 100) -> bytes:
    """Create minimal valid JPEG bytes at the given dimensions."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(0, 128, 255)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class TestResizeImageBytes:
    """Unit tests for the pure resize_image_bytes helper."""

    def test_small_image_returned_unchanged(self):
        """Image within the long-edge limit is returned as-is (no resize)."""
        png = _make_png_bytes(100, 80)
        result = resize_image_bytes(png, "image/png")
        assert result.content_bytes == png
        assert result.dimensions == "100×80"
        assert result.original_dimensions is None
        assert result.scale_factor is None
        assert result.jpeg_fallback is False

    def test_exactly_at_limit_returned_unchanged(self):
        """Image exactly at MAX_LONG_EDGE_PX is not resized."""
        png = _make_png_bytes(MAX_LONG_EDGE_PX, 100)
        result = resize_image_bytes(png, "image/png")
        assert result.dimensions == f"{MAX_LONG_EDGE_PX}×100"
        assert result.original_dimensions is None

    def test_wide_image_resized_on_long_edge(self):
        """Landscape image: width is long edge, scaled to MAX_LONG_EDGE_PX."""
        png = _make_png_bytes(2000, 1000)
        result = resize_image_bytes(png, "image/png", max_long_edge=500)
        w, h = map(int, result.dimensions.split("×"))
        assert w == 500
        assert h == 250
        assert result.original_dimensions == "2000×1000"
        assert result.scale_factor == pytest.approx(0.25, abs=0.01)
        assert result.mime_type == "image/png"
        # Deposited bytes open as a valid PNG at the new dimensions
        opened = Image.open(io.BytesIO(result.content_bytes))
        assert opened.size == (500, 250)

    def test_tall_image_resized_on_long_edge(self):
        """Portrait image: height is long edge."""
        png = _make_png_bytes(500, 2000)
        result = resize_image_bytes(png, "image/png", max_long_edge=400)
        w, h = map(int, result.dimensions.split("×"))
        assert h == 400
        assert w == 100
        assert result.original_dimensions == "500×2000"

    def test_jpeg_stays_jpeg_after_resize(self):
        """JPEG input → JPEG output, mime_type unchanged."""
        jpg = _make_jpeg_bytes(3000, 2000)
        result = resize_image_bytes(jpg, "image/jpeg", max_long_edge=600)
        assert result.mime_type == "image/jpeg"
        assert result.jpeg_fallback is False
        opened = Image.open(io.BytesIO(result.content_bytes))
        assert opened.format == "JPEG"

    def test_non_image_bytes_raises_value_error(self):
        """Non-image bytes raise ValueError (caller should skip)."""
        with pytest.raises(ValueError, match="not a valid image"):
            resize_image_bytes(b"PK\x03\x04", "image/png")

    def test_empty_bytes_raises_value_error(self):
        """Empty bytes raise ValueError."""
        with pytest.raises(ValueError, match="not a valid image"):
            resize_image_bytes(b"", "image/png")

    def test_png_jpeg_fallback_when_still_too_large(self):
        """PNG still > max_size_bytes after resize → converted to JPEG."""
        # Use a 400×400 PNG (exceeds max_long_edge=200 → resize is triggered)
        # then set max_size_bytes=1 so the resized PNG still triggers the fallback.
        png = _make_png_bytes(400, 400)
        result = resize_image_bytes(
            png,
            "image/png",
            max_long_edge=200,   # triggers resize (400 > 200)
            max_size_bytes=1,    # 1 byte — resized PNG guaranteed to exceed this
        )
        assert result.jpeg_fallback is True
        assert result.mime_type == "image/jpeg"
        opened = Image.open(io.BytesIO(result.content_bytes))
        assert opened.format == "JPEG"

    def test_real_world_dimensions_2746x1908(self):
        """2746×1908 PNG → 1568px wide after resize (the --done criterion)."""
        png = _make_png_bytes(2746, 1908)
        result = resize_image_bytes(png, "image/png")
        w, h = map(int, result.dimensions.split("×"))
        assert w == MAX_LONG_EDGE_PX
        assert result.original_dimensions == "2746×1908"
        assert result.scale_factor is not None


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

    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.write_image")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail._download_attachment_bytes")
    def test_oversized_image_is_resized_not_skipped(
        self,
        mock_download_bytes,
        mock_fetch_thread,
        mock_lookup_exfil,
        mock_write_image,
        mock_write_manifest,
        mock_get_folder,
    ):
        """2746×1908 PNG → deposited at 1568px wide; metadata notes original dimensions."""
        from tools.fetch.gmail import fetch_attachment
        from models import FetchResult

        big_png = _make_png_bytes(2746, 1908)
        thread, msg, att = self._make_thread_data("photo.png", "image/png", big_png)
        mock_fetch_thread.return_value = thread
        mock_lookup_exfil.return_value = {}
        mock_download_bytes.return_value = big_png

        folder = Path("/tmp/mise/image--photo--thread001")
        mock_get_folder.return_value = folder
        mock_write_image.return_value = folder / "photo.png"

        result = fetch_attachment("thread001", "photo.png")

        assert isinstance(result, FetchResult)
        mock_write_image.assert_called_once()
        # Resize metadata should be present
        assert result.metadata["original_dimensions"] == "2746×1908"
        assert result.metadata["scaled_to"].startswith("1568×")
        assert result.metadata["scale_factor"] is not None


# ---------------------------------------------------------------------------
# _deposit_attachment_content — eager thread extraction path
# ---------------------------------------------------------------------------

class TestDepositAttachmentContentResize:
    """
    _deposit_attachment_content handles images in the eager fetch_gmail path.
    Oversized images are resized; PIL failures produce a skip dict.
    """

    def _call(self, content_bytes: bytes, mime_type: str, filename: str = "img.png"):
        from tools.fetch.gmail import _deposit_attachment_content
        return _deposit_attachment_content(
            content_bytes=content_bytes,
            filename=filename,
            mime_type=mime_type,
            file_id="file001",
            folder=Path("/tmp/fake"),
        )

    @patch("tools.fetch.gmail.write_image")
    def test_small_image_deposited_without_resize_metadata(self, mock_write):
        """Small image (within limit) deposits without resize fields."""
        mock_write.return_value = Path("/tmp/fake/img.png")
        result = self._call(_make_png_bytes(100, 80), "image/png")
        assert result is not None
        assert result.get("skipped") is None
        assert result["dimensions"] == "100×80"
        assert "original_dimensions" not in result

    @patch("tools.fetch.gmail.write_image")
    def test_oversized_image_deposits_with_resize_metadata(self, mock_write):
        """2746×1908 PNG → deposited; result has original_dimensions and scaled_to."""
        mock_write.return_value = Path("/tmp/fake/img.png")
        result = self._call(_make_png_bytes(2746, 1908), "image/png")
        assert result is not None
        assert result.get("skipped") is None
        assert result["original_dimensions"] == "2746×1908"
        assert result["scaled_to"].startswith("1568×")
        assert result["scale_factor"] is not None
        mock_write.assert_called_once()

    @patch("tools.fetch.gmail.write_image")
    def test_mime_mismatch_produces_skip_dict(self, mock_write):
        """DOCX bytes declared as image/png → skip dict, nothing written."""
        result = self._call(b"PK\x03\x04", "image/png", "doc.png")
        assert result is not None
        assert result["skipped"] is True
        assert "not a valid image" in result["reason"]
        mock_write.assert_not_called()


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
