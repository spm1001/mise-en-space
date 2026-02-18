"""Tests for overwrite operation."""

from unittest.mock import patch, MagicMock

from models import DoResult
from server import do
from tools.overwrite import do_overwrite, _strip_headings


class TestStripHeadings:
    """Pure function tests for heading extraction."""

    def test_no_headings(self) -> None:
        text = "Just plain text\nAnd another line"
        plain, headings = _strip_headings(text)
        assert plain == text
        assert headings == []

    def test_single_heading(self) -> None:
        text = "# Title\n\nBody text"
        plain, headings = _strip_headings(text)
        assert plain == "Title\n\nBody text"
        assert len(headings) == 1
        assert headings[0] == (0, 5, 1)  # start=0, end=5 ("Title"), level=1

    def test_multiple_heading_levels(self) -> None:
        text = "# Heading 1\n## Heading 2\n### Heading 3"
        plain, headings = _strip_headings(text)
        assert plain == "Heading 1\nHeading 2\nHeading 3"
        assert len(headings) == 3
        assert headings[0][2] == 1
        assert headings[1][2] == 2
        assert headings[2][2] == 3

    def test_heading_with_body(self) -> None:
        text = "# Title\n\nParagraph one.\n\n## Section\n\nParagraph two."
        plain, headings = _strip_headings(text)
        assert "# " not in plain
        assert "## " not in plain
        assert "Title" in plain
        assert "Section" in plain
        assert len(headings) == 2

    def test_preserves_non_heading_hashes(self) -> None:
        """Hash in middle of line is not a heading."""
        text = "Use C# for development"
        plain, headings = _strip_headings(text)
        assert plain == text
        assert headings == []

    def test_heading_positions_are_correct(self) -> None:
        text = "intro\n# H1\nbody\n## H2"
        plain, headings = _strip_headings(text)
        # "intro\nH1\nbody\nH2"
        assert plain == "intro\nH1\nbody\nH2"
        # H1 starts at position 6 (after "intro\n")
        assert headings[0] == (6, 8, 1)
        # H2 starts at position 14 (after "intro\nH1\nbody\n")
        assert headings[1] == (14, 16, 2)

    def test_heading_positions_with_emoji(self) -> None:
        """Docs API uses UTF-16 code units. Emoji are 2 units, not 1."""
        # 🎯 is U+1F3AF — a surrogate pair in UTF-16 (2 code units)
        text = "🎯 intro\n# Title"
        plain, headings = _strip_headings(text)
        assert plain == "🎯 intro\nTitle"
        # "🎯 intro\n" = 2 (emoji) + 7 (" intro\n") = 9 UTF-16 units
        assert headings[0] == (9, 14, 1)  # "Title" = 5 UTF-16 units

    def test_heading_with_emoji_in_heading(self) -> None:
        """Emoji inside heading text affects end position."""
        text = "# 🚀 Launch"
        plain, headings = _strip_headings(text)
        assert plain == "🚀 Launch"
        # "🚀 Launch" = 2 + 7 = 9 UTF-16 units
        assert headings[0] == (0, 9, 1)


class TestDoOverwriteValidation:
    """Input validation via do_overwrite()."""

    def test_overwrite_without_file_id_returns_error(self) -> None:
        result = do_overwrite(content="hello")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "file_id" in result["message"]

    def test_overwrite_without_content_returns_error(self) -> None:
        result = do_overwrite(file_id="doc123")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"

    def test_overwrite_with_both_content_and_source_returns_error(self) -> None:
        result = do_overwrite(file_id="doc123", content="hello", source="/tmp/fake", base_path="/tmp")
        assert result["error"] is True
        assert "not both" in result["message"]

    def test_overwrite_validation_through_do(self) -> None:
        result = do(operation="overwrite", content="hello")
        assert result["error"] is True
        assert "file_id" in result["message"]


