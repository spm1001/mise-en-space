"""Unit tests for PDF page thumbnail rendering."""

import io
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from adapters.pdf import (
    _calculate_dpi,
    render_pdf_pages,
    PageImage,
    PdfExtractionResult,
    PdfThumbnailResult,
    TARGET_MAX_PX,
    FIXED_DPI,
    MAX_DPI,
    MAX_THUMBNAIL_PAGES,
)


class TestCalculateDpi:
    """Pure math: points → DPI for CoreGraphics path."""

    def test_a4_portrait(self) -> None:
        """A4 portrait (595×842 pts) → longest side is 842 → ~134 DPI."""
        dpi = _calculate_dpi(595, 842)
        # 842pts = 11.69in → 1568/11.69 ≈ 134
        assert dpi == 134

    def test_us_letter(self) -> None:
        """US Letter (612×792 pts) → longest side 792 → ~142 DPI."""
        dpi = _calculate_dpi(612, 792)
        # 792pts = 11.0in → 1568/11.0 ≈ 142
        assert dpi == 142

    def test_landscape(self) -> None:
        """Landscape A4 (842×595 pts) → same DPI as portrait (max dimension is the same)."""
        dpi = _calculate_dpi(842, 595)
        assert dpi == _calculate_dpi(595, 842)

    def test_tiny_page_capped(self) -> None:
        """Tiny page (144×144 pts = 2"×2") → capped at MAX_DPI."""
        dpi = _calculate_dpi(144, 144)
        # 144pts = 2in → 1568/2.0 = 784, capped at 200
        assert dpi == MAX_DPI

    def test_zero_dimensions_fallback(self) -> None:
        """Zero dimensions → FIXED_DPI fallback."""
        assert _calculate_dpi(0, 0) == FIXED_DPI
        assert _calculate_dpi(0, 842) == FIXED_DPI
        assert _calculate_dpi(595, 0) == FIXED_DPI

    def test_negative_dimensions_fallback(self) -> None:
        """Negative dimensions → FIXED_DPI fallback."""
        assert _calculate_dpi(-100, 842) == FIXED_DPI


class TestRenderPdfPages:
    """Platform dispatch and fallback chain."""

    def test_requires_bytes_or_path(self) -> None:
        """Must provide file_bytes or file_path."""
        with pytest.raises(ValueError, match="Must provide either"):
            render_pdf_pages()

    @patch("adapters.pdf._render_via_pdf2image")
    @patch("adapters.pdf._render_via_coregraphics")
    def test_darwin_tries_coregraphics_first(
        self,
        mock_cg: MagicMock,
        mock_pdf2img: MagicMock,
    ) -> None:
        """On macOS, CoreGraphics is tried first."""
        mock_cg.return_value = PdfThumbnailResult(
            pages=[], page_count=1, method="coregraphics"
        )

        with patch("sys.platform", "darwin"):
            result = render_pdf_pages(file_bytes=b"%PDF-test")

        mock_cg.assert_called_once()
        mock_pdf2img.assert_not_called()
        assert result.method == "coregraphics"

    @patch("adapters.pdf._render_via_pdf2image")
    @patch("adapters.pdf._render_via_coregraphics")
    def test_darwin_falls_back_on_import_error(
        self,
        mock_cg: MagicMock,
        mock_pdf2img: MagicMock,
    ) -> None:
        """On macOS, ImportError from CG falls back to pdf2image."""
        mock_cg.side_effect = ImportError("No PyObjC")
        mock_pdf2img.return_value = PdfThumbnailResult(
            pages=[], page_count=1, method="pdf2image"
        )

        with patch("sys.platform", "darwin"):
            result = render_pdf_pages(file_bytes=b"%PDF-test")

        mock_cg.assert_called_once()
        mock_pdf2img.assert_called_once()
        assert result.method == "pdf2image"

    @patch("adapters.pdf._render_via_pdf2image")
    @patch("adapters.pdf._render_via_coregraphics")
    def test_linux_skips_coregraphics(
        self,
        mock_cg: MagicMock,
        mock_pdf2img: MagicMock,
    ) -> None:
        """On Linux, goes straight to pdf2image."""
        mock_pdf2img.return_value = PdfThumbnailResult(
            pages=[], page_count=1, method="pdf2image"
        )

        with patch("sys.platform", "linux"):
            result = render_pdf_pages(file_bytes=b"%PDF-test")

        mock_cg.assert_not_called()
        mock_pdf2img.assert_called_once()
        assert result.method == "pdf2image"


