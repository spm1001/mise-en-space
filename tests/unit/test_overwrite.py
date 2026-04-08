"""Tests for overwrite operation."""

from unittest.mock import patch, MagicMock

from models import DoResult, MiseError, ErrorKind
from server import do
from tools.overwrite import do_overwrite

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
# GOOGLE DOC OVERWRITE (Drive import — text/markdown → formatted Google Doc)
# =============================================================================

class TestDoOverwrite:
    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content")
    def test_overwrites_doc_with_content(self, mock_upload, _sleep) -> None:
        mock_upload.return_value = {"name": "My Doc"}

        result = do_overwrite(file_id="doc123", content="New content here")

        assert isinstance(result, DoResult)
        assert result.file_id == "doc123"
        assert result.title == "My Doc"
        assert result.operation == "overwrite"
        assert result.cues["char_count"] == 16

        mock_upload.assert_called_once_with(
            "doc123", b"New content here", "text/markdown",
        )

    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content")
    def test_overwrites_doc_with_markdown_formatting(self, mock_upload, _sleep) -> None:
        """Markdown with headings, bold, tables is passed through for Drive to render."""
        mock_upload.return_value = {"name": "Styled Doc"}
        markdown = "# Title\n\nThis has **bold** and a table.\n\n| A | B |\n|---|---|\n| 1 | 2 |"

        result = do_overwrite(file_id="doc123", content=markdown)

        assert isinstance(result, DoResult)
        assert result.cues["char_count"] == len(markdown)
        # Markdown passed verbatim — Drive import handles rendering
        mock_upload.assert_called_once_with(
            "doc123", markdown.encode("utf-8"), "text/markdown",
        )

    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content")
    def test_uses_metadata_title_when_available(self, mock_upload, _sleep) -> None:
        mock_upload.return_value = {"name": "API Name"}

        result = do_overwrite(
            file_id="doc123", content="hello",
            metadata=_google_doc_metadata("Metadata Name"),
        )

        assert isinstance(result, DoResult)
        assert result.title == "Metadata Name"

    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content")
    def test_falls_back_to_api_title_without_metadata(self, mock_upload, _sleep) -> None:
        mock_upload.return_value = {"name": "From API"}

        result = do_overwrite(file_id="doc123", content="hello")

        assert isinstance(result, DoResult)
        assert result.title == "From API"

    @patch("retry.time.sleep")
    @patch("server.get_file_metadata", return_value=_google_doc_metadata())
    @patch("tools.overwrite.upload_file_content")
    def test_overwrite_routes_through_do(self, mock_upload, _meta, _sleep) -> None:
        mock_upload.return_value = {"name": "Test"}

        result = do(operation="overwrite", file_id="doc1", content="hello")

        assert result["file_id"] == "doc1"
        assert result["operation"] == "overwrite"


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
    @patch("tools.overwrite.upload_file_content")
    def test_google_doc_uses_drive_import(self, mock_upload, _sleep) -> None:
        """Google Docs with metadata passed route through Drive import."""
        mock_upload.return_value = {"name": "Google Doc"}

        result = do_overwrite(file_id="doc123", content="hello", metadata=_google_doc_metadata())

        assert isinstance(result, DoResult)
        mock_upload.assert_called_once_with("doc123", b"hello", "text/markdown")


class TestOverwriteFromFilePath:
    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content")
    def test_overwrites_doc_from_file_path(self, mock_upload, _sleep, tmp_path) -> None:
        """file_path reads content directly from a local file."""
        md_file = tmp_path / "report.md"
        md_file.write_text("# Fresh Content\n\nUpdated.")
        mock_upload.return_value = {"name": "Doc"}

        result = do_overwrite(
            file_id="doc123",
            file_path=str(md_file),
            base_path=str(tmp_path),
        )

        assert isinstance(result, DoResult)
        assert result.file_id == "doc123"
        assert result.operation == "overwrite"
        mock_upload.assert_called_once_with(
            "doc123", b"# Fresh Content\n\nUpdated.", "text/markdown",
        )

    @patch("retry.time.sleep")
    @patch("tools.plain_file.upload_file_content")
    def test_overwrites_plain_file_from_file_path(self, mock_ul, _sleep, tmp_path) -> None:
        """file_path works for plain file overwrite too."""
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("New plain content")

        result = do_overwrite(
            file_id="file123",
            file_path=str(txt_file),
            base_path=str(tmp_path),
            metadata=_plain_file_metadata(name="notes.txt", mime="text/plain"),
        )

        assert isinstance(result, DoResult)
        assert result.cues["plain_file"] is True

    def test_file_path_mutual_exclusion_with_content(self, tmp_path) -> None:
        md_file = tmp_path / "test.md"
        md_file.write_text("content")
        result = do_overwrite(
            file_id="doc123", content="inline",
            file_path=str(md_file), base_path=str(tmp_path),
        )
        assert result["error"] is True
        assert "only one" in result["message"].lower()

    def test_file_path_mutual_exclusion_with_source(self, tmp_path) -> None:
        md_file = tmp_path / "test.md"
        md_file.write_text("content")
        result = do_overwrite(
            file_id="doc123", source=str(tmp_path),
            file_path=str(md_file), base_path=str(tmp_path),
        )
        assert result["error"] is True
        assert "only one" in result["message"].lower()

    def test_file_path_not_found(self, tmp_path) -> None:
        result = do_overwrite(
            file_id="doc123",
            file_path=str(tmp_path / "missing.md"),
            base_path=str(tmp_path),
        )
        assert result["error"] is True
        assert "not found" in result["message"].lower()

    def test_file_path_containment_check(self, tmp_path) -> None:
        md_file = tmp_path / "test.md"
        md_file.write_text("content")
        result = do_overwrite(
            file_id="doc123",
            file_path=str(md_file),
            base_path=str(tmp_path / "subdir"),
        )
        assert result["error"] is True
        assert "within" in result["message"].lower()


class TestOverwriteFromSource:
    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content")
    def test_overwrite_from_source_google_doc(self, mock_upload, _sleep, tmp_path) -> None:
        """Google Doc overwrite reads content.md from source folder."""
        (tmp_path / "content.md").write_text("# From Source\n\nNew content.")
        mock_upload.return_value = {"name": "Doc"}

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
