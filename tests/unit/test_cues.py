"""
Unit tests for cues and preview output.

Cues surface decision-tree signals in fetch responses so callers don't
need to read manifest.json or Glob the deposit folder.

Preview surfaces top results in search responses so callers don't need
to guess field names when writing jq queries.
"""

import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from tools.fetch import _build_cues, _build_email_context_metadata
from models import (
    FetchResult, FetchError, SearchResult, EmailContext,
    GmailThreadData, EmailMessage, EmailAttachment, WebData,
)


# ============================================================================
# _build_cues — the core cues builder
# ============================================================================


class TestBuildCues:
    """Tests for _build_cues function."""

    def test_minimal_cues_from_empty_folder(self, tmp_path: Path) -> None:
        """Empty folder produces cues with explicit nulls/empty values."""
        cues = _build_cues(tmp_path)

        assert cues["files"] == []
        assert cues["open_comment_count"] == 0
        assert cues["warnings"] == []
        assert cues["content_length"] == 0
        assert cues["email_context"] is None

    def test_files_listed_and_sorted(self, tmp_path: Path) -> None:
        """Files are listed alphabetically."""
        (tmp_path / "manifest.json").write_text("{}")
        (tmp_path / "content.md").write_text("hello")
        (tmp_path / "comments.md").write_text("comment")

        cues = _build_cues(tmp_path)

        assert cues["files"] == ["comments.md", "content.md", "manifest.json"]

    def test_content_length_from_md(self, tmp_path: Path) -> None:
        """Content length measured from content.md."""
        content = "# Title\n\nSome markdown content here."
        (tmp_path / "content.md").write_text(content)

        cues = _build_cues(tmp_path)

        assert cues["content_length"] == len(content.encode("utf-8"))

    def test_content_length_from_csv(self, tmp_path: Path) -> None:
        """Content length measured from content.csv (sheets)."""
        content = "col1,col2\na,b\n"
        (tmp_path / "content.csv").write_text(content)

        cues = _build_cues(tmp_path)

        assert cues["content_length"] == len(content.encode("utf-8"))

    def test_open_comment_count(self, tmp_path: Path) -> None:
        """Comment count passed through."""
        cues = _build_cues(tmp_path, open_comment_count=5)

        assert cues["open_comment_count"] == 5

    def test_warnings_passed_through(self, tmp_path: Path) -> None:
        """Warnings list passed through."""
        cues = _build_cues(tmp_path, warnings=["Truncated", "Unknown element"])

        assert cues["warnings"] == ["Truncated", "Unknown element"]

    def test_none_warnings_becomes_empty_list(self, tmp_path: Path) -> None:
        """None warnings normalized to empty list."""
        cues = _build_cues(tmp_path, warnings=None)

        assert cues["warnings"] == []

    def test_email_context_serialized(self, tmp_path: Path) -> None:
        """EmailContext converted to dict with hint."""
        ctx = EmailContext(
            message_id="19c05803e16f5f83",
            from_address="alice@example.com",
            subject="Budget Q4",
        )
        cues = _build_cues(tmp_path, email_context=ctx)

        ec = cues["email_context"]
        assert ec["message_id"] == "19c05803e16f5f83"
        assert ec["from"] == "alice@example.com"
        assert ec["subject"] == "Budget Q4"
        assert "fetch(" in ec["hint"]

    def test_email_context_none_is_explicit_null(self, tmp_path: Path) -> None:
        """No email context produces explicit None (not absent key)."""
        cues = _build_cues(tmp_path)

        assert "email_context" in cues
        assert cues["email_context"] is None

    def test_gmail_participants(self, tmp_path: Path) -> None:
        """Participants list included for Gmail fetches."""
        cues = _build_cues(
            tmp_path,
            participants=["alice@example.com", "bob@example.com"],
        )

        assert cues["participants"] == ["alice@example.com", "bob@example.com"]

    def test_gmail_has_attachments(self, tmp_path: Path) -> None:
        """Attachment flag included for Gmail fetches."""
        cues = _build_cues(tmp_path, has_attachments=True)

        assert cues["has_attachments"] is True

    def test_gmail_has_attachments_false(self, tmp_path: Path) -> None:
        """has_attachments=False is present (not omitted)."""
        cues = _build_cues(tmp_path, has_attachments=False)

        assert cues["has_attachments"] is False

    def test_gmail_date_range_single(self, tmp_path: Path) -> None:
        """Single-date range included."""
        cues = _build_cues(tmp_path, date_range="2026-02-01")

        assert cues["date_range"] == "2026-02-01"

    def test_gmail_date_range_span(self, tmp_path: Path) -> None:
        """Date range span included."""
        cues = _build_cues(tmp_path, date_range="2026-01-15 to 2026-02-09")

        assert cues["date_range"] == "2026-01-15 to 2026-02-09"

    def test_gmail_fields_absent_for_non_gmail(self, tmp_path: Path) -> None:
        """Gmail-specific fields absent when not provided (Drive files)."""
        cues = _build_cues(tmp_path, open_comment_count=2)

        assert "participants" not in cues
        assert "has_attachments" not in cues
        assert "date_range" not in cues

    def test_slides_with_thumbnails(self, tmp_path: Path) -> None:
        """Slides deposit with thumbnails lists all files."""
        (tmp_path / "manifest.json").write_text("{}")
        (tmp_path / "content.md").write_text("# Slide 1\n\nContent")
        (tmp_path / "slide_01.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        (tmp_path / "slide_03.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

        cues = _build_cues(tmp_path, open_comment_count=0, warnings=[])

        assert "slide_01.png" in cues["files"]
        assert "slide_03.png" in cues["files"]
        assert len(cues["files"]) == 4  # manifest + content + 2 thumbnails

    def test_many_thumbnails_collapsed(self, tmp_path: Path) -> None:
        """Many thumbnails collapsed into compact summary."""
        (tmp_path / "manifest.json").write_text("{}")
        (tmp_path / "content.md").write_text("# Slides")
        (tmp_path / "comments.md").write_text("# Comments")
        for i in range(1, 44):
            (tmp_path / f"slide_{i:02d}.png").write_bytes(b"\x89PNG")

        cues = _build_cues(tmp_path, open_comment_count=0, warnings=[])

        # Non-thumbnail files listed individually
        assert "content.md" in cues["files"]
        assert "manifest.json" in cues["files"]
        assert "comments.md" in cues["files"]
        # Thumbnails collapsed into summary
        assert any("43 thumbnails" in f for f in cues["files"])
        assert "slide_01.png ... slide_43.png (43 thumbnails)" in cues["files"]
        # No individual slide files
        assert "slide_01.png" not in cues["files"]
        assert "slide_22.png" not in cues["files"]
        # Total: 3 non-thumbnail + 1 summary = 4
        assert len(cues["files"]) == 4

    def test_three_thumbnails_listed_individually(self, tmp_path: Path) -> None:
        """Three or fewer thumbnails listed individually (no collapse)."""
        (tmp_path / "content.md").write_text("# Slides")
        for i in range(1, 4):
            (tmp_path / f"slide_{i:02d}.png").write_bytes(b"\x89PNG")

        cues = _build_cues(tmp_path)

        assert "slide_01.png" in cues["files"]
        assert "slide_02.png" in cues["files"]
        assert "slide_03.png" in cues["files"]
        assert len(cues["files"]) == 4  # content + 3 thumbnails

    def test_directories_excluded_from_files(self, tmp_path: Path) -> None:
        """Subdirectories not listed in files."""
        (tmp_path / "content.md").write_text("hello")
        (tmp_path / "subdir").mkdir()

        cues = _build_cues(tmp_path)

        assert "subdir" not in cues["files"]
        assert cues["files"] == ["content.md"]

    def test_nonexistent_folder(self) -> None:
        """Non-existent folder produces empty cues (no crash)."""
        cues = _build_cues(Path("/nonexistent/path/xyz"))

        assert cues["files"] == []
        assert cues["content_length"] == 0

    def test_string_folder_path(self, tmp_path: Path) -> None:
        """String folder paths are accepted."""
        (tmp_path / "content.md").write_text("test")

        cues = _build_cues(str(tmp_path))

        assert cues["files"] == ["content.md"]


# ============================================================================
# _build_email_context_metadata
# ============================================================================


class TestBuildEmailContextMetadata:
    """Tests for email context serialization."""

    def test_full_context(self) -> None:
        ctx = EmailContext(
            message_id="abc123",
            from_address="user@example.com",
            subject="Re: Project update",
        )
        result = _build_email_context_metadata(ctx)

        assert result is not None
        assert result["message_id"] == "abc123"
        assert result["from"] == "user@example.com"
        assert result["subject"] == "Re: Project update"
        assert "abc123" in result["hint"]

    def test_none_input(self) -> None:
        assert _build_email_context_metadata(None) is None

    def test_minimal_context(self) -> None:
        """Context with only required field."""
        ctx = EmailContext(message_id="msg1")
        result = _build_email_context_metadata(ctx)

        assert result is not None
        assert result["message_id"] == "msg1"
        assert result["from"] is None
        assert result["subject"] is None


# ============================================================================
# FetchResult.to_dict — cues in response
# ============================================================================


class TestFetchResultCuesInResponse:
    """Verify cues appear in FetchResult.to_dict() output."""

    def test_cues_always_present(self) -> None:
        """Cues dict always included, even when empty (explicit-null principle)."""
        result = FetchResult(
            path="/tmp/doc",
            content_file="/tmp/doc/content.md",
            format="markdown",
            type="doc",
            metadata={"title": "Test"},
        )
        d = result.to_dict()

        assert "cues" in d
        assert d["cues"] == {}

    def test_cues_populated(self) -> None:
        """Populated cues appear in response."""
        cues = {
            "files": ["content.md", "manifest.json"],
            "open_comment_count": 3,
            "warnings": [],
            "content_length": 500,
            "email_context": None,
        }
        result = FetchResult(
            path="/tmp/doc",
            content_file="/tmp/doc/content.md",
            format="markdown",
            type="doc",
            metadata={"title": "Test"},
            cues=cues,
        )
        d = result.to_dict()

        assert d["cues"]["open_comment_count"] == 3
        assert d["cues"]["files"] == ["content.md", "manifest.json"]
        assert d["cues"]["email_context"] is None

    def test_gmail_cues_with_type_specific_fields(self) -> None:
        """Gmail-specific cues appear alongside base cues."""
        cues = {
            "files": ["content.md"],
            "open_comment_count": 0,
            "warnings": [],
            "content_length": 1200,
            "email_context": None,
            "participants": ["alice@example.com", "bob@example.com"],
            "has_attachments": True,
            "date_range": "2026-01-15 to 2026-02-01",
        }
        result = FetchResult(
            path="/tmp/gmail",
            content_file="/tmp/gmail/content.md",
            format="markdown",
            type="gmail",
            metadata={"subject": "Test"},
            cues=cues,
        )
        d = result.to_dict()

        assert d["cues"]["participants"] == ["alice@example.com", "bob@example.com"]
        assert d["cues"]["has_attachments"] is True
        assert d["cues"]["date_range"] == "2026-01-15 to 2026-02-01"


# ============================================================================
# FetchError — no cues (sanity check)
# ============================================================================


class TestFetchErrorNoCues:
    """Errors don't have cues."""

    def test_error_response_shape(self) -> None:
        err = FetchError(kind="not_found", message="File not found")
        d = err.to_dict()

        assert d["error"] is True
        assert "cues" not in d


# ============================================================================
# SearchResult.to_dict — preview in response
# ============================================================================


class TestSearchResultPreview:
    """Tests for preview in search responses."""

    def test_preview_with_drive_results(self) -> None:
        """Drive results preview shows name, id, mimeType (top 5)."""
        result = SearchResult(
            query="Q4 planning",
            sources=["drive"],
            drive_results=[
                {"name": "Q4 Plan.docx", "id": "abc123", "mimeType": "application/vnd.google-apps.document"},
                {"name": "Q4 Budget.xlsx", "id": "def456", "mimeType": "application/vnd.google-apps.spreadsheet"},
                {"name": "Q4 Deck.pptx", "id": "ghi789", "mimeType": "application/vnd.google-apps.presentation"},
                {"name": "Q4 Notes.docx", "id": "jkl012", "mimeType": "application/vnd.google-apps.document"},
                {"name": "Q4 Review.docx", "id": "mno345", "mimeType": "application/vnd.google-apps.document"},
                {"name": "Q4 Extra.docx", "id": "pqr678", "mimeType": "application/vnd.google-apps.document"},
            ],
            path="/tmp/search.json",
        )
        d = result.to_dict()

        assert "preview" in d
        assert len(d["preview"]["drive"]) == 5  # max_per_source=5
        assert d["preview"]["drive"][0]["name"] == "Q4 Plan.docx"
        assert d["preview"]["drive"][0]["id"] == "abc123"
        assert d["preview"]["drive"][0]["mimeType"] == "application/vnd.google-apps.document"

    def test_preview_drive_includes_email_context(self) -> None:
        """Drive preview includes email_context when present (exfil'd files)."""
        result = SearchResult(
            query="report",
            sources=["drive"],
            drive_results=[
                {
                    "name": "report.pdf", "id": "abc123", "mimeType": "application/pdf",
                    "email_context": {"message_id": "19c058", "from": "alice@co.com", "subject": "Q4 report"},
                },
                {"name": "notes.docx", "id": "def456", "mimeType": "application/vnd.google-apps.document"},
            ],
            path="/tmp/search.json",
        )
        d = result.to_dict()

        assert d["preview"]["drive"][0]["email_context"]["message_id"] == "19c058"
        assert "email_context" not in d["preview"]["drive"][1]

    def test_preview_with_gmail_results(self) -> None:
        """Gmail results preview shows subject, thread_id, from, message_count."""
        result = SearchResult(
            query="budget",
            sources=["gmail"],
            gmail_results=[
                {"subject": "Re: Budget", "thread_id": "19c058", "from": "alice@example.com", "message_count": 4, "attachment_names": ["report.pdf"]},
                {"subject": "Budget v2", "thread_id": "19c059", "from": "bob@example.com", "message_count": 1},
            ],
            path="/tmp/search.json",
        )
        d = result.to_dict()

        assert "preview" in d
        assert len(d["preview"]["gmail"]) == 2
        assert d["preview"]["gmail"][0]["subject"] == "Re: Budget"
        assert d["preview"]["gmail"][0]["thread_id"] == "19c058"
        assert d["preview"]["gmail"][0]["from"] == "alice@example.com"
        assert d["preview"]["gmail"][0]["message_count"] == 4
        assert d["preview"]["gmail"][0]["attachment_names"] == ["report.pdf"]
        assert d["preview"]["gmail"][1]["message_count"] == 1
        assert "attachment_names" not in d["preview"]["gmail"][1]  # absent when empty

    def test_preview_with_both_sources(self) -> None:
        """Both sources produce separate preview sections."""
        result = SearchResult(
            query="project",
            sources=["drive", "gmail"],
            drive_results=[
                {"name": "Project Plan", "id": "abc", "mimeType": "application/vnd.google-apps.document"},
            ],
            gmail_results=[
                {"subject": "Re: Project", "thread_id": "19c", "from": "alice@example.com"},
            ],
            path="/tmp/search.json",
        )
        d = result.to_dict()

        assert "drive" in d["preview"]
        assert "gmail" in d["preview"]

    def test_preview_absent_when_no_results(self) -> None:
        """No preview when both sources return empty."""
        result = SearchResult(
            query="nonexistent",
            sources=["drive", "gmail"],
            drive_results=[],
            gmail_results=[],
            path="/tmp/search.json",
        )
        d = result.to_dict()

        assert "preview" not in d

    def test_preview_handles_missing_fields_gracefully(self) -> None:
        """Preview doesn't crash on results with missing keys."""
        result = SearchResult(
            query="test",
            sources=["drive"],
            drive_results=[
                {"name": "Test", "id": "abc"},  # missing mimeType
            ],
            path="/tmp/search.json",
        )
        d = result.to_dict()

        assert d["preview"]["drive"][0]["mimeType"] == ""  # defaults to empty string

    def test_filesystem_first_response_shape(self) -> None:
        """Filesystem-first response includes path, counts, preview."""
        result = SearchResult(
            query="test",
            sources=["drive", "gmail"],
            drive_results=[{"name": "A", "id": "1", "mimeType": "doc"}],
            gmail_results=[{"subject": "B", "thread_id": "2", "from": "x@y.com"}],
            path="/tmp/search.json",
        )
        d = result.to_dict()

        assert d["path"] == "/tmp/search.json"
        assert d["drive_count"] == 1
        assert d["gmail_count"] == 1
        assert "query" in d
        assert "sources" in d

    def test_legacy_response_has_no_preview(self) -> None:
        """Legacy (no path) response returns full results, no preview."""
        result = SearchResult(
            query="test",
            sources=["drive"],
            drive_results=[{"name": "A", "id": "1"}],
        )
        d = result.to_dict()

        assert "preview" not in d
        assert "drive_results" in d


# ============================================================================
# Integration: cues built during fetch_doc
# ============================================================================


def _drive_metadata(mime_type: str) -> dict:
    """Helper to build minimal Drive metadata dict."""
    return {"mimeType": mime_type, "name": "test-file"}


class TestFetchDocCues:
    """Verify cues are populated correctly during doc fetch."""

    @patch("tools.fetch.drive.fetch_document")
    @patch("tools.fetch.drive.extract_doc_content", return_value="# Doc Content")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(3, "comments"))
    @patch("tools.fetch.drive.write_manifest")
    def test_doc_cues_with_comments(
        self, mock_manifest, mock_comments, mock_write, mock_extract, mock_fetch,
        tmp_path: Path,
    ) -> None:
        """Doc fetch cues include comment count and files."""
        mock_doc = MagicMock()
        mock_doc.tabs = [MagicMock()]
        mock_doc.warnings = ["Unknown element type"]
        mock_fetch.return_value = mock_doc

        # Create real deposit folder so _build_cues can iterate
        content_file = tmp_path / "content.md"
        content_file.write_text("# Doc Content")
        mock_write.return_value = content_file

        with patch("tools.fetch.drive.get_deposit_folder", return_value=tmp_path):
            result = fetch_doc("doc1", "My Doc", _drive_metadata("application/vnd.google-apps.document"))

        assert isinstance(result, FetchResult)
        cues = result.cues
        assert cues["open_comment_count"] == 3
        assert cues["warnings"] == ["Unknown element type"]
        assert "content.md" in cues["files"]
        assert cues["content_length"] > 0
        assert cues["email_context"] is None
        # Gmail fields should NOT be present
        assert "participants" not in cues
        assert "has_attachments" not in cues

    @patch("tools.fetch.drive.fetch_document")
    @patch("tools.fetch.drive.extract_doc_content", return_value="# Doc")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    def test_doc_cues_with_email_context(
        self, mock_manifest, mock_comments, mock_write, mock_extract, mock_fetch,
        tmp_path: Path,
    ) -> None:
        """Email context in cues when file was pre-exfiltrated."""
        mock_doc = MagicMock()
        mock_doc.tabs = [MagicMock()]
        mock_doc.warnings = []
        mock_fetch.return_value = mock_doc
        mock_write.return_value = tmp_path / "content.md"
        (tmp_path / "content.md").write_text("# Doc")
        ctx = EmailContext(message_id="m1", from_address="a@b.com", subject="Re: test")

        with patch("tools.fetch.drive.get_deposit_folder", return_value=tmp_path):
            result = fetch_doc("doc1", "My Doc", _drive_metadata("application/vnd.google-apps.document"), email_context=ctx)

        ec = result.cues["email_context"]
        assert ec is not None
        assert ec["message_id"] == "m1"
        assert ec["from"] == "a@b.com"


