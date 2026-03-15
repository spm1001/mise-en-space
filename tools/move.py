"""
Move operation — move files between Drive folders.

Uses Drive API's addParents/removeParents on files().update().
Single-parent enforcement: removes existing parents, adds destination.

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
    file_id: str | None = None,
    destination_folder_id: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Move a file to a different Drive folder.

    Enforces single-parent: removes all current parents, adds destination.
    Works with Shared Drives.

    Args:
        file_id: The file to move
        destination_folder_id: Target folder ID

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id or not destination_folder_id:
        missing = []
        if not file_id:
            missing.append("file_id")
        if not destination_folder_id:
            missing.append("destination_folder_id")
        return {"error": True, "kind": "invalid_input",
                "message": f"move requires {' and '.join(missing)}"}
    try:
        validate_drive_id(file_id, "file_id")
        validate_drive_id(destination_folder_id, "destination_folder_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}
    try:
        return _move_file(file_id, destination_folder_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


@with_retry(max_attempts=3, delay_ms=1000)
def _move_file(file_id: str, destination_folder_id: str) -> DoResult:
    """Move via addParents/removeParents on files().update()."""
    client = get_sync_client()

    # Validate destination is a folder before attempting the move
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
