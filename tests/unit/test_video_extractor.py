"""
Tests for video extractor.

Tests pure markdown assembly with no API mocking needed — that's the point
of extracting this from the tool layer.
"""

import pytest
from extractors.video import extract_video_content, format_duration


class TestVideoContentAssembly:
    """Tests for video content markdown assembly."""

    def test_with_full_summary(self):
        """Video with AI summary and transcript snippets."""
        result = extract_video_content(
            "Team Standup 2026-02-24",
            summary="The team discussed Q1 priorities and blockers.",
            transcript_snippets=["Alice: Let's start with priorities", "Bob: The API is ready"],
            has_summary=True,
            mime_type="video/mp4",
            duration_ms=330000,
            web_view_link="https://drive.google.com/file/d/abc123/view",
        )
        assert "# Team Standup 2026-02-24" in result
        assert "## AI Summary" in result
        assert "Q1 priorities" in result
        assert "## Transcript Snippets" in result
        assert "- Alice: Let's start" in result
        assert "- Bob: The API is ready" in result
        assert "- **Duration:** 5:30" in result
        assert "- **Type:** video/mp4" in result
        assert "abc123" in result

    def test_summary_only_no_transcript(self):
        """Video with summary but no transcript snippets."""
        result = extract_video_content(
            "Presentation",
            summary="Overview of the new architecture.",
            has_summary=True,
            mime_type="video/mp4",
        )
        assert "## AI Summary" in result
        assert "new architecture" in result
        assert "Transcript" not in result

    def test_stale_cookies_error(self):
        """Stale cookies shows refresh tip."""
        result = extract_video_content(
            "Meeting",
            summary_error="stale_cookies",
            mime_type="video/mp4",
        )
        assert "browser session expired" in result
        assert "chrome-debug" in result
        assert "## AI Summary" not in result

    def test_permission_denied_error(self):
        """Permission denied shows access message."""
        result = extract_video_content(
            "Restricted Video",
            summary_error="permission_denied",
            mime_type="video/mp4",
        )
        assert "no access" in result
        assert "chrome-debug" not in result

    def test_no_summary_cdp_available(self):
        """No summary, CDP running — no tip needed."""
        result = extract_video_content(
            "Clip",
            mime_type="video/mp4",
            cdp_available=True,
        )
        assert "No AI summary available" in result
        assert "chrome-debug" not in result

    def test_no_summary_cdp_unavailable(self):
        """No summary, CDP not running — show tip."""
        result = extract_video_content(
            "Clip",
            mime_type="video/mp4",
            cdp_available=False,
        )
        assert "No AI summary available" in result
        assert "chrome-debug" in result

    def test_metadata_section_always_present(self):
        """Metadata section appears regardless of summary status."""
        result = extract_video_content(
            "Audio File",
            mime_type="audio/mpeg",
            web_view_link="https://drive.google.com/file/d/xyz/view",
        )
        assert "## Metadata" in result
        assert "- **Type:** audio/mpeg" in result
        assert "xyz" in result

    def test_no_duration_omits_line(self):
        """Duration line omitted when not available."""
        result = extract_video_content("Clip", mime_type="video/mp4")
        assert "Duration" not in result

    def test_duration_with_hours(self):
        """Long video shows hours in duration."""
        result = extract_video_content(
            "Long Recording",
            mime_type="video/mp4",
            duration_ms=3_723_000,  # 1:02:03
        )
        assert "1:02:03" in result

    def test_duration_string_input(self):
        """Duration as string (from API) is handled."""
        result = extract_video_content(
            "Clip",
            mime_type="video/mp4",
            duration_ms="90000",  # 1:30
        )
        assert "1:30" in result


class TestFormatDuration:
    """Tests for duration formatting helper."""

    def test_seconds_only(self):
        assert format_duration(45_000) == "0:45"

    def test_minutes_and_seconds(self):
        assert format_duration(330_000) == "5:30"

    def test_hours(self):
        assert format_duration(3_723_000) == "1:02:03"

    def test_zero(self):
        assert format_duration(0) == "0:00"

    def test_exact_minute(self):
        assert format_duration(60_000) == "1:00"
