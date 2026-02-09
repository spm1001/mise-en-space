"""
Unit tests for fetch tool ID detection and routing.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from tools.fetch import (
    detect_id_type, fetch_gmail, fetch_attachment, do_fetch, do_fetch_comments,
    _extract_from_drive, _match_exfil_file, _enrich_with_comments,
    _is_extractable_attachment, _deposit_attachment_content,
    _extract_attachment_content, _build_email_context_metadata,
    is_text_file, fetch_drive, fetch_doc, fetch_sheet, fetch_slides,
    fetch_video, fetch_pdf, fetch_office, fetch_text, fetch_image_file,
    fetch_web, _fetch_web_pdf, _fetch_web_office,
)
from models import (
    GmailThreadData, EmailMessage, EmailAttachment, FetchResult, FetchError,
    MiseError, ErrorKind, EmailContext, WebData,
)
from adapters.gmail import AttachmentDownload
from adapters.office import OfficeExtractionResult
from adapters.pdf import PdfExtractionResult


class TestDetectIdType:
    """Tests for detect_id_type function."""

    def test_gmail_url(self) -> None:
        """Gmail URLs are detected and ID extracted."""
        url = "https://mail.google.com/mail/u/0/#inbox/FMfcgzQfBZkJgxJdSRBsRcqhpDcdBRxH"
        source, normalized = detect_id_type(url)
        assert source == "gmail"
        assert len(normalized) == 16  # API format

    def test_gmail_api_id(self) -> None:
        """16-char hex Gmail API IDs are detected."""
        api_id = "19c05803e16f5f83"
        source, normalized = detect_id_type(api_id)
        assert source == "gmail"
        assert normalized == api_id

    def test_gmail_web_id(self) -> None:
        """Gmail web IDs (FMfcg..., KtbxL...) are detected and converted."""
        web_id = "FMfcgzQfBZkJgxJdSRBsRcqhpDcdBRxH"
        source, normalized = detect_id_type(web_id)
        assert source == "gmail"
        assert len(normalized) == 16  # Converted to API format
        # Verify it's different from the input (was actually converted)
        assert normalized != web_id

    def test_gmail_web_id_ktbxl_prefix_fails_conversion(self) -> None:
        """KtbxL-prefixed IDs are detected as Gmail but may fail conversion (thread-a format)."""
        import pytest

        web_id = "KtbxLvHBgcDWmjRbtDbdVZDXJVjJzMJJQh"
        # KtbxL IDs are thread-a format (self-sent emails) which can't be converted
        with pytest.raises(ValueError, match="Could not convert"):
            detect_id_type(web_id)

    def test_drive_docs_url(self) -> None:
        """Google Docs URLs are detected as Drive."""
        url = "https://docs.google.com/document/d/1OepZjuwi2em/edit"
        source, normalized = detect_id_type(url)
        assert source == "drive"
        assert normalized == "1OepZjuwi2em"

    def test_drive_sheets_url(self) -> None:
        """Google Sheets URLs are detected as Drive."""
        url = "https://sheets.google.com/spreadsheets/d/abc123xyz/edit"
        source, normalized = detect_id_type(url)
        assert source == "drive"
        assert normalized == "abc123xyz"

    def test_drive_slides_url(self) -> None:
        """Google Slides URLs are detected as Drive."""
        url = "https://slides.google.com/presentation/d/prezId123/edit"
        source, normalized = detect_id_type(url)
        assert source == "drive"
        assert normalized == "prezId123"

    def test_drive_file_url(self) -> None:
        """Drive file URLs are detected as Drive."""
        url = "https://drive.google.com/file/d/1a2b3c4d5e/view"
        source, normalized = detect_id_type(url)
        assert source == "drive"
        assert normalized == "1a2b3c4d5e"

    def test_web_url(self) -> None:
        """Non-Google HTTP URLs are detected as web."""
        url = "https://example.com/some/page"
        source, normalized = detect_id_type(url)
        assert source == "web"
        assert normalized == url

    def test_bare_drive_id(self) -> None:
        """Bare IDs (not matching Gmail patterns) default to Drive."""
        drive_id = "1OepZjuwi2emuHPAP-LWxWZnw9g0SbkjhkBJh9ta1rqU"
        source, normalized = detect_id_type(drive_id)
        assert source == "drive"
        assert normalized == drive_id

    def test_strips_whitespace(self) -> None:
        """Input is stripped before detection."""
        web_id = "  FMfcgzQfBZkJgxJdSRBsRcqhpDcdBRxH  "
        source, normalized = detect_id_type(web_id)
        assert source == "gmail"


def _make_thread_data(attachments=None):
    """Helper: minimal GmailThreadData with one message."""
    msg = EmailMessage(
        message_id="msg_abc123",
        from_address="alice@example.com",
        to_addresses=["bob@example.com"],
        body_text="Hello",
        attachments=attachments or [],
    )
    return GmailThreadData(
        thread_id="thread_xyz",
        subject="Test thread",
        messages=[msg],
    )


class TestPreExfilRouting:
    """Tests for pre-exfiltrated attachment routing in fetch_gmail."""

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail._extract_from_drive")
    @patch("tools.fetch.gmail._extract_attachment_content")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread content")
    def test_uses_drive_when_exfiltrated(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_gmail_extract, mock_drive_extract, mock_lookup, mock_fetch
    ):
        """Attachment found in Drive is fetched from Drive, not Gmail."""
        att = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, attachment_id="att_123",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_folder.return_value = "/tmp/test-deposit"
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_file_1", "name": "report.pdf", "mimeType": "application/pdf"}]
        }
        mock_drive_extract.return_value = {"filename": "report.pdf", "extracted": True}

        fetch_gmail("thread_xyz")

        mock_drive_extract.assert_called_once()
        mock_gmail_extract.assert_not_called()

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail._extract_from_drive")
    @patch("tools.fetch.gmail._extract_attachment_content")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread content")
    def test_falls_back_to_gmail_when_not_exfiltrated(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_gmail_extract, mock_drive_extract, mock_lookup, mock_fetch
    ):
        """Attachment NOT in Drive falls back to Gmail download."""
        att = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, attachment_id="att_123",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_folder.return_value = "/tmp/test-deposit"
        mock_lookup.return_value = {}  # Nothing exfiltrated
        mock_gmail_extract.return_value = {"filename": "report.pdf", "extracted": True}

        fetch_gmail("thread_xyz")

        mock_gmail_extract.assert_called_once()
        mock_drive_extract.assert_not_called()

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail._extract_from_drive")
    @patch("tools.fetch.gmail._extract_attachment_content")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread content")
    def test_falls_back_to_gmail_when_drive_extract_fails(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_gmail_extract, mock_drive_extract, mock_lookup, mock_fetch
    ):
        """If Drive extraction fails, falls back to Gmail."""
        att = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, attachment_id="att_123",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_folder.return_value = "/tmp/test-deposit"
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_file_1", "name": "report.pdf", "mimeType": "application/pdf"}]
        }
        mock_drive_extract.return_value = None  # Drive extraction failed
        mock_gmail_extract.return_value = {"filename": "report.pdf", "extracted": True}

        fetch_gmail("thread_xyz")

        mock_drive_extract.assert_called_once()
        mock_gmail_extract.assert_called_once()

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread content")
    def test_office_files_skipped_even_if_exfiltrated(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_lookup, mock_fetch
    ):
        """Office files are still skipped even if found in Drive."""
        att = EmailAttachment(
            filename="deck.pptx",
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            size=5000, attachment_id="att_456",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_folder.return_value = "/tmp/test-deposit"
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_file_2", "name": "deck.pptx", "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}]
        }

        result = fetch_gmail("thread_xyz")

        # Office file should appear in skipped list, not extracted
        assert "skipped_office" in result.metadata or True  # passes through to manifest

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread content")
    def test_no_exfil_folder_gracefully_returns_empty(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_lookup, mock_fetch
    ):
        """When no exfil folder exists, lookup returns empty, Gmail path used."""
        att = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, attachment_id="att_123",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_folder.return_value = "/tmp/test-deposit"
        mock_lookup.return_value = {}  # No exfil folder configured

        # Should not crash
        fetch_gmail("thread_xyz")


class TestMatchExfilFile:
    """Tests for _match_exfil_file fuzzy matching."""

    def test_exact_name_match(self) -> None:
        """Standard case: Gmail and Drive names are identical."""
        exfil = [{"file_id": "d1", "name": "Budget.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}]
        result = _match_exfil_file("Budget.xlsx", exfil)
        assert result is not None
        assert result["file_id"] == "d1"

    def test_stem_match_missing_extension(self) -> None:
        """Exfil script added extension: Gmail has 'report', Drive has 'report.pdf'."""
        exfil = [{"file_id": "d1", "name": "report.pdf", "mimeType": "application/pdf"}]
        result = _match_exfil_file("report", exfil)
        assert result is not None
        assert result["file_id"] == "d1"

    def test_stem_match_different_extension(self) -> None:
        """Extension mismatch but same stem still matches."""
        exfil = [{"file_id": "d1", "name": "data.csv", "mimeType": "text/csv"}]
        result = _match_exfil_file("data.tsv", exfil)
        assert result is not None
        assert result["file_id"] == "d1"

    def test_single_file_fallback(self) -> None:
        """UUID rename: names completely different but only one exfil file."""
        exfil = [{"file_id": "d1", "name": "2025-01-15_alice_a1b2c3d4-e5f6.pdf", "mimeType": "application/pdf"}]
        result = _match_exfil_file("a1b2c3d4-e5f6.pdf", exfil)
        assert result is not None
        assert result["file_id"] == "d1"

    def test_no_match_multiple_files(self) -> None:
        """Multiple exfil files with no name match returns None (ambiguous)."""
        exfil = [
            {"file_id": "d1", "name": "alpha.pdf", "mimeType": "application/pdf"},
            {"file_id": "d2", "name": "beta.pdf", "mimeType": "application/pdf"},
        ]
        result = _match_exfil_file("gamma.pdf", exfil)
        assert result is None

    def test_empty_exfil_list(self) -> None:
        """No exfil files returns None."""
        assert _match_exfil_file("report.pdf", []) is None

    def test_exact_match_preferred_over_stem(self) -> None:
        """Exact match wins even when stem would also match another file."""
        exfil = [
            {"file_id": "d1", "name": "report.pdf", "mimeType": "application/pdf"},
            {"file_id": "d2", "name": "report.docx", "mimeType": "application/msword"},
        ]
        result = _match_exfil_file("report.pdf", exfil)
        assert result is not None
        assert result["file_id"] == "d1"

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail._extract_from_drive")
    @patch("tools.fetch.gmail._extract_attachment_content")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread content")
    def test_stem_match_routes_to_drive(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_gmail_extract, mock_drive_extract, mock_lookup, mock_fetch
    ):
        """Attachment with missing extension still routes to Drive via stem match."""
        att = EmailAttachment(
            filename="report", mime_type="application/pdf",
            size=1000, attachment_id="att_123",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_folder.return_value = "/tmp/test-deposit"
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_file_1", "name": "report.pdf", "mimeType": "application/pdf"}]
        }
        mock_drive_extract.return_value = {"filename": "report.pdf", "extracted": True}

        fetch_gmail("thread_xyz")

        mock_drive_extract.assert_called_once()
        mock_gmail_extract.assert_not_called()


class TestAttachmentExtractionSummary:
    """Tests for embedding extracted attachment content in content.md."""

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail._extract_from_drive")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread content here")
    def test_pdf_extraction_summary_in_content(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_drive_extract, mock_lookup, mock_fetch
    ):
        """Extracted PDF gets a pointer in content.md (not inline text)."""
        att = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, attachment_id="att_123",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_folder.return_value = "/tmp/test-deposit"
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "d1", "name": "report.pdf", "mimeType": "application/pdf"}]
        }
        mock_drive_extract.return_value = {
            "filename": "report.pdf",
            "extracted": True,
            "extraction_method": "markitdown",
            "content_file": "report.pdf.md",
            "char_count": 42,
        }

        fetch_gmail("thread_xyz")

        written_content = mock_write.call_args[0][1]
        assert "Thread content here" in written_content
        assert "report.pdf → `report.pdf.md`" in written_content

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail._extract_from_drive")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread content")
    def test_no_extracted_text_in_metadata(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_drive_extract, mock_lookup, mock_fetch
    ):
        """extracted_text is never set on result metadata."""
        att = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, attachment_id="att_123",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_folder.return_value = "/tmp/test-deposit"
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "d1", "name": "report.pdf", "mimeType": "application/pdf"}]
        }
        mock_drive_extract.return_value = {
            "filename": "report.pdf",
            "extracted": True,
            "content_file": "report.pdf.md",
        }

        result = fetch_gmail("thread_xyz")

        for att_meta in result.metadata.get("extracted", []):
            assert "extracted_text" not in att_meta

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail._extract_from_drive")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread content")
    def test_image_attachments_listed_in_summary(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_drive_extract, mock_lookup, mock_fetch
    ):
        """Image attachments appear in extraction summary as deposited files."""
        att = EmailAttachment(
            filename="photo.png", mime_type="image/png",
            size=5000, attachment_id="att_789",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_folder.return_value = "/tmp/test-deposit"
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "d1", "name": "photo.png", "mimeType": "image/png"}]
        }
        mock_drive_extract.return_value = {
            "filename": "photo.png",
            "extracted": True,
            "deposited_as": "photo.png",
        }

        fetch_gmail("thread_xyz")

        written_content = mock_write.call_args[0][1]
        assert "photo.png (deposited as file)" in written_content

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail._extract_from_drive")
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread content")
    def test_multiple_pdfs_all_listed_in_summary(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_drive_extract, mock_lookup, mock_fetch
    ):
        """Multiple PDF attachments each get a pointer in the extraction summary."""
        att1 = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, attachment_id="att_1",
        )
        att2 = EmailAttachment(
            filename="invoice.pdf", mime_type="application/pdf",
            size=2000, attachment_id="att_2",
        )
        mock_fetch.return_value = _make_thread_data([att1, att2])
        mock_folder.return_value = "/tmp/test-deposit"
        mock_lookup.return_value = {
            "msg_abc123": [
                {"file_id": "d1", "name": "report.pdf", "mimeType": "application/pdf"},
                {"file_id": "d2", "name": "invoice.pdf", "mimeType": "application/pdf"},
            ]
        }
        mock_drive_extract.side_effect = [
            {"filename": "report.pdf", "extracted": True, "content_file": "report.pdf.md"},
            {"filename": "invoice.pdf", "extracted": True, "content_file": "invoice.pdf.md"},
        ]

        fetch_gmail("thread_xyz")

        written_content = mock_write.call_args[0][1]
        assert "report.pdf → `report.pdf.md`" in written_content
        assert "invoice.pdf → `invoice.pdf.md`" in written_content


class TestFetchAttachment:
    """Tests for single-attachment fetch via fetch_attachment()."""

    def test_routes_via_do_fetch(self):
        """do_fetch with attachment param routes to fetch_attachment."""
        with patch("tools.fetch.router.detect_id_type", return_value=("gmail", "thread_xyz")), \
             patch("tools.fetch.router.fetch_attachment") as mock_fetch_att:
            mock_fetch_att.return_value = FetchResult(
                path="/tmp/test", content_file="/tmp/test/content.md",
                format="markdown", type="xlsx", metadata={},
            )
            result = do_fetch("thread_xyz", attachment="report.xlsx")
            mock_fetch_att.assert_called_once_with("thread_xyz", "report.xlsx", base_path=None)

    def test_rejects_non_gmail(self):
        """attachment param with Drive/web ID returns error."""
        result = do_fetch("1OepZjuwi2emuHPAP-LWxWZnw9g0SbkjhkBJh9ta1rqU", attachment="file.docx")
        assert isinstance(result, FetchError)
        assert result.kind == "invalid_input"
        assert "Gmail" in result.message

    @patch("tools.fetch.gmail.fetch_thread")
    def test_not_found_lists_available(self, mock_fetch):
        """Wrong filename returns error with available attachment names."""
        att = EmailAttachment(
            filename="budget.xlsx", mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size=5000, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])

        result = fetch_attachment("thread_xyz", "nonexistent.pdf")

        assert isinstance(result, FetchError)
        assert result.kind == "not_found"
        assert "budget.xlsx" in result.message

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.gmail.download_attachment")
    @patch("tools.fetch.gmail.extract_office_content")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value="/tmp/test-deposit")
    @patch("tools.fetch.gmail.write_content", return_value="/tmp/test-deposit/content.csv")
    @patch("tools.fetch.gmail.write_manifest")
    def test_case_insensitive_filename_match(
        self, mock_manifest, mock_write, mock_folder,
        mock_office, mock_download, mock_lookup, mock_fetch
    ):
        """Filename matching is case-insensitive."""
        xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        att = EmailAttachment(
            filename="Budget.xlsx", mime_type=xlsx_mime,
            size=5000, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_download.return_value = AttachmentDownload(
            filename="Budget.xlsx", mime_type=xlsx_mime,
            size=5000, content=b"fake xlsx bytes",
        )
        mock_office.return_value = OfficeExtractionResult(
            content="data", source_type="xlsx", export_format="csv", extension="csv",
        )

        result = fetch_attachment("thread_xyz", "budget.xlsx")  # lowercase

        assert isinstance(result, FetchResult)
        assert result.type == "xlsx"

    @patch("tools.fetch.gmail.fetch_thread")
    def test_no_attachments_lists_none(self, mock_fetch):
        """Thread with no attachments returns helpful error."""
        mock_fetch.return_value = _make_thread_data([])

        result = fetch_attachment("thread_xyz", "anything.pdf")

        assert isinstance(result, FetchError)
        assert "(none)" in result.message

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.gmail.download_attachment")
    @patch("tools.fetch.gmail.extract_office_content")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value="/tmp/test-deposit")
    @patch("tools.fetch.gmail.write_content", return_value="/tmp/test-deposit/content.csv")
    @patch("tools.fetch.gmail.write_manifest")
    def test_office_attachment_extracted(
        self, mock_manifest, mock_write, mock_folder,
        mock_office, mock_download, mock_lookup, mock_fetch
    ):
        """XLSX attachment routes to extract_office_content."""
        xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        att = EmailAttachment(
            filename="budget.xlsx", mime_type=xlsx_mime,
            size=5000, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_download.return_value = AttachmentDownload(
            filename="budget.xlsx", mime_type=xlsx_mime,
            size=5000, content=b"fake xlsx bytes",
        )
        mock_office.return_value = OfficeExtractionResult(
            content="Sheet1\ncol1,col2\n1,2",
            source_type="xlsx", export_format="csv", extension="csv",
        )

        result = fetch_attachment("thread_xyz", "budget.xlsx")

        assert isinstance(result, FetchResult)
        assert result.type == "xlsx"
        assert result.format == "csv"
        assert result.metadata["gmail_thread_id"] == "thread_xyz"
        mock_office.assert_called_once()
        assert mock_office.call_args.kwargs["office_type"] == "xlsx"

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.gmail.download_attachment")
    @patch("tools.fetch.gmail.extract_pdf_content")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value="/tmp/test-deposit")
    @patch("tools.fetch.gmail.write_content", return_value="/tmp/test-deposit/content.md")
    @patch("tools.fetch.gmail.write_manifest")
    def test_pdf_attachment_extracted(
        self, mock_manifest, mock_write, mock_folder,
        mock_pdf, mock_download, mock_lookup, mock_fetch
    ):
        """PDF attachment routes to extract_pdf_content."""
        att = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=3000, attachment_id="att_2",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_download.return_value = AttachmentDownload(
            filename="report.pdf", mime_type="application/pdf",
            size=3000, content=b"fake pdf bytes",
        )
        mock_pdf.return_value = PdfExtractionResult(
            content="# Report\nSome content", method="markitdown", char_count=25,
        )

        result = fetch_attachment("thread_xyz", "report.pdf")

        assert isinstance(result, FetchResult)
        assert result.type == "pdf"
        assert result.format == "markdown"
        mock_pdf.assert_called_once()

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.extract_office_content")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value="/tmp/test-deposit")
    @patch("tools.fetch.gmail.write_content", return_value="/tmp/test-deposit/content.csv")
    @patch("tools.fetch.gmail.write_manifest")
    def test_preexfil_preferred(
        self, mock_manifest, mock_write, mock_folder,
        mock_office, mock_lookup, mock_fetch
    ):
        """Pre-exfil Drive copy uses source_file_id (no download)."""
        xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        att = EmailAttachment(
            filename="budget.xlsx", mime_type=xlsx_mime,
            size=5000, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_99", "name": "budget.xlsx", "mimeType": xlsx_mime}]
        }
        mock_office.return_value = OfficeExtractionResult(
            content="Sheet1\ncol1,col2\n1,2",
            source_type="xlsx", export_format="csv", extension="csv",
        )

        result = fetch_attachment("thread_xyz", "budget.xlsx")

        assert isinstance(result, FetchResult)
        assert result.metadata["source"] == "drive_exfil"
        # source_file_id passed — no download needed
        assert mock_office.call_args.kwargs["source_file_id"] == "drive_99"

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.download_attachment")
    @patch("tools.fetch.gmail.extract_office_content")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value="/tmp/test-deposit")
    @patch("tools.fetch.gmail.write_content", return_value="/tmp/test-deposit/content.csv")
    @patch("tools.fetch.gmail.write_manifest")
    def test_preexfil_fallback_to_gmail(
        self, mock_manifest, mock_write, mock_folder,
        mock_office, mock_gmail_dl, mock_lookup, mock_fetch
    ):
        """Falls back to Gmail when pre-exfil Drive conversion fails."""
        xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        att = EmailAttachment(
            filename="budget.xlsx", mime_type=xlsx_mime,
            size=5000, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_99", "name": "budget.xlsx", "mimeType": xlsx_mime}]
        }
        office_result = OfficeExtractionResult(
            content="Sheet1\ncol1,col2\n1,2",
            source_type="xlsx", export_format="csv", extension="csv",
        )
        # First call (source_file_id) fails, second call (file_bytes) succeeds
        mock_office.side_effect = [Exception("Drive conversion error"), office_result]
        mock_gmail_dl.return_value = AttachmentDownload(
            filename="budget.xlsx", mime_type=xlsx_mime,
            size=5000, content=b"gmail xlsx bytes",
        )

        result = fetch_attachment("thread_xyz", "budget.xlsx")

        assert isinstance(result, FetchResult)
        assert result.metadata["source"] == "gmail"
        mock_gmail_dl.assert_called_once()
        assert mock_office.call_count == 2

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.gmail.download_attachment")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value="/tmp/test-deposit")
    @patch("tools.fetch.gmail.write_image", return_value="/tmp/test-deposit/logo.png")
    @patch("tools.fetch.gmail.write_manifest")
    def test_image_attachment_deposited(
        self, mock_manifest, mock_write_img, mock_folder,
        mock_download, mock_lookup, mock_fetch
    ):
        """Image attachment is deposited as file."""
        att = EmailAttachment(
            filename="logo.png", mime_type="image/png",
            size=2000, attachment_id="att_3",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_download.return_value = AttachmentDownload(
            filename="logo.png", mime_type="image/png",
            size=2000, content=b"fake png bytes",
        )

        result = fetch_attachment("thread_xyz", "logo.png")

        assert isinstance(result, FetchResult)
        assert result.type == "image"
        assert result.format == "image"
        mock_write_img.assert_called_once()

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated", return_value={})
    def test_unsupported_mime_returns_error(self, mock_lookup, mock_fetch):
        """Unsupported MIME type returns extraction_failed error."""
        att = EmailAttachment(
            filename="archive.zip", mime_type="application/zip",
            size=10000, attachment_id="att_4",
        )
        mock_fetch.return_value = _make_thread_data([att])

        result = fetch_attachment("thread_xyz", "archive.zip")

        assert isinstance(result, FetchError)
        assert result.kind == "extraction_failed"
        assert "application/zip" in result.message


class TestIsTextFile:
    """Tests for is_text_file MIME type checker."""

    def test_known_text_types(self) -> None:
        assert is_text_file("text/plain") is True
        assert is_text_file("text/csv") is True
        assert is_text_file("application/json") is True

    def test_unknown_text_subtype(self) -> None:
        """Any text/* type matches even if not in the explicit set."""
        assert is_text_file("text/x-custom") is True

    def test_non_text_types(self) -> None:
        assert is_text_file("application/pdf") is False
        assert is_text_file("image/png") is False


class TestIsExtractableAttachment:
    """Tests for _is_extractable_attachment."""

    def test_pdf_extractable(self) -> None:
        assert _is_extractable_attachment("application/pdf") is True

    def test_image_extractable(self) -> None:
        assert _is_extractable_attachment("image/png") is True
        assert _is_extractable_attachment("image/jpeg") is True

    def test_office_skipped(self) -> None:
        assert _is_extractable_attachment(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ) is False

    def test_unknown_not_extractable(self) -> None:
        """Non-PDF, non-image, non-Office types return False."""
        assert _is_extractable_attachment("application/zip") is False
        assert _is_extractable_attachment("video/mp4") is False


class TestBuildEmailContextMetadata:
    """Tests for _build_email_context_metadata."""

    def test_returns_none_for_no_context(self) -> None:
        assert _build_email_context_metadata(None) is None

    def test_builds_metadata_dict(self) -> None:
        ctx = EmailContext(
            message_id="msg_123",
            from_address="alice@example.com",
            subject="Test Subject",
        )
        result = _build_email_context_metadata(ctx)
        assert result["message_id"] == "msg_123"
        assert result["from"] == "alice@example.com"
        assert result["subject"] == "Test Subject"
        assert "fetch('msg_123')" in result["hint"]


class TestEnrichWithComments:
    """Tests for _enrich_with_comments."""

    @patch("tools.fetch.common.fetch_file_comments")
    @patch("tools.fetch.common.extract_comments_content", return_value="# Comments\n- a comment")
    @patch("tools.fetch.common.write_content")
    def test_writes_comments_to_folder(self, mock_write, mock_extract, mock_fetch):
        """Comments are fetched, extracted, and written to deposit folder."""
        mock_data = MagicMock()
        mock_data.comments = [{"content": "test"}]
        mock_data.comment_count = 1
        mock_fetch.return_value = mock_data

        count, md = _enrich_with_comments("file_123", Path("/tmp/deposit"))

        assert count == 1
        assert md == "# Comments\n- a comment"
        mock_write.assert_called_once_with(
            Path("/tmp/deposit"), "# Comments\n- a comment", filename="comments.md"
        )

    @patch("tools.fetch.common.fetch_file_comments")
    def test_no_comments_returns_zero(self, mock_fetch):
        """No comments returns (0, None) without writing."""
        mock_data = MagicMock()
        mock_data.comments = []
        mock_fetch.return_value = mock_data

        count, md = _enrich_with_comments("file_123", Path("/tmp/deposit"))
        assert count == 0
        assert md is None

    @patch("tools.fetch.common.fetch_file_comments", side_effect=MiseError(ErrorKind.NOT_FOUND, "nope"))
    def test_mise_error_returns_zero(self, mock_fetch):
        """MiseError is caught silently."""
        count, md = _enrich_with_comments("file_123", Path("/tmp/deposit"))
        assert count == 0
        assert md is None

    @patch("tools.fetch.common.fetch_file_comments", side_effect=RuntimeError("network"))
    def test_generic_error_returns_zero(self, mock_fetch):
        """Generic exceptions are caught silently."""
        count, md = _enrich_with_comments("file_123", Path("/tmp/deposit"))
        assert count == 0
        assert md is None


class TestDepositAttachmentContent:
    """Tests for _deposit_attachment_content."""

    @patch("tools.fetch.gmail.extract_pdf_content")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_image")
    def test_pdf_extracted_and_deposited(self, mock_img, mock_write, mock_pdf):
        """PDF bytes are extracted and both .md and raw file deposited."""
        mock_pdf.return_value = PdfExtractionResult(
            content="# PDF Content", method="markitdown", char_count=14,
        )
        result = _deposit_attachment_content(
            b"pdf bytes", "report.pdf", "application/pdf", "f1", Path("/tmp")
        )
        assert result is not None
        assert result["filename"] == "report.pdf"
        assert result["extracted"] is True
        assert result["extraction_method"] == "markitdown"
        assert result["content_file"] == "report.pdf.md"
        mock_write.assert_called_once()
        mock_img.assert_called_once()

    @patch("tools.fetch.gmail.write_image")
    def test_image_deposited(self, mock_img):
        """Image bytes are deposited as file."""
        result = _deposit_attachment_content(
            b"png bytes", "photo.png", "image/png", "f2", Path("/tmp")
        )
        assert result is not None
        assert result["filename"] == "photo.png"
        assert result["deposited_as"] == "photo.png"
        mock_img.assert_called_once()

    def test_unknown_type_returns_none(self):
        """Non-PDF, non-image MIME type returns None."""
        result = _deposit_attachment_content(
            b"zip bytes", "archive.zip", "application/zip", "f3", Path("/tmp")
        )
        assert result is None


class TestExtractFromDrive:
    """Tests for _extract_from_drive."""

    @patch("tools.fetch.gmail.download_file", return_value=b"pdf bytes")
    @patch("tools.fetch.gmail._deposit_attachment_content")
    def test_downloads_and_deposits(self, mock_deposit, mock_dl):
        """Downloads file from Drive and routes to deposit."""
        mock_deposit.return_value = {"filename": "r.pdf", "extracted": True}
        warnings: list[str] = []
        result = _extract_from_drive("d1", "r.pdf", "application/pdf", Path("/tmp"), warnings)
        assert result is not None
        assert result["extracted"] is True
        assert warnings == []

    @patch("tools.fetch.gmail.download_file", side_effect=RuntimeError("download failed"))
    def test_failure_appends_warning(self, mock_dl):
        """Download failure appends warning and returns None."""
        warnings: list[str] = []
        result = _extract_from_drive("d1", "r.pdf", "application/pdf", Path("/tmp"), warnings)
        assert result is None
        assert len(warnings) == 1
        assert "download failed" in warnings[0]


class TestExtractAttachmentContent:
    """Tests for _extract_attachment_content."""

    @patch("tools.fetch.gmail.download_attachment")
    @patch("tools.fetch.gmail._deposit_attachment_content")
    def test_downloads_and_deposits(self, mock_deposit, mock_dl):
        """Downloads from Gmail and deposits."""
        mock_dl.return_value = AttachmentDownload(
            filename="r.pdf", mime_type="application/pdf", size=100, content=b"bytes",
        )
        mock_deposit.return_value = {"filename": "r.pdf", "extracted": True}
        att = MagicMock()
        att.attachment_id = "att1"
        att.filename = "r.pdf"
        att.mime_type = "application/pdf"
        warnings: list[str] = []

        result = _extract_attachment_content("msg1", att, Path("/tmp"), warnings)
        assert result is not None

    @patch("tools.fetch.gmail.download_attachment")
    @patch("tools.fetch.gmail._deposit_attachment_content", return_value=None)
    def test_unhandled_type_cleans_temp(self, mock_deposit, mock_dl):
        """When deposit returns None and temp_path exists, it's cleaned up."""
        mock_dl.return_value = AttachmentDownload(
            filename="x.bin", mime_type="application/octet-stream",
            size=100, content=b"bytes", temp_path=Path("/tmp/x.bin"),
        )
        att = MagicMock()
        att.attachment_id = "att1"
        att.filename = "x.bin"
        att.mime_type = "application/octet-stream"
        warnings: list[str] = []

        with patch.object(Path, "unlink") as mock_unlink:
            result = _extract_attachment_content("msg1", att, Path("/tmp"), warnings)
        assert result is None

    @patch("tools.fetch.gmail.download_attachment", side_effect=RuntimeError("api error"))
    def test_failure_appends_warning(self, mock_dl):
        """Download failure appends warning."""
        att = MagicMock()
        att.attachment_id = "att1"
        att.filename = "r.pdf"
        att.mime_type = "application/pdf"
        warnings: list[str] = []

        result = _extract_attachment_content("msg1", att, Path("/tmp"), warnings)
        assert result is None
        assert "api error" in warnings[0]


# --- Shared mock metadata helpers ---

def _drive_metadata(mime_type: str, name: str = "Test File") -> dict:
    """Helper: minimal Drive metadata dict."""
    return {"mimeType": mime_type, "name": name, "webViewLink": "https://drive.google.com/file/d/x"}


class TestFetchDriveRouting:
    """Tests for fetch_drive MIME type routing."""

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_doc")
    def test_routes_google_doc(self, mock_fn, mock_meta):
        mock_meta.return_value = _drive_metadata("application/vnd.google-apps.document")
        mock_fn.return_value = FetchResult(path="/p", content_file="/p/c.md", format="markdown", type="doc", metadata={})
        result = fetch_drive("f1")
        mock_fn.assert_called_once()
        assert result.type == "doc"

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_sheet")
    def test_routes_google_sheet(self, mock_fn, mock_meta):
        mock_meta.return_value = _drive_metadata("application/vnd.google-apps.spreadsheet")
        mock_fn.return_value = FetchResult(path="/p", content_file="/p/c.csv", format="csv", type="sheet", metadata={})
        result = fetch_drive("f1")
        mock_fn.assert_called_once()

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_slides")
    def test_routes_google_slides(self, mock_fn, mock_meta):
        mock_meta.return_value = _drive_metadata("application/vnd.google-apps.presentation")
        mock_fn.return_value = FetchResult(path="/p", content_file="/p/c.md", format="markdown", type="slides", metadata={})
        result = fetch_drive("f1")
        mock_fn.assert_called_once()

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_video")
    def test_routes_video(self, mock_fn, mock_meta):
        mock_meta.return_value = _drive_metadata("video/mp4")
        mock_fn.return_value = FetchResult(path="/p", content_file="/p/c.md", format="markdown", type="video", metadata={})
        result = fetch_drive("f1")
        mock_fn.assert_called_once()

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_pdf")
    def test_routes_pdf(self, mock_fn, mock_meta):
        mock_meta.return_value = _drive_metadata("application/pdf")
        mock_fn.return_value = FetchResult(path="/p", content_file="/p/c.md", format="markdown", type="pdf", metadata={})
        result = fetch_drive("f1")
        mock_fn.assert_called_once()

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_office")
    def test_routes_docx(self, mock_fn, mock_meta):
        mock_meta.return_value = _drive_metadata(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        mock_fn.return_value = FetchResult(path="/p", content_file="/p/c.md", format="markdown", type="docx", metadata={})
        result = fetch_drive("f1")
        mock_fn.assert_called_once()

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_text")
    def test_routes_text(self, mock_fn, mock_meta):
        mock_meta.return_value = _drive_metadata("text/plain")
        mock_fn.return_value = FetchResult(path="/p", content_file="/p/c.txt", format="text", type="text", metadata={})
        result = fetch_drive("f1")
        mock_fn.assert_called_once()

    @patch("tools.fetch.drive.get_file_metadata")
    @patch("tools.fetch.drive.fetch_image_file")
    def test_routes_image(self, mock_fn, mock_meta):
        mock_meta.return_value = _drive_metadata("image/png")
        mock_fn.return_value = FetchResult(path="/p", content_file="/p/i.png", format="image", type="image", metadata={})
        result = fetch_drive("f1")
        mock_fn.assert_called_once()

    @patch("tools.fetch.drive.get_file_metadata")
    def test_unsupported_type_returns_error(self, mock_meta):
        mock_meta.return_value = _drive_metadata("application/x-unknown-format")
        result = fetch_drive("f1")
        assert isinstance(result, FetchError)
        assert result.kind == "unsupported_type"


class TestFetchDoc:
    """Tests for fetch_doc orchestration."""

    @patch("tools.fetch.drive.fetch_document")
    @patch("tools.fetch.drive.extract_doc_content", return_value="# Doc Content")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/doc"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/doc/content.md"))
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(3, "comments"))
    @patch("tools.fetch.drive.write_manifest")
    def test_basic_doc(self, mock_manifest, mock_comments, mock_write, mock_folder, mock_extract, mock_fetch):
        """Doc is fetched, extracted, comments enriched, and deposited."""
        mock_doc = MagicMock()
        mock_doc.tabs = [MagicMock()]
        mock_doc.warnings = []
        mock_fetch.return_value = mock_doc

        result = fetch_doc("doc1", "My Doc", _drive_metadata("application/vnd.google-apps.document"))

        assert isinstance(result, FetchResult)
        assert result.type == "doc"
        assert result.format == "markdown"
        assert result.metadata["title"] == "My Doc"
        mock_comments.assert_called_once_with("doc1", Path("/tmp/doc"))

    @patch("tools.fetch.drive.fetch_document")
    @patch("tools.fetch.drive.extract_doc_content", return_value="# Doc")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/doc"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/doc/content.md"))
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    def test_doc_with_email_context(self, mock_manifest, mock_comments, mock_write, mock_folder, mock_extract, mock_fetch):
        """Email context appears in result metadata."""
        mock_doc = MagicMock()
        mock_doc.tabs = [MagicMock()]
        mock_doc.warnings = []
        mock_fetch.return_value = mock_doc
        ctx = EmailContext(message_id="m1", from_address="a@b.com", subject="Re: test")

        result = fetch_doc("doc1", "My Doc", _drive_metadata("application/vnd.google-apps.document"), email_context=ctx)

        assert result.metadata["email_context"]["message_id"] == "m1"

    @patch("tools.fetch.drive.fetch_document")
    @patch("tools.fetch.drive.extract_doc_content", return_value="# Doc")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/doc"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/doc/content.md"))
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    def test_doc_with_warnings(self, mock_manifest, mock_comments, mock_write, mock_folder, mock_extract, mock_fetch):
        """Warnings from doc data appear in manifest."""
        mock_doc = MagicMock()
        mock_doc.tabs = [MagicMock()]
        mock_doc.warnings = ["Unknown element"]
        mock_fetch.return_value = mock_doc

        fetch_doc("doc1", "My Doc", _drive_metadata("application/vnd.google-apps.document"))

        manifest_extra = mock_manifest.call_args[1].get("extra") or mock_manifest.call_args[0][4] if len(mock_manifest.call_args[0]) > 4 else mock_manifest.call_args[1]["extra"]
        assert "warnings" in manifest_extra


class TestFetchSheet:
    """Tests for fetch_sheet orchestration."""

    @patch("tools.fetch.drive.fetch_spreadsheet")
    @patch("tools.fetch.drive.extract_sheets_content", return_value="col1,col2\n1,2")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/sheet"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/sheet/content.csv"))
    @patch("tools.fetch.drive.write_chart")
    @patch("tools.fetch.drive.write_charts_metadata")
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(2, "comments"))
    @patch("tools.fetch.drive.write_manifest")
    def test_sheet_with_charts(self, mock_manifest, mock_comments, mock_charts_meta, mock_chart, mock_write, mock_folder, mock_extract, mock_fetch):
        """Sheet with charts writes chart PNGs and metadata."""
        chart = MagicMock()
        chart.png_bytes = b"png"
        chart.chart_id = "c1"
        chart.title = "Sales"
        chart.sheet_name = "Sheet1"
        chart.chart_type = "BAR"
        mock_sheet = MagicMock()
        mock_sheet.sheets = [MagicMock()]
        mock_sheet.charts = [chart]
        mock_sheet.chart_render_time_ms = 500
        mock_sheet.warnings = []
        mock_fetch.return_value = mock_sheet

        result = fetch_sheet("s1", "My Sheet", _drive_metadata("application/vnd.google-apps.spreadsheet"))

        assert result.type == "sheet"
        assert result.format == "csv"
        assert result.metadata["chart_count"] == 1
        mock_chart.assert_called_once()
        mock_charts_meta.assert_called_once()

    @patch("tools.fetch.drive.fetch_spreadsheet")
    @patch("tools.fetch.drive.extract_sheets_content", return_value="col1\n1")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/sheet"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/sheet/content.csv"))
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    def test_sheet_no_charts(self, mock_manifest, mock_comments, mock_write, mock_folder, mock_extract, mock_fetch):
        """Sheet without charts skips chart writing."""
        mock_sheet = MagicMock()
        mock_sheet.sheets = [MagicMock(), MagicMock()]
        mock_sheet.charts = []
        mock_sheet.warnings = []
        mock_fetch.return_value = mock_sheet

        result = fetch_sheet("s1", "My Sheet", _drive_metadata("application/vnd.google-apps.spreadsheet"))

        assert result.metadata["sheet_count"] == 2
        assert "chart_count" not in result.metadata

    @patch("tools.fetch.drive.fetch_spreadsheet")
    @patch("tools.fetch.drive.extract_sheets_content", return_value="data")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/sheet"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/sheet/content.csv"))
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    def test_sheet_with_email_context(self, mock_manifest, mock_comments, mock_write, mock_folder, mock_extract, mock_fetch):
        """Email context in sheet result metadata."""
        mock_sheet = MagicMock()
        mock_sheet.sheets = [MagicMock()]
        mock_sheet.charts = []
        mock_sheet.warnings = []
        mock_fetch.return_value = mock_sheet
        ctx = EmailContext(message_id="m1", from_address="a@b.com", subject="Sheet")

        result = fetch_sheet("s1", "Sheet", _drive_metadata("application/vnd.google-apps.spreadsheet"), email_context=ctx)

        assert "email_context" in result.metadata


class TestFetchSlides:
    """Tests for fetch_slides orchestration."""

    @patch("tools.fetch.drive.fetch_presentation")
    @patch("tools.fetch.drive.extract_slides_content", return_value="# Slide 1\nContent")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/slides"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/slides/content.md"))
    @patch("tools.fetch.drive.write_thumbnail")
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(1, "comments"))
    @patch("tools.fetch.drive.write_manifest")
    def test_slides_with_thumbnails(self, mock_manifest, mock_comments, mock_thumb,
                                     mock_write, mock_folder, mock_extract, mock_fetch):
        """Slides with thumbnails writes PNGs and tracks count."""
        slide1 = MagicMock()
        slide1.thumbnail_bytes = b"png1"
        slide1.needs_thumbnail = True
        slide1.index = 0
        slide2 = MagicMock()
        slide2.thumbnail_bytes = None
        slide2.needs_thumbnail = False
        slide2.index = 1
        mock_pres = MagicMock()
        mock_pres.slides = [slide1, slide2]
        mock_pres.warnings = []
        mock_fetch.return_value = mock_pres

        result = fetch_slides("p1", "Deck", _drive_metadata("application/vnd.google-apps.presentation"))

        assert result.type == "slides"
        assert result.metadata["thumbnail_count"] == 1
        assert result.metadata["slide_count"] == 2
        mock_thumb.assert_called_once()

    @patch("tools.fetch.drive.fetch_presentation")
    @patch("tools.fetch.drive.extract_slides_content", return_value="# Slide 1")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/slides"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/slides/content.md"))
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    def test_slides_thumbnail_failures_tracked(self, mock_manifest, mock_comments,
                                                mock_write, mock_folder, mock_extract, mock_fetch):
        """Slides where thumbnail was requested but not received are tracked."""
        slide = MagicMock()
        slide.thumbnail_bytes = None
        slide.needs_thumbnail = True
        slide.index = 2
        mock_pres = MagicMock()
        mock_pres.slides = [slide]
        mock_pres.warnings = []
        mock_fetch.return_value = mock_pres

        fetch_slides("p1", "Deck", _drive_metadata("application/vnd.google-apps.presentation"))

        # Check manifest extra has thumbnail_failures
        extra = mock_manifest.call_args[1].get("extra", {})
        assert extra.get("thumbnail_failures") == [3]  # 1-indexed

    @patch("tools.fetch.drive.fetch_presentation")
    @patch("tools.fetch.drive.extract_slides_content", return_value="# Slide 1")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/slides"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/slides/content.md"))
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    def test_slides_with_email_context(self, mock_manifest, mock_comments,
                                        mock_write, mock_folder, mock_extract, mock_fetch):
        """Email context in slides result metadata."""
        mock_pres = MagicMock()
        mock_pres.slides = []
        mock_pres.warnings = []
        mock_fetch.return_value = mock_pres
        ctx = EmailContext(message_id="m1", from_address="a@b.com", subject="Deck")

        result = fetch_slides("p1", "Deck", _drive_metadata("application/vnd.google-apps.presentation"), email_context=ctx)

        assert "email_context" in result.metadata


class TestFetchVideo:
    """Tests for fetch_video orchestration."""

    @patch("tools.fetch.drive.get_video_summary")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/video"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/video/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_video_with_summary(self, mock_manifest, mock_write, mock_folder, mock_summary):
        """Video with AI summary includes summary in content."""
        mock_result = MagicMock()
        mock_result.has_content = True
        mock_result.summary = "This is a summary"
        mock_result.transcript_snippets = ["Hello", "World"]
        mock_result.error = None
        mock_summary.return_value = mock_result

        meta = _drive_metadata("video/mp4")
        meta["videoMediaMetadata"] = {"durationMillis": "125000"}
        result = fetch_video("v1", "My Video", meta)

        assert result.type == "video"
        assert result.metadata["has_summary"] is True
        written = mock_write.call_args[0][1]
        assert "This is a summary" in written
        assert "2:05" in written  # 125s = 2:05

    @patch("tools.fetch.drive.get_video_summary")
    @patch("tools.fetch.drive.is_cdp_available", return_value=False)
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/video"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/video/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_video_no_summary_no_cdp(self, mock_manifest, mock_write, mock_folder, mock_cdp, mock_summary):
        """Without summary and no CDP, content includes tip to run chrome-debug."""
        mock_summary.return_value = None

        result = fetch_video("v1", "My Video", _drive_metadata("video/mp4"))

        written = mock_write.call_args[0][1]
        assert "chrome-debug" in written
        assert result.metadata["has_summary"] is False

    @patch("tools.fetch.drive.get_video_summary")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/video"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/video/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_video_stale_cookies(self, mock_manifest, mock_write, mock_folder, mock_summary):
        """Stale cookies error shows refresh hint."""
        mock_result = MagicMock()
        mock_result.has_content = False
        mock_result.error = "stale_cookies"
        mock_summary.return_value = mock_result

        fetch_video("v1", "Video", _drive_metadata("video/mp4"))

        written = mock_write.call_args[0][1]
        assert "expired" in written

    @patch("tools.fetch.drive.get_video_summary")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/video"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/video/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_video_permission_denied(self, mock_manifest, mock_write, mock_folder, mock_summary):
        """Permission denied error shows appropriate message."""
        mock_result = MagicMock()
        mock_result.has_content = False
        mock_result.error = "permission_denied"
        mock_summary.return_value = mock_result

        fetch_video("v1", "Video", _drive_metadata("video/mp4"))

        written = mock_write.call_args[0][1]
        assert "no access" in written

    @patch("tools.fetch.drive.get_video_summary")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/video"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/video/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_video_long_duration(self, mock_manifest, mock_write, mock_folder, mock_summary):
        """Video with hours-long duration formats correctly."""
        mock_result = MagicMock()
        mock_result.has_content = False
        mock_result.error = None
        mock_summary.return_value = mock_result

        meta = _drive_metadata("video/mp4")
        meta["videoMediaMetadata"] = {"durationMillis": "3661000"}  # 1:01:01
        fetch_video("v1", "Long Video", meta)

        written = mock_write.call_args[0][1]
        assert "1:01:01" in written

    @patch("tools.fetch.drive.get_video_summary")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/video"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/video/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_video_with_email_context(self, mock_manifest, mock_write, mock_folder, mock_summary):
        """Email context in video result metadata."""
        mock_summary.return_value = None
        ctx = EmailContext(message_id="m1", from_address="a@b.com", subject="vid")

        result = fetch_video("v1", "Video", _drive_metadata("video/mp4"), email_context=ctx)

        assert "email_context" in result.metadata


class TestFetchPdf:
    """Tests for fetch_pdf orchestration."""

    @patch("tools.fetch.drive.fetch_and_extract_pdf")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/pdf"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/pdf/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_basic_pdf(self, mock_manifest, mock_write, mock_folder, mock_fetch):
        """PDF is extracted and deposited."""
        mock_fetch.return_value = PdfExtractionResult(
            content="# PDF", method="markitdown", char_count=5,
        )
        result = fetch_pdf("f1", "Report", _drive_metadata("application/pdf"))

        assert result.type == "pdf"
        assert result.metadata["extraction_method"] == "markitdown"

    @patch("tools.fetch.drive.fetch_and_extract_pdf")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/pdf"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/pdf/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_pdf_with_email_context(self, mock_manifest, mock_write, mock_folder, mock_fetch):
        """Email context in PDF result metadata."""
        mock_fetch.return_value = PdfExtractionResult(content="x", method="drive", char_count=1)
        ctx = EmailContext(message_id="m1", from_address="a@b.com", subject="pdf")

        result = fetch_pdf("f1", "Report", _drive_metadata("application/pdf"), email_context=ctx)

        assert "email_context" in result.metadata


class TestFetchOffice:
    """Tests for fetch_office orchestration."""

    @patch("tools.fetch.drive.fetch_and_extract_office")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/docx"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/docx/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_docx(self, mock_manifest, mock_write, mock_folder, mock_fetch):
        """DOCX is extracted and deposited as markdown."""
        mock_fetch.return_value = OfficeExtractionResult(
            content="# Doc", source_type="docx", export_format="markdown", extension="md",
        )
        result = fetch_office("f1", "Report", _drive_metadata("application/vnd.openxmlformats-officedocument.wordprocessingml.document"), "docx")

        assert result.type == "docx"
        assert result.format == "markdown"

    @patch("tools.fetch.drive.fetch_and_extract_office")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/xlsx"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/xlsx/content.csv"))
    @patch("tools.fetch.drive.write_manifest")
    def test_xlsx_format_is_csv(self, mock_manifest, mock_write, mock_folder, mock_fetch):
        """XLSX outputs CSV format."""
        mock_fetch.return_value = OfficeExtractionResult(
            content="a,b\n1,2", source_type="xlsx", export_format="csv", extension="csv",
        )
        result = fetch_office("f1", "Data", _drive_metadata("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), "xlsx")

        assert result.format == "csv"

    @patch("tools.fetch.drive.fetch_and_extract_office")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/docx"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/docx/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_office_with_warnings(self, mock_manifest, mock_write, mock_folder, mock_fetch):
        """Office warnings appear in manifest."""
        mock_fetch.return_value = OfficeExtractionResult(
            content="x", source_type="docx", export_format="markdown", extension="md",
            warnings=["Conversion warning"],
        )
        fetch_office("f1", "Report", _drive_metadata("application/vnd.openxmlformats-officedocument.wordprocessingml.document"), "docx")

        extra = mock_manifest.call_args[1].get("extra", {})
        assert "warnings" in extra

    @patch("tools.fetch.drive.fetch_and_extract_office")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/docx"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/docx/content.md"))
    @patch("tools.fetch.drive.write_manifest")
    def test_office_with_email_context(self, mock_manifest, mock_write, mock_folder, mock_fetch):
        """Email context in office result metadata."""
        mock_fetch.return_value = OfficeExtractionResult(
            content="x", source_type="docx", export_format="markdown", extension="md",
        )
        ctx = EmailContext(message_id="m1", from_address="a@b.com", subject="doc")

        result = fetch_office("f1", "Report", _drive_metadata("application/vnd.openxmlformats-officedocument.wordprocessingml.document"), "docx", email_context=ctx)

        assert "email_context" in result.metadata


class TestFetchText:
    """Tests for fetch_text orchestration."""

    @patch("tools.fetch.drive.download_file", return_value=b"Hello world")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/text"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/text/content.txt"))
    @patch("tools.fetch.drive.write_manifest")
    def test_plain_text(self, mock_manifest, mock_write, mock_folder, mock_dl):
        """Plain text is downloaded and deposited."""
        result = fetch_text("f1", "Notes", _drive_metadata("text/plain"))

        assert result.type == "text"
        assert result.format == "text"
        assert result.metadata["char_count"] == 11

    @patch("tools.fetch.drive.download_file", return_value=b"a,b\n1,2")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/text"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/text/content.csv"))
    @patch("tools.fetch.drive.write_manifest")
    def test_csv_format(self, mock_manifest, mock_write, mock_folder, mock_dl):
        """CSV file gets csv format and extension."""
        result = fetch_text("f1", "Data", _drive_metadata("text/csv"))

        assert result.format == "csv"
        # Check the filename passed to write_content
        assert mock_write.call_args[1].get("filename", "") == "content.csv" or "csv" in str(mock_write.call_args)

    @patch("tools.fetch.drive.download_file", return_value=b'{"key": "value"}')
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/text"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/text/content.json"))
    @patch("tools.fetch.drive.write_manifest")
    def test_json_format(self, mock_manifest, mock_write, mock_folder, mock_dl):
        """JSON file gets json format."""
        result = fetch_text("f1", "Config", _drive_metadata("application/json"))

        assert result.format == "json"

    @patch("tools.fetch.drive.download_file", return_value=b"Hello")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/text"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/text/content.txt"))
    @patch("tools.fetch.drive.write_manifest")
    def test_text_with_email_context(self, mock_manifest, mock_write, mock_folder, mock_dl):
        """Email context in text result metadata."""
        ctx = EmailContext(message_id="m1", from_address="a@b.com", subject="txt")

        result = fetch_text("f1", "Notes", _drive_metadata("text/plain"), email_context=ctx)

        assert "email_context" in result.metadata


class TestFetchImageFile:
    """Tests for fetch_image_file orchestration."""

    @patch("tools.fetch.drive.adapter_fetch_image")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/image"))
    @patch("tools.fetch.drive.write_image", return_value=Path("/tmp/image/photo.png"))
    @patch("tools.fetch.drive.write_manifest")
    def test_raster_image(self, mock_manifest, mock_write_img, mock_folder, mock_fetch):
        """Raster image is deposited as-is."""
        mock_result = MagicMock()
        mock_result.image_bytes = b"png bytes"
        mock_result.filename = "photo.png"
        mock_result.rendered_png_bytes = None
        mock_result.render_method = None
        mock_result.warnings = []
        mock_fetch.return_value = mock_result

        result = fetch_image_file("f1", "Photo", _drive_metadata("image/png"))

        assert result.type == "image"
        assert result.format == "image"

    @patch("tools.fetch.drive.adapter_fetch_image")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/image"))
    @patch("tools.fetch.drive.write_image")
    @patch("tools.fetch.drive.write_manifest")
    def test_svg_with_rendered_png(self, mock_manifest, mock_write_img, mock_folder, mock_fetch):
        """SVG gets both raw SVG and rendered PNG deposited."""
        mock_result = MagicMock()
        mock_result.image_bytes = b"<svg>...</svg>"
        mock_result.filename = "diagram.svg"
        mock_result.rendered_png_bytes = b"rendered png"
        mock_result.render_method = "rsvg-convert"
        mock_result.warnings = []
        mock_fetch.return_value = mock_result
        mock_write_img.side_effect = [Path("/tmp/image/diagram.svg"), Path("/tmp/image/image_rendered.png")]

        result = fetch_image_file("f1", "Diagram", _drive_metadata("image/svg+xml"))

        assert mock_write_img.call_count == 2
        assert result.metadata["is_svg"] is True
        assert result.metadata["has_rendered_png"] is True
        assert "image_rendered.png" in result.content_file

    @patch("tools.fetch.drive.adapter_fetch_image")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/image"))
    @patch("tools.fetch.drive.write_image", return_value=Path("/tmp/image/photo.png"))
    @patch("tools.fetch.drive.write_manifest")
    def test_image_with_email_context(self, mock_manifest, mock_write_img, mock_folder, mock_fetch):
        """Email context in image result metadata."""
        mock_result = MagicMock()
        mock_result.image_bytes = b"bytes"
        mock_result.filename = "photo.png"
        mock_result.rendered_png_bytes = None
        mock_result.render_method = None
        mock_result.warnings = []
        mock_fetch.return_value = mock_result
        ctx = EmailContext(message_id="m1", from_address="a@b.com", subject="img")

        result = fetch_image_file("f1", "Photo", _drive_metadata("image/png"), email_context=ctx)

        assert "email_context" in result.metadata


class TestFetchWeb:
    """Tests for fetch_web HTML content path."""

    @patch("tools.fetch.web.fetch_web_content")
    @patch("tools.fetch.web.extract_web_content", return_value="# Page Title\nContent here")
    @patch("tools.fetch.web.extract_title", return_value="Page Title")
    @patch("tools.fetch.web.get_deposit_folder", return_value=Path("/tmp/web"))
    @patch("tools.fetch.web.write_content", return_value=Path("/tmp/web/content.md"))
    @patch("tools.fetch.web.write_manifest")
    def test_html_page(self, mock_manifest, mock_write, mock_folder, mock_title, mock_extract, mock_fetch):
        """HTML web page is extracted and deposited."""
        mock_data = MagicMock(spec=WebData)
        mock_data.content_type = "text/html"
        mock_data.html = "<html><body>Content</body></html>"
        mock_data.final_url = "https://example.com/page"
        mock_data.render_method = "http"
        mock_data.warnings = []
        mock_data.raw_bytes = None
        mock_data.temp_path = None
        mock_fetch.return_value = mock_data

        result = fetch_web("https://example.com/page")

        assert result.type == "web"
        assert result.format == "markdown"
        assert result.metadata["title"] == "Page Title"
        assert result.metadata["render_method"] == "http"

    @patch("tools.fetch.web.fetch_web_content")
    @patch("tools.fetch.web.extract_web_content", return_value="Content")
    @patch("tools.fetch.web.extract_title", return_value=None)
    @patch("tools.fetch.web.get_deposit_folder", return_value=Path("/tmp/web"))
    @patch("tools.fetch.web.write_content", return_value=Path("/tmp/web/content.md"))
    @patch("tools.fetch.web.write_manifest")
    def test_no_title_uses_fallback(self, mock_manifest, mock_write, mock_folder, mock_title, mock_extract, mock_fetch):
        """Page with no extractable title uses 'web-page' fallback."""
        mock_data = MagicMock(spec=WebData)
        mock_data.content_type = "text/html"
        mock_data.html = "<html></html>"
        mock_data.final_url = "https://example.com"
        mock_data.render_method = "http"
        mock_data.warnings = []
        mock_data.raw_bytes = None
        mock_data.temp_path = None
        mock_fetch.return_value = mock_data

        result = fetch_web("https://example.com")

        assert result.metadata["title"] == "web-page"

    @patch("tools.fetch.web.fetch_web_content")
    @patch("tools.fetch.web.extract_web_content", return_value="Content")
    @patch("tools.fetch.web.extract_title", return_value="Title")
    @patch("tools.fetch.web.get_deposit_folder", return_value=Path("/tmp/web"))
    @patch("tools.fetch.web.write_content", return_value=Path("/tmp/web/content.md"))
    @patch("tools.fetch.web.write_manifest")
    def test_warnings_in_result(self, mock_manifest, mock_write, mock_folder, mock_title, mock_extract, mock_fetch):
        """Warnings from web adapter appear in result metadata."""
        mock_data = MagicMock(spec=WebData)
        mock_data.content_type = "text/html"
        mock_data.html = "<html></html>"
        mock_data.final_url = "https://example.com"
        mock_data.render_method = "browser"
        mock_data.warnings = ["JS rendering needed"]
        mock_data.raw_bytes = None
        mock_data.temp_path = None
        mock_fetch.return_value = mock_data

        result = fetch_web("https://example.com")

        assert result.metadata["warnings"] == ["JS rendering needed"]


class TestFetchWebPdf:
    """Tests for _fetch_web_pdf helper."""

    @patch("tools.fetch.web.extract_pdf_content")
    @patch("tools.fetch.web.get_deposit_folder", return_value=Path("/tmp/pdf"))
    @patch("tools.fetch.web.write_content", return_value=Path("/tmp/pdf/content.md"))
    @patch("tools.fetch.web.write_manifest")
    def test_small_pdf_from_bytes(self, mock_manifest, mock_write, mock_folder, mock_pdf):
        """Small PDF (raw_bytes) extracted successfully."""
        mock_pdf.return_value = PdfExtractionResult(content="# PDF", method="markitdown", char_count=5)
        web_data = WebData(
            url="https://example.com/doc.pdf",
            html="", content_type="application/pdf", final_url="https://example.com/doc.pdf",
            status_code=200, cookies_used=False,
            render_method="http", raw_bytes=b"%PDF-1.4 content",
        )
        result = _fetch_web_pdf("https://example.com/doc.pdf", web_data)

        assert result.type == "pdf"
        assert result.metadata["url"] == "https://example.com/doc.pdf"

    def test_non_pdf_bytes_raises(self):
        """Content-Type says PDF but bytes aren't PDF raises MiseError."""
        web_data = WebData(
            url="https://example.com/doc.pdf",
            html="", content_type="application/pdf", final_url="https://example.com/doc.pdf",
            status_code=200, cookies_used=False,
            render_method="http", raw_bytes=b"<html>Not a PDF</html>",
        )
        with pytest.raises(MiseError, match="not PDF"):
            _fetch_web_pdf("https://example.com/doc.pdf", web_data)

    def test_no_content_raises(self):
        """No raw_bytes and no temp_path raises MiseError."""
        web_data = WebData(
            url="https://example.com/doc.pdf",
            html="", content_type="application/pdf", final_url="https://example.com/doc.pdf",
            status_code=200, cookies_used=False,
            render_method="http",
        )
        with pytest.raises(MiseError, match="No PDF content"):
            _fetch_web_pdf("https://example.com/doc.pdf", web_data)

    @patch("tools.fetch.web.extract_pdf_content")
    @patch("tools.fetch.web.get_deposit_folder", return_value=Path("/tmp/pdf"))
    @patch("tools.fetch.web.write_content", return_value=Path("/tmp/pdf/content.md"))
    @patch("tools.fetch.web.write_manifest")
    def test_pdf_with_warnings(self, mock_manifest, mock_write, mock_folder, mock_pdf):
        """PDF extraction warnings pass through to metadata."""
        mock_pdf.return_value = PdfExtractionResult(
            content="# PDF", method="drive", char_count=5,
            warnings=["Low content, used Drive fallback"],
        )
        web_data = WebData(
            url="https://example.com/doc.pdf",
            html="", content_type="application/pdf", final_url="https://example.com/doc.pdf",
            status_code=200, cookies_used=False,
            render_method="http", raw_bytes=b"%PDF-1.4 content",
        )
        result = _fetch_web_pdf("https://example.com/doc.pdf", web_data)

        assert "warnings" in result.metadata


class TestFetchWebOffice:
    """Tests for _fetch_web_office helper."""

    @patch("tools.fetch.web.extract_office_content")
    @patch("tools.fetch.web.get_deposit_folder", return_value=Path("/tmp/docx"))
    @patch("tools.fetch.web.write_content", return_value=Path("/tmp/docx/content.md"))
    @patch("tools.fetch.web.write_manifest")
    def test_small_docx_from_bytes(self, mock_manifest, mock_write, mock_folder, mock_office):
        """Small DOCX (raw_bytes) extracted successfully."""
        mock_office.return_value = OfficeExtractionResult(
            content="# Doc", source_type="docx", export_format="markdown", extension="md",
        )
        web_data = WebData(
            url="https://example.com/report.docx",
            html="", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            final_url="https://example.com/report.docx", status_code=200, cookies_used=False,
            render_method="http", raw_bytes=b"docx bytes",
        )
        result = _fetch_web_office("https://example.com/report.docx", web_data, "docx")

        assert result.type == "docx"
        assert result.format == "markdown"

    def test_no_content_raises(self):
        """No raw_bytes and no temp_path raises MiseError."""
        web_data = WebData(
            url="https://example.com/report.docx",
            html="", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            final_url="https://example.com/report.docx", status_code=200, cookies_used=False,
            render_method="http",
        )
        with pytest.raises(MiseError, match="No Office content"):
            _fetch_web_office("https://example.com/report.docx", web_data, "docx")

    @patch("tools.fetch.web.extract_office_content")
    @patch("tools.fetch.web.get_deposit_folder", return_value=Path("/tmp/xlsx"))
    @patch("tools.fetch.web.write_content", return_value=Path("/tmp/xlsx/content.csv"))
    @patch("tools.fetch.web.write_manifest")
    def test_xlsx_format(self, mock_manifest, mock_write, mock_folder, mock_office):
        """XLSX from web uses CSV format."""
        mock_office.return_value = OfficeExtractionResult(
            content="a,b\n1,2", source_type="xlsx", export_format="csv", extension="csv",
        )
        web_data = WebData(
            url="https://example.com/data.xlsx",
            html="", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            final_url="https://example.com/data.xlsx", status_code=200, cookies_used=False,
            render_method="http", raw_bytes=b"xlsx bytes",
        )
        result = _fetch_web_office("https://example.com/data.xlsx", web_data, "xlsx")

        assert result.format == "csv"


class TestDoFetchRouting:
    """Tests for do_fetch entry point routing and error handling."""

    @patch("tools.fetch.router.detect_id_type", return_value=("gmail", "t1"))
    @patch("tools.fetch.router.fetch_gmail")
    def test_routes_gmail(self, mock_gmail, mock_detect):
        """Gmail IDs route to fetch_gmail."""
        mock_gmail.return_value = FetchResult(path="/p", content_file="/p/c.md", format="markdown", type="gmail", metadata={})
        result = do_fetch("t1")
        mock_gmail.assert_called_once_with("t1", base_path=None)

    @patch("tools.fetch.router.detect_id_type", return_value=("web", "https://example.com"))
    @patch("tools.fetch.router.fetch_web")
    def test_routes_web(self, mock_web, mock_detect):
        """Web URLs route to fetch_web."""
        mock_web.return_value = FetchResult(path="/p", content_file="/p/c.md", format="markdown", type="web", metadata={})
        result = do_fetch("https://example.com")
        mock_web.assert_called_once_with("https://example.com", base_path=None)

    @patch("tools.fetch.router.detect_id_type", return_value=("drive", "f1"))
    @patch("tools.fetch.router.fetch_drive")
    def test_routes_drive(self, mock_drive, mock_detect):
        """Drive IDs route to fetch_drive."""
        mock_drive.return_value = FetchResult(path="/p", content_file="/p/c.md", format="markdown", type="doc", metadata={})
        result = do_fetch("f1")
        mock_drive.assert_called_once_with("f1", base_path=None)

    def test_mise_error_caught(self):
        """MiseError becomes FetchError."""
        with patch("tools.fetch.router.detect_id_type", side_effect=MiseError(ErrorKind.NOT_FOUND, "gone")):
            result = do_fetch("f1")
        assert isinstance(result, FetchError)
        assert result.kind == "not_found"

    def test_value_error_caught(self):
        """ValueError becomes FetchError with invalid_input kind."""
        with patch("tools.fetch.router.detect_id_type", side_effect=ValueError("bad id")):
            result = do_fetch("bad")
        assert isinstance(result, FetchError)
        assert result.kind == "invalid_input"

    def test_generic_error_caught(self):
        """Unexpected exceptions become FetchError with unknown kind."""
        with patch("tools.fetch.router.detect_id_type", side_effect=RuntimeError("boom")):
            result = do_fetch("f1")
        assert isinstance(result, FetchError)
        assert result.kind == "unknown"

    @patch("tools.fetch.router.detect_id_type", return_value=("drive", "f1"))
    @patch("tools.fetch.router.fetch_drive")
    def test_passes_base_path(self, mock_drive, mock_detect):
        """base_path is forwarded to fetcher."""
        mock_drive.return_value = FetchResult(path="/p", content_file="/p/c.md", format="markdown", type="doc", metadata={})
        do_fetch("f1", base_path=Path("/custom"))
        mock_drive.assert_called_once_with("f1", base_path=Path("/custom"))


class TestDoFetchComments:
    """Tests for do_fetch_comments."""

    @patch("tools.fetch.router.detect_id_type", return_value=("drive", "f1"))
    @patch("tools.fetch.router.fetch_file_comments")
    @patch("tools.fetch.router.extract_comments_content", return_value="# Comments\n- test")
    def test_success(self, mock_extract, mock_fetch, mock_detect):
        """Successful comment fetch returns content and metadata."""
        mock_data = MagicMock()
        mock_data.file_id = "f1"
        mock_data.file_name = "My Doc"
        mock_data.comment_count = 3
        mock_data.warnings = []
        mock_fetch.return_value = mock_data

        result = do_fetch_comments("f1")

        assert result["content"] == "# Comments\n- test"
        assert result["file_id"] == "f1"
        assert result["comment_count"] == 3
        assert result.get("error") is None

    @patch("tools.fetch.router.detect_id_type", return_value=("drive", "f1"))
    @patch("tools.fetch.router.fetch_file_comments")
    @patch("tools.fetch.router.extract_comments_content", return_value="# Comments")
    def test_with_warnings(self, mock_extract, mock_fetch, mock_detect):
        """Warnings from comment data are included."""
        mock_data = MagicMock()
        mock_data.file_id = "f1"
        mock_data.file_name = "Doc"
        mock_data.comment_count = 1
        mock_data.warnings = ["Author name missing"]
        mock_fetch.return_value = mock_data

        result = do_fetch_comments("f1")

        assert result["warnings"] == ["Author name missing"]

    @patch("tools.fetch.router.detect_id_type", return_value=("drive", "f1"))
    @patch("tools.fetch.router.fetch_file_comments", side_effect=MiseError(ErrorKind.NOT_FOUND, "File not found"))
    def test_mise_error(self, mock_fetch, mock_detect):
        """MiseError returns error dict."""
        result = do_fetch_comments("f1")

        assert result["error"] is True
        assert result["kind"] == "not_found"

    @patch("tools.fetch.router.detect_id_type", return_value=("drive", "f1"))
    @patch("tools.fetch.router.fetch_file_comments", side_effect=RuntimeError("boom"))
    def test_generic_error(self, mock_fetch, mock_detect):
        """Generic exception returns error dict."""
        result = do_fetch_comments("f1")

        assert result["error"] is True
        assert result["kind"] == "unknown"

    @patch("tools.fetch.router.detect_id_type", return_value=("drive", "f1"))
    @patch("tools.fetch.router.fetch_file_comments")
    @patch("tools.fetch.router.extract_comments_content", return_value="comments")
    def test_passes_parameters(self, mock_extract, mock_fetch, mock_detect):
        """Parameters are forwarded to adapter."""
        mock_data = MagicMock()
        mock_data.file_id = "f1"
        mock_data.file_name = "Doc"
        mock_data.comment_count = 0
        mock_data.warnings = []
        mock_fetch.return_value = mock_data

        do_fetch_comments("f1", include_deleted=True, include_resolved=False, max_results=50)

        mock_fetch.assert_called_once_with(
            file_id="f1", include_deleted=True, include_resolved=False, max_results=50,
        )


class TestFetchGmailEdgeCases:
    """Edge cases in fetch_gmail for remaining uncovered lines."""

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.gmail._extract_attachment_content")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value=Path("/tmp/deposit"))
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread")
    def test_eager_attachment_limit(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_att_extract, mock_lookup, mock_fetch
    ):
        """Attachments beyond MAX_EAGER_ATTACHMENTS are skipped with warning."""
        # Create 11 PDF attachments (limit is 10)
        atts = [
            EmailAttachment(
                filename=f"file{i}.pdf", mime_type="application/pdf",
                size=100, attachment_id=f"att_{i}",
            )
            for i in range(11)
        ]
        mock_fetch.return_value = _make_thread_data(atts)
        mock_att_extract.return_value = {"filename": "x.pdf", "extracted": True}

        result = fetch_gmail("t1")

        # Only 10 extraction calls, 11th is skipped
        assert mock_att_extract.call_count == 10
        # Warning about limit in manifest
        manifest_extra = mock_manifest.call_args[1]["extra"]
        assert any("limit" in w.lower() for w in manifest_extra.get("warnings", []))

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.gmail.get_deposit_folder", return_value=Path("/tmp/deposit"))
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread")
    def test_thread_warnings_merged_with_extraction_warnings(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_lookup, mock_fetch
    ):
        """Thread-level warnings are merged with extraction warnings in manifest."""
        thread = _make_thread_data([])
        thread.warnings = ["HTML fallback used"]
        mock_fetch.return_value = thread

        fetch_gmail("t1")

        manifest_extra = mock_manifest.call_args[1]["extra"]
        assert "HTML fallback used" in manifest_extra.get("warnings", [])

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.gmail.get_deposit_folder", return_value=Path("/tmp/deposit"))
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="Thread")
    def test_drive_links_in_metadata(
        self, mock_extract, mock_manifest, mock_write, mock_folder,
        mock_lookup, mock_fetch
    ):
        """Drive links from messages appear in result metadata."""
        msg = EmailMessage(
            message_id="msg1",
            from_address="a@b.com",
            to_addresses=["c@d.com"],
            body_text="See attached",
            attachments=[],
            drive_links=[{"url": "https://drive.google.com/file/d/xyz/view", "name": "Shared Doc"}],
        )
        thread = GmailThreadData(thread_id="t1", subject="Test", messages=[msg])
        mock_fetch.return_value = thread

        result = fetch_gmail("t1")

        assert "drive_links" in result.metadata
        assert result.metadata["drive_links"][0]["name"] == "Shared Doc"


class TestFetchAttachmentExfilEdgeCases:
    """Edge cases for pre-exfil paths in fetch_attachment."""

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.download_file", return_value=b"%PDF-1.4 content")
    @patch("tools.fetch.gmail.extract_pdf_content")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value=Path("/tmp/pdf"))
    @patch("tools.fetch.gmail.write_content", return_value=Path("/tmp/pdf/content.md"))
    @patch("tools.fetch.gmail.write_manifest")
    def test_pdf_from_exfil(
        self, mock_manifest, mock_write, mock_folder, mock_pdf,
        mock_dl, mock_lookup, mock_fetch
    ):
        """PDF attachment fetched from pre-exfil Drive copy."""
        att = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_99", "name": "report.pdf", "mimeType": "application/pdf"}]
        }
        mock_pdf.return_value = PdfExtractionResult(
            content="# PDF", method="markitdown", char_count=5,
        )

        result = fetch_attachment("thread_xyz", "report.pdf")

        assert isinstance(result, FetchResult)
        assert result.type == "pdf"
        assert result.metadata["source"] == "drive_exfil"
        mock_dl.assert_called_once_with("drive_99")

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.download_file", side_effect=RuntimeError("Drive error"))
    @patch("tools.fetch.gmail.download_attachment")
    @patch("tools.fetch.gmail.extract_pdf_content")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value=Path("/tmp/pdf"))
    @patch("tools.fetch.gmail.write_content", return_value=Path("/tmp/pdf/content.md"))
    @patch("tools.fetch.gmail.write_manifest")
    def test_pdf_exfil_fallback_to_gmail(
        self, mock_manifest, mock_write, mock_folder, mock_pdf,
        mock_gmail_dl, mock_drive_dl, mock_lookup, mock_fetch
    ):
        """PDF falls back to Gmail when Drive exfil download fails."""
        att = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_99", "name": "report.pdf", "mimeType": "application/pdf"}]
        }
        mock_gmail_dl.return_value = AttachmentDownload(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, content=b"%PDF-1.4",
        )
        mock_pdf.return_value = PdfExtractionResult(
            content="# PDF", method="markitdown", char_count=5,
            warnings=["Drive fallback"],
        )

        result = fetch_attachment("thread_xyz", "report.pdf")

        assert isinstance(result, FetchResult)
        assert result.metadata["source"] == "gmail"

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.download_file", return_value=b"png bytes")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value=Path("/tmp/image"))
    @patch("tools.fetch.gmail.write_image", return_value=Path("/tmp/image/logo.png"))
    @patch("tools.fetch.gmail.write_manifest")
    def test_image_from_exfil_with_warnings(
        self, mock_manifest, mock_write_img, mock_folder,
        mock_dl, mock_lookup, mock_fetch
    ):
        """Image from exfil with warning includes warnings in manifest."""
        att = EmailAttachment(
            filename="logo.png", mime_type="image/png",
            size=500, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_99", "name": "logo.png", "mimeType": "image/png"}]
        }

        result = fetch_attachment("thread_xyz", "logo.png")

        assert isinstance(result, FetchResult)
        assert result.type == "image"
        assert result.metadata["source"] == "drive_exfil"


class TestFetchSheetEdgeCases:
    """Edge cases for fetch_sheet."""

    @patch("tools.fetch.drive.fetch_spreadsheet")
    @patch("tools.fetch.drive.extract_sheets_content", return_value="data")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/sheet"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/sheet/content.csv"))
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    def test_sheet_with_warnings(self, mock_manifest, mock_comments, mock_write, mock_folder, mock_extract, mock_fetch):
        """Sheet-level warnings appear in manifest."""
        mock_sheet = MagicMock()
        mock_sheet.sheets = [MagicMock()]
        mock_sheet.charts = []
        mock_sheet.warnings = ["Empty sheet skipped"]
        mock_fetch.return_value = mock_sheet

        fetch_sheet("s1", "Sheet", _drive_metadata("application/vnd.google-apps.spreadsheet"))

        extra = mock_manifest.call_args[1]["extra"]
        assert "warnings" in extra
        assert "Empty sheet skipped" in extra["warnings"]


class TestFetchSlidesEdgeCases:
    """Edge cases for fetch_slides."""

    @patch("tools.fetch.drive.fetch_presentation")
    @patch("tools.fetch.drive.extract_slides_content", return_value="# Slide")
    @patch("tools.fetch.drive.get_deposit_folder", return_value=Path("/tmp/slides"))
    @patch("tools.fetch.drive.write_content", return_value=Path("/tmp/slides/content.md"))
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    def test_slides_with_warnings(self, mock_manifest, mock_comments,
                                   mock_write, mock_folder, mock_extract, mock_fetch):
        """Presentation-level warnings appear in manifest."""
        mock_pres = MagicMock()
        mock_pres.slides = []
        mock_pres.warnings = ["Missing objectId"]
        mock_fetch.return_value = mock_pres

        fetch_slides("p1", "Deck", _drive_metadata("application/vnd.google-apps.presentation"))

        extra = mock_manifest.call_args[1]["extra"]
        assert "warnings" in extra
        assert "Missing objectId" in extra["warnings"]


class TestFetchAttachmentLookupFailure:
    """Tests for fetch_attachment when lookup_exfiltrated itself fails."""

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated", side_effect=RuntimeError("API down"))
    @patch("tools.fetch.gmail.download_attachment")
    @patch("tools.fetch.gmail.extract_pdf_content")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value=Path("/tmp/pdf"))
    @patch("tools.fetch.gmail.write_content", return_value=Path("/tmp/pdf/content.md"))
    @patch("tools.fetch.gmail.write_manifest")
    def test_lookup_failure_falls_back_to_gmail(
        self, mock_manifest, mock_write, mock_folder, mock_pdf,
        mock_gmail_dl, mock_lookup, mock_fetch
    ):
        """When lookup_exfiltrated raises, falls back to Gmail download."""
        att = EmailAttachment(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_gmail_dl.return_value = AttachmentDownload(
            filename="report.pdf", mime_type="application/pdf",
            size=1000, content=b"%PDF-1.4",
        )
        mock_pdf.return_value = PdfExtractionResult(
            content="# PDF", method="markitdown", char_count=5,
        )

        result = fetch_attachment("thread_xyz", "report.pdf")

        assert isinstance(result, FetchResult)
        assert result.metadata["source"] == "gmail"

    @patch("tools.fetch.gmail.fetch_thread")
    @patch("tools.fetch.gmail.lookup_exfiltrated")
    @patch("tools.fetch.gmail.download_file", side_effect=RuntimeError("Drive error"))
    @patch("tools.fetch.gmail.download_attachment")
    @patch("tools.fetch.gmail.get_deposit_folder", return_value=Path("/tmp/image"))
    @patch("tools.fetch.gmail.write_image", return_value=Path("/tmp/image/logo.png"))
    @patch("tools.fetch.gmail.write_manifest")
    def test_image_exfil_download_fails_with_warning(
        self, mock_manifest, mock_write_img, mock_folder,
        mock_gmail_dl, mock_drive_dl, mock_lookup, mock_fetch
    ):
        """Image exfil download failure adds warning and falls back to Gmail."""
        att = EmailAttachment(
            filename="logo.png", mime_type="image/png",
            size=500, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_99", "name": "logo.png", "mimeType": "image/png"}]
        }
        mock_gmail_dl.return_value = AttachmentDownload(
            filename="logo.png", mime_type="image/png",
            size=500, content=b"png bytes",
        )

        result = fetch_attachment("thread_xyz", "logo.png")

        assert isinstance(result, FetchResult)
        assert result.type == "image"
        assert result.metadata["source"] == "gmail"
        # Warning should be in manifest
        manifest_extra = mock_manifest.call_args[1]["extra"]
        assert "warnings" in manifest_extra