class TestDoOverwrite:
    """Overwrite logic with mocked Docs API."""

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_overwrites_doc_with_plain_text(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        # Document with existing content
        mock_service.documents().get().execute.return_value = {
            "title": "My Doc",
            "body": {"content": [
                {"endIndex": 1},
                {"endIndex": 50},
            ]},
        }

        result = do_overwrite(file_id="doc123", content="New content here")

        assert isinstance(result, DoResult)
        assert result.file_id == "doc123"
        assert result.title == "My Doc"
        assert result.operation == "overwrite"
        assert result.cues["char_count"] == 16

        # Verify batchUpdate was called
        batch_call = mock_service.documents().batchUpdate.call_args
        body = batch_call.kwargs["body"]
        requests = body["requests"]

        # Should have delete + insert
        assert any("deleteContentRange" in r for r in requests)
        assert any("insertText" in r for r in requests)

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_overwrites_empty_doc(self, mock_svc, _sleep) -> None:
        """Empty doc (just newline) — no delete needed, just insert."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.documents().get().execute.return_value = {
            "title": "Empty Doc",
            "body": {"content": [{"endIndex": 1}]},
        }

        result = do_overwrite(file_id="doc123", content="First content")

        assert isinstance(result, DoResult)
        assert result.file_id == "doc123"

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]

        # No delete for empty doc
        assert not any("deleteContentRange" in r for r in requests)
        # But still inserts
        assert any("insertText" in r for r in requests)

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_applies_heading_styles(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.documents().get().execute.return_value = {
            "title": "Styled Doc",
            "body": {"content": [{"endIndex": 1}]},
        }

        result = do_overwrite(file_id="doc123", content="# Title\n\n## Section\n\nBody")

        assert isinstance(result, DoResult)
        assert result.cues["heading_count"] == 2

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]

        style_requests = [r for r in requests if "updateParagraphStyle" in r]
        assert len(style_requests) == 2

        # First heading should be HEADING_1
        assert style_requests[0]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_1"
        # Second should be HEADING_2
        assert style_requests[1]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_2"

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_overwrite_routes_through_do(self, mock_svc, _sleep) -> None:
        """do(operation='overwrite') routes to do_overwrite."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.documents().get().execute.return_value = {
            "title": "Test",
            "body": {"content": [{"endIndex": 1}]},
        }

        result = do(operation="overwrite", file_id="doc1", content="hello")

        assert result["file_id"] == "doc1"
        assert result["operation"] == "overwrite"

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_delete_range_is_correct(self, mock_svc, _sleep) -> None:
        """Delete range should be [1, endIndex-1)."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.documents().get().execute.return_value = {
            "title": "Doc",
            "body": {"content": [
                {"endIndex": 1},
                {"endIndex": 100},
            ]},
        }

        do_overwrite(file_id="doc123", content="Replacement")

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        delete_req = next(r for r in requests if "deleteContentRange" in r)
        range_ = delete_req["deleteContentRange"]["range"]
        assert range_["startIndex"] == 1
        assert range_["endIndex"] == 99  # endIndex - 1

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_overwrite_doc_not_found(self, mock_svc, _sleep) -> None:
        """404 from Docs API becomes clean error."""
        from googleapiclient.errors import HttpError
        import httplib2

        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        resp = httplib2.Response({"status": "404"})
        mock_service.documents().get().execute.side_effect = HttpError(
            resp, b"Document not found"
        )

        result = do_overwrite(file_id="nonexistent", content="hello")

        assert result["error"] is True

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_overwrite_permission_denied(self, mock_svc, _sleep) -> None:
        """403 from Docs API becomes clean error."""
        from googleapiclient.errors import HttpError
        import httplib2

        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        resp = httplib2.Response({"status": "403"})
        mock_service.documents().get().execute.side_effect = HttpError(
            resp, b"Forbidden"
        )

        result = do_overwrite(file_id="readonly_doc", content="hello")

        assert result["error"] is True


class TestOverwriteFromSource:
    """Tests for overwrite with source path."""

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_overwrite_from_source(self, mock_svc, _sleep, tmp_path) -> None:
        """Overwrite reads content.md from source folder."""
        (tmp_path / "content.md").write_text("# From Source\n\nNew content.")

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.documents().get().execute.return_value = {
            "title": "Doc",
            "body": {"content": [{"endIndex": 1}]},
        }

        result = do_overwrite(
            file_id="doc123",
            source=str(tmp_path),
            base_path=str(tmp_path),
        )

        assert isinstance(result, DoResult)
        assert result.file_id == "doc123"
        assert result.operation == "overwrite"

    def test_overwrite_source_without_base_path_returns_error(self) -> None:
        result = do_overwrite(file_id="doc123", source="mise/some-folder/")
        assert result["error"] is True
        assert "base_path" in result["message"]
