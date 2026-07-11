"""
Docs adapter — Google Docs API wrapper.

Fetches document content, normalizes legacy/modern formats to DocData.

Uses httpx via MiseSyncClient (Phase 1 migration). Will switch to
MiseHttpClient (async) when the tools/server layer goes async.
"""

from typing import Any

from models import DocData, DocTab
from retry import with_retry
from adapters.http_client import get_sync_client
from extractors.docs import (
    annotate_checkbox_states,
    is_checkbox_list,
    parse_checkbox_markers,
)


# Google Docs API base URL
_DOCS_API = "https://docs.googleapis.com/v1/documents"

# Drive files API — used only for the markdown-export checkbox oracle below
_DRIVE_FILES_API = "https://www.googleapis.com/drive/v3/files"

# Fields to request — only what we need for extraction
# includeTabsContent gives us all tabs + body content in one call
# NOTE: Cannot mix tabs() with legacy document-level fields (body, revisionId)
# NOTE: documentTab must list explicit subfields — Google's field-mask parser
# rejects bare `documentTab` as a non-leaf message ("Invalid field selection").
DOCUMENT_FIELDS = (
    "documentId,"
    "title,"
    "tabs(tabProperties,documentTab(body,inlineObjects,lists,footnotes))"
)


def _build_tab(tab_data: dict[str, Any], index: int) -> DocTab:
    """Build DocTab from a tabs[] entry."""
    props = tab_data.get("tabProperties", {})
    doc_tab = tab_data.get("documentTab", {})

    return DocTab(
        title=props.get("title", f"Tab {index + 1}"),
        tab_id=props.get("tabId", f"tab_{index}"),
        index=index,
        body=doc_tab.get("body", {}),
        footnotes=doc_tab.get("footnotes", {}),
        lists=doc_tab.get("lists", {}),
        inline_objects=doc_tab.get("inlineObjects", {}),
    )


def _build_legacy_tab(doc: dict[str, Any]) -> DocTab:
    """Build DocTab from legacy single-tab document format."""
    return DocTab(
        title=doc.get("title", "Untitled"),
        tab_id="main",
        index=0,
        body=doc.get("body", {}),
        footnotes=doc.get("footnotes", {}),
        lists=doc.get("lists", {}),
        inline_objects=doc.get("inlineObjects", {}),
    )


def _apply_checkbox_states(client: Any, document_id: str, data: DocData) -> None:
    """Annotate checkbox paragraphs with checked-state via the markdown-export oracle.

    The Docs API does NOT expose checkbox checked-state (a checked and an
    unchecked row are byte-identical). So when a checkbox list is present, fetch
    the Drive markdown export — which renders `- [x]` / `- [ ]` — parse the
    markers, and annotate each checkbox paragraph in document order. Any failure
    (no export, permission, count mismatch) degrades to plain bullets with a
    warning; a wrong tick is never emitted. One extra API call, and only when a
    checkbox list actually exists.
    """
    has_checkbox = any(
        is_checkbox_list(list_def)
        for tab in data.tabs
        for list_def in tab.lists.values()
    )
    if not has_checkbox:
        return

    try:
        md_bytes = client.get_bytes(
            f"{_DRIVE_FILES_API}/{document_id}/export",
            params={"mimeType": "text/markdown"},
        )
        states = parse_checkbox_markers(md_bytes.decode("utf-8"))
    except Exception as exc:  # export is best-effort — never fail the whole fetch
        data.adapter_warnings.append(
            f"Checkbox tick-state unavailable: markdown export failed ({exc}). "
            "Rendering plain bullets."
        )
        return

    warning = annotate_checkbox_states(data.tabs, states)
    if warning:
        data.adapter_warnings.append(warning)


@with_retry(max_attempts=3, delay_ms=1000)
def fetch_document(document_id: str) -> DocData:
    """
    Fetch complete document data.

    Handles both legacy (single-tab) and modern (multi-tab) document formats,
    normalizing both to DocData with a list of tabs.

    Args:
        document_id: The document ID (from URL or API)

    Returns:
        DocData ready for the extractor

    Raises:
        MiseError: On API failure (converted by @with_retry)
    """
    client = get_sync_client()

    # Fetch with modern multi-tab format
    doc = client.get_json(
        f"{_DOCS_API}/{document_id}",
        params={
            "includeTabsContent": "true",
            "fields": DOCUMENT_FIELDS,
        },
    )

    title = doc.get("title", "Untitled")

    # Check for modern multi-tab format
    tabs_data = doc.get("tabs", [])

    if tabs_data:
        # Modern format: process each tab
        tabs = [_build_tab(tab, i) for i, tab in enumerate(tabs_data)]
    else:
        # Legacy format: create single tab from document root
        tabs = [_build_legacy_tab(doc)]

    doc_data = DocData(
        title=title,
        document_id=document_id,
        tabs=tabs,
    )

    # Checkbox checked-state isn't in the Docs API — resolve it via export oracle
    _apply_checkbox_states(client, document_id, doc_data)

    return doc_data
