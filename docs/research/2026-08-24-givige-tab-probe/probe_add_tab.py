"""Live probe: can Docs batchUpdate CREATE a tab? (mise-givige)

Run from repo root:  uv run python docs/research/2026-08-24-givige-tab-probe/probe_add_tab.py

Discovery doc (docs:v1 rev 20260817) carries addDocumentTab in the Request
union. This probe asserts on BEHAVIOUR, not the reference:

  A. addDocumentTab {title} on a scratch doc -> expect 200 + minted tabId
  B. insertText into the NEW tab via location.tabId (second batchUpdate)
  C. documents.get includeTabsContent=true -> 2 tabs, new tab holds the text,
     original tab byte-identical to what create put there
  D. (design bonus) addDocumentTab with a CALLER-SUPPLIED tabId -- if accepted,
     a single batch could add-and-fill in one API call; if refused, two
     sequential batchUpdates remain the route (still one do() call for wisuzu)

Cleanup: scratch doc trashed via mise do('trash') in a finally block.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from mise_en_space import Mise  # noqa: E402
from adapters.http_client import get_sync_client  # noqa: E402

DOCS_API = "https://docs.googleapis.com/v1/documents"
ORIGINAL_TEXT = "Original tab content - must remain untouched by the probe."
REDRAFT_TEXT = "Redraft body line, placed into the new tab by tabId."


def get_tab_texts(doc: dict) -> list[tuple[str, str, str]]:
    """[(tabId, title, concatenated body text)] in document order, depth-first."""
    out = []

    def walk(tabs):
        for tab in tabs:
            props = tab.get("tabProperties", {})
            body = tab.get("documentTab", {}).get("body", {}).get("content", [])
            text = ""
            for el in body:
                for pe in el.get("paragraph", {}).get("elements", []):
                    text += pe.get("textRun", {}).get("content", "")
            out.append((props.get("tabId"), props.get("title"), text))
            walk(tab.get("childTabs", []))

    walk(doc.get("tabs", []))
    return out


def main() -> int:
    m = Mise()
    import cues_util

    print(f"identity: {cues_util.current_user_email()}")

    created = m.do(
        "create",
        doc_type="doc",
        title="givige tab probe (scratch, safe to trash)",
        content=ORIGINAL_TEXT + "\n",
    )
    if created.get("error"):
        print("SETUP FAILED — create refused:", created)
        return 2
    doc_id = created.get("file_id") or created.get("id")
    if not doc_id:
        print("SETUP FAILED — no id in create result:", created)
        return 2
    print(f"scratch doc: {doc_id}")

    client = get_sync_client()
    verdicts = {}
    try:
        # --- Probe A: addDocumentTab ---
        try:
            resp = client.post_json(
                f"{DOCS_API}/{doc_id}:batchUpdate",
                json_body={
                    "requests": [
                        {"addDocumentTab": {"tabProperties": {"title": "Redraft probe"}}}
                    ]
                },
            )
            new_tab = resp["replies"][0]["addDocumentTab"]["tabProperties"]
            tab_id = new_tab["tabId"]
            verdicts["A_addDocumentTab"] = f"OK — minted tabId={tab_id!r}, reply={new_tab}"
        except Exception as e:  # noqa: BLE001 — probe records, never hides
            verdicts["A_addDocumentTab"] = f"FAILED — {type(e).__name__}: {e}"
            print_verdicts(verdicts)
            return 1

        # --- Probe B: insertText into the new tab by tabId ---
        try:
            client.post_json(
                f"{DOCS_API}/{doc_id}:batchUpdate",
                json_body={
                    "requests": [
                        {
                            "insertText": {
                                "location": {"tabId": tab_id, "index": 1},
                                "text": REDRAFT_TEXT + "\n",
                            }
                        }
                    ]
                },
            )
            verdicts["B_insertText_by_tabId"] = "OK — 200"
        except Exception as e:  # noqa: BLE001
            verdicts["B_insertText_by_tabId"] = f"FAILED — {type(e).__name__}: {e}"

        # --- Probe C: read back, assert both tabs and untouched original ---
        doc = client.get_json(
            f"{DOCS_API}/{doc_id}", params={"includeTabsContent": "true"}
        )
        tabs = get_tab_texts(doc)
        print(f"read-back: {len(tabs)} tabs")
        for tid, title, text in tabs:
            print(f"  tab {tid!r} title={title!r} text={text!r}")
        c_checks = []
        c_checks.append(("two tabs exist", len(tabs) == 2))
        if len(tabs) == 2:
            orig, new = tabs[0], tabs[1]
            c_checks.append(("original text untouched", ORIGINAL_TEXT in orig[2]))
            c_checks.append(("original has no redraft text", REDRAFT_TEXT not in orig[2]))
            c_checks.append(("new tab is the minted one", new[0] == tab_id))
            c_checks.append(("new tab titled", new[1] == "Redraft probe"))
            c_checks.append(("new tab holds redraft text", REDRAFT_TEXT in new[2]))
        verdicts["C_readback"] = "; ".join(
            f"{name}={'PASS' if ok else 'FAIL'}" for name, ok in c_checks
        )

        # --- Probe D: caller-supplied tabId (single-batch add+fill viability) ---
        try:
            resp = client.post_json(
                f"{DOCS_API}/{doc_id}:batchUpdate",
                json_body={
                    "requests": [
                        {
                            "addDocumentTab": {
                                "tabProperties": {
                                    "tabId": "t.probe-supplied-id",
                                    "title": "Supplied-id probe",
                                }
                            }
                        }
                    ]
                },
            )
            got = resp["replies"][0]["addDocumentTab"]["tabProperties"]
            verdicts["D_supplied_tabId"] = (
                f"ACCEPTED — asked 't.probe-supplied-id', got {got.get('tabId')!r} "
                f"({'honoured' if got.get('tabId') == 't.probe-supplied-id' else 'REPLACED — server mints anyway'})"
            )
        except Exception as e:  # noqa: BLE001
            verdicts["D_supplied_tabId"] = f"REFUSED — {type(e).__name__}: {e}"

        print_verdicts(verdicts)
        return 0
    finally:
        trashed = m.do("trash", file_id=doc_id)
        print(f"cleanup: trash -> {'OK' if not trashed.get('error') else trashed}")


def print_verdicts(v: dict) -> None:
    print("\n=== VERDICTS ===")
    for k, val in v.items():
        print(f"{k}: {val}")


if __name__ == "__main__":
    sys.exit(main())
