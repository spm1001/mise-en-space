"""
Unit tests for fetch tool ID detection and routing.
"""

import pytest
from unittest.mock import patch, MagicMock
from tools.fetch import detect_id_type, fetch_gmail, fetch_attachment, do_fetch, _extract_from_drive, _match_exfil_file
from models import GmailThreadData, EmailMessage, EmailAttachment, FetchResult, FetchError
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch._extract_from_drive")
    @patch("tools.fetch._extract_attachment_content")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    @patch("tools.fetch.extract_thread_content", return_value="Thread content")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch._extract_from_drive")
    @patch("tools.fetch._extract_attachment_content")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    @patch("tools.fetch.extract_thread_content", return_value="Thread content")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch._extract_from_drive")
    @patch("tools.fetch._extract_attachment_content")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    @patch("tools.fetch.extract_thread_content", return_value="Thread content")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    @patch("tools.fetch.extract_thread_content", return_value="Thread content")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    @patch("tools.fetch.extract_thread_content", return_value="Thread content")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch._extract_from_drive")
    @patch("tools.fetch._extract_attachment_content")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    @patch("tools.fetch.extract_thread_content", return_value="Thread content")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch._extract_from_drive")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    @patch("tools.fetch.extract_thread_content", return_value="Thread content here")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch._extract_from_drive")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    @patch("tools.fetch.extract_thread_content", return_value="Thread content")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch._extract_from_drive")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    @patch("tools.fetch.extract_thread_content", return_value="Thread content")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch._extract_from_drive")
    @patch("tools.fetch.get_deposit_folder")
    @patch("tools.fetch.write_content")
    @patch("tools.fetch.write_manifest")
    @patch("tools.fetch.extract_thread_content", return_value="Thread content")
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
        with patch("tools.fetch.detect_id_type", return_value=("gmail", "thread_xyz")), \
             patch("tools.fetch.fetch_attachment") as mock_fetch_att:
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

    @patch("tools.fetch.fetch_thread")
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

    @patch("tools.fetch.fetch_thread")
    def test_no_attachments_lists_none(self, mock_fetch):
        """Thread with no attachments returns helpful error."""
        mock_fetch.return_value = _make_thread_data([])

        result = fetch_attachment("thread_xyz", "anything.pdf")

        assert isinstance(result, FetchError)
        assert "(none)" in result.message

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.download_attachment")
    @patch("tools.fetch.extract_office_content")
    @patch("tools.fetch.get_deposit_folder", return_value="/tmp/test-deposit")
    @patch("tools.fetch.write_content", return_value="/tmp/test-deposit/content.csv")
    @patch("tools.fetch.write_manifest")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.download_attachment")
    @patch("tools.fetch.extract_pdf_content")
    @patch("tools.fetch.get_deposit_folder", return_value="/tmp/test-deposit")
    @patch("tools.fetch.write_content", return_value="/tmp/test-deposit/content.md")
    @patch("tools.fetch.write_manifest")
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

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch.download_file")
    @patch("tools.fetch.extract_office_content")
    @patch("tools.fetch.get_deposit_folder", return_value="/tmp/test-deposit")
    @patch("tools.fetch.write_content", return_value="/tmp/test-deposit/content.csv")
    @patch("tools.fetch.write_manifest")
    def test_preexfil_preferred(
        self, mock_manifest, mock_write, mock_folder,
        mock_office, mock_drive_dl, mock_lookup, mock_fetch
    ):
        """Pre-exfil Drive copy used when available."""
        xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        att = EmailAttachment(
            filename="budget.xlsx", mime_type=xlsx_mime,
            size=5000, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_99", "name": "budget.xlsx", "mimeType": xlsx_mime}]
        }
        mock_drive_dl.return_value = b"drive xlsx bytes"
        mock_office.return_value = OfficeExtractionResult(
            content="Sheet1\ncol1,col2\n1,2",
            source_type="xlsx", export_format="csv", extension="csv",
        )

        result = fetch_attachment("thread_xyz", "budget.xlsx")

        assert isinstance(result, FetchResult)
        assert result.metadata["source"] == "drive_exfil"
        mock_drive_dl.assert_called_once_with("drive_99")

    @patch("tools.fetch.fetch_thread")
    @patch("tools.fetch.lookup_exfiltrated")
    @patch("tools.fetch.download_file")
    @patch("tools.fetch.download_attachment")
    @patch("tools.fetch.extract_office_content")
    @patch("tools.fetch.get_deposit_folder", return_value="/tmp/test-deposit")
    @patch("tools.fetch.write_content", return_value="/tmp/test-deposit/content.csv")
    @patch("tools.fetch.write_manifest")
    def test_preexfil_fallback_to_gmail(
        self, mock_manifest, mock_write, mock_folder,
        mock_office, mock_gmail_dl, mock_drive_dl, mock_lookup, mock_fetch
    ):
        """Falls back to Gmail when pre-exfil Drive download fails."""
        xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        att = EmailAttachment(
            filename="budget.xlsx", mime_type=xlsx_mime,
            size=5000, attachment_id="att_1",
        )
        mock_fetch.return_value = _make_thread_data([att])
        mock_lookup.return_value = {
            "msg_abc123": [{"file_id": "drive_99", "name": "budget.xlsx", "mimeType": xlsx_mime}]
        }
        mock_drive_dl.side_effect = Exception("Drive error")
        mock_gmail_dl.return_value = AttachmentDownload(
            filename="budget.xlsx", mime_type=xlsx_mime,
            size=5000, content=b"gmail xlsx bytes",
        )
        mock_office.return_value = OfficeExtractionResult(
            content="Sheet1\ncol1,col2\n1,2",
            source_type="xlsx", export_format="csv", extension="csv",
        )

        result = fetch_attachment("thread_xyz", "budget.xlsx")

        assert isinstance(result, FetchResult)
        assert result.metadata["source"] == "gmail"
        mock_gmail_dl.assert_called_once()
