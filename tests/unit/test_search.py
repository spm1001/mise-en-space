"""
Tests for search tool implementation.

Tests format functions (pure) and do_search wiring (mocked adapters).
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from models import (
    ActivityActor,
    ActivitySearchResult,
    ActivityTarget,
    CalendarAttachment,
    CalendarAttendee,
    CalendarEvent,
    CalendarSearchResult,
    CommentActivity,
    DriveSearchResult,
    DriveSearchResults,
    SearchResult,
    GmailSearchResult,
    GmailSearchResults,
    EmailContext,
    MiseError,
    ErrorKind,
)


@pytest.fixture(autouse=True)
def _not_guest_mode(monkeypatch):
    """This module states its credential env explicitly.

    tests/unit/conftest.py sets MISE_TOKEN_PATH for hermeticity, but that
    flips mise into guest mode, where omitted search sources default to
    ['drive'] — the very contract several tests here pin for NORMAL mode.
    The guest-mode test below sets the var itself, which overrides this.
    """
    monkeypatch.delenv("MISE_TOKEN_PATH", raising=False)
from tools.search import (
    format_drive_result,
    format_gmail_result,
    format_activity_result,
    format_calendar_result,
    _build_meeting_context_index,
    _enrich_drive_results_with_meetings,
    do_search,
)


# ============================================================================
# FORMAT FUNCTIONS (pure, no mocks needed)
# ============================================================================


class TestFormatDriveResult:
    """Test Drive result serialization."""

    def test_basic_fields(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Test Doc",
            mime_type="application/vnd.google-apps.document",
            modified_time=datetime(2026, 1, 15, 10, 30),
            owners=["alice@example.com"],
            web_view_link="https://docs.google.com/document/d/abc123",
        )
        formatted = format_drive_result(result)

        assert formatted["id"] == "abc123"
        assert formatted["name"] == "Test Doc"
        assert formatted["mimeType"] == "application/vnd.google-apps.document"
        assert formatted["modified"] == "2026-01-15T10:30:00"
        assert formatted["owners"] == ["alice@example.com"]
        assert formatted["url"] == "https://docs.google.com/document/d/abc123"

    def test_created_time_included(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Test Doc",
            mime_type="application/vnd.google-apps.document",
            created_time=datetime(2025, 12, 1, 9, 0),
            modified_time=datetime(2026, 1, 15, 10, 30),
        )
        formatted = format_drive_result(result)
        assert formatted["created"] == "2025-12-01T09:00:00"
        assert formatted["modified"] == "2026-01-15T10:30:00"

    def test_none_dates(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Test Doc",
            mime_type="application/pdf",
        )
        formatted = format_drive_result(result)
        assert formatted["created"] is None
        assert formatted["modified"] is None

    def test_snippet_included(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Test Doc",
            mime_type="text/plain",
            snippet="...matching text...",
        )
        formatted = format_drive_result(result)
        assert formatted["snippet"] == "...matching text..."

    def test_email_context_included(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Attachment.pdf",
            mime_type="application/pdf",
            email_context=EmailContext(
                message_id="thread789",
                from_address="bob@example.com",
                subject="Re: Project Update",
            ),
        )
        formatted = format_drive_result(result)

        assert "email_context" in formatted
        assert formatted["email_context"]["message_id"] == "thread789"
        assert formatted["email_context"]["from"] == "bob@example.com"
        assert formatted["email_context"]["subject"] == "Re: Project Update"
        assert "fetch" in formatted["email_context"]["hint"]

    def test_no_email_context(self) -> None:
        result = DriveSearchResult(
            file_id="abc123",
            name="Test Doc",
            mime_type="text/plain",
        )
        formatted = format_drive_result(result)
        assert "email_context" not in formatted


class TestFormatGmailResult:
    """Test Gmail result serialization."""

    def test_basic_fields(self) -> None:
        result = GmailSearchResult(
            thread_id="thread456",
            subject="Weekly Update",
            snippet="Here's the latest...",
            date=datetime(2026, 2, 1, 9, 0),
            from_address="alice@example.com",
            message_count=3,
            has_attachments=True,
            attachment_names=["report.pdf", "data.xlsx"],
        )
        formatted = format_gmail_result(result)

        assert formatted["thread_id"] == "thread456"
        assert formatted["subject"] == "Weekly Update"
        assert formatted["date"] == "2026-02-01T09:00:00"
        assert formatted["from"] == "alice@example.com"
        assert formatted["message_count"] == 3
        assert formatted["has_attachments"] is True
        assert formatted["attachment_names"] == ["report.pdf", "data.xlsx"]

    def test_none_date(self) -> None:
        result = GmailSearchResult(
            thread_id="t1",
            subject="No date",
            snippet="",
        )
        formatted = format_gmail_result(result)
        assert formatted["date"] is None

    def test_label_fields_included(self) -> None:
        result = GmailSearchResult(
            thread_id="t1",
            subject="Labelled",
            snippet="...",
            is_unread=True,
            label_ids=["INBOX", "UNREAD", "IMPORTANT"],
        )
        formatted = format_gmail_result(result)
        assert formatted["is_unread"] is True
        assert formatted["labels"] == ["INBOX", "UNREAD", "IMPORTANT"]

    def test_label_fields_defaults(self) -> None:
        result = GmailSearchResult(
            thread_id="t1",
            subject="No labels",
            snippet="",
        )
        formatted = format_gmail_result(result)
        assert formatted["is_unread"] is False
        assert formatted["labels"] == []

    def test_web_link_present_when_other_party_visible(self) -> None:
        """A thread with another party at an endpoint gets a clickable URL (mise-hetaba)."""
        result = GmailSearchResult(
            thread_id="19b0e7fe6f653f69",
            subject="From a colleague",
            snippet="",
            from_address="Alice <alice@example.com>",
            last_sender="me@example.com",
        )
        with patch('adapters.gmail.current_user_email', return_value="me@example.com"):
            formatted = format_gmail_result(result)
        assert formatted["web_link"] == (
            "https://mail.google.com/mail/#all/FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph"
        )

    def test_web_link_absent_for_possibly_self_sent(self) -> None:
        """Both endpoints the user's own → may be thread-a, no derivable token (mise-lerulo)."""
        result = GmailSearchResult(
            thread_id="19b0e7fe6f653f69",
            subject="Note to self",
            snippet="",
            from_address="me@example.com",
            last_sender="me@example.com",
        )
        with patch('adapters.gmail.current_user_email', return_value="me@example.com"):
            formatted = format_gmail_result(result)
        assert "web_link" not in formatted

    def test_web_link_absent_when_identity_unresolved(self) -> None:
        """Tri-state discipline: can't-tell must not mint a possibly-wrong link."""
        result = GmailSearchResult(
            thread_id="19b0e7fe6f653f69",
            subject="Whoever",
            snippet="",
            from_address="alice@example.com",
            last_sender="alice@example.com",
        )
        with patch('adapters.gmail.current_user_email', return_value=None):
            formatted = format_gmail_result(result)
        assert "web_link" not in formatted

    def test_latest_message_signals_exposed(self) -> None:
        """last_sender/from_me/unread_count reach the serialized result (mise-samono)."""
        result = GmailSearchResult(
            thread_id="t1",
            subject="Whose move?",
            snippet="latest text",
            from_address="originator@example.com",
            last_sender="Sarah <sarah@example.com>",
            from_me=False,
            unread_count=5,
        )
        formatted = format_gmail_result(result)
        assert formatted["last_sender"] == "Sarah <sarah@example.com>"
        assert formatted["from_me"] is False
        assert formatted["unread_count"] == 5

    def test_latest_message_signals_defaults(self) -> None:
        """Defaults: last_sender None, from_me None (tri-state), unread_count 0."""
        formatted = format_gmail_result(
            GmailSearchResult(thread_id="t1", subject="", snippet="")
        )
        assert formatted["last_sender"] is None
        assert formatted["from_me"] is None
        assert formatted["unread_count"] == 0