class TestRenderViaPdf2image:
    """pdf2image backend tests (mocked — no real poppler needed)."""

    @patch("pdf2image.convert_from_bytes")
    def test_renders_pages_from_bytes(self, mock_convert: MagicMock) -> None:
        """Renders pages from in-memory bytes."""
        from adapters.pdf import _render_via_pdf2image

        # Create fake PIL images
        mock_img = MagicMock()
        mock_img.width = 1240
        mock_img.height = 1754
        mock_img.save = lambda buf, format: buf.write(b"PNG_DATA")
        mock_convert.return_value = [mock_img]

        result = _render_via_pdf2image(file_bytes=b"%PDF-test")

        assert len(result.pages) == 1
        assert result.pages[0].page_index == 0
        assert result.pages[0].width_px == 1240
        assert result.pages[0].height_px == 1754
        assert result.method == "pdf2image"
        mock_convert.assert_called_once()

    @patch("pdf2image.convert_from_path")
    def test_renders_pages_from_path(self, mock_convert: MagicMock, tmp_path: Path) -> None:
        """Renders pages from file path."""
        from adapters.pdf import _render_via_pdf2image

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-test")

        mock_img = MagicMock()
        mock_img.width = 1240
        mock_img.height = 1754
        mock_img.save = lambda buf, format: buf.write(b"PNG_DATA")
        mock_convert.return_value = [mock_img]

        result = _render_via_pdf2image(file_path=pdf_file)

        assert len(result.pages) == 1
        mock_convert.assert_called_once_with(
            str(pdf_file), dpi=FIXED_DPI, fmt="png", last_page=MAX_THUMBNAIL_PAGES
        )

    @patch("pdf2image.convert_from_bytes")
    def test_poppler_not_installed_raises_import_error(self, mock_convert: MagicMock) -> None:
        """Missing poppler-utils raises ImportError (not crash)."""
        from pdf2image.exceptions import PDFInfoNotInstalledError
        from adapters.pdf import _render_via_pdf2image

        mock_convert.side_effect = PDFInfoNotInstalledError("pdftoppm not found")

        with pytest.raises(ImportError, match="poppler-utils not installed"):
            _render_via_pdf2image(file_bytes=b"%PDF-test")

    @patch("pdf2image.convert_from_bytes")
    def test_corrupt_pdf_raises_value_error(self, mock_convert: MagicMock) -> None:
        """Corrupt PDF raises ValueError."""
        from pdf2image.exceptions import PDFPageCountError
        from adapters.pdf import _render_via_pdf2image

        mock_convert.side_effect = PDFPageCountError("Unable to get page count")

        with pytest.raises(ValueError, match="Could not determine PDF page count"):
            _render_via_pdf2image(file_bytes=b"NOT_A_PDF")


