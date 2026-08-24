"""Refuter probe: can add-and-fill ride ONE batchUpdate after all?

Attacks the mise-givige D interpretation ("server mints tabId, so add-and-fill
cannot ride one batchUpdate"). Route under test: addDocumentTab at index 0 then
a tabId-less insertText in the SAME batch — Location.tabId docs say "when
omitted, the request is applied to the first tab".

Scratch doc created via mise do(create), trashed in finally.
"""

import sys
from pathlib import Path

REPO = Path("/home/modha/repos/spm1001/mise-en-space")
sys.path.insert(0, str(REPO))

from mise_en_space import Mise  # noqa: E402
from adapters.http_client import get_sync_client  # noqa: E402

DOCS_API = "https://docs.googleapis.com/v1/documents"


def tab_rows(doc):
    out = []

    def walk(tabs):
        for t in tabs:
            p = t.get("tabProperties", {})
            body = t.get("documentTab", {}).get("body", {}).get("content", [])
            text = ""
            for el in body:
                for pe in el.get("paragraph", {}).get("elements", []):
                    text += pe.get("textRun", {}).get("content", "")
            out.append((p.get("tabId"), p.get("index"), p.get("title"), text))
            walk(t.get("childTabs", []))

    walk(doc.get("tabs", []))
    return out


def main() -> int:
    m = Mise()
    created = m.do(
        "create",
        doc_type="doc",
        title="givige REFUTER one-batch probe (scratch, safe to trash)",
        content="Original.\n",
    )
    if created.get("error"):
        print("SETUP FAILED:", created)
        return 2
    doc_id = created.get("file_id") or created.get("id")
    print("scratch doc:", doc_id)
    client = get_sync_client()
    try:
        try:
            resp = client.post_json(
                f"{DOCS_API}/{doc_id}:batchUpdate",
                json_body={
                    "requests": [
                        {
                            "addDocumentTab": {
                                "tabProperties": {"title": "One-batch tab", "index": 0}
                            }
                        },
                        {
                            "insertText": {
                                "location": {"index": 1},
                                "text": "SINGLE-BATCH FILL\n",
                            }
                        },
                    ]
                },
            )
            print("one-batch ACCEPTED; replies:", resp.get("replies"))
        except Exception as e:  # noqa: BLE001
            print("one-batch REFUSED:", type(e).__name__, e)
            r = getattr(e, "response", None)
            if r is not None:
                print("body:", r.text[:800])

        doc = client.get_json(
            f"{DOCS_API}/{doc_id}", params={"includeTabsContent": "true"}
        )
        for row in tab_rows(doc):
            print("TAB:", row)
        return 0
    finally:
        trashed = m.do("trash", file_id=doc_id)
        print("cleanup: trash ->", "OK" if not trashed.get("error") else trashed)


if __name__ == "__main__":
    sys.exit(main())
