"""
Trash operation — remove Drive files and Gmail drafts via do() verb.

One op, two ID spaces, routed deterministically by shape:
- Gmail draft IDs (`r` + digits, e.g. r7776227818802419254 / r-360475…)
  → drafts.delete. PERMANENT — a discarded draft is gone immediately.
- Drive IDs (base62-ish) → files.update(trashed=true). RECOVERABLE —
  Drive trash keeps files ~30 days ("move to bin" in the UI).

The asymmetry is Google's, not ours; the per-item cue names which fate
applied. Batch supported (file_id as list), matching archive/star/label.

Deliberately NOT in remote mode's allowed ops — destructive.
"""

import logging
import re
from typing import Any

from adapters.gmail import delete_draft
from adapters.http_client import get_sync_client
from models import DoResult, MiseError
from retry import with_retry
from tools.gmail_ops import _batch_result
from validation import validate_drive_id

logger = logging.getLogger(__name__)

_DRIVE_API = "https://www.googleapis.com/drive/v3/files"

# Gmail draft IDs as observed from the drafts API: "r" + optional "-" + digits.
# Drive IDs never match (they're 25+ mixed base62 chars), so routing is
# deterministic — no try-one-then-the-other misroute risk (mise-dizupe's shape).
_DRAFT_ID_RE = re.compile(r"^r-?\d+$")


def _is_draft_id(file_id: str) -> bool:
    return bool(_DRAFT_ID_RE.match(file_id))


@with_retry(max_attempts=3, delay_ms=1000)
def _trash_drive_file(file_id: str) -> dict[str, Any]:
    """Move a Drive file to trash via files.update(trashed=true)."""
    client = get_sync_client()
    return client.patch_json(
        f"{_DRIVE_API}/{file_id}",
        json_body={"trashed": True},
        params={"fields": "id,name,webViewLink", "supportsAllDrives": "true"},
    )


def _trash_one(file_id: str) -> tuple[str, str, str]:
    """Trash a single item. Returns (kind, title, web_link).

    kind is 'draft_discarded' or 'drive_trashed'. Raises MiseError on failure.
    """
    if _is_draft_id(file_id):
        delete_draft(file_id)
        return "draft_discarded", "", ""
    updated = _trash_drive_file(file_id)
    return "drive_trashed", updated.get("name", ""), updated.get("webViewLink", "")


_CUE_BY_KIND = {
    "draft_discarded": "Draft discarded — permanent, drafts have no trash",
    "drive_trashed": "Moved to Drive trash — recoverable for ~30 days",
}


def do_trash(
    file_id: str | list[str] | None = None,
    **_kwargs: Any,
) -> DoResult | dict[str, Any]:
    """
    Trash Drive file(s) or discard Gmail draft(s).

    Routes by ID shape: draft IDs (r + digits) are discarded permanently
    via drafts.delete; Drive IDs go to the recoverable Drive trash.
    When file_id is a list, processes each sequentially and returns a
    batch summary with per-item results.
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "trash requires 'file_id' (Drive file ID or Gmail draft ID)"}

    # Batch path
    if isinstance(file_id, list):
        return _do_batch_trash(file_id)

    # Single path
    try:
        _validate_trash_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    try:
        kind, title, web_link = _trash_one(file_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    return DoResult(
        file_id=file_id,
        title=title,
        web_link=web_link,
        operation="trash",
        cues={"action": _CUE_BY_KIND[kind]},
    )


def _validate_trash_id(file_id: str, param_name: str) -> None:
    """Draft IDs pass their own shape check; everything else must be Drive-shaped."""
    if _is_draft_id(file_id):
        return
    validate_drive_id(file_id, param_name)


def _do_batch_trash(file_ids: list[str]) -> dict[str, Any]:
    """Trash multiple items, collecting per-item results."""
    try:
        for i, fid in enumerate(file_ids):
            _validate_trash_id(fid, f"file_id[{i}]")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    results: list[dict[str, Any]] = []
    succeeded = failed = 0

    for fid in file_ids:
        try:
            kind, title, _ = _trash_one(fid)
            entry: dict[str, Any] = {"file_id": fid, "ok": True, "result": kind}
            if title:
                entry["title"] = title
            results.append(entry)
            succeeded += 1
        except MiseError as e:
            results.append({"file_id": fid, "ok": False, "error": e.message})
            failed += 1

    return _batch_result("trash", results, succeeded, failed)
