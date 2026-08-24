"""Measure what each Google Docs write path does to control characters.

The card (mise-melaso) arrives with ONE path already measured — Docs
batchUpdate ``insertText`` deletes ``\\f`` and ``\\x00`` under a 200 — and
asks the unmeasured question: does the Drive markdown IMPORT path
(``do(create)`` / ``do(overwrite)``) preserve them?  ``\\f`` matters
because it is the page marker mise's own PDF extraction writes into
``content.md`` (``pdf_page_fidelity`` in tools/fetch/common.py counts it),
so a PDF deposit pasted into a Doc can lose every page boundary silently.

Five probes, each on its own scratch doc, all trashed in ``finally``:

  A  create        — Drive markdown import (files.create, text/markdown)
  B  append        — Docs API insertText (re-measures the card's claim)
  C  append tab=   — Docs API insertText into a server-minted tab
  D  replace_text  — Docs API replaceAllText's replaceText field
  E  create file   — plain .md byte upload, no conversion (the control:
                     if this loses nothing, it is the lossless route to
                     name in the cue)

Read-back is deliberately TWO instruments, because they answer different
questions: ``documents.get`` reports the document's own text runs and
structural elements (so a form feed that became a real page break shows up
as a ``pageBreak`` element, not as text), and the ``text/plain`` export
reports what a reader downloads.  A char that vanishes from both is gone.

Run:  MELASO_PROBE_TAG=<unique-token> uv run --all-extras python \\
          docs/research/2026-08-24-melaso-control-chars/probe_control_chars.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from adapters.http_client import get_sync_client  # noqa: E402
from mise_en_space import Mise  # noqa: E402

DOCS_API = "https://docs.googleapis.com/v1/documents"
DRIVE_API = "https://www.googleapis.com/drive/v3/files"

TAG = os.environ.get("MELASO_PROBE_TAG", "melaso-probe")
SCRATCH = Path(os.environ.get("MELASO_PROBE_SCRATCH", "/tmp/melaso-probe"))

# One sentinel per control character: a unique pair of ASCII letters with the
# character between them.  Reading the pair back tells all three outcomes
# apart — "FFAFFB" = deleted, "FFA\fFFB" = survived, "FFA\nFFB" = converted.
SENTINELS: list[tuple[str, str, str]] = [
    ("formfeed", "\f", "FFA{}FFB"),
    ("nul", "\x00", "NULA{}NULB"),
    ("crlf", "\r\n", "CRA{}CRB"),
    ("tab", "\t", "TABA{}TABB"),
    ("vtab", "\v", "VTA{}VTB"),
]


def probe_content() -> str:
    """Each sentinel its own paragraph, so markdown import can't merge them."""
    lines = ["MELASOPROBE START", ""]
    for _name, ch, shape in SENTINELS:
        lines.append(shape.format(ch))
        lines.append("")
    lines.append("MELASOPROBE END")
    return "\n".join(lines) + "\n"