# ============================================================================
# do_search WIRING (mocked adapters)
# ============================================================================


class TestDoSearch:
    """Test search orchestration and error handling."""

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_both_sources_default(self, mock_drive, mock_gmail, mock_write) -> None:
        """Default searches both Drive and Gmail."""
        mock_drive.return_value = DriveSearchResults(results=[
            DriveSearchResult(file_id="d1", name="Doc", mime_type="text/plain"),
        ])
        mock_gmail.return_value = GmailSearchResults(results=[
            GmailSearchResult(thread_id="t1", subject="Email", snippet="..."),
        ])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test query")

        assert result.query == "test query"
        assert result.sources == ["drive", "gmail"]
        assert len(result.drive_results) == 1
        assert len(result.gmail_results) == 1
        assert result.drive_results[0]["id"] == "d1"
        assert result.gmail_results[0]["thread_id"] == "t1"
        mock_drive.assert_called_once()
        mock_gmail.assert_called_once()

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_gmail_truncated_adds_cue(self, mock_drive, mock_gmail, mock_write) -> None:
        """Truncated Gmail results add a warning cue."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_gmail.return_value = GmailSearchResults(
            results=[
                GmailSearchResult(thread_id="t1", subject="Email", snippet="..."),
            ],
            truncated=True,
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert "gmail_truncated" in result.cues
        assert "capped at 1" in result.cues["gmail_truncated"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_drive_truncated_adds_cue(self, mock_drive, mock_gmail, mock_write) -> None:
        """Drive now reports a ceiling the way Gmail always has (mise-werevi).

        The asymmetry was the whole bug: Gmail said 'more exist', Drive said
        nothing, and the silent one is what produced a false 'no such document'
        in a regulatory filing record.
        """
        mock_drive.return_value = DriveSearchResults(
            results=[DriveSearchResult(file_id="d1", name="Doc", mime_type="text/plain")],
            truncated=True,
        )
        mock_gmail.return_value = GmailSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert "drive_truncated" in result.cues
        assert "ceiling, not a population" in result.cues["drive_truncated"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_untruncated_drive_gains_no_cue(self, mock_drive, mock_gmail, mock_write) -> None:
        """Regression guard: a complete result set must not cry wolf."""
        mock_drive.return_value = DriveSearchResults(
            results=[DriveSearchResult(file_id="d1", name="Doc", mime_type="text/plain")],
            truncated=False,
        )
        mock_gmail.return_value = GmailSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert "drive_truncated" not in result.cues


class TestRawQuery:
    """mise-decaza — Drive's query language, which one fullText clause can't say."""

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_raw_query_passes_through_unescaped(self, mock_drive, mock_write) -> None:
        """The whole point: `or` survives. escape_drive_query would have turned
        this into a literal search for the string \"name contains 'PCA'\"."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search(raw_query="name contains 'PCA' or name contains 'GeoX'", sources=["drive"])

        sent = mock_drive.call_args.args[0]
        assert "name contains 'PCA' or name contains 'GeoX'" in sent
        assert "\\'" not in sent          # not escaped
        assert "fullText contains" not in sent   # replaced the clause, not wrapped by it
        assert "trashed = false" in sent  # still ANDed on

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_raw_query_is_parenthesised(self, mock_drive, mock_write) -> None:
        """A top-level `or` inside raw_query must not rebind against the clauses
        we AND on — `trashed = false and a or b` would match trashed files."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search(raw_query="name contains 'a' or name contains 'b'", sources=["drive"])

        sent = mock_drive.call_args.args[0]
        assert "(name contains 'a' or name contains 'b')" in sent

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_raw_query_composes_with_type(self, mock_drive, mock_write) -> None:
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search(raw_query="name contains 'PCA'", sources=["drive"], type="slides")

        sent = mock_drive.call_args.args[0]
        assert "name contains 'PCA'" in sent
        assert "application/vnd.google-apps.presentation" in sent

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_raw_query_scopes_to_drive(self, mock_drive, mock_gmail, mock_write) -> None:
        """Load-bearing, not tidy: `query` is empty alongside raw_query, and an
        empty Gmail query matches the whole mailbox rather than nothing."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search(raw_query="name contains 'PCA'")

        assert result.sources == ["drive"]
        mock_gmail.assert_not_called()
        assert "raw_query scopes to Drive only" in result.cues["sources_note"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_raw_query_labels_the_deposit(self, mock_drive, mock_write) -> None:
        """Otherwise an empty slug lands on disk, since `query` is unset.

        Both halves matter, and the second one shipped broken in 1.25.0: the
        SearchResult was labelled correctly while write_search_results still got
        the raw `query` param, so every raw search deposited as 'search--untitled'.
        Caught by looking at a real filename, not by any test — which is why the
        filename is asserted here and not just the label.
        """
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search(raw_query="name contains 'PCA'")

        assert result.query == "name contains 'PCA'"
        assert mock_write.call_args.args[0] == "name contains 'PCA'"


class TestPreviewPartialCue:
    """The preview is the only thing an LLM caller reliably reads, so it must
    advertise its own incompleteness. Distinct from drive_truncated: this is
    'shown vs fetched', that is 'fetched vs matched'."""

    def _results(self, n: int) -> SearchResult:
        r = SearchResult(query="q", sources=["drive"])
        r.drive_results = [
            {"id": f"d{i}", "name": f"Doc {i}", "mimeType": "text/plain", "modified": None}
            for i in range(n)
        ]
        r.path = "/tmp/fake/search-results.json"
        return r

    def test_preview_shorter_than_count_says_so(self) -> None:
        d = self._results(25).to_dict()
        assert len(d["preview"]["drive"]) == 5
        assert d["drive_count"] == 25
        assert "showing 5 of 25" in d["cues"]["preview_partial"]
        assert "/tmp/fake/search-results.json" in d["cues"]["preview_partial"]

    def test_fully_previewed_search_gains_no_cue(self) -> None:
        """Positive control — without this the cue could be unconditional."""
        d = self._results(3).to_dict()
        assert len(d["preview"]["drive"]) == 3
        assert "preview_partial" not in d["cues"]

    def test_both_cues_can_fire_together(self) -> None:
        """5 shown of 100 fetched of more-than-that matched — three different
        numbers, and the caller needs all three to reason about absence."""
        r = self._results(100)
        r.cues["drive_truncated"] = "Results capped at 100 — MORE MATCHED."
        d = r.to_dict()
        assert "showing 5 of 100" in d["cues"]["preview_partial"]
        assert "MORE MATCHED" in d["cues"]["drive_truncated"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_drive_only(self, mock_drive, mock_gmail, mock_write) -> None:
        """Only Drive searched when sources=['drive']."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive"])

        mock_drive.assert_called_once()
        mock_gmail.assert_not_called()
        assert result.sources == ["drive"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    @patch('tools.search.override_path')
    def test_guest_mode_defaults_to_drive_only(
        self, mock_override, mock_drive, mock_gmail, mock_write
    ) -> None:
        """Guest mode (MISE_TOKEN_PATH set) defaults omitted sources to Drive only.

        The caller-owned credential has no Gmail scope, so an omitted-sources
        search must not attempt Gmail (mise-kivane).
        """
        mock_override.return_value = Path("/tmp/guest-token.json")
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        mock_drive.assert_called_once()
        mock_gmail.assert_not_called()
        assert result.sources == ["drive"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    @patch('tools.search.override_path')
    def test_guest_mode_honours_explicit_sources(
        self, mock_override, mock_drive, mock_gmail, mock_write
    ) -> None:
        """Explicit sources override the guest-mode default — opt-in still works."""
        mock_override.return_value = Path("/tmp/guest-token.json")
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_gmail.return_value = GmailSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "gmail"])

        mock_drive.assert_called_once()
        mock_gmail.assert_called_once()
        assert result.sources == ["drive", "gmail"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    @patch('tools.search.ambient_mode')
    def test_ambient_mode_defaults_to_drive_only(
        self, mock_ambient, mock_drive, mock_gmail, mock_write
    ) -> None:
        """Ambient mode defaults omitted sources to Drive only (mise-wasagu).

        Same narrowing as guest mode, different reason: a service account
        has no Gmail mailbox at all.
        """
        mock_ambient.return_value = True
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        mock_drive.assert_called_once()
        mock_gmail.assert_not_called()
        assert result.sources == ["drive"]
        assert not result.errors  # narrowing an OMITTED list needs no error

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    @patch('tools.search.ambient_mode')
    def test_ambient_mode_refuses_explicit_gmail_with_teaching_text(
        self, mock_ambient, mock_drive, mock_gmail, mock_write
    ) -> None:
        """An EXPLICIT non-Drive source in ambient mode gets a teaching
        errors[] entry, not a silent narrowing and not an opaque Google 403
        (mise-wasagu). Unlike guest mode, explicit opt-in cannot work here —
        there is no mailbox behind the credential at any scope."""
        mock_ambient.return_value = True
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "gmail", "people"])

        mock_gmail.assert_not_called()
        assert result.sources == ["drive"]
        (error,) = result.errors
        assert "ambient (service-account) mode" in error
        assert "Gmail" in error and "People" in error
        assert "no Gmail mailbox" in error

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_gmail_only(self, mock_drive, mock_gmail, mock_write) -> None:
        """Only Gmail searched when sources=['gmail']."""
        mock_gmail.return_value = GmailSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["gmail"])

        mock_drive.assert_not_called()
        mock_gmail.assert_called_once()
        assert result.sources == ["gmail"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    def test_calendar_gets_query_and_truncation_cue(self, mock_calendar, mock_write) -> None:
        """Calendar receives the user's query and a truncated result raises
        the calendar_truncated cue (mise-bidopi)."""
        from models import CalendarSearchResult
        mock_calendar.return_value = CalendarSearchResult(events=[], truncated=True)
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("Gareth", sources=["calendar"])

        assert mock_calendar.call_args.kwargs["query"] == "Gareth"
        assert "calendar_truncated" in result.cues
        assert "nearest to now" in result.cues["calendar_truncated"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    def test_calendar_no_cue_when_complete(self, mock_calendar, mock_write) -> None:
        """No truncation cue when the window fit within the cap."""
        from models import CalendarSearchResult
        mock_calendar.return_value = CalendarSearchResult(events=[], truncated=False)
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("Gareth", sources=["calendar"])

        assert "calendar_truncated" not in result.cues

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_drive_error_doesnt_block_gmail(self, mock_drive, mock_gmail, mock_write) -> None:
        """Drive failure still returns Gmail results."""
        mock_drive.side_effect = MiseError(ErrorKind.RATE_LIMITED, "API quota exceeded")
        mock_gmail.return_value = GmailSearchResults(results=[
            GmailSearchResult(thread_id="t1", subject="Email", snippet="..."),
        ])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert len(result.gmail_results) == 1
        assert len(result.errors) == 1
        assert "Drive" in result.errors[0]
        assert "quota" in result.errors[0]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_gmail_error_doesnt_block_drive(self, mock_drive, mock_gmail, mock_write) -> None:
        """Gmail failure still returns Drive results."""
        mock_drive.return_value = DriveSearchResults(results=[
            DriveSearchResult(file_id="d1", name="Doc", mime_type="text/plain"),
        ])
        mock_gmail.side_effect = Exception("connection reset")
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert len(result.drive_results) == 1
        assert len(result.errors) == 1
        assert "Gmail" in result.errors[0]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_gmail_mise_error_captured(self, mock_drive, mock_gmail, mock_write) -> None:
        """Gmail MiseError uses e.message in error string."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_gmail.side_effect = MiseError(ErrorKind.RATE_LIMITED, "quota exceeded")
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert len(result.errors) == 1
        assert "quota exceeded" in result.errors[0]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_both_fail_returns_errors(self, mock_drive, mock_gmail, mock_write) -> None:
        """Both sources failing returns both errors."""
        mock_drive.side_effect = Exception("drive boom")
        mock_gmail.side_effect = Exception("gmail boom")
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert len(result.errors) == 2
        assert len(result.drive_results) == 0
        assert len(result.gmail_results) == 0

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_max_results_passed_through(self, mock_drive, mock_write) -> None:
        """max_results parameter forwarded to adapter."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search("test", sources=["drive"], max_results=5)

        _, kwargs = mock_drive.call_args
        assert kwargs["max_results"] == 5

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_query_escaped_for_drive(self, mock_drive, mock_write) -> None:
        """Drive query has single quotes escaped."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search("user's report", sources=["drive"])

        call_args = mock_drive.call_args[0][0]
        # escape_drive_query escapes single quotes
        assert "\\'" in call_args

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_results_deposited_to_file(self, mock_drive, mock_gmail, mock_write) -> None:
        """Results written to filesystem via write_search_results."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_gmail.return_value = GmailSearchResults(results=[])
        mock_write.return_value = "/workspace/mise/search-results.json"

        result = do_search("test")

        mock_write.assert_called_once()
        assert result.path == "/workspace/mise/search-results.json"


# ============================================================================
# SCOPED SEARCH (folder_id)
# ============================================================================


class TestScopedSearch:
    """Test folder-scoped search via folder_id parameter."""

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_folder_id_passed_to_adapter(self, mock_drive, mock_write) -> None:
        """folder_id is forwarded to search_files."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search("GA4", sources=["drive"], folder_id="abc123")

        _, kwargs = mock_drive.call_args
        assert kwargs.get("folder_id") == "abc123"

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_folder_id_forces_drive_only(self, mock_drive, mock_gmail, mock_write) -> None:
        """When folder_id set, Gmail is excluded even if in sources."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive", "gmail"], folder_id="abc123")

        mock_gmail.assert_not_called()
        assert "gmail" not in result.sources

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_scope_note_in_cues_with_results(self, mock_drive, mock_write) -> None:
        """Scope note present in cues even when results are found."""
        mock_drive.return_value = DriveSearchResults(results=[
            DriveSearchResult(file_id="f1", name="tv-conversions.md", mime_type="text/markdown"),
        ])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive"], folder_id="folder123")

        assert "scope" in result.cues
        assert "non-recursive" in result.cues["scope"]
        assert "folder123" in result.cues["scope"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_scope_note_in_cues_zero_results(self, mock_drive, mock_write) -> None:
        """Scope note present in cues even on zero results."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive"], folder_id="folder456")

        assert "scope" in result.cues
        assert "non-recursive" in result.cues["scope"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_no_scope_note_without_folder_id(self, mock_drive, mock_gmail, mock_write) -> None:
        """No cues when folder_id not set (unscoped search)."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_gmail.return_value = GmailSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test")

        assert not result.cues  # empty dict

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_cues_in_to_dict_output(self, mock_drive, mock_write) -> None:
        """Cues appear in the MCP response dict when set."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive"], folder_id="folder789")
        d = result.to_dict()

        assert "cues" in d
        assert "scope" in d["cues"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_sources_note_when_gmail_dropped(self, mock_drive, mock_gmail, mock_write) -> None:
        """sources_note present in cues when Gmail is excluded due to folder_id."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive", "gmail"], folder_id="folder123")

        assert "sources_note" in result.cues
        assert "Gmail" in result.cues["sources_note"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_files')
    def test_no_sources_note_when_drive_only_from_start(self, mock_drive, mock_write) -> None:
        """No sources_note when caller already requested drive-only with folder_id."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("GA4", sources=["drive"], folder_id="folder123")

        assert "sources_note" not in result.cues

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_unscoped_search_unchanged(self, mock_drive, mock_gmail, mock_write) -> None:
        """folder_id=None produces identical behaviour to omitting it."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_gmail.return_value = GmailSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result_none = do_search("test", folder_id=None)
        result_omit = do_search("test")

        assert result_none.sources == result_omit.sources
        assert result_none.cues == result_omit.cues
        # Both calls pass folder_id=None to adapter
        for call in mock_drive.call_args_list:
            assert call.kwargs.get("folder_id") is None


# ============================================================================
# FORMAT ACTIVITY RESULT (pure, no mocks needed)
# ============================================================================


def _make_activity(
    *,
    file_id: str = "doc123",
    file_name: str = "Test Doc",
    action_type: str = "comment",
    actor_name: str = "Alice",
    timestamp: str = "2026-02-23T10:00:00Z",
    mentioned_users: list[str] | None = None,
    mime_type: str = "application/vnd.google-apps.document",
    web_link: str = "https://docs.google.com/document/d/doc123/edit",
) -> CommentActivity:
    """Build a CommentActivity for testing."""
    return CommentActivity(
        activity_id="act/1",
        timestamp=timestamp,
        actor=ActivityActor(name=actor_name),
        target=ActivityTarget(
            file_id=file_id,
            file_name=file_name,
            mime_type=mime_type,
            web_link=web_link,
        ),
        action_type=action_type,
        mentioned_users=mentioned_users or [],
    )


class TestFormatActivityResult:
    """Test Activity result serialization."""

    def test_basic_fields(self) -> None:
        activity = _make_activity()
        formatted = format_activity_result(activity)

        assert formatted["file_id"] == "doc123"
        assert formatted["file_name"] == "Test Doc"
        assert formatted["action_type"] == "comment"
        assert formatted["actor"] == "Alice"
        assert formatted["timestamp"] == "2026-02-23T10:00:00Z"
        assert formatted["url"] == "https://docs.google.com/document/d/doc123/edit"
        assert "mentioned_users" not in formatted  # Empty list omitted

    def test_with_mentions(self) -> None:
        activity = _make_activity(mentioned_users=["Bob", "Carol"])
        formatted = format_activity_result(activity)

        assert formatted["mentioned_users"] == ["Bob", "Carol"]

    def test_mime_type_included(self) -> None:
        activity = _make_activity(mime_type="application/vnd.google-apps.spreadsheet")
        formatted = format_activity_result(activity)

        assert formatted["mime_type"] == "application/vnd.google-apps.spreadsheet"


# ============================================================================
# ACTIVITY SEARCH ORCHESTRATION
# ============================================================================


class TestActivitySearch:
    """Test activity source in do_search."""

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    def test_activity_only(self, mock_activity, mock_write) -> None:
        """Activity-only search returns activity results."""
        mock_activity.return_value = ActivitySearchResult(
            activities=[_make_activity()],
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["activity"])

        assert result.sources == ["activity"]
        assert len(result.activity_results) == 1
        assert result.activity_results[0]["file_id"] == "doc123"
        mock_activity.assert_called_once()

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_activity_with_drive_and_gmail(self, mock_drive, mock_gmail, mock_activity, mock_write) -> None:
        """All three sources can run together."""
        mock_drive.return_value = DriveSearchResults(results=[
            DriveSearchResult(file_id="d1", name="Doc", mime_type="text/plain"),
        ])
        mock_gmail.return_value = GmailSearchResults(results=[
            GmailSearchResult(thread_id="t1", subject="Email", snippet="..."),
        ])
        mock_activity.return_value = ActivitySearchResult(
            activities=[_make_activity()],
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "gmail", "activity"])

        assert len(result.drive_results) == 1
        assert len(result.gmail_results) == 1
        assert len(result.activity_results) == 1
        mock_drive.assert_called_once()
        mock_gmail.assert_called_once()
        mock_activity.assert_called_once()

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    def test_activity_error_captured(self, mock_activity, mock_write) -> None:
        """Activity failure captured in errors, not raised."""
        mock_activity.side_effect = MiseError(
            ErrorKind.NETWORK_ERROR, "API timeout"
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["activity"])

        assert result.activity_results == []
        assert any("Activity search failed" in e for e in result.errors)

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    def test_activity_empty_results(self, mock_activity, mock_write) -> None:
        """Empty activity results handled cleanly."""
        mock_activity.return_value = ActivitySearchResult(activities=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["activity"])

        assert result.activity_results == []
        assert result.errors == []

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    def test_activity_max_results_forwarded(self, mock_activity, mock_write) -> None:
        """max_results passed as page_size to activity search."""
        mock_activity.return_value = ActivitySearchResult(activities=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        do_search("test", sources=["activity"], max_results=10)

        mock_activity.assert_called_once_with(page_size=10)

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    @patch('tools.search.search_files')
    def test_activity_excluded_by_folder_id(self, mock_drive, mock_activity, mock_write) -> None:
        """Activity source dropped when folder_id is set."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "activity"], folder_id="folder123")

        assert result.sources == ["drive"]
        mock_activity.assert_not_called()
        assert "Activity" in result.cues.get("sources_note", "")

    @patch('tools.search.write_search_results')
    @patch('tools.search.search_comment_activities')
    @patch('tools.search.search_threads')
    @patch('tools.search.search_files')
    def test_activity_error_doesnt_block_others(self, mock_drive, mock_gmail, mock_activity, mock_write) -> None:
        """Activity failure doesn't block Drive/Gmail results."""
        mock_drive.return_value = DriveSearchResults(results=[
            DriveSearchResult(file_id="d1", name="Doc", mime_type="text/plain"),
        ])
        mock_gmail.return_value = GmailSearchResults(results=[
            GmailSearchResult(thread_id="t1", subject="Email", snippet="..."),
        ])
        mock_activity.side_effect = Exception("API broke")
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "gmail", "activity"])

        assert len(result.drive_results) == 1
        assert len(result.gmail_results) == 1
        assert result.activity_results == []
        assert any("Activity search failed" in e for e in result.errors)


