"""Tests for overwrite operation."""

from unittest.mock import patch, MagicMock

from models import DoResult, MiseError, ErrorKind
from server import do
from tools.overwrite import do_overwrite, _strip_headings

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


def _google_doc_metadata(name: str = "Test Doc") -> dict:
    return {
        "mimeType": GOOGLE_DOC_MIME,
        "name": name,
        "webViewLink": f"https://docs.google.com/document/d/doc123/edit",
    }


def _plain_file_metadata(
    name: str = "readme.md", mime: str = "text/markdown", size: int = 1024,
) -> dict:
    return {
        "mimeType": mime,
        "name": name,
        "webViewLink": f"https://drive.google.com/file/d/file123/view",
        "size": str(size),
    }


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
        assert plain == "intro\nH1\nbody\nH2"
        assert headings[0] == (6, 8, 1)
        assert headings[1] == (14, 16, 2)

    def test_heading_positions_with_emoji(self) -> None:
        """Docs API uses UTF-16 code units. Emoji are 2 units, not 1."""
        text = "🎯 intro\n# Title"
        plain, headings = _strip_headings(text)
        assert plain == "🎯 intro\nTitle"
        assert headings[0] == (9, 14, 1)

    def test_heading_with_emoji_in_heading(self) -> None:
        """Emoji inside heading text affects end position."""
        text = "# 🚀 Launch"
        plain, headings = _strip_headings(text)
        assert plain == "🚀 Launch"
        assert headings[0] == (0, 9, 1)


# =============================================================================
# VALIDATION (no API calls)
# =============================================================================

class TestDoOverwriteValidation:
    def test_overwrite_without_file_id_returns_error(self) -> None:
        result = do_overwrite(content="hello")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "file_id" in result["message"]

    def test_overwrite_with_both_content_and_source_returns_error(self) -> None:
        result = do_overwrite(file_id="doc123", content="hello", source="/tmp/fake", base_path="/tmp")
        assert result["error"] is True
        assert "not both" in result["message"]

    def test_overwrite_validation_through_do(self) -> None:
        result = do(operation="overwrite", content="hello")
        assert result["error"] is True
        assert "file_id" in result["message"]


# =============================================================================
# GOOGLE DOC OVERWRITE (Docs API path — metadata=None falls through)
# =============================================================================

class TestDoOverwrite:
    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_overwrites_doc_with_plain_text(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.documents().get().execute.return_value = {
            "title": "My Doc",
            "body": {"content": [{"endIndex": 1}, {"endIndex": 50}]},
        }

        result = do_overwrite(file_id="doc123", content="New content here")

        assert isinstance(result, DoResult)
        assert result.file_id == "doc123"
        assert result.title == "My Doc"
        assert result.operation == "overwrite"
        assert result.cues["char_count"] == 16

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        assert any("deleteContentRange" in r for r in requests)
        assert any("insertText" in r for r in requests)

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_overwrites_empty_doc(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.documents().get().execute.return_value = {
            "title": "Empty Doc",
            "body": {"content": [{"endIndex": 1}]},
        }

        result = do_overwrite(file_id="doc123", content="First content")

        assert isinstance(result, DoResult)
        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        assert not any("deleteContentRange" in r for r in requests)
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
        assert style_requests[0]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_1"
        assert style_requests[1]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_2"

    @patch("retry.time.sleep")
    @patch("server.get_file_metadata", return_value=_google_doc_metadata())
    @patch("tools.overwrite.get_docs_service")
    def test_overwrite_routes_through_do(self, mock_svc, _meta, _sleep) -> None:
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
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.documents().get().execute.return_value = {
            "title": "Doc",
            "body": {"content": [{"endIndex": 1}, {"endIndex": 100}]},
        }

        do_overwrite(file_id="doc123", content="Replacement")

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        delete_req = next(r for r in requests if "deleteContentRange" in r)
        range_ = delete_req["deleteContentRange"]["range"]
        assert range_["startIndex"] == 1
        assert range_["endIndex"] == 99


# =============================================================================
# ERROR PATHS
# =============================================================================

class TestOverwriteErrorPaths:
    @patch("server.get_file_metadata")
    def test_overwrite_file_not_found_through_do(self, mock_meta) -> None:
        mock_meta.side_effect = MiseError(ErrorKind.NOT_FOUND, "File not found")

        result = do(operation="overwrite", file_id="nonexistent", content="hello")

        assert result["error"] is True
        assert result["kind"] == "not_found"

    @patch("server.get_file_metadata")
    def test_overwrite_permission_denied_through_do(self, mock_meta) -> None:
        mock_meta.side_effect = MiseError(ErrorKind.PERMISSION_DENIED, "No access")

        result = do(operation="overwrite", file_id="readonly_doc", content="hello")

        assert result["error"] is True
        assert result["kind"] == "permission_denied"


# =============================================================================
# PLAIN FILE OVERWRITE (Drive Files API path — metadata passed directly)
# =============================================================================

class TestPlainFileOverwrite:
    @patch("retry.time.sleep")
    @patch("tools.plain_file.upload_file_content")
    def test_overwrites_markdown_with_content(self, mock_ul, _sleep) -> None:
        result = do_overwrite(
            file_id="file123", content="# New Content\n\nFresh start.",
            metadata=_plain_file_metadata(),
        )

        assert isinstance(result, DoResult)
        assert result.operation == "overwrite"
        assert result.cues["plain_file"] is True
        assert result.cues["mime_type"] == "text/markdown"

        uploaded = mock_ul.call_args[0][1]
        assert uploaded == b"# New Content\n\nFresh start."

    @patch("retry.time.sleep")
    @patch("tools.plain_file.upload_file_content")
    def test_overwrites_from_source(self, mock_ul, _sleep, tmp_path) -> None:
        (tmp_path / "content.md").write_text("# From deposit\n\nContent here.")

        result = do_overwrite(
            file_id="file123",
            source=str(tmp_path),
            base_path=str(tmp_path),
            metadata=_plain_file_metadata(),
        )

        assert isinstance(result, DoResult)
        assert result.cues["plain_file"] is True

        uploaded = mock_ul.call_args[0][1]
        assert b"# From deposit" in uploaded

    @patch("retry.time.sleep")
    @patch("tools.plain_file.upload_file_content")
    def test_overwrites_binary_file(self, mock_ul, _sleep) -> None:
        """Binary overwrite works — full replacement, no text decode needed."""
        result = do_overwrite(
            file_id="file123", content="raw bytes here",
            metadata=_plain_file_metadata(name="photo.jpg", mime="image/jpeg"),
        )

        assert isinstance(result, DoResult)
        assert result.cues["plain_file"] is True

    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_google_doc_still_uses_docs_api(self, mock_svc, _sleep) -> None:
        """Google Docs with metadata passed still route through Docs API."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.documents().get().execute.return_value = {
            "title": "Google Doc",
            "body": {"content": [{"endIndex": 1}]},
        }

        result = do_overwrite(file_id="doc123", content="hello", metadata=_google_doc_metadata())

        assert isinstance(result, DoResult)
        mock_service.documents().batchUpdate.assert_called_once()


class TestOverwriteFromSource:
    @patch("retry.time.sleep")
    @patch("tools.overwrite.get_docs_service")
    def test_overwrite_from_source_google_doc(self, mock_svc, _sleep, tmp_path) -> None:
        """Google Doc overwrite reads content.md from source folder."""
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
