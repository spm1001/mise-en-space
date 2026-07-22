"""
Pre-edit restore point for Google Doc mutations (mise-cizuzi).

Named versions have no API surface — the Drive Revision resource has no name
field, and patching one anyway returns 200 while silently ignoring it;
keepForever is likewise binary-content-only, so a pre-edit revision can be
neither labelled nor pinned (all probed live 2026-07-22). What IS available:
the head revision's id + modifiedTime, read BEFORE the edit — a precise
pointer into File → Version history.

capture_restore_point reads that anchor (best-effort: a failed capture warns
and never blocks the edit the user asked for). For overwrite — the one op
that replaces a doc wholesale — it additionally posts a document-level
'[agent]' comment naming the exact Version history entry, so the restore
point is visible in the doc's own UI, not just in this tool's response.
"""

from datetime import datetime
from typing import Any

from adapters.drive import create_comment, get_head_revision
from logging_config import logger
from models import DoResult

_AGENT_PREFIX = "[agent] "


def _humanise_time(rfc3339: str) -> str:
    """RFC 3339 UTC → the local-time string the Version history UI shows."""
    try:
        dt = datetime.fromisoformat(rfc3339.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d %b %Y, %H:%M %Z")
    except ValueError:
        return rfc3339


def capture_restore_point(file_id: str, *, comment: bool = False) -> dict[str, Any]:
    """
    Capture the pre-edit revision anchor; optionally mark it with a comment.

    Returns a cues fragment to merge into the edit's DoResult:
        restore_point: {"revision_id", "modified_time"}   (anchor read OK)
        restore_point_comment: <comment id>                (comment posted)
        warnings: [...]                                    (any step failed)

    Best-effort by design: the edit is what the user asked for, so a failed
    capture degrades to a warning rather than blocking. Call BEFORE mutating.
    """
    cues: dict[str, Any] = {}
    try:
        revision = get_head_revision(file_id)
    except Exception as exc:
        logger.warning(f"restore point capture failed for {file_id}: {exc}")
        cues["warnings"] = [
            f"Restore point unavailable (could not read revisions: {exc}) — "
            "proceeding with the edit; Version history remains your fallback."
        ]
        return cues

    cues["restore_point"] = {
        "revision_id": revision["id"],
        "modified_time": revision["modifiedTime"],
    }

    if comment:
        text = (
            f"{_AGENT_PREFIX}Restore point before overwrite: File → Version "
            f"history → {_humanise_time(revision['modifiedTime'])} "
            f"(revision {revision['id']}). Resolve this comment once the new "
            "version is confirmed good."
        )
        try:
            posted = create_comment(file_id, text)
            cues["restore_point_comment"] = posted.id
        except Exception as exc:
            logger.warning(f"restore point comment failed for {file_id}: {exc}")
            cues.setdefault("warnings", []).append(
                f"Restore-point comment could not be posted ({exc}); the "
                "anchor above still identifies the pre-edit version."
            )

    return cues


def merge_restore_cues(
    result: DoResult | dict[str, Any], fragment: dict[str, Any]
) -> DoResult | dict[str, Any]:
    """Fold a capture fragment into a successful DoResult's cues.

    Error dicts pass through untouched — a failed edit doesn't need an anchor
    (and any already-posted comment harmlessly marks the aborted attempt).
    """
    if isinstance(result, DoResult) and fragment:
        warnings = fragment.get("warnings")
        for key, value in fragment.items():
            if key == "warnings":
                continue
            result.cues[key] = value
        if warnings:
            result.cues.setdefault("warnings", []).extend(warnings)
    return result