class TestSearchResultModel:
    """Test SearchResult model with activity_results."""

    def test_full_results_includes_activity(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["activity"],
            activity_results=[{"file_id": "f1", "action_type": "comment"}],
        )
        full = result.full_results()
        assert "activity_results" in full
        assert len(full["activity_results"]) == 1

    def test_to_dict_includes_activity_count(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["activity"],
            activity_results=[{"file_id": "f1", "action_type": "comment", "file_name": "Doc", "actor": "Alice", "timestamp": "2026-02-23"}],
            path="/tmp/fake/results.json",
        )
        d = result.to_dict()
        assert d["activity_count"] == 1

    def test_preview_includes_activity(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["activity"],
            activity_results=[{
                "file_id": "f1",
                "file_name": "Doc",
                "action_type": "comment",
                "actor": "Alice",
                "timestamp": "2026-02-23",
                "mentioned_users": ["Bob"],
            }],
            path="/tmp/fake/results.json",
        )
        d = result.to_dict()
        assert "activity" in d["preview"]
        assert d["preview"]["activity"][0]["mentioned_users"] == ["Bob"]

    def test_full_results_includes_calendar(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["calendar"],
            calendar_results=[{"event_id": "e1", "summary": "Standup"}],
        )
        full = result.full_results()
        assert "calendar_results" in full

    def test_to_dict_includes_calendar_count(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["calendar"],
            calendar_results=[{"event_id": "e1", "summary": "Standup", "start_time": "2026-02-23", "attendee_count": 3}],
            path="/tmp/fake/results.json",
        )
        d = result.to_dict()
        assert d["calendar_count"] == 1

    def test_preview_includes_calendar(self) -> None:
        from models import SearchResult
        result = SearchResult(
            query="test",
            sources=["calendar"],
            calendar_results=[{
                "event_id": "e1",
                "summary": "Planning",
                "start_time": "2026-02-23T10:00:00Z",
                "attendee_count": 5,
                "attachment_count": 2,
                "meet_link": "https://meet.google.com/abc",
            }],
            path="/tmp/fake/results.json",
        )
        d = result.to_dict()
        assert "calendar" in d["preview"]
        item = d["preview"]["calendar"][0]
        assert item["summary"] == "Planning"
        assert item["has_meet"] is True
        assert item["attachment_count"] == 2


