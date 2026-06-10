"""
Pre-exfil matching — pair Gmail attachments with their Drive copies.

A background script exfiltrates email attachments to Drive (where fullText
indexing makes PDF content searchable). When fetching, the Drive copy is
preferred — faster, already indexed. These helpers decide which Drive file
corresponds to which attachment.
"""

from typing import Any

from models import EmailAttachment


def _names_match(att_filename: str, drive_filename: str) -> bool:
    """
    Exact or stem match between an attachment filename and a Drive filename.

    The exfil script may modify filenames:
    - ensureExtension: "report" → "report.pdf"  (stem match catches this)
    - smartFilename: UUID names get date+sender prefix  (no match — falls to fallback)
    """
    if att_filename == drive_filename:
        return True
    att_stem = att_filename.rsplit(".", 1)[0] if "." in att_filename else att_filename
    drive_stem = drive_filename.rsplit(".", 1)[0] if "." in drive_filename else drive_filename
    return att_stem == drive_stem


def _match_exfil_for_message(
    attachments: list[EmailAttachment],
    exfil_files: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    Match all attachments for a message to pre-exfil'd Drive files.

    Uses a consumed pool so each exfil file is assigned at most once.
    This prevents the same Drive file matching multiple attachments — the
    original bug that deposited DOCX bytes under PNG filenames.

    Two-pass algorithm:
    1. Name/stem matches consume from pool greedily (deterministic, unambiguous).
    2. 1:1 fallback: if exactly one attachment is still unmatched AND exactly one
       exfil file remains AND their MIME categories agree → assign it.
       This handles UUID-renamed files where name matching fails entirely.

    Returns dict mapping attachment_id → exfil file dict.
    """
    if not exfil_files or not attachments:
        return {}

    pool = list(exfil_files)  # mutable copy — consumed as matches are made
    matched: dict[str, dict[str, Any]] = {}

    # Pass 1: exact and stem matches, consuming from pool
    for att in attachments:
        for i, f in enumerate(pool):
            if _names_match(att.filename, f["name"]):
                matched[att.attachment_id] = f
                pool.pop(i)
                break

    # Pass 2: 1:1 fallback — only when certainty is absolute
    unmatched = [a for a in attachments if a.attachment_id not in matched]
    if len(unmatched) == 1 and len(pool) == 1:
        att, exfil = unmatched[0], pool[0]
        exfil_cat = exfil.get("mimeType", "").split("/")[0]
        att_cat = att.mime_type.split("/")[0]
        if exfil_cat and exfil_cat == att_cat:
            matched[att.attachment_id] = exfil

    return matched
