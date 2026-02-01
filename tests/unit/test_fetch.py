"""
Unit tests for fetch tool ID detection and routing.
"""

import pytest
from tools.fetch import detect_id_type


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
