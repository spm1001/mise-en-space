"""Unit tests for workspace manager."""

import pytest
from pathlib import Path

from workspace import (
    slugify,
    get_deposit_folder,
    write_content,
    write_thumbnail,
    write_image,
    write_chart,
    write_charts_metadata,
    write_manifest,
    enrich_manifest,
    list_deposit_folders,
    parse_folder_name,
    get_deposit_summary,
)
from workspace.manager import write_search_results


class TestSlugify:
    """Tests for the slugify function."""

    def test_basic_slug(self) -> None:
        """Test basic slugification."""
        assert slugify("AMI Deck 2026") == "ami-deck-2026"

    def test_removes_special_chars(self) -> None:
        """Test that special characters are removed."""
        assert slugify("Q4 Planning (Draft)!!!") == "q4-planning-draft"

    def test_handles_unicode(self) -> None:
        """Test that unicode is normalized."""
        assert slugify("Über Cool Präsentation") == "uber-cool-prasentation"

    def test_collapses_hyphens(self) -> None:
        """Test that multiple hyphens are collapsed."""
        assert slugify("A   B   C") == "a-b-c"

    def test_truncates_long_titles(self) -> None:
        """Test that long titles are truncated."""
        long_title = "This is a very long presentation title that exceeds the maximum"
        result = slugify(long_title, max_length=30)
        assert len(result) <= 30

    def test_empty_string(self) -> None:
        """Test that empty strings return 'untitled'."""
        assert slugify("") == "untitled"
        assert slugify("!!!") == "untitled"


class TestGetDepositFolder:
    """Tests for deposit folder creation."""

    def test_creates_folder(self, tmp_path: Path) -> None:
        """Test that folder is created."""
        folder = get_deposit_folder(
            content_type="slides",
            title="Test Presentation",
            resource_id="1ABC123XYZ",
            base_path=tmp_path,
        )

        assert folder.exists()
        assert folder.is_dir()

    def test_folder_naming(self, tmp_path: Path) -> None:
        """Test folder naming convention."""
        folder = get_deposit_folder(
            content_type="slides",
            title="AMI Deck 2026",
            resource_id="1OepZjuwi2emuHPAP-LWxWZnw9g0SbkjhkBJh9ta1rqU",
            base_path=tmp_path,
        )

        # Should be under mise/
        assert folder.parent.name == "mise"

        # Should have correct structure
        name = folder.name
        assert name.startswith("slides--")
        assert "ami-deck-2026" in name
        assert name.endswith("--1OepZjuwi2em")  # Truncated ID

    def test_different_content_types(self, tmp_path: Path) -> None:
        """Test different content type prefixes."""
        for content_type in ["slides", "doc", "sheet", "gmail"]:
            folder = get_deposit_folder(
                content_type=content_type,  # type: ignore
                title="Test",
                resource_id="abc123",
                base_path=tmp_path,
            )
            assert folder.name.startswith(f"{content_type}--")


class TestWriteContent:
    """Tests for content writing."""

    def test_writes_markdown(self, tmp_path: Path) -> None:
        """Test writing markdown content."""
        folder = get_deposit_folder(
            content_type="slides",
            title="Test",
            resource_id="abc123",
            base_path=tmp_path,
        )

        content = "# Test\n\nHello world"
        path = write_content(folder, content)

        assert path.exists()
        assert path.name == "content.md"
        assert path.read_text() == content

    def test_custom_filename(self, tmp_path: Path) -> None:
        """Test writing with custom filename."""
        folder = get_deposit_folder(
            content_type="sheet",
            title="Test",
            resource_id="abc123",
            base_path=tmp_path,
        )

        content = "a,b,c\n1,2,3"
        path = write_content(folder, content, filename="content.csv")

        assert path.name == "content.csv"


class TestWriteThumbnail:
    """Tests for thumbnail writing."""

    def test_writes_png(self, tmp_path: Path) -> None:
        """Test writing PNG thumbnail."""
        folder = get_deposit_folder(
            content_type="slides",
            title="Test",
            resource_id="abc123",
            base_path=tmp_path,
        )

        # Fake PNG bytes
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"fake image data"
        path = write_thumbnail(folder, png_bytes, slide_index=0)

        assert path.exists()
        assert path.name == "slide_01.png"
        assert path.read_bytes() == png_bytes

    def test_index_formatting(self, tmp_path: Path) -> None:
        """Test that slide indices are 1-indexed and zero-padded."""
        folder = get_deposit_folder(
            content_type="slides",
            title="Test",
            resource_id="abc123",
            base_path=tmp_path,
        )

        # Write several thumbnails
        for i in range(12):
            write_thumbnail(folder, b"data", slide_index=i)

        files = sorted(f.name for f in folder.glob("slide_*.png"))

        assert files[0] == "slide_01.png"
        assert files[9] == "slide_10.png"
        assert files[11] == "slide_12.png"