# Need to import fetch functions used in integration tests
from tools.fetch import fetch_doc, fetch_sheet, fetch_slides, fetch_web


# ============================================================================
# Integration: cues built during fetch_sheet
# ============================================================================


class TestFetchSheetCues:
    """Verify cues are populated correctly during sheet fetch."""

    @patch("tools.fetch.drive.fetch_spreadsheet")
    @patch("tools.fetch.drive.extract_sheets_content", return_value="col1,col2\n1,2")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_chart")
    @patch("tools.fetch.drive.write_charts_metadata")
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(1, "comments"))
    @patch("tools.fetch.drive.write_manifest")
    @patch("tools.fetch.drive._write_per_tab_csvs", return_value=[])
    def test_sheet_cues(
        self, mock_tabs, mock_manifest, mock_comments, mock_charts_meta, mock_chart,
        mock_write, mock_extract, mock_fetch, tmp_path: Path,
    ) -> None:
        """Sheet cues include comment count."""
        mock_data = MagicMock()
        mock_data.charts = []
        mock_data.warnings = []
        mock_data.chart_render_time_ms = 0
        mock_fetch.return_value = mock_data

        content_file = tmp_path / "content.csv"
        content_file.write_text("col1,col2\n1,2")
        mock_write.return_value = content_file

        with patch("tools.fetch.drive.get_deposit_folder", return_value=tmp_path):
            result = fetch_sheet("sheet1", "My Sheet", _drive_metadata("application/vnd.google-apps.spreadsheet"))

        cues = result.cues
        assert cues["open_comment_count"] == 1
        assert "content.csv" in cues["files"]
        assert cues["content_length"] > 0

    @patch("tools.fetch.drive.fetch_spreadsheet")
    @patch("tools.fetch.drive.extract_sheets_content", return_value="combined")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    @patch("tools.fetch.drive._write_per_tab_csvs", return_value=[])
    def test_sheet_cues_multi_tab(
        self, mock_tabs, mock_manifest, mock_comments,
        mock_write, mock_extract, mock_fetch, tmp_path: Path,
    ) -> None:
        """Multi-tab sheet cues include tab_count and tab_names."""
        tab1 = MagicMock()
        tab1.name = "Revenue"
        tab2 = MagicMock()
        tab2.name = "Costs"
        mock_data = MagicMock()
        mock_data.sheets = [tab1, tab2]
        mock_data.charts = []
        mock_data.warnings = []
        mock_fetch.return_value = mock_data

        content_file = tmp_path / "content.csv"
        content_file.write_text("combined")
        mock_write.return_value = content_file

        with patch("tools.fetch.drive.get_deposit_folder", return_value=tmp_path):
            result = fetch_sheet("sheet1", "Multi", _drive_metadata("application/vnd.google-apps.spreadsheet"))

        cues = result.cues
        assert cues["tab_count"] == 2
        assert cues["tab_names"] == ["Revenue", "Costs"]


