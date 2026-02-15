"""
Move operation — move files between Drive folders.

Uses Drive API's addParents/removeParents on files().update().
Single-parent enforcement: removes existing parents, adds destination.
"""

from typing import Any

from adapters.services import get_drive_service
from models import MiseError, ErrorKind
from retry import with_retry


def do_move(
    file_id: str,
    destination_folder_id: str,
) -> dict[str, Any]:
    """
    Move a file to a different Drive folder.

    Enforces single-parent: removes all current parents, adds destination.
    Works with Shared Drives.

    Args:
        file_id: The file to move
        destination_folder_id: Target folder ID

    Returns:
        Dict with file_id, title, web_link, destination info
    """
    try:
        return _move_file(file_id, destination_folder_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


@with_retry(max_attempts=3, delay_ms=1000)
def _move_file(file_id: str, destination_folder_id: str) -> dict[str, Any]:
    """Move via addParents/removeParents on files().update()."""
    service = get_drive_service()

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

    # Get destination folder name for cues
    dest_folder = (
        service.files()
        .get(fileId=destination_folder_id, fields="name", supportsAllDrives=True)
        .execute()
    )

    return {
        "file_id": updated["id"],
        "title": updated.get("name", ""),
        "web_link": updated.get("webViewLink", ""),
        "operation": "move",
        "cues": {
            "destination_folder": dest_folder.get("name", destination_folder_id),
            "destination_folder_id": destination_folder_id,
            "previous_parents": current_parents,
        },
    }
