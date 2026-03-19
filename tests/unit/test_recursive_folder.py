"""Tests for recursive folder listing (adapter, extractor, and fetch wiring)."""

from unittest.mock import patch, MagicMock, call

from models import FolderItem, FolderFile, FolderListing, FolderTreeNode


def _make_listing(
    subfolders: list[tuple[str, str]] | None = None,
    files: list[tuple[str, str, str]] | None = None,
    truncated: bool = False,
) -> FolderListing:
    """Build a FolderListing from simple tuples."""
    sfs = [FolderItem(id=id, name=name) for id, name in (subfolders or [])]
    fs = [FolderFile(id=id, name=name, mime_type=mt) for id, name, mt in (files or [])]
    types = sorted({f.mime_type for f in fs})
    return FolderListing(
        subfolders=sfs, files=fs,
        file_count=len(fs), folder_count=len(sfs),
        item_count=len(sfs) + len(fs), types=types,
        truncated=truncated,
    )


class TestListFolderRecursive:
    """adapters.drive.list_folder_recursive traversal and caps."""

    @patch("adapters.drive.list_folder")
    def test_single_level_no_subfolders(self, mock_list):
        from adapters.drive import list_folder_recursive

        mock_list.return_value = _make_listing(
            files=[("f1", "doc.txt", "text/plain")],
        )
        tree = list_folder_recursive("root", "Root")
        assert tree.id == "root"
        assert tree.name == "Root"
        assert tree.listing.file_count == 1
        assert tree.children == []
        mock_list.assert_called_once_with("root")

    @patch("adapters.drive.list_folder")
    def test_two_levels(self, mock_list):
        from adapters.drive import list_folder_recursive

        root_listing = _make_listing(
            subfolders=[("sub1", "SubA")],
            files=[("f1", "root.txt", "text/plain")],
        )
        sub_listing = _make_listing(
            files=[("f2", "child.txt", "text/plain")],
        )
        mock_list.side_effect = [root_listing, sub_listing]

        tree = list_folder_recursive("root", "Root")
        assert len(tree.children) == 1
        assert tree.children[0].name == "SubA"
        assert tree.children[0].listing.file_count == 1

    @patch("adapters.drive.list_folder")
    def test_depth_cap(self, mock_list):
        from adapters.drive import list_folder_recursive

        # Each level has one subfolder
        def make_deep_listing(fid):
            return _make_listing(subfolders=[("deeper", "Deeper")])

        mock_list.side_effect = make_deep_listing

        tree = list_folder_recursive("root", "Root", max_depth=2)
        # Depth 0 → root, depth 1 → "deeper", depth 2 would exceed
        assert len(tree.children) == 1  # depth 1
        child = tree.children[0]
        assert child.children == []  # depth 2 not traversed
        assert child.depth_truncated is True
        assert mock_list.call_count == 2  # root + 1 child, not further

    @patch("adapters.drive.list_folder")
    def test_item_cap(self, mock_list):
        from adapters.drive import list_folder_recursive

        # Root has 5 files + 1 subfolder, subfolder has 5 files
        root_listing = _make_listing(
            subfolders=[("sub1", "SubA")],
            files=[(f"f{i}", f"file{i}.txt", "text/plain") for i in range(5)],
        )
        sub_listing = _make_listing(
            files=[(f"sf{i}", f"subfile{i}.txt", "text/plain") for i in range(5)],
        )
        mock_list.side_effect = [root_listing, sub_listing]

        # max_items=8: root has 6 items (5 files + 1 folder), sub has 5 — total 11
        # but we allow the sub traversal to start since items_seen was 6 < 8
        tree = list_folder_recursive("root", "Root", max_items=8)
        assert len(tree.children) == 1  # sub was traversed (started before cap)


class TestExtractFolderTree:
    """extractors.folder.extract_folder_tree rendering."""

    def test_renders_title(self):
        from extractors.folder import extract_folder_tree

        tree = FolderTreeNode(
            id="root", name="Projects",
            listing=_make_listing(),
            children=[],
        )
        result = extract_folder_tree(tree)
        assert result.startswith("# Projects")

    def test_renders_files_and_subfolders(self):
        from extractors.folder import extract_folder_tree

        child = FolderTreeNode(
            id="sub1", name="Docs",
            listing=_make_listing(
                files=[("f2", "readme.md", "text/markdown")],
            ),
            children=[],
        )
        tree = FolderTreeNode(
            id="root", name="Root",
            listing=_make_listing(
                subfolders=[("sub1", "Docs")],
                files=[("f1", "notes.txt", "text/plain")],
            ),
            children=[child],
        )
        result = extract_folder_tree(tree)
        assert "notes.txt" in result
        assert "Docs/" in result
        assert "readme.md" in result
        assert "`sub1`" in result  # folder ID visible

    def test_renders_depth_truncation(self):
        from extractors.folder import extract_folder_tree

        tree = FolderTreeNode(
            id="root", name="Root",
            listing=_make_listing(subfolders=[("s1", "Deep")]),
            children=[],
            depth_truncated=True,
        )
        result = extract_folder_tree(tree)
        assert "not traversed" in result or "depth limit" in result

    def test_renders_untraversed_subfolders(self):
        from extractors.folder import extract_folder_tree

        # Listing has subfolder "sub1" but no child node for it
        tree = FolderTreeNode(
            id="root", name="Root",
            listing=_make_listing(subfolders=[("sub1", "Skipped")]),
            children=[],
        )
        result = extract_folder_tree(tree)
        assert "not traversed" in result
        assert "Skipped" in result


class TestFetchFolderRecursiveWiring:
    """fetch(folder_id, recursive=True) threads through correctly."""

    @patch("tools.fetch.drive.adapter_list_folder_recursive")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_manifest")
    def test_recursive_fetch_calls_recursive_adapter(
        self, mock_manifest, mock_write, mock_folder, mock_recursive
    ):
        from tools.fetch.drive import fetch_folder
        from pathlib import Path

        mock_folder.return_value = Path("/tmp/test-folder")
        mock_write.return_value = Path("/tmp/test-folder/content.md")
        mock_recursive.return_value = FolderTreeNode(
            id="root", name="Root",
            listing=_make_listing(files=[("f1", "a.txt", "text/plain")]),
            children=[],
        )

        result = fetch_folder("root", "Root", {}, base_path=Path("/tmp"), recursive=True)
        mock_recursive.assert_called_once_with("root", folder_name="Root")
        assert result.type == "folder"
        assert result.cues.get("recursive") is True

    @patch("tools.fetch.drive.adapter_list_folder")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_manifest")
    def test_non_recursive_uses_flat_adapter(
        self, mock_manifest, mock_write, mock_folder, mock_flat
    ):
        from tools.fetch.drive import fetch_folder
        from pathlib import Path

        mock_folder.return_value = Path("/tmp/test-folder")
        mock_write.return_value = Path("/tmp/test-folder/content.md")
        mock_flat.return_value = _make_listing(
            files=[("f1", "a.txt", "text/plain")],
        )

        result = fetch_folder("root", "Root", {}, base_path=Path("/tmp"), recursive=False)
        mock_flat.assert_called_once_with("root")
        assert result.cues.get("recursive") is None
