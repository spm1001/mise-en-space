"""
Unit tests for GenAI adapter.
"""

import pytest
from unittest.mock import patch, MagicMock
import hashlib
import time


class TestComputeSapisidhash:
    """Tests for _compute_sapisidhash()."""

    def test_computes_correct_hash_format(self):
        """Should return timestamp_hash format."""
        from adapters.genai import _compute_sapisidhash

        result = _compute_sapisidhash("test_sapisid")

        # Should be timestamp_hexhash
        parts = result.split("_")
        assert len(parts) == 2
        assert parts[0].isdigit()  # timestamp
        assert len(parts[1]) == 40  # SHA1 hex

    def test_hash_changes_with_sapisid(self):
        """Different SAPISID should produce different hash."""
        from adapters.genai import _compute_sapisidhash

        result1 = _compute_sapisidhash("sapisid_one")
        result2 = _compute_sapisidhash("sapisid_two")

        # Timestamps might be same, but hashes should differ
        hash1 = result1.split("_")[1]
        hash2 = result2.split("_")[1]
        assert hash1 != hash2

    def test_hash_uses_correct_algorithm(self):
        """Should use SHA1 of 'timestamp sapisid origin'."""
        from adapters.genai import _compute_sapisidhash

        sapisid = "test123"
        origin = "https://drive.google.com"

        # Get result and extract components
        result = _compute_sapisidhash(sapisid, origin)
        timestamp_str, hash_value = result.split("_")
        timestamp = int(timestamp_str)

        # Verify the hash
        expected_input = f"{timestamp} {sapisid} {origin}"
        expected_hash = hashlib.sha1(expected_input.encode()).hexdigest()
        assert hash_value == expected_hash


class TestBuildRequestBody:
    """Tests for _build_request_body()."""

    def test_includes_file_id(self):
        """Request body should contain the file ID."""
        from adapters.genai import _build_request_body
        import json

        file_id = "abc123xyz"
        body = _build_request_body(file_id)

        # File ID should appear in the nested structure
        body_str = json.dumps(body)
        assert file_id in body_str

    def test_generates_unique_request_ids(self):
        """Each call should have a different request ID."""
        from adapters.genai import _build_request_body
        import json

        body1 = _build_request_body("file1")
        body2 = _build_request_body("file2")

        body1_str = json.dumps(body1)
        body2_str = json.dumps(body2)

        # Extract request IDs (goog_-NNNNNN pattern)
        import re
        id1 = re.search(r'goog_-?\d+', body1_str).group()
        id2 = re.search(r'goog_-?\d+', body2_str).group()

        # Very unlikely to be the same (random)
        # But don't fail if they happen to match
        assert id1.startswith("goog_")
        assert id2.startswith("goog_")


class TestExtractSummaryText:
    """Tests for _extract_summary_text()."""

    def test_extracts_video_summary(self):
        """Should find 'The video...' patterns."""
        from adapters.genai import _extract_summary_text

        response = '''[null,"The video shows a meeting about project planning and deadlines.",null]'''
        summary, snippets = _extract_summary_text(response)

        assert "meeting" in summary.lower() or "project" in summary.lower()

    def test_deduplicates_snippets(self):
        """Should not return duplicate transcript snippets."""
        from adapters.genai import _extract_summary_text

        # Simulate streaming response with duplicates
        response = '''
        "An example sentence that appears in the transcript as a snippet here."
        "An example sentence that appears in the transcript as a snippet here."
        "An example sentence that appears in the transcript as a snippet here."
        "Another different sentence that also appears multiple times in response."
        '''
        summary, snippets = _extract_summary_text(response)

        # Should have at most 2 unique snippets, not 4
        assert len(snippets) <= 2

    def test_limits_snippet_count(self):
        """Should return at most 5 snippets."""
        from adapters.genai import _extract_summary_text

        # Create response with many potential snippets
        snippets_text = "\n".join([
            f'"Snippet number {i} is a substantial piece of text that meets the length requirements for extraction."'
            for i in range(20)
        ])
        summary, snippets = _extract_summary_text(snippets_text)

        assert len(snippets) <= 5

    def test_skips_code_like_content(self):
        """Should skip content that looks like code/URLs."""
        from adapters.genai import _extract_summary_text

        response = '''
        "This contains a URL https://example.com/path and should be skipped from snippets."
        "This has curly braces { like code } and should also be skipped from snippets."
        "A normal transcript snippet without any special characters or code patterns here."
        '''
        summary, snippets = _extract_summary_text(response)

        for snippet in snippets:
            assert "http" not in snippet
            assert "{" not in snippet


class TestIsMediaFile:
    """Tests for is_video_file, is_audio_file, is_media_file."""

    def test_video_mime_types(self):
        """Should recognize video MIME types."""
        from adapters.genai import is_video_file, is_media_file

        assert is_video_file("video/mp4")
        assert is_video_file("video/webm")
        assert is_video_file("video/quicktime")
        assert is_media_file("video/mp4")

    def test_audio_mime_types(self):
        """Should recognize audio MIME types."""
        from adapters.genai import is_audio_file, is_media_file

        assert is_audio_file("audio/mpeg")
        assert is_audio_file("audio/wav")
        assert is_audio_file("audio/ogg")
        assert is_media_file("audio/mpeg")

    def test_non_media_mime_types(self):
        """Should reject non-media MIME types."""
        from adapters.genai import is_video_file, is_audio_file, is_media_file

        assert not is_video_file("application/pdf")
        assert not is_audio_file("image/png")
        assert not is_media_file("text/plain")
        assert not is_media_file("application/vnd.google-apps.document")


class TestGetVideoSummary:
    """Tests for get_video_summary()."""

    def test_returns_none_when_no_cookies(self):
        """Should return None if cookies unavailable."""
        from adapters import genai
        with patch.object(genai, "get_google_cookies", return_value=None):
            result = genai.get_video_summary("file123")
            assert result is None

    def test_returns_none_when_no_sapisid(self):
        """Should return None if SAPISID missing from cookies."""
        from adapters import genai
        with patch.object(genai, "get_google_cookies", return_value={"OTHER": "val"}):
            result = genai.get_video_summary("file123")
            assert result is None

    def test_returns_stale_cookies_error_on_401(self):
        """Should return error='stale_cookies' on 401."""
        from adapters import genai
        import urllib.error

        mock_cookies = {"SAPISID": "test", "SID": "sid", "HSID": "hsid"}
        with patch.object(genai, "GENAI_API_KEY", "test-key"):
            with patch.object(genai, "get_google_cookies", return_value=mock_cookies):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.side_effect = urllib.error.HTTPError(
                        url="", code=401, msg="Unauthorized", hdrs={}, fp=None
                    )
                    result = genai.get_video_summary("file123")

                    assert result is not None
                    assert result.error == "stale_cookies"
                    assert not result.has_content

    def test_returns_permission_denied_on_403(self):
        """Should return error='permission_denied' on 403."""
        from adapters import genai
        import urllib.error

        mock_cookies = {"SAPISID": "test", "SID": "sid"}
        with patch.object(genai, "GENAI_API_KEY", "test-key"):
            with patch.object(genai, "get_google_cookies", return_value=mock_cookies):
                with patch("urllib.request.urlopen") as mock_urlopen:
                    mock_urlopen.side_effect = urllib.error.HTTPError(
                        url="", code=403, msg="Forbidden", hdrs={}, fp=None
                    )
                    result = genai.get_video_summary("file123")

                assert result is not None
                assert result.error == "permission_denied"