# ============================================================================
# FORMAT CALENDAR RESULT (pure, no mocks needed)
# ============================================================================


def _make_calendar_event(
    *,
    event_id: str = "evt1",
    summary: str = "Team Sync",
    start_time: str = "2026-02-23T10:00:00Z",
    end_time: str = "2026-02-23T11:00:00Z",
    attendees: list[CalendarAttendee] | None = None,
    attachments: list[CalendarAttachment] | None = None,
    meet_link: str | None = None,
    organizer_email: str | None = "boss@example.com",
) -> CalendarEvent:
    """Build a CalendarEvent for testing."""
    return CalendarEvent(
        event_id=event_id,
        summary=summary,
        start_time=start_time,
        end_time=end_time,
        attendees=attendees or [],
        attachments=attachments or [],
        meet_link=meet_link,
        organizer_email=organizer_email,
    )


class TestFormatCalendarResult:
    """Test Calendar result serialization."""

    def test_basic_fields(self) -> None:
        event = _make_calendar_event()
        formatted = format_calendar_result(event)

        assert formatted["event_id"] == "evt1"
        assert formatted["summary"] == "Team Sync"
        assert formatted["start_time"] == "2026-02-23T10:00:00Z"
        assert formatted["organizer"] == "boss@example.com"
        assert formatted["attendee_count"] == 0
        assert "attachments" not in formatted
        assert "meet_link" not in formatted

    def test_with_attendees(self) -> None:
        event = _make_calendar_event(attendees=[
            CalendarAttendee(email="alice@example.com", display_name="Alice", response_status="accepted"),
            CalendarAttendee(email="room@resource.calendar.google.com", is_resource=True),
        ])
        formatted = format_calendar_result(event)

        assert formatted["attendee_count"] == 1  # Room excluded
        assert len(formatted["attendees"]) == 1
        assert formatted["attendees"][0]["email"] == "alice@example.com"

    def test_with_attachments(self) -> None:
        event = _make_calendar_event(attachments=[
            CalendarAttachment(file_id="doc1", title="Agenda"),
            CalendarAttachment(file_id="doc2", title="Notes"),
        ])
        formatted = format_calendar_result(event)

        assert formatted["attachment_count"] == 2
        assert formatted["attachments"][0]["file_id"] == "doc1"

    def test_with_meet_link(self) -> None:
        event = _make_calendar_event(meet_link="https://meet.google.com/abc-defg")
        formatted = format_calendar_result(event)

        assert formatted["meet_link"] == "https://meet.google.com/abc-defg"


