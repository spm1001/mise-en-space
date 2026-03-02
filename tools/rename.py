"""
Rename operation — rename a Drive file in-place.

Uses Drive API files().update() with a name body field.
"""

from typing import Any

from adapters.services import get_drive_service
from models import DoResult, MiseError
from retry import with_retry


def do_rename(
    file_id: str | None = None,
    title: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Rename a file in Google Drive.

    Args:
        file_id: The file to rename
        title: The new name for the file

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id or not title:
        missing = []
        if not file_id:
            missing.append("file_id")
        if not title:
            missing.append("title")
        return {"error": True, "kind": "invalid_input",
                "message": f"rename requires {' and '.join(missing)}"}
    try:
        return _rename_file(file_id, title)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


@with_retry(max_attempts=3, delay_ms=1000)
def _rename_file(file_id: str, title: str) -> DoResult:
    """Rename via files().update() with name body field."""
    service = get_drive_service()

    updated = (
        service.files()
        .update(
            fileId=file_id,
            body={"name": title},
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )

    return DoResult(
        file_id=updated["id"],
        title=updated.get("name", ""),
        web_link=updated.get("webViewLink", ""),
        operation="rename",
        cues={"action": f"Renamed to '{title}'"},
    )
