"""
Tests for extractors/folder.py — pure function, no I/O.
"""

import pytest
from extractors.folder import extract_folder_content


def make_listing(
    subfolders: list | None = None,
    files: list | None = None,
    truncated: bool = False,
    item_count: int | None = None,
) -> dict:
    """Build a minimal listing dict as list_folder() would return."""
    sf = subfolders or []
    fs = files or []
    types = sorted({f["mimeType"] for f in fs if f.get("mimeType")})
    count = item_count if item_count is not None else len(sf) + len(fs)
    return {
        "subfolders": sf,
        "files": fs,
        "file_count": len(fs),
        "folder_count": len(sf),
        "item_count": count,
        "types": types,
        "truncated": truncated,
    }


class TestExtractFolderContent:
    def test_empty_folder(self) -> None:
        listing = make_listing()
        content = extract_folder_content(listing)
        assert "## Subfolders" in content
        assert "## Files" in content
        # Both sections report none
        assert content.count("**(none)**") == 2

    def test_subfolders_only(self) -> None:
        listing = make_listing(
            subfolders=[
                {"id": "1abc", "name": "docs"},
                {"id": "2def", "name": "blog"},
            ]
        )
        content = extract_folder_content(listing)
        assert "docs/  →  `1abc`" in content
        assert "blog/  →  `2def`" in content
        # Files section should say none
        assert "**(none)**" in content

    def test_files_only(self) -> None:
        listing = make_listing(
            files=[
                {"id": "f1", "name": "faq.md", "mimeType": "text/markdown"},
                {"id": "f2", "name": "intro.md", "mimeType": "text/markdown"},
            ]
        )
        content = extract_folder_content(listing)
        # Subfolders section should say none
        assert "## Subfolders" in content
        lines = content.splitlines()
        subfolder_idx = next(i for i, l in enumerate(lines) if "## Subfolders" in l)
        assert "**(none)**" in lines[subfolder_idx + 2]
        # Files section exists
        assert "## Files" in content
        assert "faq.md" in content
        assert "intro.md" in content

    def test_files_grouped_by_type(self) -> None:
        listing = make_listing(
            files=[
                {"id": "f1", "name": "a.md", "mimeType": "text/markdown"},
                {"id": "f2", "name": "b.md", "mimeType": "text/markdown"},
                {"id": "f3", "name": "data.csv", "mimeType": "text/csv"},
            ]
        )
        content = extract_folder_content(listing)
        # Two separate sections for two types
        assert "text/csv" in content
        assert "text/markdown" in content
        assert "data.csv" in content
        assert "a.md" in content and "b.md" in content

    def test_single_type_shows_mime_in_header(self) -> None:
        listing = make_listing(
            files=[
                {"id": "f1", "name": "a.md", "mimeType": "text/markdown"},
                {"id": "f2", "name": "b.md", "mimeType": "text/markdown"},
            ]
        )
        content = extract_folder_content(listing)
        # Always show MIME type even for a homogeneous listing
        assert "## Files (2 · text/markdown)" in content

    def test_title_as_h1(self) -> None:
        listing = make_listing()
        content = extract_folder_content(listing, title="My Knowledge Base")
        assert content.startswith("# My Knowledge Base")

    def test_no_title_no_h1(self) -> None:
        listing = make_listing()
        content = extract_folder_content(listing)
        assert not content.startswith("# ")  # no H1; ## sections are fine

    def test_mixed_subfolders_and_files(self) -> None:
        listing = make_listing(
            subfolders=[
                {"id": "sf1", "name": "case-studies"},
                {"id": "sf2", "name": "managed-tools"},
            ],
            files=[
                {"id": "f1", "name": "faq.md", "mimeType": "text/markdown"},
                {"id": "f2", "name": "glossary.md", "mimeType": "text/markdown"},
                {"id": "f3", "name": "intro.md", "mimeType": "text/markdown"},
            ],
        )
        content = extract_folder_content(listing)
        assert "case-studies/  →  `sf1`" in content
        assert "managed-tools/  →  `sf2`" in content
        assert "faq.md" in content
        assert "glossary.md" in content
        assert "intro.md" in content

    def test_truncated_flag_adds_notice(self) -> None:
        listing = make_listing(
            files=[{"id": f"f{i}", "name": f"file{i}.md", "mimeType": "text/markdown"} for i in range(5)],
            truncated=True,
            item_count=5,
        )
        content = extract_folder_content(listing)
        assert "Note:" in content
        assert "300" in content  # mentions the limit

    def test_not_truncated_no_notice(self) -> None:
        listing = make_listing(
            files=[{"id": "f1", "name": "file.md", "mimeType": "text/markdown"}],
            truncated=False,
        )
        content = extract_folder_content(listing)
        assert "Note:" not in content

    def test_subfolder_id_is_action_target(self) -> None:
        """The ID in backticks is the fetch target — assert format exactly."""
        listing = make_listing(
            subfolders=[{"id": "1UclqiqLBfe3BfLRNFTWb0eDbnssxA3Tp", "name": "wiki"}]
        )
        content = extract_folder_content(listing)
        assert "wiki/  →  `1UclqiqLBfe3BfLRNFTWb0eDbnssxA3Tp`" in content
