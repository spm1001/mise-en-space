"""Tests for surgical edit operations (prepend, append, replace_text)."""

from unittest.mock import patch, MagicMock

from models import DoResult, MiseError, ErrorKind
from server import do
from tools.edit import do_prepend, do_append, do_replace_text

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


def _google_doc_metadata(name: str = "Test Doc") -> dict:
    """Metadata dict for a Google Doc (routes to Docs API)."""
    return {
        "mimeType": GOOGLE_DOC_MIME,
        "name": name,
        "webViewLink": f"https://docs.google.com/document/d/doc123/edit",
    }


def _plain_file_metadata(
    name: str = "readme.md", mime: str = "text/markdown", size: int = 1024,
) -> dict:
    """Metadata dict for a plain file (routes to Drive Files API)."""
    return {
        "mimeType": mime,
        "name": name,
        "webViewLink": f"https://drive.google.com/file/d/file123/view",
        "size": str(size),
    }


def _mock_sync_client(end_index: int = 50, title: str = "Test Doc"):
    """Create a mock httpx sync client with standard document response."""
    mock_client = MagicMock()
    mock_client.get_json.return_value = {
        "title": title,
        "body": {"content": [{"endIndex": 1}, {"endIndex": end_index}]},
    }
    return mock_client


# =============================================================================
# VALIDATION (no API calls — no mocks needed)
# =============================================================================

class TestDoPrependValidation:
    def test_prepend_without_file_id_returns_error(self) -> None:
        result = do_prepend(content="hello")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_prepend_empty_content_returns_error(self) -> None:
        result = do_prepend("doc123", "")
        assert result["error"] is True
        assert "content" in result["message"]

    def test_prepend_validation_through_do(self) -> None:
        result = do(operation="prepend", content="hello")
        assert result["error"] is True
        assert "file_id" in result["message"]


class TestDoAppendValidation:
    def test_append_without_file_id_returns_error(self) -> None:
        result = do_append(content="hello")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_append_empty_content_returns_error(self) -> None:
        result = do_append("doc123", "")
        assert result["error"] is True
        assert "content" in result["message"]

    def test_append_validation_through_do(self) -> None:
        result = do(operation="append", content="hello")
        assert result["error"] is True
        assert "file_id" in result["message"]


class TestDoReplaceTextValidation:
    def test_replace_without_file_id_returns_error(self) -> None:
        result = do_replace_text(find="old", content="new")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_replace_without_find_returns_error(self) -> None:
        result = do_replace_text(file_id="doc1", content="new")
        assert result["error"] is True
        assert "find" in result["message"]

    def test_replace_without_content_returns_error(self) -> None:
        result = do_replace_text("doc123", "old", None)
        assert result["error"] is True
        assert "content" in result["message"]

    def test_replace_validation_through_do(self) -> None:
        result = do(operation="replace_text", find="old", content="new")
        assert result["error"] is True
        assert "file_id" in result["message"]


# =============================================================================
# GOOGLE DOC OPERATIONS (existing Docs API path)
# metadata=None (default) falls through to Docs API — no metadata mock needed
# =============================================================================

class TestDoPrepend:
    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_prepends_text(self, mock_get_client, _sleep) -> None:
        mock_client = _mock_sync_client()
        mock_get_client.return_value = mock_client

        result = do_prepend("doc123", "Executive Summary\n\n")

        assert isinstance(result, DoResult)
        assert result.file_id == "doc123"
        assert result.operation == "prepend"
        assert result.cues["inserted_chars"] == 19

        # Verify post_json was called with insertText at index 1
        post_calls = mock_client.post_json.call_args_list
        assert len(post_calls) == 1
        call_kwargs = post_calls[0][1]
        requests = call_kwargs["json_body"]["requests"]
        insert_req = requests[0]["insertText"]
        assert insert_req["location"]["index"] == 1
        assert insert_req["text"] == "Executive Summary\n\n"

    @patch("retry.time.sleep")
    @patch("server.get_file_metadata", return_value=_google_doc_metadata())
    @patch("tools.edit.get_sync_client")
    def test_prepend_routes_through_do(self, mock_get_client, _meta, _sleep) -> None:
        mock_get_client.return_value = _mock_sync_client()

        result = do(operation="prepend", file_id="doc1", content="Hello\n")

        assert result["operation"] == "prepend"
        assert result["file_id"] == "doc1"