# ============================================================================
# MEETING CONTEXT INDEX (pure)
# ============================================================================


class TestBuildMeetingContextIndex:
    """Test _build_meeting_context_index cross-referencing."""

    def test_builds_index_from_attachments(self) -> None:
        events = [
            _make_calendar_event(
                summary="Review",
                attachments=[CalendarAttachment(file_id="doc1", title="Draft")],
            ),
        ]
        index = _build_meeting_context_index(events)

        assert "doc1" in index
        assert index["doc1"][0]["summary"] == "Review"

    def test_multiple_events_same_file(self) -> None:
        events = [
            _make_calendar_event(
                event_id="e1", summary="Draft review",
                attachments=[CalendarAttachment(file_id="doc1", title="Draft")],
            ),
            _make_calendar_event(
                event_id="e2", summary="Final review",
                attachments=[CalendarAttachment(file_id="doc1", title="Draft")],
            ),
        ]
        index = _build_meeting_context_index(events)

        assert len(index["doc1"]) == 2

    def test_events_without_attachments_skipped(self) -> None:
        events = [_make_calendar_event()]  # No attachments
        index = _build_meeting_context_index(events)

        assert index == {}

    def test_empty_events(self) -> None:
        assert _build_meeting_context_index([]) == {}


class TestEnrichDriveResults:
    """Test _enrich_drive_results_with_meetings mutation."""

    def test_enriches_matching_file(self) -> None:
        drive_results = [{"id": "doc1", "name": "Draft"}]
        index = {"doc1": [{"summary": "Review", "start_time": "2026-02-23T10:00:00Z"}]}

        _enrich_drive_results_with_meetings(drive_results, index)

        assert "meeting_context" in drive_results[0]
        assert drive_results[0]["meeting_context"][0]["summary"] == "Review"

    def test_non_matching_file_unchanged(self) -> None:
        drive_results = [{"id": "doc2", "name": "Other"}]
        index = {"doc1": [{"summary": "Review"}]}

        _enrich_drive_results_with_meetings(drive_results, index)

        assert "meeting_context" not in drive_results[0]

    def test_empty_index(self) -> None:
        drive_results = [{"id": "doc1", "name": "Draft"}]

        _enrich_drive_results_with_meetings(drive_results, {})

        assert "meeting_context" not in drive_results[0]


