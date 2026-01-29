"""
Tests for attachment filtering logic.
"""

import pytest
from filters import is_trivial_attachment, filter_attachments, get_filter_config


class TestGetFilterConfig:
    """Tests for filter config loading."""

    def test_loads_config(self):
        """Config loads successfully."""
        config = get_filter_config()
        assert "version" in config
        assert "excluded_mime_types" in config
        assert "excluded_filename_patterns" in config
        assert "image_size_threshold_bytes" in config


class TestIsTrivialAttachment:
    """Tests for is_trivial_attachment function."""

    # Empty/missing filename
    def test_empty_filename_is_trivial(self):
        """Empty filename is trivial."""
        assert is_trivial_attachment("", "application/pdf", 1000) is True

    def test_whitespace_filename_is_trivial(self):
        """Whitespace-only filename is trivial."""
        assert is_trivial_attachment("   ", "application/pdf", 1000) is True

    # Calendar invites
    def test_ics_mime_is_trivial(self):
        """Calendar invite (text/calendar) is trivial."""
        assert is_trivial_attachment("invite.ics", "text/calendar", 500) is True

    def test_ics_application_mime_is_trivial(self):
        """Calendar invite (application/ics) is trivial."""
        assert is_trivial_attachment("meeting.ics", "application/ics", 500) is True

    # VCards
    def test_vcard_is_trivial(self):
        """VCard is trivial."""
        assert is_trivial_attachment("contact.vcf", "text/vcard", 500) is True

    def test_vcard_x_is_trivial(self):
        """VCard (x-vcard) is trivial."""
        assert is_trivial_attachment("contact.vcf", "text/x-vcard", 500) is True

    # GIFs
    def test_gif_is_trivial(self):
        """GIFs are trivial (typically animated reactions/logos)."""
        assert is_trivial_attachment("reaction.gif", "image/gif", 50000) is True

    # Generic filenames
    def test_generic_image_png_is_trivial(self):
        """Generic 'image.png' filename is trivial."""
        assert is_trivial_attachment("image.png", "image/png", 50000) is True

    def test_generic_image_jpg_is_trivial(self):
        """Generic 'image.jpg' filename is trivial."""
        assert is_trivial_attachment("image.jpg", "image/jpeg", 50000) is True

    def test_generic_image_numbered_is_trivial(self):
        """Generic numbered image filename is trivial."""
        assert is_trivial_attachment("image001.png", "image/png", 50000) is True
        assert is_trivial_attachment("image2.jpg", "image/jpeg", 50000) is True

    def test_generic_photo_is_trivial(self):
        """Generic 'photo' filename is trivial."""
        assert is_trivial_attachment("photo.png", "image/png", 50000) is True

    def test_generic_attachment_pdf_is_trivial(self):
        """Generic 'attachment.pdf' is trivial."""
        assert is_trivial_attachment("attachment.pdf", "application/pdf", 50000) is True

    def test_generic_document_is_trivial(self):
        """Generic 'document.pdf' is trivial."""
        assert is_trivial_attachment("document.pdf", "application/pdf", 50000) is True

    def test_generic_file_is_trivial(self):
        """Generic 'file' prefix is trivial."""
        assert is_trivial_attachment("file.pdf", "application/pdf", 50000) is True

    def test_untitled_is_trivial(self):
        """'Untitled' prefix is trivial."""
        assert is_trivial_attachment("untitled", "application/pdf", 50000) is True
        assert is_trivial_attachment("untitled.docx", "application/octet-stream", 50000) is True

    def test_screenshot_is_trivial(self):
        """Generic 'screenshot' filename is trivial."""
        assert is_trivial_attachment("screenshot.png", "image/png", 50000) is True

    # Small images
    def test_small_image_is_trivial(self):
        """Small images (<200KB) are trivial (logos/signatures)."""
        assert is_trivial_attachment("logo.png", "image/png", 50000) is True  # 50KB
        assert is_trivial_attachment("signature.png", "image/png", 100000) is True  # 100KB

    def test_small_jpeg_is_trivial(self):
        """Small JPEG is trivial."""
        assert is_trivial_attachment("headshot.jpg", "image/jpeg", 150000) is True

    # NOT trivial
    def test_large_image_is_not_trivial(self):
        """Large images (>200KB) with descriptive names are NOT trivial."""
        assert is_trivial_attachment("chart.png", "image/png", 250000) is False
        assert is_trivial_attachment("team_photo.jpg", "image/jpeg", 500000) is False

    def test_pdf_with_real_name_is_not_trivial(self):
        """PDF with descriptive name is NOT trivial."""
        assert is_trivial_attachment("Q4_Budget_Report.pdf", "application/pdf", 50000) is False

    def test_docx_with_real_name_is_not_trivial(self):
        """DOCX with descriptive name is NOT trivial."""
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert is_trivial_attachment("Project_Proposal.docx", mime, 50000) is False

    def test_xlsx_with_real_name_is_not_trivial(self):
        """XLSX with descriptive name is NOT trivial."""
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert is_trivial_attachment("Sales_Data_2024.xlsx", mime, 50000) is False

    def test_pptx_is_not_trivial(self):
        """PPTX is NOT trivial."""
        mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        assert is_trivial_attachment("Quarterly_Review.pptx", mime, 500000) is False

    def test_large_png_with_descriptive_name_is_not_trivial(self):
        """Large PNG with descriptive name is NOT trivial."""
        assert is_trivial_attachment("architecture_diagram.png", "image/png", 500000) is False

    # Case insensitivity
    def test_case_insensitive_filtering(self):
        """Filtering is case-insensitive."""
        assert is_trivial_attachment("IMAGE.PNG", "image/png", 50000) is True
        assert is_trivial_attachment("Image.Png", "image/png", 50000) is True
        assert is_trivial_attachment("PHOTO.JPG", "image/jpeg", 50000) is True


class TestFilterAttachments:
    """Tests for filter_attachments function."""

    def test_filters_trivial_attachments(self):
        """Filters out trivial attachments from list."""
        attachments = [
            {"filename": "report.pdf", "mime_type": "application/pdf", "size": 100000},
            {"filename": "image.png", "mime_type": "image/png", "size": 50000},  # trivial
            {"filename": "invite.ics", "mime_type": "text/calendar", "size": 500},  # trivial
            {"filename": "data.xlsx", "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "size": 200000},
        ]

        filtered = filter_attachments(attachments)

        assert len(filtered) == 2
        assert filtered[0]["filename"] == "report.pdf"
        assert filtered[1]["filename"] == "data.xlsx"

    def test_handles_camelcase_mime_type(self):
        """Handles both snake_case and camelCase mime type keys."""
        attachments = [
            {"filename": "report.pdf", "mimeType": "application/pdf", "size": 100000},
            {"filename": "image.png", "mimeType": "image/png", "size": 50000},  # trivial
        ]

        filtered = filter_attachments(attachments)

        assert len(filtered) == 1
        assert filtered[0]["filename"] == "report.pdf"

    def test_empty_list_returns_empty(self):
        """Empty input returns empty output."""
        assert filter_attachments([]) == []

    def test_all_trivial_returns_empty(self):
        """All trivial attachments returns empty list."""
        attachments = [
            {"filename": "image.png", "mime_type": "image/png", "size": 50000},
            {"filename": "invite.ics", "mime_type": "text/calendar", "size": 500},
        ]

        filtered = filter_attachments(attachments)
        assert filtered == []