# ============================================================================
# Integration: cues built during fetch_slides
# ============================================================================


class TestFetchSlidesCues:
    """Verify cues are populated correctly during slides fetch."""

    @patch("tools.fetch.drive.fetch_presentation")
    @patch("tools.fetch.drive.extract_slides_content", return_value="# Slide 1\n\nContent")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_thumbnail")
    @patch("tools.fetch.drive._enrich_with_comments", return_value=(0, None))
    @patch("tools.fetch.drive.write_manifest")
    def test_slides_cues_with_thumbnails(
        self, mock_manifest, mock_comments, mock_thumb, mock_write,
        mock_extract, mock_fetch, tmp_path: Path,
    ) -> None:
        """Slides cues list thumbnail files."""
        slide1 = MagicMock()
        slide1.thumbnail_bytes = b"\x89PNG"
        slide1.index = 0
        slide2 = MagicMock()
        slide2.thumbnail_bytes = None
        slide2.index = 1

        mock_data = MagicMock()
        mock_data.slides = [slide1, slide2]
        mock_data.warnings = []
        mock_data.thumbnails_included = True
        mock_fetch.return_value = mock_data

        content_file = tmp_path / "content.md"
        content_file.write_text("# Slide 1\n\nContent")
        mock_write.return_value = content_file
        # Simulate thumbnail being written
        (tmp_path / "slide_01.png").write_bytes(b"\x89PNG")

        with patch("tools.fetch.drive.get_deposit_folder", return_value=tmp_path):
            result = fetch_slides("pres1", "My Deck", _drive_metadata("application/vnd.google-apps.presentation"))

        cues = result.cues
        assert "slide_01.png" in cues["files"]
        assert cues["open_comment_count"] == 0


