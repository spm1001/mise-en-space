"""
Tests for adapters/drive.list_folder() — pagination and shared-drive flags.
"""

from unittest.mock import patch, MagicMock, call

import pytest

from tests.helpers import mock_api_chain, seal_service
from adapters.drive import list_folder, GOOGLE_FOLDER_MIME
from models import FolderItem, FolderFile, FolderListing


def _make_item(id: str, name: str, mime: str) -> dict:
    return {"id": id, "name": name, "mimeType": mime}


def _make_folder_item(id: str, name: str) -> dict:
    return _make_item(id, name, GOOGLE_FOLDER_MIME)


def _make_file_item(id: str, name: str, mime: str = "text/markdown") -> dict:
    return _make_item(id, name, mime)


class TestListFolder:
    def test_single_page_result(self) -> None:
        """Single-page folder — returns FolderListing, not truncated."""
        mock_service = MagicMock()
        page1 = {
            "files": [
                _make_folder_item("sf1", "docs"),
                _make_file_item("f1", "readme.md"),
                _make_file_item("f2", "faq.md"),
            ]
            # no nextPageToken
        }
        mock_api_chain(mock_service, "files.list.execute", page1)

        with patch("adapters.drive.get_drive_service", return_value=mock_service):
            result = list_folder("folder-id-123")

        assert isinstance(result, FolderListing)
        assert result.folder_count == 1
        assert result.file_count == 2
        assert result.truncated is False
        assert result.subfolders == [FolderItem(id="sf1", name="docs")]
        assert len(result.files) == 2
        assert "text/markdown" in result.types

    def test_supportsAllDrives_and_includeItems_always_set(self) -> None:
        """Both shared-drive flags must be True on every API call."""
        mock_service = MagicMock()
        mock_service.files.return_value.list.return_value.execute.return_value = {"files": []}

        with patch("adapters.drive.get_drive_service", return_value=mock_service):
            list_folder("folder-id-123")

        call_kwargs = mock_service.files.return_value.list.call_args.kwargs
        assert call_kwargs.get("supportsAllDrives") is True
        assert call_kwargs.get("includeItemsFromAllDrives") is True

    def test_three_page_pagination(self) -> None:
        """Three pages fetched; after page 3 no nextPageToken → not truncated."""
        mock_service = MagicMock()
        pages = [
            {"files": [_make_file_item(f"f{i}", f"file{i}.md") for i in range(100)], "nextPageToken": "tok1"},
            {"files": [_make_file_item(f"f{i}", f"file{i}.md") for i in range(100, 200)], "nextPageToken": "tok2"},
            {"files": [_make_file_item(f"f{i}", f"file{i}.md") for i in range(200, 250)]},
        ]
        mock_service.files.return_value.list.return_value.execute.side_effect = pages

        with patch("adapters.drive.get_drive_service", return_value=mock_service):
            result = list_folder("folder-id-123")

        assert result.file_count == 250
        assert result.truncated is False
        assert mock_service.files.return_value.list.call_count == 3

    def test_truncation_when_page_token_after_page3(self) -> None:
        """nextPageToken present after page 3 → truncated=True."""
        mock_service = MagicMock()
        pages = [
            {"files": [_make_file_item(f"f{i}", f"file{i}.md") for i in range(100)], "nextPageToken": "tok1"},
            {"files": [_make_file_item(f"f{i}", f"file{i}.md") for i in range(100, 200)], "nextPageToken": "tok2"},
            {"files": [_make_file_item(f"f{i}", f"file{i}.md") for i in range(200, 300)], "nextPageToken": "tok3"},
        ]
        mock_service.files.return_value.list.return_value.execute.side_effect = pages

        with patch("adapters.drive.get_drive_service", return_value=mock_service):
            result = list_folder("folder-id-123")

        assert result.file_count == 300
        assert result.truncated is True
        assert result.item_count == 300

    def test_empty_folder(self) -> None:
        """Empty folder — both counts zero, no error."""
        mock_service = MagicMock()
        mock_service.files.return_value.list.return_value.execute.return_value = {"files": []}

        with patch("adapters.drive.get_drive_service", return_value=mock_service):
            result = list_folder("empty-folder-id")

        assert result.file_count == 0
        assert result.folder_count == 0
        assert result.truncated is False
        assert result.subfolders == []
        assert result.files == []
        assert result.types == []

    def test_mixed_types_collected(self) -> None:
        """Types list contains distinct MIME types from files (not folders)."""
        mock_service = MagicMock()
        mock_service.files.return_value.list.return_value.execute.return_value = {
            "files": [
                _make_file_item("f1", "doc.pdf", "application/pdf"),
                _make_file_item("f2", "sheet.csv", "text/csv"),
                _make_file_item("f3", "readme.md", "text/markdown"),
                _make_folder_item("sf1", "assets"),  # folders excluded from types
            ]
        }

        with patch("adapters.drive.get_drive_service", return_value=mock_service):
            result = list_folder("folder-id-123")

        assert result.types == sorted(["application/pdf", "text/csv", "text/markdown"])
        assert result.folder_count == 1
        assert GOOGLE_FOLDER_MIME not in result.types

    def test_page_token_passed_on_subsequent_pages(self) -> None:
        """pageToken from page N passed to page N+1 call."""
        mock_service = MagicMock()
        pages = [
            {"files": [_make_file_item("f1", "a.md")], "nextPageToken": "page2token"},
            {"files": [_make_file_item("f2", "b.md")]},
        ]
        mock_service.files.return_value.list.return_value.execute.side_effect = pages

        with patch("adapters.drive.get_drive_service", return_value=mock_service):
            result = list_folder("folder-id-123")

        calls = mock_service.files.return_value.list.call_args_list
        assert len(calls) == 2
        assert calls[1].kwargs.get("pageToken") == "page2token"
        assert result.file_count == 2
