"""
Overwrite operation — replace full content of a Google Doc or plain file.

Google Docs: Drive import (text/markdown → formatted Google Doc).
Plain files: Drive Files API (upload new content directly).

Preserves file ID, sharing, location, and revision history.

Routing contract: metadata is pre-fetched at dispatch level (server.py) and
passed via metadata= param. If metadata is None (direct call, not via do()),
we fall through to the Google Doc path for backward compatibility.
"""

from pathlib import Path
from typing import Any

from adapters.drive import (
    GOOGLE_DOC_MIME,
    GOOGLE_FORM_MIME,
    GOOGLE_SHEET_MIME,
    upload_file_content,
)
from markdown_import import convert_fenced_blocks
from models import DoResult, MiseError
from tools.common import resolve_source as _resolve_source
from workspace.manager import deposit_lock_for_source
from tools.doc_chips import CHIP_REF_RE, insert_chips_in_doc, parse_chip_refs
from tools.doc_tabs import get_doc_tabs_meta
from tools.doc_footnotes import apply_footnote_cues, footnotes_for_import
from tools.form_edit import form_overwrite
from tools.plain_file import plain_overwrite
from tools.restore_point import capture_restore_point, merge_restore_cues
from tools.sheet_edit import sheet_overwrite
from validation import validate_drive_id


