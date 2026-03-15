"""Tests for do() dispatch infrastructure."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import orjson

from models import DoResult, FetchResult, FetchError, SearchResult
import server
from server import _DISPATCH, _REQUIRED_PARAMS, _REMOTE_ALLOWED_OPS, do, fetch, search
from tools import OPERATIONS


class TestDispatchConstant:
    """OPERATIONS constant and _DISPATCH dict stay in sync."""

    def test_operations_matches_dispatch_keys(self) -> None:
        """Every operation in OPERATIONS has a dispatch handler, and vice versa."""
        assert set(OPERATIONS) == set(_DISPATCH.keys())

    def test_operations_is_frozenset(self) -> None:
        """OPERATIONS is immutable."""
        assert isinstance(OPERATIONS, frozenset)

    def test_required_params_matches_dispatch_keys(self) -> None:
        """Every operation in _DISPATCH has a _REQUIRED_PARAMS entry."""
        assert set(_REQUIRED_PARAMS.keys()) == set(_DISPATCH.keys())

    def test_unknown_operation_returns_error(self) -> None:
        result = do(operation="explode")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "explode" in result["message"]
        # Error message lists supported operations
        for op in OPERATIONS:
            assert op in result["message"]

    def test_missing_required_params_returns_error(self) -> None:
        """do() with missing required params returns clear error naming them."""
        result = do(operation="move")
        assert result["error"] is True
        assert result["kind"] == "INVALID_INPUT"
        assert "file_id" in result["message"]
        assert "destination_folder_id" in result["message"]

    def test_missing_single_required_param(self) -> None:
        result = do(operation="rename", file_id="f1")
        assert result["error"] is True
        assert "title" in result["message"]


class TestAllOperationsReturnDoResult:
    """Every operation returns DoResult on success (not raw dict)."""

    @patch("retry.time.sleep")
    @patch("tools.move.get_sync_client")
    def test_move_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.move import do_move

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.side_effect = [
            {"mimeType": "application/vnd.google-apps.folder", "name": "Dest"},
            {"id": "f1", "name": "Test", "parents": ["old"], "webViewLink": ""},
        ]
        mock_client.patch_json.return_value = {
            "id": "f1", "name": "Test", "parents": ["new"], "webViewLink": "",
        }

        result = do_move("f1", "new")
        assert isinstance(result, DoResult)
        assert result.operation == "move"

    @patch("retry.time.sleep")
    @patch("tools.rename.get_sync_client")
    def test_rename_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.rename import do_rename

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.patch_json.return_value = {
            "id": "f1", "name": "New Name", "webViewLink": "",
        }

        result = do_rename("f1", "New Name")
        assert isinstance(result, DoResult)
        assert result.operation == "rename"

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_share_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.share import do_share

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        result = do_share("f1", "alice@example.com", confirm=True)
        assert isinstance(result, DoResult)
        assert result.operation == "share"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_prepend_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.edit import do_prepend

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "title": "Doc", "body": {"content": [{"endIndex": 50}]},
        }

        result = do_prepend("doc1", "hello")
        assert isinstance(result, DoResult)
        assert result.operation == "prepend"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_append_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.edit import do_append

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "title": "Doc", "body": {"content": [{"endIndex": 50}]},
        }

        result = do_append("doc1", "hello")
        assert isinstance(result, DoResult)
        assert result.operation == "append"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_replace_text_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.edit import do_replace_text

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "title": "Doc", "body": {"content": [{"endIndex": 50}]},
        }
        mock_client.post_json.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 1}}],
        }

        result = do_replace_text("doc1", "old", "new")
        assert isinstance(result, DoResult)
        assert result.operation == "replace_text"

    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content")
    def test_overwrite_returns_do_result(self, mock_upload, _sleep) -> None:
        from tools.overwrite import do_overwrite

        mock_upload.return_value = {"name": "Doc"}

        result = do_overwrite(file_id="doc1", content="hello")
        assert isinstance(result, DoResult)
        assert result.operation == "overwrite"

    @patch("retry.time.sleep")
    @patch("tools.create.get_sync_client")
    def test_create_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.create import do_create

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.request.return_value = MagicMock(
            content=orjson.dumps({
                "id": "doc1",
                "webViewLink": "https://docs.google.com/document/d/doc1/edit",
                "name": "Test",
            })
        )

        result = do_create("# Test", "Test")
        assert isinstance(result, DoResult)
        assert result.operation == "create"


class TestRemoteModeFiltering:
    """Remote mode restricts do() to safe operations only."""

    def test_remote_allowed_ops_is_subset_of_operations(self) -> None:
        """Every remote-allowed op must exist in the full OPERATIONS set."""
        assert _REMOTE_ALLOWED_OPS <= OPERATIONS

    def test_remote_blocks_restricted_ops(self) -> None:
        """Restricted operations return clear error in remote mode."""
        restricted = OPERATIONS - _REMOTE_ALLOWED_OPS
        assert len(restricted) > 0, "Test is meaningless if nothing is restricted"

        with patch.object(server, "_REMOTE_MODE", True):
            for op in restricted:
                result = do(operation=op)
                assert result["error"] is True, f"{op} should be blocked"
                assert "remote mode" in result["message"].lower(), f"{op} error unclear"

    def test_remote_error_does_not_leak_restricted_ops(self) -> None:
        """Error message lists only allowed ops, not the full set."""
        with patch.object(server, "_REMOTE_MODE", True):
            result = do(operation="overwrite")
            # Should list allowed ops
            for op in _REMOTE_ALLOWED_OPS:
                assert op in result["message"]
            # Should NOT list restricted ops
            for op in (OPERATIONS - _REMOTE_ALLOWED_OPS):
                assert op not in result["message"]

    def test_remote_allows_safe_ops(self) -> None:
        """Allowed ops pass through the remote gate (may still fail on params)."""
        with patch.object(server, "_REMOTE_MODE", True):
            for op in _REMOTE_ALLOWED_OPS:
                result = do(operation=op)
                # Should NOT get the "remote mode" error — may get param errors instead
                if result.get("error"):
                    assert "remote mode" not in result["message"].lower(), (
                        f"{op} was blocked by remote gate but shouldn't be"
                    )

    def test_stdio_mode_allows_all_ops(self) -> None:
        """In stdio mode, all ops pass through the remote gate."""
        with patch.object(server, "_REMOTE_MODE", False):
            result = do(operation="overwrite")
            # Should NOT get remote mode error (may get param error)
            if result.get("error"):
                assert "remote mode" not in result["message"].lower()


class TestDoResultToDictRoundTrip:
    """DoResult.to_dict() produces the expected MCP response shape."""

    def test_basic_to_dict(self) -> None:
        result = DoResult(
            file_id="f1", title="Test", web_link="https://example.com",
            operation="move", cues={"key": "val"},
        )
        d = result.to_dict()
        assert d == {
            "file_id": "f1", "title": "Test", "web_link": "https://example.com",
            "operation": "move", "cues": {"key": "val"},
        }

    def test_extras_merged_into_dict(self) -> None:
        result = DoResult(
            file_id="f1", title="Test", web_link="https://example.com",
            operation="create", cues={}, extras={"type": "doc"},
        )
        d = result.to_dict()
        assert d["type"] == "doc"
        assert d["operation"] == "create"


class TestFetchResultInlineContent:
    """FetchResult carries inline content for remote mode."""

    def test_to_dict_omits_content_when_none(self) -> None:
        result = FetchResult(
            path="mise/doc--test--abc/", content_file="mise/doc--test--abc/content.md",
            format="markdown", type="doc", metadata={"title": "Test"},
        )
        d = result.to_dict()
        assert "content" not in d
        assert "comments" not in d

    def test_to_dict_includes_content_when_set(self) -> None:
        result = FetchResult(
            path="mise/doc--test--abc/", content_file="mise/doc--test--abc/content.md",
            format="markdown", type="doc", metadata={"title": "Test"},
            content="# Hello\n\nWorld", comments="## Comments\n\n- Fix typo",
        )
        d = result.to_dict()
        assert d["content"] == "# Hello\n\nWorld"
        assert d["comments"] == "## Comments\n\n- Fix typo"


class TestRemoteFetch:
    """Remote fetch reads content back from deposit and returns inline."""

    def _make_fetch_result(self, base_path: Path, content: str, comments: str | None = None) -> FetchResult:
        """Create a deposit folder with content and return a FetchResult pointing to it."""
        folder = base_path / "mise" / "doc--test--abc123"
        folder.mkdir(parents=True, exist_ok=True)
        content_path = folder / "content.md"
        content_path.write_text(content)
        if comments:
            (folder / "comments.md").write_text(comments)
        return FetchResult(
            path=str(folder), content_file=str(content_path),
            format="markdown", type="doc", metadata={"title": "Test"},
            cues={"files": ["content.md"], "warnings": [], "content_length": len(content)},
        )

    def test_remote_fetch_includes_inline_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected_content = "# Test Document\n\nHello world."
            result = self._make_fetch_result(Path(tmp), expected_content)

            with patch.object(server, "_REMOTE_MODE", True), \
                 patch("server.do_fetch", return_value=result):
                d = fetch(file_id="abc123", base_path=tmp)

        assert d["content"] == expected_content
        assert "comments" not in d

    def test_remote_fetch_includes_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._make_fetch_result(
                Path(tmp), "# Doc", comments="## Open Comments\n\n- Fix this",
            )

            with patch.object(server, "_REMOTE_MODE", True), \
                 patch("server.do_fetch", return_value=result):
                d = fetch(file_id="abc123", base_path=tmp)

        assert d["content"] == "# Doc"
        assert d["comments"] == "## Open Comments\n\n- Fix this"

    def test_remote_fetch_uses_temp_dir_when_no_base_path(self) -> None:
        """When base_path is empty, remote fetch creates a temp dir and cleans up."""
        with tempfile.TemporaryDirectory() as tmp:
            result = self._make_fetch_result(Path(tmp), "# Content")

            with patch.object(server, "_REMOTE_MODE", True), \
                 patch("server.do_fetch", return_value=result) as mock_fetch:
                d = fetch(file_id="abc123")

            # do_fetch was called (base_path will be the temp dir)
            assert mock_fetch.called
            assert d["content"] == "# Content"

    def test_remote_fetch_passes_through_errors(self) -> None:
        error = FetchError(kind="not_found", message="File not found")

        with patch.object(server, "_REMOTE_MODE", True), \
             patch("server.do_fetch", return_value=error):
            d = fetch(file_id="abc123", base_path="/tmp")

        assert d["error"] is True
        assert d["kind"] == "not_found"

    def test_stdio_fetch_does_not_inline(self) -> None:
        """Stdio mode returns normal result without inline content."""
        result = FetchResult(
            path="mise/doc--test--abc/", content_file="mise/doc--test--abc/content.md",
            format="markdown", type="doc", metadata={"title": "Test"}, cues={},
        )

        with patch.object(server, "_REMOTE_MODE", False), \
             patch("server.do_fetch", return_value=result):
            d = fetch(file_id="abc123", base_path="/tmp/project")

        assert "content" not in d

    def test_remote_fetch_skips_binary_content(self) -> None:
        """Binary formats (images) get metadata but no inline content."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "mise" / "image--photo--abc123"
            folder.mkdir(parents=True)
            img_path = folder / "content.png"
            img_path.write_bytes(b"\x89PNG fake image data")

            result = FetchResult(
                path=str(folder), content_file=str(img_path),
                format="image", type="image", metadata={"title": "Photo"},
                cues={"files": ["content.png"], "warnings": [], "content_length": 20},
            )

            with patch.object(server, "_REMOTE_MODE", True), \
                 patch("server.do_fetch", return_value=result):
                d = fetch(file_id="abc123", base_path=tmp)

        assert "content" not in d
        assert any("binary" in w.lower() for w in d["cues"]["warnings"])


