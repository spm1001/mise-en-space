"""
Integration tests for the fetch MCP tool.

Run with: uv run pytest tests/integration/test_fetch_tool.py -v -m integration
"""

import json
import pytest
from pathlib import Path

from server import fetch


IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"


@pytest.fixture
def integration_ids() -> dict[str, str]:
    """Load integration test IDs from config file."""
    if not IDS_FILE.exists():
        pytest.skip(f"Integration IDs not configured. Create {IDS_FILE}")
    with open(IDS_FILE) as f:
        return json.load(f)


@pytest.fixture
def cleanup_mise_fetch():
    """Clean up mise-fetch folder after tests."""
    import shutil
    from pathlib import Path

    mise_fetch = Path.cwd() / "mise-fetch"
    yield mise_fetch
    # Cleanup after test
    if mise_fetch.exists():
        shutil.rmtree(mise_fetch)


@pytest.mark.integration
def test_fetch_doc(integration_ids: dict[str, str], cleanup_mise_fetch) -> None:
    """Test fetching a Google Doc."""
    doc_id = integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("test_doc_id not in integration_ids.json")

    result = fetch(doc_id)

    assert "error" not in result, f"Fetch failed: {result}"
    assert result["type"] == "doc"
    assert result["format"] == "markdown"
    assert "path" in result

    # Verify files exist
    folder = Path(result["path"])
    assert folder.exists()
    assert (folder / "content.md").exists()
    assert (folder / "manifest.json").exists()

    # Verify content is not empty
    content = (folder / "content.md").read_text()
    assert len(content) > 0


@pytest.mark.integration
def test_fetch_sheet(integration_ids: dict[str, str], cleanup_mise_fetch) -> None:
    """Test fetching a Google Sheet."""
    sheet_id = integration_ids.get("test_sheet_id")
    if not sheet_id:
        pytest.skip("test_sheet_id not in integration_ids.json")

    result = fetch(sheet_id)

    assert "error" not in result, f"Fetch failed: {result}"
    assert result["type"] == "sheet"
    assert result["format"] == "csv"

    folder = Path(result["path"])
    assert folder.exists()
    assert (folder / "content.csv").exists()
    assert (folder / "manifest.json").exists()


@pytest.mark.integration
def test_fetch_slides(integration_ids: dict[str, str], cleanup_mise_fetch) -> None:
    """Test fetching Google Slides."""
    presentation_id = integration_ids.get("test_presentation_id")
    if not presentation_id:
        pytest.skip("test_presentation_id not in integration_ids.json")

    result = fetch(presentation_id)

    assert "error" not in result, f"Fetch failed: {result}"
    assert result["type"] == "slides"
    assert result["format"] == "markdown"
    assert "slide_count" in result["metadata"]

    folder = Path(result["path"])
    assert folder.exists()
    assert (folder / "content.md").exists()
    assert (folder / "manifest.json").exists()


@pytest.mark.integration
def test_fetch_gmail(integration_ids: dict[str, str], cleanup_mise_fetch) -> None:
    """Test fetching a Gmail thread."""
    thread_id = integration_ids.get("test_thread_id")
    if not thread_id:
        pytest.skip("test_thread_id not in integration_ids.json")

    result = fetch(thread_id)

    assert "error" not in result, f"Fetch failed: {result}"
    assert result["type"] == "gmail"
    assert result["format"] == "markdown"
    assert "message_count" in result["metadata"]

    folder = Path(result["path"])
    assert folder.exists()
    assert (folder / "content.md").exists()
    assert (folder / "manifest.json").exists()


@pytest.mark.integration
def test_fetch_drive_url(integration_ids: dict[str, str], cleanup_mise_fetch) -> None:
    """Test fetching via Drive URL."""
    doc_id = integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("test_doc_id not in integration_ids.json")

    # Construct a URL
    url = f"https://docs.google.com/document/d/{doc_id}/edit"
    result = fetch(url)

    assert "error" not in result, f"Fetch failed: {result}"
    assert result["type"] == "doc"


@pytest.mark.integration
def test_fetch_invalid_id(cleanup_mise_fetch) -> None:
    """Test that invalid ID returns error, not exception."""
    result = fetch("invalid-id-that-does-not-exist-12345")

    assert "error" in result
    assert result["error"] is True
    # Should have error details
    assert "kind" in result or "message" in result


