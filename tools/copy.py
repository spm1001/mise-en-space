"""
Copy operation — duplicate Drive files into a destination folder.

The verb the gather-into-one-folder workflow needed and didn't have: a
regulatory evidence pack, a board pack, any share-a-snapshot job. Before this,
the only routes were the Drive UI's /copy URL (human-only) or `move`, which
relocates the original out from under everyone else's links.

Batch is the common case — the job that prompted this copied eight files at
once — so the list form is first-class rather than a loop bolted on.

Structural twin of tools/move.py: same validate-destination-once, same
per-item batch summary, same @with_retry on the single-file worker.
"""

from typing import Any

from adapters.http_client import get_sync_client
from models import DoResult, MiseError, ErrorKind
from retry import with_retry
from validation import validate_drive_id


# Drive API v3 base URL
_DRIVE_API = "https://www.googleapis.com/drive/v3/files"

_FOLDER_MIME = "application/vnd.google-apps.folder"


def do_copy(
    file_id: str | list[str] | None = None,
    folder_id: str | None = None,
    title: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Copy file(s) into a Drive folder, leaving the originals untouched.

    Args:
        file_id: The file(s) to copy — single ID or list of IDs
        folder_id: Destination folder ID. Optional — omitted, the copy lands
            beside the original, which is Drive's own default for files.copy.
        title: Rename the copy. Single-file only — applying one name to a batch
            would produce N identically-named files, which is never wanted.

    Returns:
        DoResult on single success, batch summary dict on list input,
        error dict on failure
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "copy requires 'file_id'"}

    if folder_id is not None:
        try:
            validate_drive_id(folder_id, "folder_id")
        except ValueError as e:
            return {"error": True, "kind": "invalid_input", "message": str(e)}

    if isinstance(file_id, list):
        if title:
            return {"error": True, "kind": "invalid_input",
                    "message": "'title' renames a single copy; a batch would get N files "
                               "with the same name. Copy them, then rename individually."}
        return _do_batch_copy(file_id, folder_id)

    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}
    try:
        return _copy_file(file_id, folder_id, title)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}


def _get_dest_meta(folder_id: str) -> dict[str, Any]:
    """Fetch destination metadata and assert it is a folder. Raises MiseError if not."""
    client = get_sync_client()
    dest_meta = client.get_json(
        f"{_DRIVE_API}/{folder_id}",
        params={"fields": "mimeType,name", "supportsAllDrives": "true"},
    )
    if dest_meta.get("mimeType") != _FOLDER_MIME:
        dest_name = dest_meta.get("name", folder_id)
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"Destination '{dest_name}' ({folder_id}) is not a folder "
            f"(type: {dest_meta.get('mimeType', 'unknown')})",
        )
    return dest_meta


def _do_batch_copy(file_ids: list[str], folder_id: str | None) -> dict[str, Any]:
    """Copy multiple files, collecting per-file results."""
    try:
        for i, fid in enumerate(file_ids):
            validate_drive_id(fid, f"file_id[{i}]")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    # Destination checked once, not per file — a wrong folder should fail before
    # anything is duplicated, not halfway through leaving orphans behind.
    dest_meta: dict[str, Any] | None = None
    if folder_id is not None:
        try:
            dest_meta = _get_dest_meta(folder_id)
        except MiseError as e:
            return {"error": True, "kind": e.kind.value, "message": e.message}

    results: list[dict[str, Any]] = []
    succeeded = failed = blocked = 0

    for fid in file_ids:
        try:
            r = _copy_file(fid, folder_id, None, dest_meta)
            results.append({
                "source_id": fid,
                "copy_id": r.file_id,
                "title": r.title,
                "web_link": r.web_link,
                "ok": True,
            })
            succeeded += 1
        except MiseError as e:
            # A copy-restricted file is a permission fact about the source, not a
            # transport failure — worth counting separately so a caller can tell
            # "the API broke" from "this file may not be duplicated".
            is_blocked = e.kind is ErrorKind.PERMISSION_DENIED
            results.append({
                "source_id": fid,
                "ok": False,
                "blocked": is_blocked,
                "error": e.message,
            })
            if is_blocked:
                blocked += 1
            else:
                failed += 1

    summary: dict[str, Any] = {
        "operation": "copy",
        "batch": True,
        "total": len(file_ids),
        "succeeded": succeeded,
        "failed": failed,
        "blocked": blocked,
        "folder_id": folder_id,
        # source→copy mapping is the point, not a nicety: a copy strips all
        # provenance, and the gather-into-a-folder job exists to build an index.
        "results": results,
    }
    return summary


@with_retry(max_attempts=3, delay_ms=1000)
def _copy_file(
    file_id: str,
    folder_id: str | None,
    title: str | None,
    dest_meta: dict[str, Any] | None = None,
) -> DoResult:
    """Copy one file via files/{id}/copy, pre-flighting capabilities.canCopy."""
    client = get_sync_client()

    if folder_id is not None and dest_meta is None:
        dest_meta = _get_dest_meta(folder_id)

    # Pre-flight: third-party-authored files can carry a copy restriction, and
    # the bare POST failure is opaque about why. One extra GET buys a message
    # that names the cause.
    source = client.get_json(
        f"{_DRIVE_API}/{file_id}",
        params={"fields": "id,name,mimeType,capabilities(canCopy)", "supportsAllDrives": "true"},
    )
    if source.get("capabilities", {}).get("canCopy") is False:
        raise MiseError(
            ErrorKind.PERMISSION_DENIED,
            f"'{source.get('name', file_id)}' cannot be copied — the owner has "
            "restricted copying (capabilities.canCopy is false). Ask them for a "
            "copy, or link to the original instead of duplicating it.",
        )
    if source.get("mimeType") == _FOLDER_MIME:
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"'{source.get('name', file_id)}' is a folder. Drive's files.copy does "
            "not recurse into folders — copy the files inside it instead.",
        )

    body: dict[str, Any] = {}
    if title:
        body["name"] = title
    if folder_id is not None:
        body["parents"] = [folder_id]

    copied = client.post_json(
        f"{_DRIVE_API}/{file_id}/copy",
        json_body=body,
        params={"fields": "id,name,webViewLink", "supportsAllDrives": "true"},
    )

    cues: dict[str, Any] = {
        "source_id": file_id,
        "source_title": source.get("name", ""),
        "copy_id": copied["id"],
    }
    if folder_id is not None and dest_meta is not None:
        cues["destination_folder"] = dest_meta.get("name", folder_id)
        cues["destination_folder_id"] = folder_id
    else:
        cues["destination_folder"] = "alongside the original (no folder_id given)"

    return DoResult(
        file_id=copied["id"],
        title=copied.get("name", ""),
        web_link=copied.get("webViewLink", ""),
        operation="copy",
        cues=cues,
    )
