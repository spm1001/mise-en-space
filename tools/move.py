"""
Move operation — move files between Drive folders.

Uses Drive API's addParents/removeParents on files().update().
Single-parent enforcement: removes existing parents, adds destination.
"""

from typing import Any

from adapters.services import get_drive_service
from models import DoResult, MiseError, ErrorKind
from retry import with_retry


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
        return _move_file(file_id, destination_folder_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


@with_retry(max_attempts=3, delay_ms=1000)
def _move_file(file_id: str, destination_folder_id: str) -> DoResult:
    """Move via addParents/removeParents on files().update()."""
    service = get_drive_service()

    # Validate destination is a folder before attempting the move
    dest_meta = (
        service.files()
        .get(fileId=destination_folder_id, fields="mimeType,name", supportsAllDrives=True)
        .execute()
    )
    if dest_meta.get("mimeType") != "application/vnd.google-apps.folder":
        dest_name = dest_meta.get("name", destination_folder_id)
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"Destination '{dest_name}' ({destination_folder_id}) is not a folder "
            f"(type: {dest_meta.get('mimeType', 'unknown')})",
        )

    # Get current parents so we can remove them
    current = (
        service.files()
        .get(fileId=file_id, fields="id,name,parents,webViewLink", supportsAllDrives=True)
        .execute()
    )

    current_parents = current.get("parents", [])
    remove_parents = ",".join(current_parents) if current_parents else None

    # Move: remove old parents, add new one
    update_kwargs: dict[str, Any] = {
        "fileId": file_id,
        "addParents": destination_folder_id,
        "fields": "id,name,parents,webViewLink",
        "supportsAllDrives": True,
    }
    if remove_parents:
        update_kwargs["removeParents"] = remove_parents

    updated = service.files().update(**update_kwargs).execute()

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
