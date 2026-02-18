"""Tests for do tool (create and future operations)."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from models import CreateResult, CreateError
from server import do
from tools.create import (
    do_create, _read_source, _read_multi_tab_source, _csv_text_to_values,
    DOC_TYPE_TO_MIME,
)


class TestDoToolRouting:
    """MCP do() wrapper routes operations correctly."""

    def test_unknown_operation_returns_error(self) -> None:
        result = do(operation="explode")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "explode" in result["message"]

    def test_create_without_content_returns_error(self) -> None:
        result = do(operation="create", title="Title")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "content" in result["message"]

    def test_create_without_title_returns_error(self) -> None:
        result = do(operation="create", content="# Hello")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "title" in result["message"]

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_create_routes_to_do_create(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "Test",
        }

        result = do(operation="create", content="# Test", title="Test")

        assert result["file_id"] == "doc1"
        assert result["type"] == "doc"

    def test_default_operation_is_create(self) -> None:
        """Calling do() without operation should default to create."""
        result = do(content=None, title=None)
        # Should hit the create validation (missing content/title), not unknown operation
        assert result["kind"] == "invalid_input"
        assert "content" in result["message"]


class TestDoCreateValidation:
    """Input validation before API calls."""

    def test_invalid_doc_type_returns_error(self) -> None:
        result = do_create("content", "Title", doc_type="invalid")
        assert isinstance(result, CreateError)
        assert result.kind == "invalid_input"

    def test_slides_not_implemented(self) -> None:
        result = do_create("content", "Title", doc_type="slides")
        assert isinstance(result, CreateError)
        assert result.kind == "not_implemented"


class TestDoCreateDoc:
    """Test doc creation with mocked Drive API."""

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_creates_doc(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "new_doc_id",
            "webViewLink": "https://docs.google.com/document/d/new_doc_id/edit",
            "name": "My Document",
        }

        result = do_create("# Hello", "My Document")

        assert isinstance(result, CreateResult)
        assert result.file_id == "new_doc_id"
        assert result.web_link == "https://docs.google.com/document/d/new_doc_id/edit"
        assert result.title == "My Document"
        assert result.doc_type == "doc"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_creates_doc_with_folder(self, mock_svc, _sleep) -> None:
        """folder_id passed as parent."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "In Folder",
        }

        result = do_create("content", "In Folder", folder_id="folder123")

        assert isinstance(result, CreateResult)

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_missing_name_uses_title(self, mock_svc, _sleep) -> None:
        """If API doesn't return name, falls back to provided title."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
        }

        result = do_create("content", "Fallback Title")

        assert isinstance(result, CreateResult)
        assert result.title == "Fallback Title"


class TestCreateCues:
    """Post-action cues on create responses."""

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_cues_include_folder_name(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "Test",
            "parents": ["folder123"],
        }
        mock_service.files().get().execute.return_value = {"name": "Project Files"}

        result = do_create("# Test", "Test", folder_id="folder123")

        assert isinstance(result, CreateResult)
        assert result.cues is not None
        assert result.cues["folder"] == "Project Files"
        assert result.cues["folder_id"] == "folder123"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_cues_degrade_without_parents(self, mock_svc, _sleep) -> None:
        """No parents in response → cues still present with fallback."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "Test",
        }

        result = do_create("# Test", "Test")

        assert isinstance(result, CreateResult)
        assert result.cues is not None
        assert result.cues["folder"] == "My Drive"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_cues_in_to_dict(self, mock_svc, _sleep) -> None:
        """Cues appear in serialized output."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "Test",
            "parents": ["f1"],
        }
        mock_service.files().get().execute.return_value = {"name": "Archive"}

        result = do_create("# Test", "Test")
        d = result.to_dict()

        assert "cues" in d
        assert d["cues"]["folder"] == "Archive"


class TestDoCreateErrorHandling:
    """Unexpected exceptions return CreateError, not crashes."""

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_unexpected_exception_returns_create_error(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.side_effect = Exception("boom")

        result = do_create("# Test", "Test")

        assert isinstance(result, CreateError)
        assert result.kind == "unknown"
        assert "boom" in result.message

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_sheet_unexpected_exception_returns_create_error(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.side_effect = RuntimeError("quota exceeded")

        result = do_create("a,b\n1,2", "Test", doc_type="sheet")

        assert isinstance(result, CreateError)
        assert "quota exceeded" in result.message


class TestDoCreateSheet:
    """Test sheet creation with mocked Drive API."""

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_creates_sheet_from_csv(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "Q4 Analysis",
        }

        csv_content = "Name,Amount\nAlice,100\nBob,200"
        result = do_create(csv_content, "Q4 Analysis", doc_type="sheet")

        assert isinstance(result, CreateResult)
        assert result.file_id == "sheet1"
        assert result.doc_type == "sheet"
        assert result.title == "Q4 Analysis"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_sheet_uses_csv_mimetype(self, mock_svc, _sleep) -> None:
        """Verify Drive upload uses text/csv, not text/markdown."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "Test",
        }

        do_create("a,b\n1,2", "Test", doc_type="sheet")

        # Inspect the media_body passed to files().create()
        _, kwargs = mock_service.files().create.call_args
        assert kwargs["media_body"].mimetype() == "text/csv"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_sheet_with_folder(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "In Folder",
            "parents": ["folder789"],
        }
        mock_service.files().get().execute.return_value = {"name": "Reports"}

        result = do_create("a,b\n1,2", "In Folder", doc_type="sheet", folder_id="folder789")

        assert isinstance(result, CreateResult)
        assert result.cues["folder"] == "Reports"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_sheet_routes_through_do(self, mock_svc, _sleep) -> None:
        """do(operation=create, doc_type=sheet) reaches _create_sheet."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "Budget",
        }

        result = do(operation="create", content="a,b\n1,2", title="Budget", doc_type="sheet")

        assert result["file_id"] == "sheet1"
        assert result["type"] == "sheet"


class TestDocTypeMapping:
    """Verify doc type constants."""

    def test_all_types_mapped(self) -> None:
        assert "doc" in DOC_TYPE_TO_MIME
        assert "sheet" in DOC_TYPE_TO_MIME
        assert "slides" in DOC_TYPE_TO_MIME


class TestReadSource:
    """Tests for _read_source() — reading content from deposit folders."""

    def test_reads_content_md_for_doc(self, tmp_path: Path) -> None:
        """Reads content.md when doc_type=doc."""
        (tmp_path / "content.md").write_text("# Hello World")
        content, title = _read_source(tmp_path, "doc")
        assert content == "# Hello World"
        assert title is None  # No manifest

    def test_reads_content_csv_for_sheet(self, tmp_path: Path) -> None:
        """Reads content.csv when doc_type=sheet."""
        (tmp_path / "content.csv").write_text("Name,Amount\nAlice,100")
        content, title = _read_source(tmp_path, "sheet")
        assert content == "Name,Amount\nAlice,100"

    def test_title_from_manifest(self, tmp_path: Path) -> None:
        """Extracts title from manifest.json if present."""
        (tmp_path / "content.md").write_text("# Report")
        (tmp_path / "manifest.json").write_text(json.dumps({
            "type": "doc",
            "title": "Quarterly Report",
            "id": "draft-001",
        }))
        content, title = _read_source(tmp_path, "doc")
        assert content == "# Report"
        assert title == "Quarterly Report"

    def test_missing_content_file_raises(self, tmp_path: Path) -> None:
        """Raises MiseError when expected content file is missing."""
        from models import MiseError
        with pytest.raises(MiseError) as exc_info:
            _read_source(tmp_path, "doc")
        assert "content.md" in str(exc_info.value)

    def test_unsupported_doc_type_raises(self, tmp_path: Path) -> None:
        """Raises MiseError for doc types without source support."""
        from models import MiseError
        with pytest.raises(MiseError) as exc_info:
            _read_source(tmp_path, "slides")
        assert "not supported" in str(exc_info.value)

    def test_malformed_manifest_ignored(self, tmp_path: Path) -> None:
        """Bad manifest.json doesn't crash — title falls back to None."""
        (tmp_path / "content.md").write_text("# Test")
        (tmp_path / "manifest.json").write_text("not json{{{")
        content, title = _read_source(tmp_path, "doc")
        assert content == "# Test"
        assert title is None


