"""
Move operation — move files between Drive folders.

Uses Drive API's addParents/removeParents on files().update().
Single-parent enforcement: removes existing parents, adds destination.
Supports batch: pass a list of file_ids to move multiple files in one call.

Uses httpx via MiseSyncClient (Phase 1 migration).
"""

from typing import Any

from adapters.http_client import get_sync_client
from models import DoResult, MiseError, ErrorKind
from retry import with_retry
from validation import validate_drive_id


# Drive API v3 base URL
_DRIVE_API = "https://www.googleapis.com/drive/v3/files"


def do_move(
    file_id: str | list[str] | None = None,
    destination_folder_id: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Move file(s) to a different Drive folder.

    Enforces single-parent: removes all current parents, adds destination.
    Works with Shared Drives.

    When file_id is a list, moves each file sequentially and returns a
    batch summary with per-file results.

    Args:
        file_id: The file(s) to move — single ID or list of IDs
        destination_folder_id: Target folder ID

    Returns:
        DoResult on single success, batch summary dict on list input,
        error dict on failure
    """
    if not file_id or not destination_folder_id:
        missing = []
        if not file_id:
            missing.append("file_id")
        if not destination_folder_id:
            missing.append("destination_folder_id")
        return {"error": True, "kind": "invalid_input",
                "message": f"move requires {' and '.join(missing)}"}

    # Batch path
    if isinstance(file_id, list):
        return _do_batch_move(file_id, destination_folder_id)

    # Single path
    try:
        validate_drive_id(file_id, "file_id")
        validate_drive_id(destination_folder_id, "destination_folder_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}
    try:
        return _move_file(file_id, destination_folder_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


def _get_dest_meta(destination_folder_id: str) -> dict[str, Any]:
    """Fetch destination metadata and assert it is a folder. Raises MiseError if not."""
    client = get_sync_client()
    dest_meta = client.get_json(
        f"{_DRIVE_API}/{destination_folder_id}",
        params={"fields": "mimeType,name", "supportsAllDrives": "true"},
    )
    if dest_meta.get("mimeType") != "application/vnd.google-apps.folder":
        dest_name = dest_meta.get("name", destination_folder_id)
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"Destination '{dest_name}' ({destination_folder_id}) is not a folder "
            f"(type: {dest_meta.get('mimeType', 'unknown')})",
        )
    return dest_meta


def _do_batch_move(
    file_ids: list[str], destination_folder_id: str,
) -> dict[str, Any]:
    """Move multiple files, collecting per-file results."""
    # Validate all IDs upfront
    try:
        validate_drive_id(destination_folder_id, "destination_folder_id")
        for i, fid in enumerate(file_ids):
            validate_drive_id(fid, f"file_id[{i}]")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    # Validate destination is a folder once — not per file
    try:
        dest_meta = _get_dest_meta(destination_folder_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0

    for fid in file_ids:
        try:
            r = _move_file(fid, destination_folder_id, dest_meta)
            results.append({"file_id": r.file_id, "title": r.title, "ok": True})
            succeeded += 1
        except MiseError as e:
            results.append({"file_id": fid, "ok": False, "error": e.message})
            failed += 1

    return {
        "operation": "move",
        "batch": True,
        "total": len(file_ids),
        "succeeded": succeeded,
        "failed": failed,
        "destination_folder_id": destination_folder_id,
        "results": results,
    }


@with_retry(max_attempts=3, delay_ms=1000)
def _move_file(
    file_id: str,
    destination_folder_id: str,
    dest_meta: dict[str, Any] | None = None,
) -> DoResult:
    """Move via addParents/removeParents on files().update()."""
    client = get_sync_client()

    if dest_meta is None:
        dest_meta = _get_dest_meta(destination_folder_id)

    # Get current parents so we can remove them
    current = client.get_json(
        f"{_DRIVE_API}/{file_id}",
        params={"fields": "id,name,parents,webViewLink", "supportsAllDrives": "true"},
    )

    current_parents = current.get("parents", [])
    remove_parents = ",".join(current_parents) if current_parents else None

    # Move: remove old parents, add new one
    params: dict[str, Any] = {
        "addParents": destination_folder_id,
        "fields": "id,name,parents,webViewLink",
        "supportsAllDrives": "true",
    }
    if remove_parents:
        params["removeParents"] = remove_parents

    updated = client.patch_json(
        f"{_DRIVE_API}/{file_id}",
        params=params,
    )

    return DoResult(
        file_id=updated["id"],
        title=updated.get("name", ""),
        web_link=updated.get("webViewLink", ""),
        operation="move",
        cues={
            "destination_folder": dest_meta.get("name", destination_folder_id),
            "destination_folder_id": destination_folder_id,
            "previous_parents": current_parents,
        },
    )
