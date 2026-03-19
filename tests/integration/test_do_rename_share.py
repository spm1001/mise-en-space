"""
Integration tests for rename and share do() operations.

Run with: uv run pytest tests/integration/test_do_rename_share.py -v -m integration
"""

import pytest

from server import do
from adapters.http_client import get_sync_client
from models import DoResult

_DRIVE_API = "https://www.googleapis.com/drive/v3/files"


@pytest.fixture
def cleanup_created_files():
    """Track and delete created files after test."""
    created_ids: list[str] = []
    yield created_ids

    if created_ids:
        client = get_sync_client()
        for file_id in created_ids:
            try:
                client.delete(f"{_DRIVE_API}/{file_id}")
            except Exception:
                pass


def _create_test_doc(cleanup: list[str], title: str = "mise-test-rename-share") -> str:
    """Helper: create a throwaway doc, track for cleanup, return file_id."""
    result = do(operation="create", content="# Test\n\nTemporary.", title=title)
    assert "error" not in result, f"Setup failed: {result}"
    cleanup.append(result["file_id"])
    return result["file_id"]


# --- Rename ---


@pytest.mark.integration
def test_rename_basic(cleanup_created_files: list[str]) -> None:
    """Rename a doc and verify the new name via Drive API."""
    file_id = _create_test_doc(cleanup_created_files, "mise-test-before-rename")

    result = do(operation="rename", file_id=file_id, title="mise-test-after-rename")

    assert "error" not in result, f"Rename failed: {result}"
    assert result["operation"] == "rename"
    assert result["title"] == "mise-test-after-rename"
    assert result["file_id"] == file_id

    # Verify via Drive API
    client = get_sync_client()
    meta = client.get_json(
        f"{_DRIVE_API}/{file_id}",
        params={"fields": "name"},
    )
    assert meta["name"] == "mise-test-after-rename"


@pytest.mark.integration
def test_rename_preserves_content(cleanup_created_files: list[str]) -> None:
    """Rename doesn't alter document content."""
    from adapters.docs import fetch_document
    from extractors.docs import extract_doc_content

    result = do(
        operation="create",
        content="# Important\n\nDo not lose this.",
        title="mise-test-rename-content",
    )
    assert "error" not in result
    file_id = result["file_id"]
    cleanup_created_files.append(file_id)

    do(operation="rename", file_id=file_id, title="mise-test-renamed-content")

    doc_data = fetch_document(file_id)
    content = extract_doc_content(doc_data)
    assert "Important" in content
    assert "Do not lose this" in content


@pytest.mark.integration
def test_rename_nonexistent_file() -> None:
    """Rename a file that doesn't exist returns error."""
    result = do(operation="rename", file_id="nonexistent_file_id_xyz", title="New")

    assert "error" in result
    assert result["error"] is True


# --- Share: Preview (no confirm) ---


@pytest.mark.integration
def test_share_preview(cleanup_created_files: list[str]) -> None:
    """Share without confirm returns preview, does NOT create permissions."""
    file_id = _create_test_doc(cleanup_created_files, "mise-test-share-preview")

    result = do(operation="share", file_id=file_id, to="test-share@example.com")

    assert result.get("preview") is True
    assert "Would share" in result["message"]
    assert result["role"] == "reader"
    assert result["shared_with"] == ["test-share@example.com"]

    # Verify no permissions were created (only owner should exist)
    client = get_sync_client()
    perms = client.get_json(
        f"{_DRIVE_API}/{file_id}/permissions",
        params={"fields": "permissions(emailAddress,role)"},
    )
    emails = [p.get("emailAddress", "") for p in perms.get("permissions", [])]
    assert "test-share@example.com" not in emails


# --- Share: Confirmed ---


@pytest.mark.integration
def test_share_confirmed_non_google_account(cleanup_created_files: list[str]) -> None:
    """Share with non-Google email falls back to notification and succeeds."""
    file_id = _create_test_doc(cleanup_created_files, "mise-test-share-confirmed")

    result = do(
        operation="share", file_id=file_id,
        to="test-share@example.com", confirm=True,
    )

    assert "error" not in result, f"Share failed: {result}"
    assert result["operation"] == "share"
    assert result["cues"]["role"] == "reader"
    assert result["cues"]["shared_with"] == ["test-share@example.com"]
    # Non-Google account triggers notification fallback
    assert "test-share@example.com" in result["cues"]["notified"]
    assert "notification_note" in result["cues"]

    # Verify permission exists
    client = get_sync_client()
    perms = client.get_json(
        f"{_DRIVE_API}/{file_id}/permissions",
        params={"fields": "permissions(emailAddress,role)"},
    )
    perm_map = {p.get("emailAddress"): p["role"] for p in perms.get("permissions", [])}
    assert "test-share@example.com" in perm_map
    assert perm_map["test-share@example.com"] == "reader"


@pytest.mark.integration
def test_share_with_role_non_google(cleanup_created_files: list[str]) -> None:
    """Share with explicit writer role for non-Google account."""
    file_id = _create_test_doc(cleanup_created_files, "mise-test-share-writer")

    result = do(
        operation="share", file_id=file_id,
        to="test-share@example.com", role="writer", confirm=True,
    )

    assert "error" not in result, f"Share failed: {result}"
    assert result["cues"]["role"] == "writer"
    assert "test-share@example.com" in result["cues"]["notified"]

    client = get_sync_client()
    perms = client.get_json(
        f"{_DRIVE_API}/{file_id}/permissions",
        params={"fields": "permissions(emailAddress,role)"},
    )
    perm_map = {p.get("emailAddress"): p["role"] for p in perms.get("permissions", [])}
    assert perm_map.get("test-share@example.com") == "writer"


@pytest.mark.integration
def test_share_nonexistent_file() -> None:
    """Share a file that doesn't exist returns error."""
    result = do(
        operation="share", file_id="nonexistent_file_id_xyz",
        to="test-share@example.com", confirm=True,
    )

    assert "error" in result
    assert result["error"] is True
