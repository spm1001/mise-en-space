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
    annotate_suggestion_markup,
    count_suggestions,
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

# suggestions= param → Docs API suggestionsViewMode. The first call always uses
# SUGGESTIONS_INLINE so unresolved suggestions are visible (and countable) as
# suggestedInsertionIds/suggestedDeletionIds on text runs; preview modes come
# from a second call made only when suggestions actually exist.
_SUGGESTION_VIEW_MODES = {
    "accepted": "PREVIEW_SUGGESTIONS_ACCEPTED",
    "original": "PREVIEW_WITHOUT_SUGGESTIONS",
    "markup": "SUGGESTIONS_INLINE",
}


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


def _build_tabs(doc: dict[str, Any]) -> list[DocTab]:
    """Normalize a documents.get response to a list of DocTab."""
    tabs_data = doc.get("tabs", [])
    if tabs_data:
        return [_build_tab(tab, i) for i, tab in enumerate(tabs_data)]
    return [_build_legacy_tab(doc)]


@with_retry(max_attempts=3, delay_ms=1000)
def fetch_document(document_id: str, suggestions: str = "accepted") -> DocData:
    """
    Fetch complete document data.

    Handles both legacy (single-tab) and modern (multi-tab) document formats,
    normalizing both to DocData with a list of tabs.

    Suggested edits: the first call fetches SUGGESTIONS_INLINE so unresolved
    suggestions are countable. When none exist (the common case) that response
    IS the document and no further call is made. When suggestions are present,
    `suggestions=` decides the view:

    - "accepted" (default): second call with PREVIEW_SUGGESTIONS_ACCEPTED —
      the suggester's intended text, deletions honoured.
    - "original": second call with PREVIEW_WITHOUT_SUGGESTIONS — the
      pre-suggestion text.
    - "markup": no second call; suggestion runs render as CriticMarkup
      ({++ins++}/{--del--} with [sN] pairing tags).

    Mirrors the checkbox-oracle pattern: the extra call is paid only when the
    condition it resolves actually exists.

    Args:
        document_id: The document ID (from URL or API)
        suggestions: 'accepted' | 'original' | 'markup'

    Returns:
        DocData ready for the extractor (suggestion_count/suggestions_mode set)

    Raises:
        MiseError: On API failure — or an unknown suggestions mode (the raised
            ValueError is converted at the @with_retry boundary; the router
            pre-validates, so that path is defense-in-depth only)
    """
    if suggestions not in _SUGGESTION_VIEW_MODES:
        raise ValueError(
            f"suggestions must be one of {sorted(_SUGGESTION_VIEW_MODES)} — got {suggestions!r}"
        )

    client = get_sync_client()

    # First call: inline view, so unresolved suggestions are visible/countable
    doc = client.get_json(
        f"{_DOCS_API}/{document_id}",
        params={
            "includeTabsContent": "true",
            "fields": DOCUMENT_FIELDS,
            "suggestionsViewMode": "SUGGESTIONS_INLINE",
        },
    )

    title = doc.get("title", "Untitled")
    tabs = _build_tabs(doc)
    suggestion_count = count_suggestions(tabs)
    adapter_warnings: list[str] = []

    if suggestion_count > 0:
        if suggestions == "markup":
            annotate_suggestion_markup(tabs)
            adapter_warnings.append(
                f"Document carries {suggestion_count} unresolved suggested edit(s), "
                "rendered as markup: {++inserted++} / {--deleted--}; matching [sN] "
                "tags pair the delete+insert halves of one replace. The Docs API "
                "does not expose who made each suggestion. Re-fetch with "
                "suggestions='accepted' for the clean intended text."
            )
        else:
            # Preview modes: let Google resolve the suggestions server-side
            doc = client.get_json(
                f"{_DOCS_API}/{document_id}",
                params={
                    "includeTabsContent": "true",
                    "fields": DOCUMENT_FIELDS,
                    "suggestionsViewMode": _SUGGESTION_VIEW_MODES[suggestions],
                },
            )
            tabs = _build_tabs(doc)
            if suggestions == "accepted":
                adapter_warnings.append(
                    f"Document carries {suggestion_count} unresolved suggested "
                    "edit(s); content shows them as ACCEPTED (the suggester's "
                    "intended text — suggested deletions are gone from this "
                    "render). Re-fetch with suggestions='markup' to see the "
                    "edits, or 'original' for the pre-suggestion text."
                )
            else:
                adapter_warnings.append(
                    f"Document carries {suggestion_count} unresolved suggested "
                    "edit(s); content shows the ORIGINAL text with all "
                    "suggestions ignored. Re-fetch with suggestions='markup' to "
                    "see the edits, or 'accepted' for the intended text."
                )

    doc_data = DocData(
        title=title,
        document_id=document_id,
        tabs=tabs,
        suggestion_count=suggestion_count,
        suggestions_mode=suggestions,
    )
    doc_data.adapter_warnings.extend(adapter_warnings)

    # Checkbox checked-state isn't in the Docs API — resolve it via export oracle
    _apply_checkbox_states(client, document_id, doc_data)

    return doc_data
