"""Tests for cross-source search ergonomics features."""

import pytest
from datetime import datetime

from models import EmailContext, DriveSearchResult
from adapters.drive import parse_email_context
from tools.search import format_drive_result, format_gmail_result
from tools.fetch.common import _build_email_context_metadata
from models import GmailSearchResult


class TestParseEmailContext:
    """Tests for parse_email_context function."""

    def test_parses_full_exfil_description(self):
        """Parse complete exfil description with all fields."""
        description = """From: alice@example.com
Subject: Budget analysis
Date: 2026-01-15T10:30:00.000Z
Message ID: 18f4a5b6c7d8e9f0
Content Hash: abc123def456"""

        result = parse_email_context(description)

        assert result is not None
        assert result.message_id == "18f4a5b6c7d8e9f0"
        assert result.from_address == "alice@example.com"
        assert result.subject == "Budget analysis"
        assert result.date == "2026-01-15T10:30:00.000Z"

    def test_returns_none_without_message_id(self):
        """Return None if Message ID is missing."""
        description = """From: alice@example.com
Subject: Budget analysis"""

        result = parse_email_context(description)
        assert result is None

    def test_returns_none_for_empty_description(self):
        """Return None for empty string."""
        assert parse_email_context("") is None
        assert parse_email_context(None) is None

    def test_parses_minimal_description(self):
        """Parse description with only Message ID."""
        description = "Message ID: 18f4a5b6c7d8e9f0"

        result = parse_email_context(description)

        assert result is not None
        assert result.message_id == "18f4a5b6c7d8e9f0"
        assert result.from_address is None
        assert result.subject is None
        assert result.date is None

    def test_handles_multiline_subject(self):
        """Subject extraction stops at newline."""
        description = """From: alice@example.com
Subject: Re: Long thread about stuff
Date: 2026-01-15T10:30:00.000Z
Message ID: abc123"""

        result = parse_email_context(description)
        assert result.subject == "Re: Long thread about stuff"

    def test_handles_email_with_display_name(self):
        """From field may include display name."""
        description = """From: Alice Smith <alice@example.com>
Message ID: abc123"""

        result = parse_email_context(description)
        assert result.from_address == "Alice Smith <alice@example.com>"


class TestFormatDriveResultWithEmailContext:
    """Tests for email_context in Drive search results."""

    def test_includes_email_context_when_present(self):
        """Drive result includes email_context for exfil'd files."""
        email_ctx = EmailContext(
            message_id="18f4a5b6c7d8e9f0",
            from_address="alice@example.com",
            subject="Budget analysis",
        )
        result = DriveSearchResult(
            file_id="abc123",
            name="budget.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            email_context=email_ctx,
        )

        formatted = format_drive_result(result)

        assert "email_context" in formatted
        assert formatted["email_context"]["message_id"] == "18f4a5b6c7d8e9f0"
        assert formatted["email_context"]["from"] == "alice@example.com"
        assert formatted["email_context"]["subject"] == "Budget analysis"
        assert "hint" in formatted["email_context"]
        assert "fetch('18f4a5b6c7d8e9f0')" in formatted["email_context"]["hint"]

    def test_hint_names_the_id_type_not_merely_the_call(self):
        """The hint must say WHICH id this is — mise-saroca.

        `message_id` here is a Gmail MESSAGE id, regexed out of a Drive file's
        description by the apps-script exfil. A message id resolves as a thread only
        when that message HEADS its thread, so the old hint — "Use fetch('<id>') to
        get source email" — promised a fetch that 404s mid-thread and left the caller
        nothing to recognise the failure by. One such 404 cost a 7-minute detour on
        2026-07-31 (docs/2026-08-01-usage-review.md, finding 2).

        The pre-existing test above asserts only that the hint contains
        `fetch('<id>')`, which the OLD wording satisfied too — so it could never have
        caught this. Naming the id type is the load-bearing half of the fix and is
        pinned here so a future reword cannot quietly drop it.
        """
        ctx = EmailContext(message_id="18fe27655760c61b", from_address="a@b.com")

        hint = ctx.to_cue()["hint"].lower()

        assert "message id" in hint
        assert "not a thread id" in hint
        assert "fetch('18fe27655760c61b')" in hint

    def test_both_mint_sites_emit_an_identical_cue(self):
        """search and fetch must not drift apart on this block — mise-saroca.

        Both layers held byte-identical hand-rolled copies of the email_context dict
        until 2026-08-02, and the hint was wrong in both, so it had to be corrected in
        both. They now share EmailContext.to_cue(); this goes red if either grows its
        own copy back.
        """
        ctx = EmailContext(
            message_id="18f4a5b6c7d8e9f0",
            from_address="alice@example.com",
            subject="Budget analysis",
        )
        search_side = format_drive_result(
            DriveSearchResult(
                file_id="abc123",
                name="budget.xlsx",
                mime_type="application/pdf",
                email_context=ctx,
            )
        )["email_context"]

        fetch_side = _build_email_context_metadata(ctx)

        assert search_side == fetch_side

    def test_omits_email_context_when_absent(self):
        """Drive result without email_context doesn't include field."""
        result = DriveSearchResult(
            file_id="abc123",
            name="regular-file.pdf",
            mime_type="application/pdf",
        )

        formatted = format_drive_result(result)

        assert "email_context" not in formatted


class TestGmailMetadataEnrichment:
    """Tests for attachments and drive_links in Gmail results."""

    def test_gmail_search_includes_attachment_names(self):
        """Gmail search results include attachment_names."""
        result = GmailSearchResult(
            thread_id="thread123",
            subject="Report attached",
            snippet="Here's the report...",
            has_attachments=True,
            attachment_names=["report.pdf", "data.xlsx"],
        )

        formatted = format_gmail_result(result)

        assert formatted["has_attachments"] is True
        assert formatted["attachment_names"] == ["report.pdf", "data.xlsx"]

    def test_gmail_search_empty_attachments(self):
        """Gmail search results handle no attachments."""
        result = GmailSearchResult(
            thread_id="thread123",
            subject="Quick note",
            snippet="Just a quick note...",
            has_attachments=False,
            attachment_names=[],
        )

        formatted = format_gmail_result(result)

        assert formatted["has_attachments"] is False
        assert formatted["attachment_names"] == []
