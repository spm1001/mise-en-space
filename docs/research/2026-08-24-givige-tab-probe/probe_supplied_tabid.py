"""D2 probe: is the supplied-tabId 400 about format, or categorical? (mise-givige)

Run from repo root:  uv run python docs/research/2026-08-24-givige-tab-probe/probe_supplied_tabid.py

Probe D in probe_add_tab.py tried one supplied-id format and got a bare 400.
This settles the ambiguity: both a 't.'-prefixed and a bare id, with the
response BODY captured. Observed 2026-08-24 (as sameer.modha@itv.com, scratch
doc 1XOInej8lQxF8kZCf4TIC5nsoIiHFjTrCuf2koN_aGmY, trashed after), both formats:

    400 INVALID_ARGUMENT
    "Invalid requests[0].addDocumentTab: Tab ID should not be specified in
     the properties when adding a tab."

Categorical, not a format complaint: the server always mints the tabId.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

from mise_en_space import Mise  # noqa: E402
from adapters.http_client import get_sync_client  # noqa: E402

DOCS_API = "https://docs.googleapis.com/v1/documents"


def main() -> int:
    m = Mise()
    client = get_sync_client()
    about = client.get_json(
        "https://www.googleapis.com/drive/v3/about", params={"fields": "user"}
    )
    print("identity (drive about):", about["user"].get("emailAddress"))

    created = m.do(
        "create",
        doc_type="doc",
        title="givige D2 probe (scratch, safe to trash)",
        content="x\n",
    )
    if created.get("error"):
        print("SETUP FAILED:", created)
        return 2
    doc_id = created.get("file_id") or created.get("id")
    print("scratch:", doc_id)

    any_accepted = False
    try:
        for supplied in ["t.probesupplied123", "probesupplied123"]:
            try:
                r = client.post_json(
                    f"{DOCS_API}/{doc_id}:batchUpdate",
                    json_body={
                        "requests": [
                            {
                                "addDocumentTab": {
                                    "tabProperties": {"tabId": supplied, "title": "D2"}
                                }
                            }
                        ]
                    },
                )
                any_accepted = True
                print(
                    f"supplied {supplied!r}: ACCEPTED ->",
                    r["replies"][0]["addDocumentTab"]["tabProperties"],
                )
            except httpx.HTTPStatusError as e:
                print(
                    f"supplied {supplied!r}: {e.response.status_code} body:",
                    e.response.text[:400],
                )
    finally:
        trashed = m.do("trash", file_id=doc_id)
        print("cleanup:", "OK" if not trashed.get("error") else trashed)
    # Exit 0 = the negative held (both refused). An acceptance is the surprise.
    return 1 if any_accepted else 0


if __name__ == "__main__":
    sys.exit(main())