# ============================================================================
# CALENDAR SEARCH ORCHESTRATION
# ============================================================================


class TestCalendarSearch:
    """Test calendar source in do_search."""

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    def test_calendar_only(self, mock_calendar, mock_write) -> None:
        """Calendar-only search returns events."""
        mock_calendar.return_value = CalendarSearchResult(
            events=[_make_calendar_event()],
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["calendar"])

        assert result.sources == ["calendar"]
        assert len(result.calendar_results) == 1
        assert result.calendar_results[0]["summary"] == "Team Sync"

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    @patch('tools.search.search_files')
    def test_drive_enriched_with_calendar(self, mock_drive, mock_calendar, mock_write) -> None:
        """Drive results get meeting_context when calendar has matching attachments."""
        mock_drive.return_value = DriveSearchResults(results=[
            DriveSearchResult(file_id="doc1", name="Agenda", mime_type="text/plain"),
        ])
        mock_calendar.return_value = CalendarSearchResult(
            events=[
                _make_calendar_event(
                    summary="Team standup",
                    attachments=[CalendarAttachment(file_id="doc1", title="Agenda")],
                    attendees=[CalendarAttendee(email="alice@example.com")],
                ),
            ],
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("agenda", sources=["drive", "calendar"])

        assert len(result.drive_results) == 1
        assert "meeting_context" in result.drive_results[0]
        assert result.drive_results[0]["meeting_context"][0]["summary"] == "Team standup"

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    @patch('tools.search.search_files')
    def test_no_enrichment_when_no_matches(self, mock_drive, mock_calendar, mock_write) -> None:
        """No meeting_context added when calendar has no matching attachments."""
        mock_drive.return_value = DriveSearchResults(results=[
            DriveSearchResult(file_id="doc1", name="Random", mime_type="text/plain"),
        ])
        mock_calendar.return_value = CalendarSearchResult(
            events=[_make_calendar_event()],  # No attachments
        )
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "calendar"])

        assert "meeting_context" not in result.drive_results[0]

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    def test_calendar_error_captured(self, mock_calendar, mock_write) -> None:
        """Calendar failure captured in errors."""
        mock_calendar.side_effect = MiseError(ErrorKind.NETWORK_ERROR, "timeout")
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["calendar"])

        assert result.calendar_results == []
        assert any("Calendar search failed" in e for e in result.errors)

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    @patch('tools.search.search_files')
    def test_calendar_excluded_by_folder_id(self, mock_drive, mock_calendar, mock_write) -> None:
        """Calendar source dropped when folder_id is set."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("test", sources=["drive", "calendar"], folder_id="folder123")

        assert result.sources == ["drive"]
        mock_calendar.assert_not_called()
        assert "Calendar" in result.cues.get("sources_note", "")


class TestTypeFilter:
    """search(type=...) applies MIME type clause to Drive query."""

    @patch("tools.search.write_search_results")
    @patch("tools.search.search_files")
    def test_type_spreadsheet_adds_mime_clause(self, mock_drive, mock_write) -> None:
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/results.json"

        do_search("budget", sources=["drive"], type="spreadsheet")

        query_arg = mock_drive.call_args[0][0]
        assert "application/vnd.google-apps.spreadsheet" in query_arg
        assert "fullText contains 'budget'" in query_arg

    @patch("tools.search.write_search_results")
    @patch("tools.search.search_files")
    def test_type_sheet_alias(self, mock_drive, mock_write) -> None:
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/results.json"

        do_search("q4", sources=["drive"], type="sheet")

        query_arg = mock_drive.call_args[0][0]
        assert "application/vnd.google-apps.spreadsheet" in query_arg

    @patch("tools.search.write_search_results")
    @patch("tools.search.search_files")
    def test_type_image_uses_contains_clause(self, mock_drive, mock_write) -> None:
        """image type uses 'contains' not '=' since MIME prefix match."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/results.json"

        do_search("logo", sources=["drive"], type="image")

        query_arg = mock_drive.call_args[0][0]
        assert "mimeType contains 'image/'" in query_arg

    @patch("tools.search.write_search_results")
    @patch("tools.search.search_files")
    def test_type_without_query(self, mock_drive, mock_write) -> None:
        """type without query omits fullText clause — lists all of that type."""
        mock_drive.return_value = DriveSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/results.json"

        do_search(type="folder", sources=["drive"])

        query_arg = mock_drive.call_args[0][0]
        assert "fullText" not in query_arg
        assert "application/vnd.google-apps.folder" in query_arg
        assert "trashed = false" in query_arg

    @patch("tools.search.write_search_results")
    @patch("tools.search.search_threads")
    def test_type_ignored_with_no_drive_source_adds_cue(self, mock_gmail, mock_write) -> None:
        mock_gmail.return_value = GmailSearchResults(results=[])
        mock_write.return_value = "/tmp/fake/results.json"

        result = do_search("test", sources=["gmail"], type="spreadsheet")

        assert "type_note" in result.cues
        assert "Drive not in sources" in result.cues["type_note"]

    def test_invalid_type_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="Unknown type"):
            do_search("test", type="banana")


