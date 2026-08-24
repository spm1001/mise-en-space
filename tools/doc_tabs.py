"""
Google Doc tabs — add a tab and place content in it (mise-wisuzu).

Contract bought by live probes (docs/research/2026-08-24-givige-tab-probe/):

- ``addDocumentTab`` is GA (discovery docs:v1 rev 20260817) and the SERVER
  mints the tabId — supplying one is a categorical 400
  (probe_supplied_tabid.py).
- The one-batch shortcut ``[addDocumentTab + insertText with no tabId]``
  returns 200 and silently writes into the ORIGINAL tab
  (probe_one_batch_fill.py): Location's first-tab default does not resolve
  to the just-added tab, even when the new tab sits at index 0. So the route
  is TWO SEQUENTIAL batchUpdates — add, read the minted tabId from the
  reply, insert by location.tabId. tests/unit/test_doc_tabs.py pins the
  first batch as add-only; do not "optimise" the two calls into one.
- Tab content is PLAIN TEXT. mise's rich-markdown door is Drive's whole-file
  import engine (files().update, text/markdown), and that engine FLATTENS a
  multi-tab doc to a single tab — measured live (probe_drive_import_vs_tabs
  .py): both the just-added tab and the original tab's content were
  destroyed under an HTTP 200, with the surviving tab keeping the ORIGINAL
  tab's id. There is no import route into a tab; the only rich route would
  be a markdown→batchUpdate compiler, which is deliberately unbuilt (v1
  ships plain text with the limitation cued).

The same probe is why ``get_doc_tabs_meta`` exists: do(overwrite) on a
multi-tab Google Doc would silently destroy every tab but the first, so
tools/overwrite.py counts tabs through this module and refuses first.
"""

from typing import Any

from adapters.http_client import get_sync_client
from models import ErrorKind, MiseError

_DOCS_API = "https://docs.googleapis.com/v1/documents"

# Docs tabs nest at most 3 levels in the UI; mask each level explicitly so
# no documentTab content rides the response (fields masks don't recurse).
_TAB_PROPS_FIELDS = (
    "title,tabs(tabProperties,childTabs(tabProperties,"
    "childTabs(tabProperties,childTabs(tabProperties))))"
)


def get_doc_tabs_meta(file_id: str) -> dict[str, Any]:
    """Doc title plus flat tab properties, depth-first — content never fetched.

    Returns ``{"title": str, "tabs": [{"tab_id", "title", "index", "depth"}]}``.
    ``includeTabsContent`` stays false; the fields mask keeps the response to
    properties only, so this costs one small GET regardless of doc size.
    """
    client = get_sync_client()
    doc = client.get_json(
        f"{_DOCS_API}/{file_id}", params={"fields": _TAB_PROPS_FIELDS}
    )
    tabs: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]], depth: int) -> None:
        for node in nodes:
            props = node.get("tabProperties", {})
            tabs.append(
                {
                    "tab_id": props.get("tabId"),
                    "title": props.get("title"),
                    "index": props.get("index"),
                    "depth": depth,
                }
            )
            walk(node.get("childTabs", []), depth + 1)

    walk(doc.get("tabs", []), 0)
    return {"title": doc.get("title", "Untitled"), "tabs": tabs}


def add_tab_with_content(
    file_id: str, tab_title: str, text: str
) -> dict[str, Any]:
    """Add a tab and place plain text in it — two sequential batchUpdates.

    Returns the minted tabProperties dict (tabId, title, index). Never
    collapse this into one batch: the single-batch shape returns 200 and
    writes the ORIGINAL tab (see module docstring).
    """
    client = get_sync_client()

    # Batch 1 — add only. The server mints the tabId; supplying one is a 400.
    resp = client.post_json(
        f"{_DOCS_API}/{file_id}:batchUpdate",
        json_body={
            "requests": [
                {"addDocumentTab": {"tabProperties": {"title": tab_title}}}
            ]
        },
    )
    minted = resp["replies"][0]["addDocumentTab"]["tabProperties"]

    # Batch 2 — fill by the minted tabId. A fresh tab's body is a single
    # newline; index 1 is the start of its text. No retry wrapper on this
    # flow: re-running after a partial failure would mint a SECOND tab, so
    # a batch-2 failure surfaces loudly with the orphan named instead.
    try:
        client.post_json(
            f"{_DOCS_API}/{file_id}:batchUpdate",
            json_body={
                "requests": [
                    {
                        "insertText": {
                            "location": {"tabId": minted["tabId"], "index": 1},
                            "text": text,
                        }
                    }
                ]
            },
        )
    except MiseError as e:
        raise MiseError(
            e.kind,
            f"Tab {tab_title!r} was created (id {minted['tabId']}) but "
            f"placing the content failed: {e.message}. The empty tab is "
            f"still in the doc — re-running append with tab= would create "
            f"ANOTHER tab, so either delete the empty one in the UI first "
            f"or leave it and retry with a different title.",
            details=e.details,
            retryable=False,
        ) from e
    return minted