def read_doc(doc_id: str) -> tuple[str, list[str]]:
    """Concatenated text runs plus the names of non-text structural elements.

    Walks every tab, so the tab= probe is read by the same instrument as
    the rest.  Element names matter: a form feed that Docs turned into a
    real page break would arrive as ``pageBreak``, which is "survives
    visibly" rather than "silently thinned".
    """
    client = get_sync_client()
    doc = client.get_json(
        f"{DOCS_API}/{doc_id}", params={"includeTabsContent": "true"}
    )
    text_parts: list[str] = []
    elements: list[str] = []

    def walk_body(body: dict[str, Any]) -> None:
        for el in body.get("content", []):
            para = el.get("paragraph")
            if not para:
                for key in el:
                    if key in ("startIndex", "endIndex"):
                        continue
                    elements.append(key)
                    text_parts.append(f"<block:{key}>")
                continue
            # A page break can arrive as a paragraph STYLE, not only as an
            # inline element — both are "survives visibly", so both are
            # rendered into the text stream the verdicts read.
            if para.get("paragraphStyle", {}).get("pageBreakBefore"):
                elements.append("pageBreakBefore")
                text_parts.append("<pageBreakBefore>")
            for pe in para.get("elements", []):
                for key, val in pe.items():
                    if key in ("startIndex", "endIndex"):
                        continue
                    if key == "textRun":
                        text_parts.append(val.get("content", ""))
                    else:
                        elements.append(key)
                        text_parts.append(f"<{key}>")

    def walk_tabs(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            dt = tab.get("documentTab")
            if dt:
                walk_body(dt.get("body", {}))
            walk_tabs(tab.get("childTabs", []))

    if doc.get("tabs"):
        walk_tabs(doc["tabs"])
    else:  # pre-tabs shape, kept so the probe can't silently read nothing
        walk_body(doc.get("body", {}))
    return "".join(text_parts), sorted(set(elements))


def export_text(doc_id: str) -> str:
    client = get_sync_client()
    raw = client.get_bytes(
        f"{DRIVE_API}/{doc_id}/export", params={"mimeType": "text/plain"}
    )
    return raw.decode("utf-8")


def verdicts(sent: str, got: str) -> dict[str, str]:
    """Per-sentinel verdict from the read-back text."""
    out: dict[str, str] = {}
    for name, ch, shape in SENTINELS:
        head, tail = shape.format("|").split("|")
        survived = shape.format(ch) in got
        fused = (head + tail) in got
        if survived:
            out[name] = "SURVIVED"
        elif fused:
            out[name] = "DELETED (neighbours fused)"
        else:
            # Something sits between the neighbours but is not the char.
            idx = got.find(head)
            end = got.find(tail, idx) if idx != -1 else -1
            if idx == -1 or end == -1:
                out[name] = "SENTINEL ABSENT (write or read-back lost it)"
            else:
                between = got[idx + len(head) : end]
                out[name] = f"CONVERTED to {between!r}"
    assert sent  # sent text is the control; keep the signature honest
    return out


def trash(file_id: str) -> None:
    client = get_sync_client()
    client.patch_json(
        f"{DRIVE_API}/{file_id}",
        json_body={"trashed": True},
        params={"supportsAllDrives": "true"},
    )
    print(f"  trashed {file_id}")


def report(label: str, sent: str, doc_id: str) -> dict[str, Any]:
    api_text, elements = read_doc(doc_id)
    exported = export_text(doc_id)
    row = {
        "path": label,
        "doc_id": doc_id,
        "docs_api": verdicts(sent, api_text),
        "export_text_plain": verdicts(sent, exported),
        "non_text_elements": elements,
        "api_text_repr": api_text,
    }
    print(f"\n=== {label} ===")
    print("  docs.get   :", json.dumps(row["docs_api"], indent=None))
    print("  export/txt :", json.dumps(row["export_text_plain"], indent=None))
    print("  elements   :", elements)
    return row


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    mise = Mise(base_path=SCRATCH)
    content = probe_content()
    print("probe content repr:", repr(content))
    rows: list[dict[str, Any]] = []
    created: list[str] = []

    def make(title_suffix: str, body: str) -> str:
        res = mise.do(
            "create",
            doc_type="doc",
            title=f"{TAG} {title_suffix} (scratch, safe to trash)",
            content=body,
        )
        if res.get("error"):
            raise SystemExit(f"SETUP FAILED ({title_suffix}): {res}")
        doc_id = res["file_id"]
        created.append(doc_id)
        print(f"scratch doc [{title_suffix}]: {doc_id}")
        return doc_id

    try:
        # A — Drive markdown import, the card's unmeasured question
        rows.append(report("A create (drive markdown import)", content,
                           make("A-create", content)))

        # B — Docs insertText via append
        doc_b = make("B-append", "Seed paragraph.\n")
        res_b = mise.do("append", file_id=doc_b, content=content)
        print("  append result:", res_b)
        rows.append(report("B append (docs insertText)", content, doc_b))

        # C — Docs insertText into a new tab
        doc_c = make("C-append-tab", "Seed paragraph.\n")
        res_c = mise.do("append", file_id=doc_c, content=content, tab="Probe")
        print("  append tab result:", res_c)
        rows.append(report("C append tab= (docs insertText)", content, doc_c))

        # D — replaceAllText's replaceText field
        doc_d = make("D-replace", "REPLACEMEHERE\n")
        res_d = mise.do("replace_text", file_id=doc_d,
                        find="REPLACEMEHERE", content=content)
        print("  replace result:", res_d)
        rows.append(report("D replace_text (docs replaceAllText)", content, doc_d))

        # E — plain .md byte upload (control: no conversion engine at all)
        res_e = mise.do(
            "create", doc_type="file",
            title=f"{TAG} E-plain-md.md", content=content,
        )
        if res_e.get("error"):
            raise SystemExit(f"SETUP FAILED (E): {res_e}")
        doc_e = res_e["file_id"]
        created.append(doc_e)
        client = get_sync_client()
        got_e = client.get_bytes(
            f"{DRIVE_API}/{doc_e}", params={"alt": "media"}
        ).decode("utf-8")
        row_e = {
            "path": "E create doc_type=file (.md byte upload)",
            "doc_id": doc_e,
            "download": verdicts(content, got_e),
            "api_text_repr": got_e,
        }
        print("\n=== E create doc_type=file (.md byte upload) ===")
        print("  download   :", json.dumps(row_e["download"]))
        rows.append(row_e)

        out = SCRATCH / "melaso-control-chars-results.json"
        out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nresults written: {out}")
        return 0
    finally:
        print("\ncleanup:")
        for fid in created:
            try:
                trash(fid)
            except Exception as e:  # noqa: BLE001 — cleanup must not mask results
                print(f"  TRASH FAILED {fid}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    raise SystemExit(main())