class TestSourceParam:
    """Tests for do_create() with source parameter."""

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_creates_doc_from_source(self, mock_svc, _sleep, tmp_path: Path) -> None:
        """source reads content.md and creates a doc."""
        (tmp_path / "content.md").write_text("# From Deposit")

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "From Deposit",
        }

        result = do_create(title="From Deposit", source=tmp_path)

        assert isinstance(result, CreateResult)
        assert result.file_id == "doc1"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_creates_sheet_from_source(self, mock_svc, _sleep, tmp_path: Path) -> None:
        """source reads content.csv and creates a sheet."""
        (tmp_path / "content.csv").write_text("Name,Amount\nAlice,100")

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "Data",
        }

        result = do_create(title="Data", doc_type="sheet", source=tmp_path)

        assert isinstance(result, CreateResult)
        assert result.file_id == "sheet1"
        assert result.doc_type == "sheet"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_title_falls_back_to_manifest(self, mock_svc, _sleep, tmp_path: Path) -> None:
        """Title from manifest used when not passed explicitly."""
        (tmp_path / "content.md").write_text("# Report")
        (tmp_path / "manifest.json").write_text(json.dumps({
            "type": "doc", "title": "Manifest Title", "id": "d1",
        }))

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "Manifest Title",
        }

        result = do_create(source=tmp_path)  # No title param

        assert isinstance(result, CreateResult)
        assert result.title == "Manifest Title"

    def test_source_and_content_conflict(self, tmp_path: Path) -> None:
        """Providing both source and content returns error."""
        (tmp_path / "content.md").write_text("# Test")

        result = do_create(content="# Inline", title="Test", source=tmp_path)

        assert isinstance(result, CreateError)
        assert result.kind == "invalid_input"
        assert "either" in result.message.lower()

    def test_no_content_no_source_returns_error(self) -> None:
        """Neither content nor source returns error."""
        result = do_create(title="Test")

        assert isinstance(result, CreateError)
        assert result.kind == "invalid_input"
        assert "content" in result.message.lower()

    def test_source_missing_file_returns_error(self, tmp_path: Path) -> None:
        """Source folder without expected content file returns error."""
        result = do_create(title="Test", source=tmp_path)

        assert isinstance(result, CreateError)
        assert result.kind == "invalid_input"
        assert "content.md" in result.message

    def test_source_no_title_no_manifest_returns_error(self, tmp_path: Path) -> None:
        """Source without title param and without manifest returns error."""
        (tmp_path / "content.md").write_text("# Test")

        result = do_create(source=tmp_path)  # No title, no manifest

        assert isinstance(result, CreateError)
        assert result.kind == "invalid_input"
        assert "title" in result.message.lower()