# ============================================================================
# SERVER.PY SEARCH() VALIDATION (MCP entry point early-return paths)
# ============================================================================

class TestSearchMCPValidation:
    """server.search() early-return validation paths (before do_search is called)."""

    def test_invalid_type_returns_error(self) -> None:
        import server
        from server import search
        with patch.object(server, "_REMOTE_MODE", False), \
             patch("server.do_search") as mock_do:
            result = search(query="test", type="banana", base_path="/tmp")
        assert result.get("error") is True
        assert result.get("kind") == "invalid_input"
        assert "banana" in result.get("message", "")
        mock_do.assert_not_called()

    def test_empty_query_no_type_no_folder_returns_error(self) -> None:
        import server
        from server import search
        with patch.object(server, "_REMOTE_MODE", False), \
             patch("server.do_search") as mock_do:
            result = search(query="", base_path="/tmp")
        assert result.get("error") is True
        assert result.get("kind") == "invalid_input"
        mock_do.assert_not_called()

    def test_missing_base_path_returns_error_in_stdio_mode(self) -> None:
        import server
        from server import search
        with patch.object(server, "_REMOTE_MODE", False), \
             patch("server.do_search") as mock_do:
            result = search(query="something")
        assert result.get("error") is True
        assert result.get("kind") == "invalid_input"
        assert "base_path" in result.get("message", "")
        mock_do.assert_not_called()


    def test_drive_syntax_in_query_is_refused_not_keyword_searched(self) -> None:
        """mise-decaza. Before the guard this returned ten plausible-looking wrong
        files — a 1:1 doc and a probation review among them — because the escaped
        string became a fullText search for the words name, contains and PCA. A
        wrong answer with no warning is worse than a refusal."""
        import server
        from server import search
        with patch.object(server, "_REMOTE_MODE", False), \
             patch("server.do_search") as mock_do:
            result = search(query="name contains 'PCA'", base_path="/tmp")
        assert result.get("kind") == "invalid_input"
        assert "raw_query" in result.get("message", "")
        mock_do.assert_not_called()

    def test_ordinary_query_still_reaches_do_search(self) -> None:
        """Positive control for the guard — without it, the test above would pass
        against a guard that rejected everything."""
        import server
        from server import search
        with patch.object(server, "_REMOTE_MODE", False), \
             patch("server.do_search") as mock_do:
            mock_do.return_value.to_dict.return_value = {"ok": True}
            search(query="ViewersLogic post campaign analysis", base_path="/tmp")
        mock_do.assert_called_once()

    def test_query_and_raw_query_together_are_refused(self) -> None:
        import server
        from server import search
        with patch.object(server, "_REMOTE_MODE", False), \
             patch("server.do_search") as mock_do:
            result = search(query="PCA", raw_query="name contains 'PCA'", base_path="/tmp")
        assert result.get("kind") == "invalid_input"
        assert "not both" in result.get("message", "")
        mock_do.assert_not_called()

    def test_raw_query_alone_satisfies_the_something_to_search_gate(self) -> None:
        import server
        from server import search
        with patch.object(server, "_REMOTE_MODE", False), \
             patch("server.do_search") as mock_do:
            mock_do.return_value.to_dict.return_value = {"ok": True}
            search(raw_query="name contains 'PCA'", base_path="/tmp")
        mock_do.assert_called_once()
        assert mock_do.call_args.kwargs["raw_query"] == "name contains 'PCA'"

    def test_raw_query_reaches_remote_mode(self) -> None:
        """Caught during /close. raw_query was accepted at the tool boundary and
        never passed to search_remote, so a remote caller asking for
        `name contains 'PCA'` fell through to query="" — which builds
        `trashed = false` with no clause and returns an arbitrary slice of the
        WHOLE Drive, presented as results. Same disease as the rest of the day:
        a param accepted and silently dropped, producing confident nonsense.

        Threaded rather than rejected: raw_query is a read-side refinement, and
        the remote whitelist exists to gate WRITES."""
        import server
        from server import search
        with patch.object(server, "_REMOTE_MODE", True), \
             patch("server.search_remote", return_value={"ok": True}) as mock_remote:
            search(raw_query="name contains 'PCA'", base_path="/tmp")
        assert "name contains 'PCA'" in mock_remote.call_args.args


