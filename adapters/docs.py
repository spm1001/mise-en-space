"""
Docs adapter — Google Docs API wrapper.

Fetches document content, normalizes legacy/modern formats to DocData.
"""

from typing import Any

from models import DocData, DocTab
from retry import with_retry
from adapters.services import get_docs_service


# Fields to request — only what we need for extraction
# includeTabsContent gives us all tabs + body content in one call
# NOTE: Cannot mix tabs() with legacy document-level fields (body, revisionId)
DOCUMENT_FIELDS = (
    "documentId,"
    "title,"
    "tabs(tabProperties,documentTab)"  # Modern multi-tab format
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
    service = get_docs_service()

    # Fetch with modern multi-tab format
    doc = (
        service.documents()
        .get(
            documentId=document_id,
            includeTabsContent=True,
            fields=DOCUMENT_FIELDS,
        )
        .execute()
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

    return DocData(
        title=title,
        document_id=document_id,
        tabs=tabs,
    )