class TestManifestEnrichment:
    """Tests for post-creation manifest enrichment."""

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_manifest_enriched_after_creation(self, mock_svc, _sleep, tmp_path: Path) -> None:
        """Manifest gets status, file_id, web_link, created_at after create."""
        (tmp_path / "content.md").write_text("# Report")
        (tmp_path / "manifest.json").write_text(json.dumps({
            "type": "doc", "title": "Report", "id": "draft-001",
        }))

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "Report",
        }

        do_create(title="Report", source=tmp_path)

        # Re-read manifest
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["status"] == "created"
        assert manifest["file_id"] == "doc1"
        assert manifest["web_link"] == "https://docs.google.com/document/d/doc1/edit"
        assert "created_at" in manifest
        # Original fields preserved
        assert manifest["type"] == "doc"
        assert manifest["title"] == "Report"

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_no_manifest_no_crash(self, mock_svc, _sleep, tmp_path: Path) -> None:
        """Source folder without manifest.json doesn't crash on enrichment."""
        (tmp_path / "content.md").write_text("# Test")

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "Test",
        }

        result = do_create(title="Test", source=tmp_path)

        assert isinstance(result, CreateResult)
        # No manifest.json to enrich — should succeed without error
        assert not (tmp_path / "manifest.json").exists()

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_inline_content_does_not_enrich(self, mock_svc, _sleep) -> None:
        """Inline content (no source) doesn't attempt manifest enrichment."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "doc1",
            "webViewLink": "https://docs.google.com/document/d/doc1/edit",
            "name": "Test",
        }

        result = do_create(content="# Test", title="Test")

        assert isinstance(result, CreateResult)
        # No crash, no enrichment attempt


class TestSourceThroughDoRouting:
    """Tests that source param flows from do() MCP wrapper to do_create()."""

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_source_routes_through_do(self, mock_svc, _sleep, tmp_path: Path) -> None:
        """do(operation=create, source=...) reaches do_create with resolved path."""
        (tmp_path / "content.csv").write_text("a,b\n1,2")
        (tmp_path / "manifest.json").write_text(json.dumps({
            "type": "sheet", "title": "Test Sheet", "id": "draft",
        }))

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "Test Sheet",
        }

        result = do(
            operation="create",
            source=str(tmp_path),
            doc_type="sheet",
            title="Test Sheet",
            base_path=str(tmp_path),
        )

        assert result["file_id"] == "sheet1"
        assert result["type"] == "sheet"

    def test_do_without_content_or_source_returns_error(self) -> None:
        """do(operation=create) with neither content nor source errors."""
        result = do(operation="create", title="Test")
        assert result["error"] is True
        assert "content" in result["message"] or "source" in result["message"]


class TestCsvTextToValues:
    """Tests for _csv_text_to_values helper."""

    def test_simple_csv(self) -> None:
        assert _csv_text_to_values("a,b\n1,2\n") == [["a", "b"], ["1", "2"]]

    def test_quoted_fields(self) -> None:
        result = _csv_text_to_values('"hello, world",=SUM(A1:A3)\n')
        assert result == [["hello, world", "=SUM(A1:A3)"]]

    def test_empty_csv(self) -> None:
        assert _csv_text_to_values("") == []

    def test_formula_preserved(self) -> None:
        """Formulae (=prefix) are preserved as plain strings for USER_ENTERED."""
        result = _csv_text_to_values("total,=A1+A2\n")
        assert result[0][1] == "=A1+A2"


class TestReadMultiTabSource:
    """Tests for _read_multi_tab_source."""

    def test_reads_tabs_in_manifest_order(self, tmp_path: Path) -> None:
        (tmp_path / "content_revenue.csv").write_text("a,b\n1,2\n")
        (tmp_path / "content_costs.csv").write_text("x,y\n3,4\n")
        (tmp_path / "manifest.json").write_text(json.dumps({
            "type": "sheet",
            "title": "Budget",
            "tabs": [
                {"name": "Revenue", "filename": "content_revenue.csv"},
                {"name": "Costs", "filename": "content_costs.csv"},
            ],
        }))

        tabs = _read_multi_tab_source(tmp_path)
        assert len(tabs) == 2
        assert tabs[0][0] == "Revenue"
        assert "a,b" in tabs[0][1]
        assert tabs[1][0] == "Costs"

    def test_missing_tab_file_raises(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(json.dumps({
            "type": "sheet",
            "tabs": [{"name": "Missing", "filename": "content_missing.csv"}],
        }))

        from models import MiseError
        with pytest.raises(MiseError) as exc_info:
            _read_multi_tab_source(tmp_path)
        assert "content_missing.csv" in str(exc_info.value)

    def test_no_tabs_in_manifest_raises(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(json.dumps({"type": "sheet"}))

        from models import MiseError
        with pytest.raises(MiseError) as exc_info:
            _read_multi_tab_source(tmp_path)
        assert "No tabs" in str(exc_info.value)


class TestMultiTabSheetCreation:
    """Tests for multi-tab sheet creation via hybrid path."""

    def _make_multi_tab_deposit(self, tmp_path: Path) -> Path:
        """Helper: create a multi-tab deposit folder."""
        (tmp_path / "content.csv").write_text("combined")
        (tmp_path / "content_revenue.csv").write_text("Product,Amount\nWidgets,1000\n")
        (tmp_path / "content_costs.csv").write_text("Item,Cost\nRent,500\n")
        (tmp_path / "manifest.json").write_text(json.dumps({
            "type": "sheet",
            "title": "Budget",
            "id": "draft-123",
            "tabs": [
                {"name": "Revenue", "filename": "content_revenue.csv"},
                {"name": "Costs", "filename": "content_costs.csv"},
            ],
        }))
        return tmp_path

    @patch("retry.time.sleep")
    @patch("tools.create.rename_sheet")
    @patch("tools.create.add_sheet", return_value=1)
    @patch("tools.create.update_sheet_values", return_value=4)
    @patch("tools.create.get_drive_service")
    def test_multi_tab_creates_sheet_with_tabs(
        self, mock_svc, mock_update, mock_add, mock_rename, _sleep, tmp_path: Path,
    ) -> None:
        """Multi-tab deposit creates spreadsheet with multiple tabs."""
        deposit = self._make_multi_tab_deposit(tmp_path)

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "Budget",
        }

        result = do_create(title="Budget", doc_type="sheet", source=deposit)

        assert isinstance(result, CreateResult)
        assert result.file_id == "sheet1"
        assert result.doc_type == "sheet"

        # Tab 1: renamed from CSV upload default
        mock_rename.assert_called_once_with("sheet1", sheet_id=0, new_title="Revenue")

        # Tab 2: added via Sheets API
        mock_add.assert_called_once_with("sheet1", "Costs")

        # Tab 2: values written
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0] == "sheet1"
        assert "'Costs'!A1" in call_args[1]["range_"]

    @patch("retry.time.sleep")
    @patch("tools.create.rename_sheet")
    @patch("tools.create.add_sheet", return_value=1)
    @patch("tools.create.update_sheet_values", return_value=4)
    @patch("tools.create.get_drive_service")
    def test_multi_tab_cues_include_tab_info(
        self, mock_svc, mock_update, mock_add, mock_rename, _sleep, tmp_path: Path,
    ) -> None:
        """Result cues include tab_count and tab_names."""
        deposit = self._make_multi_tab_deposit(tmp_path)

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "Budget",
        }

        result = do_create(title="Budget", doc_type="sheet", source=deposit)

        assert result.cues["tab_count"] == 2
        assert result.cues["tab_names"] == ["Revenue", "Costs"]

    @patch("retry.time.sleep")
    @patch("tools.create.get_drive_service")
    def test_single_tab_deposit_uses_csv_upload(self, mock_svc, _sleep, tmp_path: Path) -> None:
        """Single-tab deposit (no tabs in manifest) uses fast CSV upload path."""
        (tmp_path / "content.csv").write_text("a,b\n1,2\n")
        (tmp_path / "manifest.json").write_text(json.dumps({
            "type": "sheet", "title": "Simple",
        }))

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "Simple",
        }

        result = do_create(doc_type="sheet", source=tmp_path)

        assert isinstance(result, CreateResult)
        # No Sheets API calls — just CSV upload
        assert "tab_count" not in result.cues

    @patch("retry.time.sleep")
    @patch("tools.create.rename_sheet")
    @patch("tools.create.add_sheet", return_value=1)
    @patch("tools.create.update_sheet_values", return_value=4)
    @patch("tools.create.get_drive_service")
    def test_multi_tab_manifest_enriched(
        self, mock_svc, mock_update, mock_add, mock_rename, _sleep, tmp_path: Path,
    ) -> None:
        """Manifest enriched with creation receipt after multi-tab create."""
        deposit = self._make_multi_tab_deposit(tmp_path)

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "Budget",
        }

        do_create(title="Budget", doc_type="sheet", source=deposit)

        manifest = json.loads((deposit / "manifest.json").read_text())
        assert manifest["status"] == "created"
        assert manifest["file_id"] == "sheet1"

    @patch("retry.time.sleep")
    @patch("tools.create.rename_sheet")
    @patch("tools.create.add_sheet", return_value=1)
    @patch("tools.create.update_sheet_values", return_value=0)
    @patch("tools.create.get_drive_service")
    def test_multi_tab_with_formula_cells(
        self, mock_svc, mock_update, mock_add, mock_rename, _sleep, tmp_path: Path,
    ) -> None:
        """Formulae in CSV cells are passed through for USER_ENTERED."""
        (tmp_path / "content.csv").write_text("combined")
        (tmp_path / "content_data.csv").write_text("a,b\n1,2\n")
        (tmp_path / "content_totals.csv").write_text("label,formula\nTotal,=SUM(Data!B:B)\n")
        (tmp_path / "manifest.json").write_text(json.dumps({
            "type": "sheet",
            "title": "With Formulae",
            "tabs": [
                {"name": "Data", "filename": "content_data.csv"},
                {"name": "Totals", "filename": "content_totals.csv"},
            ],
        }))

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "sheet1",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet1/edit",
            "name": "With Formulae",
        }

        result = do_create(title="With Formulae", doc_type="sheet", source=tmp_path)

        assert isinstance(result, CreateResult)
        # The formula is in the values passed to update_sheet_values
        call_args = mock_update.call_args
        values = call_args[1]["values"]
        assert any("=SUM(Data!B:B)" in str(row) for row in values)