class TestRenderFailsGracefully:
    """Most important test: thumbnail failure must never break text extraction."""

    @patch("adapters.pdf.render_pdf_pages")
    @patch("adapters.pdf.get_file_size")
    @patch("adapters.pdf.download_file")
    @patch("adapters.pdf._extract_with_markitdown")
    def test_thumbnail_failure_preserves_text_extraction(
        self,
        mock_markitdown: MagicMock,
        mock_download: MagicMock,
        mock_get_size: MagicMock,
        mock_render: MagicMock,
    ) -> None:
        """Rendering failure → thumbnails=None, warning, content intact."""
        from adapters.pdf import fetch_and_extract_pdf

        mock_get_size.return_value = 1024  # Small file
        mock_download.return_value = b"%PDF-content"
        mock_markitdown.return_value = "Extracted text " * 100
        mock_render.side_effect = RuntimeError("poppler exploded")

        result = fetch_and_extract_pdf("file123")

        assert result.content == "Extracted text " * 100
        assert result.method == "markitdown"
        assert result.thumbnails is None
        assert any("Thumbnail rendering failed" in w for w in result.warnings)

    @patch("adapters.pdf.render_pdf_pages")
    @patch("adapters.pdf.get_file_size")
    @patch("adapters.pdf.download_file_to_temp")
    @patch("adapters.pdf.MarkItDown")
    def test_large_file_thumbnail_failure_preserves_text(
        self,
        mock_markitdown_class: MagicMock,
        mock_download_temp: MagicMock,
        mock_get_size: MagicMock,
        mock_render: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Large file path: rendering failure → content intact, temp cleaned up."""
        from adapters.pdf import fetch_and_extract_pdf, STREAMING_THRESHOLD_BYTES

        mock_get_size.return_value = STREAMING_THRESHOLD_BYTES + 1
        temp_file = tmp_path / "large.pdf"
        temp_file.write_bytes(b"%PDF-content")
        mock_download_temp.return_value = temp_file

        mock_md = MagicMock()
        mock_md.convert_local.return_value = MagicMock(text_content="Large file content " * 100)
        mock_markitdown_class.return_value = mock_md

        mock_render.side_effect = ImportError("No poppler")

        result = fetch_and_extract_pdf("large_id")

        assert result.content == "Large file content " * 100
        assert result.thumbnails is None
        assert any("Thumbnail rendering failed" in w for w in result.warnings)

    @patch("adapters.pdf.render_pdf_pages")
    @patch("adapters.pdf.get_file_size")
    @patch("adapters.pdf.download_file")
    @patch("adapters.pdf._extract_with_markitdown")
    def test_successful_thumbnails_attached_to_result(
        self,
        mock_markitdown: MagicMock,
        mock_download: MagicMock,
        mock_get_size: MagicMock,
        mock_render: MagicMock,
    ) -> None:
        """Successful rendering → thumbnails attached to result."""
        from adapters.pdf import fetch_and_extract_pdf

        mock_get_size.return_value = 1024
        mock_download.return_value = b"%PDF-content"
        mock_markitdown.return_value = "Extracted text " * 100
        mock_render.return_value = PdfThumbnailResult(
            pages=[
                PageImage(page_index=0, image_bytes=b"PNG1", width_px=1240, height_px=1754),
                PageImage(page_index=1, image_bytes=b"PNG2", width_px=1240, height_px=1754),
            ],
            page_count=2,
            method="pdf2image",
        )

        result = fetch_and_extract_pdf("file123")

        assert result.thumbnails is not None
        assert len(result.thumbnails.pages) == 2
        assert result.thumbnails.page_count == 2
        assert result.thumbnails.method == "pdf2image"


class TestPageCap:
    """Verify MAX_THUMBNAIL_PAGES cap."""

    @patch("pdf2image.convert_from_bytes")
    @patch("pdf2image.pdfinfo_from_bytes")
    def test_cap_at_100_pages(self, mock_info: MagicMock, mock_convert: MagicMock) -> None:
        """200-page PDF → only first 100 rendered, warning about limit."""
        from adapters.pdf import _render_via_pdf2image

        # pdf2image returns MAX_THUMBNAIL_PAGES images (the last_page cap)
        mock_img = MagicMock()
        mock_img.width = 1240
        mock_img.height = 1754
        mock_img.save = lambda buf, format: buf.write(b"PNG")
        mock_convert.return_value = [mock_img] * MAX_THUMBNAIL_PAGES

        # pdfinfo says there are 200 pages total
        mock_info.return_value = {"Pages": 200}

        result = _render_via_pdf2image(file_bytes=b"%PDF-test")

        assert len(result.pages) == MAX_THUMBNAIL_PAGES
        assert result.page_count == 200
        assert any("limited to first 100 of 200" in w for w in result.warnings)


class TestPdfThumbnailDeposit:
    """Test deposit of thumbnails via tool layer (drive path)."""

    @patch("tools.fetch.drive.fetch_and_extract_pdf")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive._deposit_pdf_thumbnails")
    @patch("tools.fetch.drive.write_manifest")
    def test_thumbnails_deposited_via_shared_helper(
        self,
        mock_manifest: MagicMock,
        mock_deposit_thumbs: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_extract: MagicMock,
        tmp_path: Path,
    ) -> None:
        """fetch_pdf calls shared helper and merges extras into manifest."""
        from tools.fetch.drive import fetch_pdf

        folder = tmp_path / "pdf--test--abc123"
        folder.mkdir()

        result_obj = PdfExtractionResult(
            content="# PDF Content",
            method="markitdown",
            char_count=100,
            thumbnails=PdfThumbnailResult(
                pages=[
                    PageImage(page_index=0, image_bytes=b"PNG1", width_px=1240, height_px=1754),
                    PageImage(page_index=1, image_bytes=b"PNG2", width_px=1240, height_px=1754),
                    PageImage(page_index=2, image_bytes=b"PNG3", width_px=1240, height_px=1754),
                ],
                page_count=3,
                method="pdf2image",
            ),
        )
        mock_extract.return_value = result_obj
        mock_deposit_thumbs.return_value = {
            "page_count": 3,
            "has_thumbnails": True,
            "thumbnail_count": 3,
            "thumbnail_method": "pdf2image",
        }
        mock_get_folder.return_value = folder
        mock_write_content.return_value = folder / "content.md"

        fetch_pdf("abc123", "Test PDF", {"mimeType": "application/pdf"})

        # Shared helper called with folder and result
        mock_deposit_thumbs.assert_called_once_with(folder, result_obj)

        # Manifest includes thumbnail metadata via spread
        manifest_call = mock_manifest.call_args
        extra = manifest_call.kwargs.get("extra") or manifest_call[1].get("extra") or (manifest_call[0][4] if len(manifest_call[0]) > 4 else {})
        assert extra["page_count"] == 3
        assert extra["has_thumbnails"] is True
        assert extra["thumbnail_count"] == 3

    @patch("tools.fetch.drive.fetch_and_extract_pdf")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive._deposit_pdf_thumbnails")
    @patch("tools.fetch.drive.write_manifest")
    def test_no_thumbnails_no_manifest_fields(
        self,
        mock_manifest: MagicMock,
        mock_deposit_thumbs: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_extract: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When thumbnails=None, shared helper returns empty dict."""
        from tools.fetch.drive import fetch_pdf

        folder = tmp_path / "pdf--test--abc123"
        folder.mkdir()

        mock_extract.return_value = PdfExtractionResult(
            content="# PDF Content",
            method="markitdown",
            char_count=100,
            thumbnails=None,
        )
        mock_deposit_thumbs.return_value = {}
        mock_get_folder.return_value = folder
        mock_write_content.return_value = folder / "content.md"

        fetch_pdf("abc123", "Test PDF", {"mimeType": "application/pdf"})

        manifest_call = mock_manifest.call_args
        extra = manifest_call.kwargs.get("extra") or manifest_call[1].get("extra") or (manifest_call[0][4] if len(manifest_call[0]) > 4 else {})
        assert "page_count" not in extra
        assert "has_thumbnails" not in extra


class TestWebPdfThumbnails:
    """Test thumbnail rendering in the web PDF path."""

    @patch("tools.fetch.web.render_pdf_pages")
    @patch("tools.fetch.web.extract_pdf_content")
    @patch("tools.fetch.web._deposit_pdf_thumbnails")
    @patch("tools.fetch.web.get_deposit_folder")
    @patch("tools.fetch.web.write_content")
    @patch("tools.fetch.web.write_manifest")
    def test_web_pdf_renders_thumbnails_from_bytes(
        self,
        mock_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_deposit_thumbs: MagicMock,
        mock_extract: MagicMock,
        mock_render: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Web PDF with raw_bytes renders thumbnails."""
        from tools.fetch.web import _fetch_web_pdf
        from models import WebData

        web_data = WebData(
            url="https://example.com/doc.pdf",
            html="",
            final_url="https://example.com/doc.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method="http",
            raw_bytes=b"%PDF-test-content",
        )

        mock_extract.return_value = PdfExtractionResult(
            content="PDF text",
            method="markitdown",
            char_count=100,
        )
        mock_render.return_value = PdfThumbnailResult(
            pages=[PageImage(page_index=0, image_bytes=b"PNG", width_px=1240, height_px=1754)],
            page_count=1,
            method="pdf2image",
        )
        mock_deposit_thumbs.return_value = {"page_count": 1, "has_thumbnails": True, "thumbnail_count": 1}
        folder = tmp_path / "pdf--doc--hash"
        folder.mkdir()
        mock_get_folder.return_value = folder
        mock_write_content.return_value = folder / "content.md"

        result = _fetch_web_pdf("https://example.com/doc.pdf", web_data, base_path=tmp_path)

        mock_render.assert_called_once_with(file_bytes=b"%PDF-test-content")
        mock_deposit_thumbs.assert_called_once()

    @patch("tools.fetch.web.render_pdf_pages")
    @patch("tools.fetch.web.extract_pdf_content")
    @patch("tools.fetch.web._deposit_pdf_thumbnails")
    @patch("tools.fetch.web.get_deposit_folder")
    @patch("tools.fetch.web.write_content")
    @patch("tools.fetch.web.write_manifest")
    def test_web_pdf_renders_thumbnails_from_temp_path(
        self,
        mock_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_deposit_thumbs: MagicMock,
        mock_extract: MagicMock,
        mock_render: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Web PDF with temp_path renders thumbnails from path."""
        from tools.fetch.web import _fetch_web_pdf
        from models import WebData

        temp_pdf = tmp_path / "temp.pdf"
        temp_pdf.write_bytes(b"%PDF-large-content")

        web_data = WebData(
            url="https://example.com/big.pdf",
            html="",
            final_url="https://example.com/big.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method="http",
            temp_path=temp_pdf,
        )

        mock_extract.return_value = PdfExtractionResult(
            content="PDF text",
            method="markitdown",
            char_count=100,
        )
        mock_render.return_value = PdfThumbnailResult(
            pages=[PageImage(page_index=0, image_bytes=b"PNG", width_px=1240, height_px=1754)],
            page_count=1,
            method="pdf2image",
        )
        mock_deposit_thumbs.return_value = {"page_count": 1, "has_thumbnails": True, "thumbnail_count": 1}
        folder = tmp_path / "pdf--big--hash"
        folder.mkdir()
        mock_get_folder.return_value = folder
        mock_write_content.return_value = folder / "content.md"

        result = _fetch_web_pdf("https://example.com/big.pdf", web_data, base_path=tmp_path)

        mock_render.assert_called_once_with(file_path=temp_pdf)
        mock_deposit_thumbs.assert_called_once()

    @patch("tools.fetch.web.render_pdf_pages")
    @patch("tools.fetch.web.extract_pdf_content")
    @patch("tools.fetch.web.get_deposit_folder")
    @patch("tools.fetch.web.write_content")
    @patch("tools.fetch.web.write_manifest")
    def test_web_pdf_thumbnail_failure_preserves_result(
        self,
        mock_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_extract: MagicMock,
        mock_render: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Web PDF thumbnail failure → result still returned with warning."""
        from tools.fetch.web import _fetch_web_pdf
        from models import WebData

        web_data = WebData(
            url="https://example.com/doc.pdf",
            html="",
            final_url="https://example.com/doc.pdf",
            status_code=200,
            content_type="application/pdf",
            cookies_used=False,
            render_method="http",
            raw_bytes=b"%PDF-test",
        )

        mock_extract.return_value = PdfExtractionResult(
            content="PDF text",
            method="markitdown",
            char_count=100,
        )
        mock_render.side_effect = RuntimeError("poppler crashed")
        folder = tmp_path / "pdf--doc--hash"
        folder.mkdir()
        mock_get_folder.return_value = folder
        mock_write_content.return_value = folder / "content.md"

        result = _fetch_web_pdf("https://example.com/doc.pdf", web_data, base_path=tmp_path)

        assert result.type == "pdf"
        assert result.format == "markdown"


class TestDepositPdfThumbnailsHelper:
    """Test the shared _deposit_pdf_thumbnails helper."""

    def test_writes_pngs_and_returns_extras(self, tmp_path: Path) -> None:
        """Helper writes page PNGs and returns manifest extras dict."""
        from tools.fetch.common import _deposit_pdf_thumbnails

        folder = tmp_path / "deposit"
        folder.mkdir()

        result = PdfExtractionResult(
            content="text",
            method="markitdown",
            char_count=4,
            thumbnails=PdfThumbnailResult(
                pages=[
                    PageImage(page_index=0, image_bytes=b"PNG1", width_px=1240, height_px=1754),
                    PageImage(page_index=1, image_bytes=b"PNG2", width_px=1240, height_px=1754),
                ],
                page_count=2,
                method="pdf2image",
            ),
        )

        extras = _deposit_pdf_thumbnails(folder, result)

        # PNGs written
        assert (folder / "page_01.png").read_bytes() == b"PNG1"
        assert (folder / "page_02.png").read_bytes() == b"PNG2"

        # Extras correct
        assert extras["page_count"] == 2
        assert extras["has_thumbnails"] is True
        assert extras["thumbnail_count"] == 2
        assert extras["thumbnail_method"] == "pdf2image"
        assert "thumbnail_failures" not in extras

    def test_returns_empty_dict_when_no_thumbnails(self, tmp_path: Path) -> None:
        """No thumbnails → empty dict, no files written."""
        from tools.fetch.common import _deposit_pdf_thumbnails

        folder = tmp_path / "deposit"
        folder.mkdir()

        result = PdfExtractionResult(
            content="text", method="markitdown", char_count=4, thumbnails=None
        )

        extras = _deposit_pdf_thumbnails(folder, result)

        assert extras == {}
        assert list(folder.iterdir()) == []

    def test_tracks_missing_pages(self, tmp_path: Path) -> None:
        """Gap in rendered indices → thumbnail_failures in extras."""
        from tools.fetch.common import _deposit_pdf_thumbnails

        folder = tmp_path / "deposit"
        folder.mkdir()

        # 3 pages total, but only page 0 and 2 rendered (page 1 missing)
        result = PdfExtractionResult(
            content="text",
            method="markitdown",
            char_count=4,
            thumbnails=PdfThumbnailResult(
                pages=[
                    PageImage(page_index=0, image_bytes=b"PNG1", width_px=100, height_px=100),
                    PageImage(page_index=2, image_bytes=b"PNG3", width_px=100, height_px=100),
                ],
                page_count=3,
                method="pdf2image",
            ),
        )

        extras = _deposit_pdf_thumbnails(folder, result)

        assert extras["thumbnail_count"] == 2
        assert extras["thumbnail_failures"] == [2]  # 1-indexed: page 2 missing


class TestGmailAttachmentThumbnails:
    """Test thumbnail rendering in the Gmail single-attachment PDF path."""

    @patch("tools.fetch.gmail.render_pdf_pages")
    @patch("tools.fetch.gmail.extract_pdf_content")
    @patch("tools.fetch.gmail._deposit_pdf_thumbnails")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.download_attachment")
    def test_gmail_attachment_pdf_renders_thumbnails(
        self,
        mock_download_att: MagicMock,
        mock_exfil: MagicMock,
        mock_fetch_thread: MagicMock,
        mock_manifest: MagicMock,
        mock_write_content: MagicMock,
        mock_get_folder: MagicMock,
        mock_deposit_thumbs: MagicMock,
        mock_extract: MagicMock,
        mock_render: MagicMock,
        tmp_path: Path,
    ) -> None:
        """fetch_attachment for PDF renders thumbnails and calls shared helper."""
        from tools.fetch.gmail import fetch_attachment
        from models import EmailMessage, EmailAttachment, GmailThreadData

        att = EmailAttachment(
            filename="report.pdf",
            mime_type="application/pdf",
            size=1024,
            attachment_id="att123",
        )
        msg = EmailMessage(
            message_id="msg1",
            from_address="sender@test.com",
            to_addresses=["me@test.com"],
            attachments=[att],
        )
        thread = GmailThreadData(
            thread_id="thread1",
            subject="Test",
            messages=[msg],
        )
        mock_fetch_thread.return_value = thread
        mock_exfil.return_value = {}

        # Mock download
        mock_dl = MagicMock()
        mock_dl.content = b"%PDF-report-content"
        mock_download_att.return_value = mock_dl

        mock_extract.return_value = PdfExtractionResult(
            content="Report text",
            method="markitdown",
            char_count=100,
        )
        mock_render.return_value = PdfThumbnailResult(
            pages=[PageImage(page_index=0, image_bytes=b"PNG", width_px=1240, height_px=1754)],
            page_count=1,
            method="pdf2image",
        )
        mock_deposit_thumbs.return_value = {
            "page_count": 1, "has_thumbnails": True, "thumbnail_count": 1, "thumbnail_method": "pdf2image",
        }

        folder = tmp_path / "pdf--report--thread1"
        folder.mkdir()
        mock_get_folder.return_value = folder
        mock_write_content.return_value = folder / "content.md"

        result = fetch_attachment("thread1", "report.pdf", base_path=tmp_path)

        mock_render.assert_called_once_with(file_bytes=b"%PDF-report-content")
        mock_deposit_thumbs.assert_called_once()
        assert result.type == "pdf"


class TestCuesPagePrefix:
    """Test that _build_cues recognizes page_ prefix thumbnails."""

    def test_page_thumbnails_collapsed_in_cues(self, tmp_path: Path) -> None:
        """page_01.png through page_36.png collapsed into summary."""
        from tools.fetch.common import _build_cues

        # Create a deposit folder with page thumbnails
        folder = tmp_path / "pdf--test--abc123"
        folder.mkdir()
        (folder / "content.md").write_text("# Test")
        (folder / "manifest.json").write_text("{}")
        for i in range(36):
            (folder / f"page_{i + 1:02d}.png").write_bytes(b"PNG")

        cues = _build_cues(folder)

        # Should have collapsed thumbnail summary
        files = cues["files"]
        thumb_entries = [f for f in files if "thumbnail" in f]
        assert len(thumb_entries) == 1
        assert "36 thumbnails" in thumb_entries[0]
        assert "page_01.png" in thumb_entries[0]
        assert "page_36.png" in thumb_entries[0]

    def test_few_page_thumbnails_listed_individually(self, tmp_path: Path) -> None:
        """3 or fewer page thumbnails listed individually (not collapsed)."""
        from tools.fetch.common import _build_cues

        folder = tmp_path / "pdf--test--abc123"
        folder.mkdir()
        (folder / "content.md").write_text("# Test")
        for i in range(3):
            (folder / f"page_{i + 1:02d}.png").write_bytes(b"PNG")

        cues = _build_cues(folder)

        files = cues["files"]
        page_entries = [f for f in files if f.startswith("page_")]
        assert len(page_entries) == 3


class TestWritePageThumbnail:
    """Test workspace/manager.py write_page_thumbnail."""

    def test_writes_page_png(self, tmp_path: Path) -> None:
        """Writes page_01.png with correct naming."""
        from workspace.manager import write_page_thumbnail

        result = write_page_thumbnail(tmp_path, b"PNG_DATA", 0)

        assert result == tmp_path / "page_01.png"
        assert result.read_bytes() == b"PNG_DATA"

    def test_zero_padded_naming(self, tmp_path: Path) -> None:
        """Page indices are 1-indexed and zero-padded."""
        from workspace.manager import write_page_thumbnail

        p1 = write_page_thumbnail(tmp_path, b"P1", 0)
        p9 = write_page_thumbnail(tmp_path, b"P9", 8)
        p10 = write_page_thumbnail(tmp_path, b"P10", 9)

        assert p1.name == "page_01.png"
        assert p9.name == "page_09.png"
        assert p10.name == "page_10.png"