def do_overwrite(
    file_id: str | None = None,
    content: str | None = None,
    source: str | None = None,
    base_path: str | None = None,
    metadata: dict[str, Any] | None = None,
    file_path: str | None = None,
    restore_comment: bool = True,
    range_: str | None = None,
) -> DoResult | dict[str, Any]:
    """
    Replace full content of a Google Doc or plain file.

    Args:
        file_id: Target file ID
        content: Content string (mutually exclusive with source and file_path)
        source: Path to deposit folder with content file
        base_path: Working directory for resolving relative paths
        metadata: Pre-fetched file metadata (from dispatch). If None, assumes Google Doc.
        file_path: Local file path to read content from (no deposit folder needed)
        restore_comment: Google Docs only — post a '[agent]' comment naming the
            pre-edit Version history entry before overwriting (default True;
            pass False on shared docs where the comment notification is noise)
        range_: Spreadsheets only — A1 notation aiming the write: a bare tab
            name replaces that tab, "Tab!F9:F15" writes exactly those cells,
            "Tab!F9" anchors the CSV's shape there (mise-vadoko)

    Returns:
        DoResult on success, error dict on failure
    """
    if not file_id:
        return {"error": True, "kind": "invalid_input",
                "message": "overwrite requires 'file_id'"}
    try:
        validate_drive_id(file_id, "file_id")
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    # Validate source path early (before API call)
    try:
        resolved_source = _resolve_source(source, base_path)
    except ValueError as e:
        return {"error": True, "kind": "invalid_input", "message": str(e)}

    # Resolve file_path — read content directly from a local file
    if file_path:
        inputs = sum([content is not None, resolved_source is not None])
        if inputs > 0:
            return {
                "error": True,
                "kind": "invalid_input",
                "message": "Provide only one of 'content', 'source', or 'file_path'",
            }
        resolved = Path(file_path)
        if not resolved.is_absolute() and base_path:
            resolved = Path(base_path) / resolved
        resolved = resolved.resolve()
        # No containment check — see tools/create.py: stdio is local and
        # single-user; the remote gate in server.py is the real boundary.
        if not resolved.exists():
            return {"error": True, "kind": "invalid_input",
                    "message": f"File not found: {file_path}"}
        if not resolved.is_file():
            return {"error": True, "kind": "invalid_input",
                    "message": f"Not a file: {file_path}"}
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"error": True, "kind": "invalid_input",
                    "message": f"File is not valid UTF-8 text: {file_path}"}

    if resolved_source and content:
        return {
            "error": True,
            "kind": "invalid_input",
            "message": "Provide 'content' or 'source', not both",
        }

    if not content and not resolved_source:
        return {
            "error": True,
            "kind": "invalid_input",
            "message": "overwrite requires 'content', 'source', or 'file_path'",
        }

    # Route by file type: Sheets → values API (mise-lirugi), Forms →
    # batchUpdate from spec (mise-wemuki), Google Docs → Drive import,
    # plain files → Drive Files API
    if metadata and metadata.get("mimeType") == GOOGLE_SHEET_MIME:
        return sheet_overwrite(file_id, content, metadata, range_)
    if range_:
        mime = metadata.get("mimeType", "unknown") if metadata else "unknown"
        return {
            "error": True, "kind": "invalid_input",
            "message": f"range= applies only to spreadsheets — this file is "
                       f"{mime}. For a Doc, use replace_text/prepend/append.",
        }
    if metadata and metadata.get("mimeType") == GOOGLE_FORM_MIME:
        return form_overwrite(file_id, content, metadata)
    if metadata and metadata.get("mimeType") != GOOGLE_DOC_MIME:
        return plain_overwrite(file_id, content, source, base_path, metadata)

    # Google Doc path — read content from source if needed. Under the deposit
    # lock: a concurrent fetch of the same resource wipes-then-rewrites this
    # folder, and an unlocked read torn mid-rewrite would replace a live Doc's
    # content with a partial file, silently (cold review, mise-bapije
    # 2026-08-24 — restore_point is recovery, not prevention).
    if resolved_source:
        with deposit_lock_for_source(resolved_source):
            content_file = resolved_source / "content.md"
            if not content_file.exists():
                return {
                    "error": True,
                    "kind": "invalid_input",
                    "message": f"No content.md in source folder: {resolved_source}",
                }
            content = content_file.read_text(encoding="utf-8")

    # Multi-tab guard (mise-wisuzu): overwrite rides Drive's whole-file
    # markdown import, which FLATTENS a multi-tab doc to a single tab —
    # measured live 2026-08-24 (docs/research/2026-08-24-givige-tab-probe/
    # probe_drive_import_vs_tabs.py: both the second tab and the original
    # tab's content destroyed under an HTTP 200). Refuse rather than corrupt
    # — the same contract choice the multi-tab sheet path made (mise-vadoko).
    # Fail-open on a failed read (a Drive-scoped credential can write where
    # a Docs-scoped read is refused), with the gap disclosed as a warning.
    guard_warning: str | None = None
    try:
        doc_tabs = get_doc_tabs_meta(file_id)["tabs"]
    except Exception as e:  # noqa: BLE001 — guard never blocks a legit overwrite
        doc_tabs = []
        guard_warning = (
            f"Could not check this doc's tab structure before overwriting "
            f"({type(e).__name__}) — if the doc has multiple tabs, overwrite "
            f"destroys every tab but the first."
        )
    if len(doc_tabs) > 1:
        tab_titles = ", ".join(repr(t["title"]) for t in doc_tabs[:5])
        return {
            "error": True, "kind": "invalid_input",
            "message": (
                f"This doc has {len(doc_tabs)} tabs ({tab_titles}) and "
                f"overwrite rides Drive's whole-file markdown import, which "
                f"would silently DESTROY every tab but the first (measured "
                f"2026-08-24). To add content as a new tab: do(append, "
                f"file_id=…, content=…, tab='Title'). For in-place edits: "
                f"replace_text / prepend / append."
            ),
        }

    title = metadata.get("name", "Untitled") if metadata else None

    # Whole-line @url smart-chip requests, same opt-in grain as create
    # (mise-rafote): parse to placeholders before the markdown import, insert
    # real chips after it.
    chip_refs = []
    if content and CHIP_REF_RE.search(content):
        content, chip_refs = parse_chip_refs(content)

    # Markdown footnotes: strip definitions pre-import, real footnotes after (mise-rubucu)
    content, footnote_state = footnotes_for_import("doc", content)

    # Pre-edit restore point (Google Doc path only — we're past all other
    # routing). Overwrite replaces the doc wholesale, so it also gets the
    # UI-visible marker comment unless opted out. Captured BEFORE the write.
    restore_cues = capture_restore_point(file_id, comment=restore_comment)

    try:
        result = _overwrite_doc(file_id, content, title=title)  # type: ignore[arg-type]
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    if guard_warning:
        result.cues.setdefault("warnings", []).append(guard_warning)

    if chip_refs:
        chip_result = insert_chips_in_doc(file_id, chip_refs)
        if chip_result.get("chips_inserted"):
            result.cues["chips_inserted"] = chip_result["chips_inserted"]
        if chip_result.get("chip_errors"):
            result.cues["chip_errors"] = chip_result["chip_errors"]

    # Literal [^N] anchors become real Docs footnotes (mise-rubucu)
    apply_footnote_cues(result.cues, file_id, footnote_state)

    return merge_restore_cues(result, restore_cues)


def _overwrite_doc(
    file_id: str, markdown: str, *, title: str | None = None,
) -> DoResult:
    """Replace document content via Drive import (markdown → formatted Google Doc).

    Uses files().update() with text/markdown media type, which triggers the same
    import conversion as files().create() — headings, bold, tables, lists all render.
    """
    markdown = convert_fenced_blocks(markdown)
    result = upload_file_content(file_id, markdown.encode("utf-8"), "text/markdown")
    doc_title = title or result.get("name", "Untitled")

    return DoResult(
        file_id=file_id,
        title=doc_title,
        web_link=f"https://docs.google.com/document/d/{file_id}/edit",
        operation="overwrite",
        cues={"char_count": len(markdown)},
    )