class TestWriteManifest:
    """Tests for manifest writing."""

    def test_writes_manifest(self, tmp_path: Path) -> None:
        """Test writing manifest.json."""
        import json

        folder = get_deposit_folder("slides", "Test", "abc123", tmp_path)
        path = write_manifest(
            folder,
            content_type="slides",
            title="Test Presentation",
            resource_id="abc123xyz",
        )

        assert path.exists()
        assert path.name == "manifest.json"

        manifest = json.loads(path.read_text())
        assert manifest["type"] == "slides"
        assert manifest["title"] == "Test Presentation"
        assert manifest["id"] == "abc123xyz"
        assert "fetched_at" in manifest

    def test_manifest_with_extra_fields(self, tmp_path: Path) -> None:
        """Test manifest with additional metadata."""
        import json

        folder = get_deposit_folder("slides", "Test", "abc123", tmp_path)
        path = write_manifest(
            folder,
            content_type="slides",
            title="Test",
            resource_id="abc123",
            extra={"slide_count": 43, "has_thumbnails": True},
        )

        manifest = json.loads(path.read_text())
        assert manifest["slide_count"] == 43
        assert manifest["has_thumbnails"] is True


class TestListDepositFolders:
    """Tests for listing deposit folders."""

    def test_lists_folders(self, tmp_path: Path) -> None:
        """Test listing deposit folders."""
        # Create some folders
        get_deposit_folder("slides", "A", "id1", tmp_path)
        get_deposit_folder("doc", "B", "id2", tmp_path)
        get_deposit_folder("sheet", "C", "id3", tmp_path)

        folders = list_deposit_folders(tmp_path)

        assert len(folders) == 3

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Test with no deposits."""
        folders = list_deposit_folders(tmp_path)
        assert folders == []


class TestParseFolderName:
    """Tests for parsing folder names."""

    def test_parses_valid_name(self, tmp_path: Path) -> None:
        """Test parsing valid folder name."""
        folder = tmp_path / "mise" / "slides--ami-deck-2026--1OepZju"
        folder.mkdir(parents=True)

        result = parse_folder_name(folder)

        assert result == {
            "type": "slides",
            "title_slug": "ami-deck-2026",
            "id": "1OepZju",
        }

    def test_handles_title_with_dashes(self, tmp_path: Path) -> None:
        """Test titles that contain double-dashes."""
        folder = tmp_path / "mise" / "doc--q4-planning--draft--abc123"
        folder.mkdir(parents=True)

        result = parse_folder_name(folder)

        assert result is not None
        assert result["type"] == "doc"
        assert result["title_slug"] == "q4-planning--draft"
        assert result["id"] == "abc123"

    def test_returns_none_for_invalid(self, tmp_path: Path) -> None:
        """Test that invalid names return None."""
        folder = tmp_path / "random-folder"
        folder.mkdir(parents=True)

        result = parse_folder_name(folder)

        assert result is None


class TestGetDepositSummary:
    """Tests for deposit summary."""

    def test_summary_with_thumbnails(self, tmp_path: Path) -> None:
        """Test summary includes thumbnail info."""
        folder = get_deposit_folder("slides", "Test", "abc123", tmp_path)
        write_content(folder, "# Test")
        write_thumbnail(folder, b"png1", 0)
        write_thumbnail(folder, b"png2", 1)

        summary = get_deposit_summary(folder)

        assert summary["type"] == "slides"
        assert summary["content_file"] == "content.md"
        assert summary["thumbnail_count"] == 2
        assert "slide_01.png" in summary["thumbnails"]
        assert "slide_02.png" in summary["thumbnails"]

    def test_summary_without_thumbnails(self, tmp_path: Path) -> None:
        """Test summary for non-slides content."""
        folder = get_deposit_folder("doc", "Notes", "xyz789", tmp_path)
        write_content(folder, "# Notes")

        summary = get_deposit_summary(folder)

        assert summary["type"] == "doc"
        assert summary["content_file"] == "content.md"
        assert "thumbnails" not in summary

    def test_summary_with_charts(self, tmp_path: Path) -> None:
        """Summary includes chart info when charts present."""
        folder = get_deposit_folder("sheet", "Data", "abc123", tmp_path)
        write_content(folder, "# Data")
        write_chart(folder, b"chart1", 0)
        write_chart(folder, b"chart2", 1)

        summary = get_deposit_summary(folder)

        assert summary["chart_count"] == 2
        assert "chart_01.png" in summary["charts"]
        assert "chart_02.png" in summary["charts"]


class TestWriteImage:
    """Tests for image writing."""

    def test_writes_png(self, tmp_path: Path) -> None:
        folder = get_deposit_folder("image", "Photo", "img1", tmp_path)
        path = write_image(folder, b"\x89PNG data", "image.png")

        assert path.exists()
        assert path.name == "image.png"
        assert path.read_bytes() == b"\x89PNG data"

    def test_writes_svg(self, tmp_path: Path) -> None:
        folder = get_deposit_folder("image", "Diagram", "img2", tmp_path)
        path = write_image(folder, b"<svg>...</svg>", "image.svg")

        assert path.name == "image.svg"


class TestWriteChart:
    """Tests for chart PNG writing."""

    def test_writes_chart(self, tmp_path: Path) -> None:
        folder = get_deposit_folder("sheet", "Data", "s1", tmp_path)
        path = write_chart(folder, b"chart data", chart_index=0)

        assert path.exists()
        assert path.name == "chart_01.png"
        assert path.read_bytes() == b"chart data"

    def test_index_formatting(self, tmp_path: Path) -> None:
        folder = get_deposit_folder("sheet", "Data", "s1", tmp_path)
        path = write_chart(folder, b"x", chart_index=9)

        assert path.name == "chart_10.png"


class TestWriteChartsMetadata:
    """Tests for charts.json writing."""

    def test_writes_metadata(self, tmp_path: Path) -> None:
        import json

        folder = get_deposit_folder("sheet", "Data", "s1", tmp_path)
        charts = [
            {"title": "Revenue", "type": "LINE", "sheet_name": "Sheet1"},
            {"title": "Costs", "type": "BAR", "sheet_name": "Sheet2"},
        ]
        path = write_charts_metadata(folder, charts)

        assert path.exists()
        assert path.name == "charts.json"
        data = json.loads(path.read_text())
        assert len(data) == 2
        assert data[0]["title"] == "Revenue"


class TestWriteSearchResults:
    """Tests for search result deposition."""

    def test_writes_search_json(self, tmp_path: Path) -> None:
        import json

        results = {"drive_results": [{"id": "d1"}], "gmail_results": []}
        path = write_search_results("Q4 planning", results, base_path=tmp_path)

        assert path.exists()
        assert path.parent.name == "mise"
        assert path.name.startswith("search--q4-planning--")
        assert path.suffix == ".json"

        data = json.loads(path.read_text())
        assert data["drive_results"][0]["id"] == "d1"

    def test_creates_mise_fetch_dir(self, tmp_path: Path) -> None:
        """mise/ created if it doesn't exist."""
        path = write_search_results("test", {}, base_path=tmp_path)
        assert (tmp_path / "mise").is_dir()


class TestEnrichManifest:
    """Tests for post-creation manifest enrichment."""

    def test_merges_fields(self, tmp_path: Path) -> None:
        import json

        folder = get_deposit_folder("doc", "Test", "abc123", tmp_path)
        write_manifest(folder, "doc", "Test", "abc123")

        enrich_manifest(folder, {
            "status": "created",
            "file_id": "doc1",
            "web_link": "https://docs.google.com/document/d/doc1/edit",
        })

        manifest = json.loads((folder / "manifest.json").read_text())
        assert manifest["status"] == "created"
        assert manifest["file_id"] == "doc1"
        # Original fields preserved
        assert manifest["type"] == "doc"
        assert manifest["title"] == "Test"

    def test_raises_if_no_manifest(self, tmp_path: Path) -> None:
        """FileNotFoundError when manifest.json doesn't exist."""
        with pytest.raises(FileNotFoundError):
            enrich_manifest(tmp_path, {"status": "created"})