# ============================================================================
# Integration: cues built during fetch_web
# ============================================================================


class TestFetchWebCues:
    """Verify cues are populated correctly during web fetch."""

    @patch("tools.fetch.web.fetch_web_content")
    @patch("tools.fetch.web.extract_web_content", return_value="# Extracted Article")
    @patch("tools.fetch.web.extract_title", return_value="Article Title")
    @patch("tools.fetch.web.write_content")
    @patch("tools.fetch.web.write_manifest")
    def test_web_cues_basic(
        self, mock_manifest, mock_write, mock_title, mock_extract, mock_fetch,
        tmp_path: Path,
    ) -> None:
        """Web fetch cues have no comments or email context."""
        web_data = WebData(
            url="https://example.com/article",
            html="<html><body>Hello</body></html>",
            final_url="https://example.com/article",
            status_code=200,
            content_type="text/html",
            cookies_used=False,
            render_method="http",
        )
        mock_fetch.return_value = web_data

        content_file = tmp_path / "content.md"
        content_file.write_text("# Extracted Article")
        mock_write.return_value = content_file

        with patch("tools.fetch.web.get_deposit_folder", return_value=tmp_path):
            result = fetch_web("https://example.com/article")

        cues = result.cues
        assert cues["email_context"] is None
        assert cues["open_comment_count"] == 0
        assert "content.md" in cues["files"]
        assert cues["content_length"] > 0
        # Web fetches shouldn't have Gmail fields
        assert "participants" not in cues

    @patch("tools.fetch.web.fetch_web_content")
    @patch("tools.fetch.web.extract_web_content", return_value="# Article")
    @patch("tools.fetch.web.extract_title", return_value="Title")
    @patch("tools.fetch.web.write_content")
    @patch("tools.fetch.web.write_manifest")
    def test_web_cues_with_warnings(
        self, mock_manifest, mock_write, mock_title, mock_extract, mock_fetch,
        tmp_path: Path,
    ) -> None:
        """Warnings from web adapter appear in cues."""
        web_data = WebData(
            url="https://example.com",
            html="<html><body>Hello</body></html>",
            final_url="https://example.com/redirected",
            status_code=200,
            content_type="text/html",
            cookies_used=False,
            render_method="http",
            warnings=["Redirected to different URL"],
        )
        mock_fetch.return_value = web_data
        mock_write.return_value = tmp_path / "content.md"
        (tmp_path / "content.md").write_text("# Article")

        with patch("tools.fetch.web.get_deposit_folder", return_value=tmp_path):
            result = fetch_web("https://example.com")

        assert "Redirected to different URL" in result.cues["warnings"]
