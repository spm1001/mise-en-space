"""Live probe: can the Drive markdown IMPORT engine be aimed at a new tab? (mise-wisuzu)

Run from repo root:
  uv run python docs/research/2026-08-24-givige-tab-probe/probe_drive_import_vs_tabs.py

The wisuzu falsifier ("a clever and simple way across the bonkers API surface")
demands this before committing to plain insertText: mise's rich-markdown door is
Drive's files().update import engine (whole-document, text/markdown), and IF that
engine writes into the tab at index 0 while preserving sibling tabs, then
[addDocumentTab{index:0} -> Drive import -> reorder] would place a FULL-FIDELITY
markdown redraft in a new tab. This probe also settles the mise-vuloju watch item
(whether Drive markdown overwrite preserves tabs at all — unmeasured before now).

Shape:
  A. create scratch doc (original text in tab t.0)
  B. addDocumentTab {title: 'Redraft', index: 0} -> minted tabId, read-back
     confirms the NEW tab sits at index 0
  C. files().update with rich markdown (heading + bold + bullets)
  D. read back includeTabsContent=true:
       - how many tabs survive?
       - which tab holds the imported markdown?
       - did the original text survive, and in which tab?
       - is the heading a real HEADING_1 (fidelity marker)?

Cleanup: scratch doc trashed in a finally block.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from mise_en_space import Mise  # noqa: E402
from adapters.http_client import get_sync_client  # noqa: E402
from adapters.drive import upload_file_content  # noqa: E402

DOCS_API = "https://docs.googleapis.com/v1/documents"
ORIGINAL_TEXT = "Original tab content - must remain untouched by the probe."
IMPORT_MD = "# Redraft heading\n\nBody with **bold** fidelity marker.\n\n- bullet one\n- bullet two\n"


def tab_report(doc: dict) -> list[dict]:
    """[{tab_id, title, index, text, has_heading1, has_bold}] depth-first."""
    out = []

    def walk(tabs):
        for tab in tabs:
            props = tab.get("tabProperties", {})
            body = tab.get("documentTab", {}).get("body", {}).get("content", [])
            text = ""
            has_h1 = False
            has_bold = False
            for el in body:
                para = el.get("paragraph", {})
                style = para.get("paragraphStyle", {}).get("namedStyleType", "")
                if style == "HEADING_1":
                    has_h1 = True
                for pe in para.get("elements", []):
                    run = pe.get("textRun", {})
                    text += run.get("content", "")
                    if run.get("textStyle", {}).get("bold"):
                        has_bold = True
            out.append({
                "tab_id": props.get("tabId"),
                "title": props.get("title"),
                "index": props.get("index"),
                "text": text,
                "has_heading1": has_h1,
                "has_bold": has_bold,
            })
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
        title="wisuzu import-vs-tabs probe (scratch, safe to trash)",
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
    failed = False
    try:
        # --- B: add a tab at index 0 ---
        resp = client.post_json(
            f"{DOCS_API}/{doc_id}:batchUpdate",
            json_body={
                "requests": [
                    {
                        "addDocumentTab": {
                            "tabProperties": {"title": "Redraft", "index": 0}
                        }
                    }
                ]
            },
        )
        new_props = resp["replies"][0]["addDocumentTab"]["tabProperties"]
        new_tab_id = new_props["tabId"]
        print(f"B: minted tabId={new_tab_id!r} at index={new_props.get('index')}")

        doc = client.get_json(
            f"{DOCS_API}/{doc_id}", params={"includeTabsContent": "true"}
        )
        pre = tab_report(doc)
        print("pre-import tabs:")
        for t in pre:
            print(f"  index={t['index']} id={t['tab_id']!r} title={t['title']!r} text={t['text']!r}")
        if not (len(pre) == 2 and pre[0]["tab_id"] == new_tab_id):
            # tabs[] order in the read-back is documented as document order;
            # if the new tab is not first, say so and continue — the import
            # result is still the finding.
            print("NOTE: new tab did not read back at position 0")

        # --- C: Drive markdown import (mise's rich-fidelity door) ---
        try:
            upload_file_content(doc_id, IMPORT_MD.encode("utf-8"), "text/markdown")
            print("C: files().update markdown import -> 200")
        except Exception as e:  # noqa: BLE001 — probe records, never hides
            print(f"C: import FAILED — {type(e).__name__}: {e}")
            failed = True
            return 1

        # --- D: read back ---
        doc = client.get_json(
            f"{DOCS_API}/{doc_id}", params={"includeTabsContent": "true"}
        )
        post = tab_report(doc)
        print(f"post-import: {len(post)} tabs")
        for t in post:
            print(
                f"  index={t['index']} id={t['tab_id']!r} title={t['title']!r} "
                f"h1={t['has_heading1']} bold={t['has_bold']} text={t['text']!r}"
            )

        checks = []
        checks.append(("both tabs survive import", len(post) == 2))
        by_id = {t["tab_id"]: t for t in post}
        orig = next((t for t in post if t["tab_id"] not in (new_tab_id,)), None)
        new = by_id.get(new_tab_id)
        if new is not None:
            checks.append(("import landed in NEW tab", "Redraft heading" in new["text"]))
            checks.append(("fidelity: real HEADING_1", new["has_heading1"]))
            checks.append(("fidelity: real bold", new["has_bold"]))
            checks.append(("no original text in new tab", ORIGINAL_TEXT not in new["text"]))
        else:
            checks.append(("new tab still exists", False))
        if orig is not None:
            checks.append(("original text survived", ORIGINAL_TEXT in orig["text"]))
            checks.append(("no import bleed into original", "Redraft heading" not in orig["text"]))
        else:
            checks.append(("original tab still exists", False))

        print("\n=== VERDICTS ===")
        for name, ok in checks:
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        failed = not all(ok for _, ok in checks)
        return 1 if failed else 0
    finally:
        trashed = m.do("trash", file_id=doc_id)
        print(f"cleanup: trash -> {'OK' if not trashed.get('error') else trashed}")


if __name__ == "__main__":
    sys.exit(main())
