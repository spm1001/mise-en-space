"""
Tests for validation and ID conversion utilities.
"""

import pytest

from validation import (
    extract_drive_file_id,
    extract_gmail_id,
    extract_gmail_id_from_url,
    convert_gmail_web_id,
    is_gmail_web_id,
    is_gmail_api_id,
    is_valid_email,
    normalize_email,
)


class TestDriveIdExtraction:
    """Tests for Drive file ID extraction."""

    def test_extracts_from_docs_url(self):
        """Extract ID from Google Docs URL."""
        url = "https://docs.google.com/document/d/1ABC123_test/edit"
        assert extract_drive_file_id(url) == "1ABC123_test"

    def test_extracts_from_sheets_url(self):
        """Extract ID from Google Sheets URL."""
        url = "https://docs.google.com/spreadsheets/d/1XYZ789/edit#gid=0"
        assert extract_drive_file_id(url) == "1XYZ789"

    def test_extracts_from_drive_file_url(self):
        """Extract ID from Drive file URL."""
        url = "https://drive.google.com/file/d/0BwGZ5_abc123/view"
        assert extract_drive_file_id(url) == "0BwGZ5_abc123"

    def test_extracts_from_open_url(self):
        """Extract ID from drive.google.com/open?id= URL."""
        url = "https://drive.google.com/open?id=1abc123"
        assert extract_drive_file_id(url) == "1abc123"

    def test_extracts_from_folder_url(self):
        """Extract ID from Drive folder URL."""
        url = "https://drive.google.com/drive/folders/1_UMRzD4KScPks"
        assert extract_drive_file_id(url) == "1_UMRzD4KScPks"

    def test_returns_bare_id(self):
        """Return bare ID unchanged."""
        assert extract_drive_file_id("1ABC123_test") == "1ABC123_test"

    def test_rejects_invalid_id(self):
        """Reject invalid file ID format."""
        with pytest.raises(ValueError, match="Invalid file ID"):
            extract_drive_file_id("not a valid id!")

    def test_rejects_empty(self):
        """Reject empty input."""
        with pytest.raises(ValueError, match="required"):
            extract_drive_file_id("")


class TestGmailIdConversion:
    """Tests for Gmail ID conversion."""

    def test_is_gmail_api_id(self):
        """Detect valid API IDs."""
        assert is_gmail_api_id("19b0e7fe6f653f69")
        assert is_gmail_api_id("0000000000000000")
        assert not is_gmail_api_id("FMfcgzQdzmSk")  # Too short, wrong chars
        assert not is_gmail_api_id("19b0e7fe6f653f6")  # Too short

    def test_is_gmail_web_id(self):
        """Detect web UI IDs."""
        assert is_gmail_web_id("FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph")
        assert is_gmail_web_id("KtbxLwGXnfZWVpRNLkCVXBbfkLGPdh")
        assert not is_gmail_web_id("19b0e7fe6f653f69")  # API format

    def test_convert_gmail_web_id(self):
        """Convert web ID to API ID."""
        # This is a real conversion - the web ID decodes to a thread-f format
        web_id = "FMfcgzQfBZdVqDtDZnXwMRWvRZjGhdWN"
        api_id = convert_gmail_web_id(web_id)
        # Should be 16 hex chars
        assert api_id is not None
        assert len(api_id) == 16
        assert all(c in '0123456789abcdef' for c in api_id)

    def test_extract_gmail_id_from_url(self):
        """Extract and convert from Gmail URL."""
        url = "https://mail.google.com/mail/u/0/#sent/FMfcgzQfBZdVqDtDZnXwMRWvRZjGhdWN"
        api_id = extract_gmail_id_from_url(url)
        assert api_id is not None
        assert len(api_id) == 16

    def test_extract_gmail_id_returns_api_id(self):
        """Return API ID unchanged."""
        api_id = "19b0e7fe6f653f69"
        assert extract_gmail_id(api_id) == api_id

    def test_extract_gmail_id_converts_web_id(self):
        """Convert web ID automatically."""
        web_id = "FMfcgzQfBZdVqDtDZnXwMRWvRZjGhdWN"
        result = extract_gmail_id(web_id)
        assert len(result) == 16
        assert result != web_id  # Should be converted

    def test_extract_gmail_id_from_full_url(self):
        """Extract from full Gmail URL."""
        url = "https://mail.google.com/mail/u/0/#inbox/FMfcgzQfBZdVqDtDZnXwMRWvRZjGhdWN"
        result = extract_gmail_id(url)
        assert len(result) == 16

    def test_rejects_non_gmail_url(self):
        """Reject non-Gmail URLs."""
        with pytest.raises(ValueError, match="Not a Gmail URL"):
            extract_gmail_id("https://example.com/something")


class TestEmailValidation:
    """Tests for email validation."""

    def test_valid_emails(self):
        """Accept valid email addresses."""
        assert is_valid_email("user@example.com")
        assert is_valid_email("user.name@example.co.uk")
        assert is_valid_email("user+tag@example.com")

    def test_invalid_emails(self):
        """Reject invalid email addresses."""
        assert not is_valid_email("")
        assert not is_valid_email("not-an-email")
        assert not is_valid_email("@example.com")
        assert not is_valid_email("user@")

    def test_normalize_email(self):
        """Normalize email addresses."""
        assert normalize_email("User@Example.COM") == "user@example.com"
        assert normalize_email("  user@example.com  ") == "user@example.com"

    def test_normalize_rejects_invalid(self):
        """Reject invalid emails during normalization."""
        with pytest.raises(ValueError, match="Invalid email"):
            normalize_email("not-an-email")