class TestDoAppend:
    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_appends_text(self, mock_get_client, _sleep) -> None:
        mock_client = _mock_sync_client(end_index=100)
        mock_get_client.return_value = mock_client

        result = do_append("doc123", "\n\n--- Notes ---")

        assert isinstance(result, DoResult)
        assert result.operation == "append"
        assert result.cues["inserted_chars"] == 15

        # Verify post_json was called with insertText at endIndex - 1
        post_calls = mock_client.post_json.call_args_list
        assert len(post_calls) == 1
        call_kwargs = post_calls[0][1]
        requests = call_kwargs["json_body"]["requests"]
        insert_req = requests[0]["insertText"]
        assert insert_req["location"]["index"] == 99  # endIndex - 1

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_append_to_empty_doc(self, mock_get_client, _sleep) -> None:
        mock_client = _mock_sync_client(end_index=1)
        mock_get_client.return_value = mock_client

        result = do_append("doc123", "First content")

        post_calls = mock_client.post_json.call_args_list
        call_kwargs = post_calls[0][1]
        requests = call_kwargs["json_body"]["requests"]
        # Empty doc: max(1-1, 1) = 1
        assert requests[0]["insertText"]["location"]["index"] == 1

    @patch("retry.time.sleep")
    @patch("server.get_file_metadata", return_value=_google_doc_metadata())
    @patch("tools.edit.get_sync_client")
    def test_append_routes_through_do(self, mock_get_client, _meta, _sleep) -> None:
        mock_get_client.return_value = _mock_sync_client()

        result = do(operation="append", file_id="doc1", content="Tail\n")

        assert result["operation"] == "append"


class TestDoReplaceText:
    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_replaces_text(self, mock_get_client, _sleep) -> None:
        mock_client = _mock_sync_client()
        mock_get_client.return_value = mock_client

        mock_client.post_json.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 3}}],
        }

        result = do_replace_text("doc123", "DRAFT", "FINAL")

        assert isinstance(result, DoResult)
        assert result.operation == "replace_text"
        assert result.cues["find"] == "DRAFT"
        assert result.cues["replace"] == "FINAL"
        assert result.cues["occurrences_changed"] == 3

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_replace_with_empty_string_deletes(self, mock_get_client, _sleep) -> None:
        """Empty replace string effectively deletes all matches."""
        mock_client = _mock_sync_client()
        mock_get_client.return_value = mock_client

        mock_client.post_json.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 2}}],
        }

        result = do_replace_text("doc123", "remove me", "")

        assert isinstance(result, DoResult)
        assert result.cues["replace"] == ""
        assert result.cues["occurrences_changed"] == 2

    @patch("retry.time.sleep")
    @patch("server.get_file_metadata", return_value=_google_doc_metadata())
    @patch("tools.edit.get_sync_client")
    def test_replace_routes_through_do(self, mock_get_client, _meta, _sleep) -> None:
        mock_client = _mock_sync_client()
        mock_get_client.return_value = mock_client
        mock_client.post_json.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 1}}],
        }

        result = do(operation="replace_text", file_id="doc1", find="old", content="new")

        assert result["operation"] == "replace_text"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_replace_zero_occurrences(self, mock_get_client, _sleep) -> None:
        mock_client = _mock_sync_client()
        mock_get_client.return_value = mock_client
        mock_client.post_json.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 0}}],
        }

        result = do_replace_text("doc123", "nonexistent", "replacement")

        assert isinstance(result, DoResult)
        assert result.cues["occurrences_changed"] == 0


# =============================================================================
# ERROR PATHS (dispatch metadata lookup fails)
# =============================================================================

class TestEditErrorPaths:
    """API errors surface cleanly via dispatch metadata pre-fetch."""

    @patch("server.get_file_metadata")
    def test_prepend_file_not_found_through_do(self, mock_meta) -> None:
        mock_meta.side_effect = MiseError(ErrorKind.NOT_FOUND, "File not found: nonexistent")

        result = do(operation="prepend", file_id="nonexistent", content="hello")

        assert result["error"] is True
        assert result["kind"] == "not_found"

    @patch("server.get_file_metadata")
    def test_append_permission_denied_through_do(self, mock_meta) -> None:
        mock_meta.side_effect = MiseError(ErrorKind.PERMISSION_DENIED, "No access")

        result = do(operation="append", file_id="readonly", content="hello")

        assert result["error"] is True
        assert result["kind"] == "permission_denied"

    @patch("server.get_file_metadata")
    def test_replace_text_file_not_found_through_do(self, mock_meta) -> None:
        mock_meta.side_effect = MiseError(ErrorKind.NOT_FOUND, "File not found")

        result = do(operation="replace_text", file_id="nonexistent", find="x", content="y")

        assert result["error"] is True
        assert result["kind"] == "not_found"


# =============================================================================
# PLAIN FILE OPERATIONS (Drive Files API path — metadata passed directly)
# =============================================================================