class TestCalendarTimeWindow:
    """Explicit time_min/time_max on search (mise-riduka)."""

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    def test_window_threads_to_adapter_parsed(self, mock_calendar, mock_write) -> None:
        """ISO strings arrive at the adapter as aware datetimes — a bare date
        as time_max widened to the END of its day."""
        from datetime import timezone
        mock_calendar.return_value = CalendarSearchResult(events=[])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("", sources=["calendar"],
                           time_min="2026-08-03", time_max="2026-08-05")

        kwargs = mock_calendar.call_args.kwargs
        assert kwargs["time_min"] == datetime(2026, 8, 3, tzinfo=timezone.utc)
        assert kwargs["time_max"] == datetime(2026, 8, 6, tzinfo=timezone.utc)
        assert "calendar window" in result.cues["calendar_window"]

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    def test_unfiltered_default_window_needs_no_query(self, mock_calendar, mock_write) -> None:
        """sources=['calendar'] with no query lists the default ±7d window."""
        mock_calendar.return_value = CalendarSearchResult(events=[_make_calendar_event()])
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("", sources=["calendar"])

        assert len(result.calendar_results) == 1
        kwargs = mock_calendar.call_args.kwargs
        assert kwargs["time_min"] is None and kwargs["time_max"] is None
        assert "calendar_window" not in result.cues

    @patch('tools.search.write_search_results')
    @patch('tools.search.list_events')
    def test_explicit_window_truncation_teaches_the_cursor(self, mock_calendar, mock_write) -> None:
        mock_calendar.return_value = CalendarSearchResult(
            events=[_make_calendar_event()], truncated=True)
        mock_write.return_value = "/tmp/fake/search-results.json"

        result = do_search("", sources=["calendar"],
                           time_min="2026-08-01", time_max="2026-08-10")

        cue = result.cues["calendar_truncated"]
        assert "chronological head" in cue and "time_min" in cue

    def test_window_without_calendar_source_refuses(self) -> None:
        """A window that scopes nothing is accept-and-drop — refuse instead."""
        with pytest.raises(ValueError, match="calendar"):
            do_search("x", sources=["drive"], time_min="2026-08-03")

    def test_window_with_folder_id_refuses(self) -> None:
        """folder_id narrows to Drive, which would silently drop the window."""
        with pytest.raises(ValueError, match="folder_id"):
            do_search("", sources=["calendar", "drive"], time_min="2026-08-03",
                      folder_id="1234567890abc")

    def test_garbage_bound_raises_teaching_error(self) -> None:
        with pytest.raises(ValueError, match="ISO date"):
            do_search("", sources=["calendar"], time_min="next tuesday")

    def test_window_reaches_remote_mode(self) -> None:
        """Same disease as raw_query above: a param accepted at the boundary
        must ride the remote path too, or a remote caller gets the default
        window presented as their answer."""
        import server
        from server import search
        with patch.object(server, "_REMOTE_MODE", True), \
             patch("server.search_remote", return_value={"ok": True}) as mock_remote:
            search(sources=["calendar"], time_min="2026-08-03",
                   time_max="2026-08-05", base_path="/tmp")
        assert mock_remote.call_args.kwargs["time_min"] == "2026-08-03"
        assert mock_remote.call_args.kwargs["time_max"] == "2026-08-05"


class TestSearchGate:
    """The all-empty refusal at the server boundary, and its two new escapes
    (mise-riduka: the gate was the ONLY thing between callers and the
    unfiltered calendar listing the adapter supported all along)."""

    def test_all_empty_still_refuses_and_teaches_the_new_routes(self) -> None:
        from server import search
        result = search(base_path="/tmp")
        assert result["kind"] == "invalid_input"
        assert "time_min" in result["message"]

    def test_sole_calendar_source_passes_the_gate(self) -> None:
        from server import search
        # No base_path: reaching the base_path error PROVES the gate opened.
        result = search(sources=["calendar"])
        assert "base_path" in result["message"]

    def test_window_passes_the_gate(self) -> None:
        from server import search
        result = search(time_min="2026-08-03")
        assert "base_path" in result["message"]

    def test_wrapper_converts_window_refusal_to_teaching_json(self) -> None:
        """do_search's ValueError refusals reach MCP callers as invalid_input
        JSON, not a traceback — the fetch router's conversion, applied here."""
        from server import search
        result = search(query="x", sources=["drive"], time_min="2026-08-03",
                        base_path="/tmp")
        assert result["kind"] == "invalid_input"
        assert "calendar" in result["message"]


class TestLastModifiedByInResults:
    """mise-tanoti: 'modified <date> by <who>' must be renderable from mise
    data alone — Shared Drive files have no owners, so last_modified_by is
    the only honest author signal a consumer can show."""

    def test_format_drive_result_carries_last_modified_by(self) -> None:
        from models import DriveSearchResult
        from tools.search import format_drive_result

        r = DriveSearchResult(
            file_id="f1", name="corpus.pdf", mime_type="application/pdf",
            last_modified_by="Jane Analyst",
        )
        assert format_drive_result(r)["last_modified_by"] == "Jane Analyst"

    def test_manifest_extra_carries_last_modified_by(self) -> None:
        from tools.fetch.common import add_file_provenance

        extra: dict = {}
        add_file_provenance(extra, {
            "modifiedTime": "2026-08-24T09:16:46Z",
            "lastModifyingUser": {
                "displayName": "Jane Analyst",
                "emailAddress": "jane@example.com",
            },
        })
        assert extra["last_modified_by"] == "Jane Analyst"
        assert extra["modified_time"] == "2026-08-24T09:16:46Z"

    def test_manifest_extra_falls_back_to_email_and_omits_when_absent(self) -> None:
        from tools.fetch.common import add_file_provenance

        extra: dict = {}
        add_file_provenance(extra, {
            "lastModifyingUser": {"emailAddress": "sa@project.iam.gserviceaccount.com"},
        })
        assert extra["last_modified_by"] == "sa@project.iam.gserviceaccount.com"

        extra2: dict = {}
        add_file_provenance(extra2, {"modifiedTime": "2026-08-24T09:00:00Z"})
        assert "last_modified_by" not in extra2