class TestRemoteSearch:
    """Remote search returns full results inline without filesystem deposit."""

    def test_remote_search_returns_full_results(self) -> None:
        search_result = SearchResult(
            query="Q4 planning",
            sources=["drive"],
            drive_results=[
                {"name": "Q4 Report", "id": "abc123", "mimeType": "application/vnd.google-apps.document"},
                {"name": "Q4 Budget", "id": "def456", "mimeType": "application/vnd.google-apps.spreadsheet"},
            ],
        )
        # Simulate do_search setting the path (as it normally does)
        search_result.path = "/tmp/mise/search--q4-planning--2026.json"

        with patch.object(server, "_REMOTE_MODE", True), \
             patch("server.do_search", return_value=search_result):
            d = search(query="Q4 planning", base_path="/tmp")

        # Remote mode strips path — full results returned inline
        assert "path" not in d
        assert d["query"] == "Q4 planning"
        assert len(d["drive_results"]) == 2

    def test_remote_search_works_without_base_path(self) -> None:
        search_result = SearchResult(
            query="test", sources=["drive"], drive_results=[],
        )
        search_result.path = "/tmp/mise/search--test.json"

        with patch.object(server, "_REMOTE_MODE", True), \
             patch("server.do_search", return_value=search_result):
            d = search(query="test")

        assert d["query"] == "test"

    def test_stdio_search_requires_base_path(self) -> None:
        with patch.object(server, "_REMOTE_MODE", False):
            d = search(query="test")

        assert d["error"] is True
        assert "base_path" in d["message"]