class TestPlainFilePrepend:
    @patch("retry.time.sleep")
    @patch("tools.plain_file.upload_file_content")
    @patch("tools.plain_file.download_file_content")
    def test_prepends_to_markdown_file(self, mock_dl, mock_ul, _sleep) -> None:
        mock_dl.return_value = b"# Existing Content\n\nBody text."

        prepend_text = "---\ntitle: New\n---\n\n"
        result = do_prepend("file123", prepend_text, metadata=_plain_file_metadata())

        assert isinstance(result, DoResult)
        assert result.operation == "prepend"
        assert result.cues["plain_file"] is True
        assert result.cues["inserted_chars"] == len(prepend_text)

        # Verify upload was called with prepended content
        uploaded = mock_ul.call_args[0][1]
        assert uploaded.startswith(b"---\ntitle: New\n---\n\n# Existing Content")

    def test_rejects_binary_file(self) -> None:
        result = do_prepend("file123", "hello", metadata=_plain_file_metadata(
            name="photo.jpg", mime="image/jpeg",
        ))

        assert result["error"] is True
        assert "binary" in result["message"].lower()


class TestPlainFileAppend:
    @patch("retry.time.sleep")
    @patch("tools.plain_file.upload_file_content")
    @patch("tools.plain_file.download_file_content")
    def test_appends_to_json_file(self, mock_dl, mock_ul, _sleep) -> None:
        mock_dl.return_value = b'{"key": "value"}'

        result = do_append("file123", '\n// end', metadata=_plain_file_metadata(
            name="config.json", mime="application/json",
        ))

        assert isinstance(result, DoResult)
        assert result.operation == "append"
        assert result.cues["plain_file"] is True

        uploaded = mock_ul.call_args[0][1]
        assert uploaded == b'{"key": "value"}\n// end'


class TestPlainFileReplaceText:
    @patch("retry.time.sleep")
    @patch("tools.plain_file.upload_file_content")
    @patch("tools.plain_file.download_file_content")
    def test_replaces_text_in_markdown(self, mock_dl, mock_ul, _sleep) -> None:
        mock_dl.return_value = b"# Introduction\n\nDRAFT: This is a draft.\nDRAFT: Another draft."

        result = do_replace_text("file123", "DRAFT", "FINAL", metadata=_plain_file_metadata())

        assert isinstance(result, DoResult)
        assert result.cues["occurrences_changed"] == 2
        assert result.cues["plain_file"] is True

        uploaded = mock_ul.call_args[0][1]
        assert b"FINAL: This is a draft." in uploaded
        assert b"DRAFT" not in uploaded

    @patch("retry.time.sleep")
    @patch("tools.plain_file.download_file_content")
    def test_replace_text_not_found(self, mock_dl, _sleep) -> None:
        mock_dl.return_value = b"No matches here."

        result = do_replace_text("file123", "nonexistent", "replacement", metadata=_plain_file_metadata())

        assert isinstance(result, DoResult)
        assert result.cues["occurrences_changed"] == 0
        assert "warning" in result.cues

    def test_rejects_binary_for_replace_text(self) -> None:
        result = do_replace_text("file123", "find", "replace", metadata=_plain_file_metadata(
            name="image.png", mime="image/png",
        ))

        assert result["error"] is True
        assert "binary" in result["message"].lower()

    @patch("retry.time.sleep")
    @patch("tools.plain_file.upload_file_content")
    @patch("tools.plain_file.download_file_content")
    def test_svg_is_text_safe(self, mock_dl, mock_ul, _sleep) -> None:
        """SVG files (image/svg+xml) should be treated as text, not binary."""
        mock_dl.return_value = b'<svg><text>OLD</text></svg>'

        result = do_replace_text("file123", "OLD", "NEW", metadata=_plain_file_metadata(
            name="diagram.svg", mime="image/svg+xml",
        ))

        assert isinstance(result, DoResult)
        assert result.cues["occurrences_changed"] == 1

    def test_rejects_google_sheet(self) -> None:
        """Google Sheets routed here should get a clear error, not an opaque API failure."""
        result = do_replace_text("file123", "find", "replace", metadata=_plain_file_metadata(
            name="Budget.gsheet", mime="application/vnd.google-apps.spreadsheet",
        ))

        assert result["error"] is True
        assert "Spreadsheet" in result["message"]

    @patch("retry.time.sleep")
    @patch("tools.plain_file.upload_file_content")
    @patch("tools.plain_file.download_file_content")
    def test_large_file_warning(self, mock_dl, mock_ul, _sleep) -> None:
        """Files >5MB trigger a warning in cues."""
        mock_dl.return_value = b"x" * 100

        result = do_replace_text("file123", "x", "y", metadata=_plain_file_metadata(
            size=6 * 1024 * 1024,  # 6MB
        ))

        assert isinstance(result, DoResult)
        assert "warning" in result.cues
        assert "6.0MB" in result.cues["warning"]