@pytest.mark.integration
def test_fetch_manifest_structure(integration_ids: dict[str, str], cleanup_mise_fetch) -> None:
    """Test that manifest has expected fields."""
    doc_id = integration_ids.get("test_doc_id")
    if not doc_id:
        pytest.skip("test_doc_id not in integration_ids.json")

    result = fetch(doc_id)
    assert "error" not in result

    folder = Path(result["path"])
    manifest = json.loads((folder / "manifest.json").read_text())

    assert "type" in manifest
    assert "title" in manifest
    assert "id" in manifest
    assert "fetched_at" in manifest


# --- PDF Tests ---


@pytest.mark.integration
def test_fetch_pdf(integration_ids: dict[str, str], cleanup_mise_fetch) -> None:
    """Test fetching a PDF file."""
    pdf_id = integration_ids.get("test_pdf_id")
    if not pdf_id:
        pytest.skip("test_pdf_id not in integration_ids.json")

    result = fetch(pdf_id)

    assert "error" not in result, f"Fetch failed: {result}"
    assert result["type"] == "pdf"
    assert result["format"] == "markdown"
    assert "path" in result

    # Verify files exist
    folder = Path(result["path"])
    assert folder.exists()
    assert (folder / "content.md").exists()
    assert (folder / "manifest.json").exists()

    # Verify content is not empty
    content = (folder / "content.md").read_text()
    assert len(content) > 0


@pytest.mark.integration
def test_fetch_pdf_extraction_method(integration_ids: dict[str, str], cleanup_mise_fetch) -> None:
    """Test that PDF extraction reports which method was used."""
    pdf_id = integration_ids.get("test_pdf_id")
    if not pdf_id:
        pytest.skip("test_pdf_id not in integration_ids.json")

    result = fetch(pdf_id)

    assert "error" not in result, f"Fetch failed: {result}"

    # Check manifest has extraction method
    folder = Path(result["path"])
    manifest = json.loads((folder / "manifest.json").read_text())
    assert "extraction_method" in manifest
    assert manifest["extraction_method"] in ("markitdown", "drive")


# --- Office File Tests ---


@pytest.mark.integration
def test_fetch_docx(integration_ids: dict[str, str], cleanup_mise_fetch) -> None:
    """Test fetching a DOCX file."""
    docx_id = integration_ids.get("test_docx_id")
    if not docx_id:
        pytest.skip("test_docx_id not in integration_ids.json")

    result = fetch(docx_id)

    assert "error" not in result, f"Fetch failed: {result}"
    assert result["type"] == "docx"
    assert result["format"] == "markdown"
    assert "path" in result

    # Verify files exist
    folder = Path(result["path"])
    assert folder.exists()
    assert (folder / "content.md").exists()
    assert (folder / "manifest.json").exists()

    # Verify content is not empty
    content = (folder / "content.md").read_text()
    assert len(content) > 0


@pytest.mark.integration
def test_fetch_xlsx(integration_ids: dict[str, str], cleanup_mise_fetch) -> None:
    """Test fetching an XLSX file."""
    xlsx_id = integration_ids.get("test_xlsx_id")
    if not xlsx_id:
        pytest.skip("test_xlsx_id not in integration_ids.json")

    result = fetch(xlsx_id)

    assert "error" not in result, f"Fetch failed: {result}"
    assert result["type"] == "xlsx"
    assert result["format"] == "csv"
    assert "path" in result

    # Verify files exist
    folder = Path(result["path"])
    assert folder.exists()
    assert (folder / "content.csv").exists()
    assert (folder / "manifest.json").exists()

    # Verify content is not empty
    content = (folder / "content.csv").read_text()
    assert len(content) > 0


# --- Comments Tests ---


@pytest.mark.integration
def test_fetch_comments(integration_ids: dict[str, str]) -> None:
    """Test fetching comments from a file via tool layer."""
    from server import fetch_comments

    doc_id = integration_ids.get("test_doc_with_comments_id")
    if not doc_id:
        pytest.skip("test_doc_with_comments_id not in integration_ids.json")

    result = fetch_comments(doc_id)

    assert "error" not in result, f"Fetch failed: {result}"
    # Verify structure, not content
    assert isinstance(result.get("content"), str)
    assert result.get("comment_count", 0) >= 0


@pytest.mark.integration
def test_fetch_comments_no_comments(integration_ids: dict[str, str]) -> None:
    """Test fetching comments from a file with no comments."""
    from server import fetch_comments

    # Use an existing test doc that might not have comments
    doc_id = integration_ids.get("test_sheet_id")  # Sheets typically have no comments
    if not doc_id:
        pytest.skip("test_sheet_id not in integration_ids.json")

    result = fetch_comments(doc_id)

    # Should succeed even if no comments
    assert "error" not in result or result.get("comment_count") == 0
