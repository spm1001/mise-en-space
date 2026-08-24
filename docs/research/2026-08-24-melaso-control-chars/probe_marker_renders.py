"""Does the chosen remedy actually land visibly? (mise-melaso)

The fix claims two things a mocked test cannot check: that a ``---`` line
imports as a REAL horizontal rule (so a converted page break "survives
visibly" rather than becoming three literal hyphens), and that stripping
the NUL is what saves the tail of a document from the import's silent
truncation.  Both get measured through the real API before the claim is
written down.

  F1  PDF-shaped content (form feeds) through sanitise(rich=True) →
      do(create) → is there a boundary element in the document?
  F2  the same through sanitise(rich=False) → do(append) → does the
      literal marker arrive in the text?
  F3  NUL-bearing content through sanitise(rich=True) → do(create) →
      does the tail that probe A3 lost now arrive?

Run:  MELASO_PROBE_TAG=<unique-token> uv run --all-extras python \\
          docs/research/2026-08-24-melaso-control-chars/probe_marker_renders.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mise_en_space import Mise  # noqa: E402
from probe_control_chars import read_doc, trash  # noqa: E402
from tools.doc_control_chars import sanitise_doc_content  # noqa: E402

TAG = os.environ.get("MELASO_PROBE_TAG", "melaso-probe")
SCRATCH = Path(os.environ.get("MELASO_PROBE_SCRATCH", "/tmp/melaso-probe"))

# What a two-page PDF deposit's content.md actually looks like: pdftotext
# ends each page with a form feed.
PDF_SHAPED = "Page one body text.\n\fPage two body text.\n\fPage three.\n"
NUL_SHAPED = "HEADMARKER\n\nBEFORE\x00AFTER\n\nTAILMARKER\n"


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    mise = Mise(base_path=SCRATCH)
    created: list[str] = []
    rows: list[dict[str, Any]] = []

    def make(suffix: str, body: str) -> str:
        res = mise.do(
            "create", doc_type="doc",
            title=f"{TAG} {suffix} (scratch, safe to trash)", content=body,
        )
        if res.get("error"):
            raise SystemExit(f"SETUP FAILED ({suffix}): {res}")
        created.append(res["file_id"])
        print(f"scratch doc [{suffix}]: {res['file_id']}")
        return res["file_id"]

    try:
        # F1 — rich path: does '---' become a real horizontal rule?
        body, warns, counts = sanitise_doc_content(PDF_SHAPED, rich=True)
        print("\n=== F1 rich sanitise ===")
        print("  sanitised repr:", repr(body))
        print("  counts        :", counts)
        print("  warnings      :", warns)
        text, elements = read_doc(make("F1-rich-marker", body))
        print("  read-back     :", repr(text))
        print("  elements      :", elements)
        print("  literal '---' in text:", "---" in text)
        rows.append({"case": "F1 rich marker", "text": text,
                     "elements": elements, "counts": counts})

        # F2 — plain path: the marker as literal text through insertText
        body2, warns2, counts2 = sanitise_doc_content(PDF_SHAPED, rich=False)
        doc2 = make("F2-plain-marker", "Seed.\n")
        res2 = mise.do("append", file_id=doc2, content=body2)
        print("\n=== F2 plain sanitise (append) ===")
        print("  cues          :", res2.get("cues"))
        text2, elements2 = read_doc(doc2)
        print("  read-back     :", repr(text2))
        print("  literal '---' in text:", "---" in text2)
        rows.append({"case": "F2 plain marker", "text": text2,
                     "counts": counts2, "warnings": warns2})

        # F3 — the NUL payoff: does the tail arrive now?
        body3, warns3, counts3 = sanitise_doc_content(NUL_SHAPED, rich=True)
        text3, _ = read_doc(make("F3-nul-stripped", body3))
        print("\n=== F3 NUL stripped ===")
        print("  counts        :", counts3)
        print("  read-back     :", repr(text3))
        print("  TAILMARKER present:", "TAILMARKER" in text3)
        rows.append({"case": "F3 nul stripped", "text": text3,
                     "counts": counts3, "warnings": warns3})

        out = SCRATCH / "melaso-marker-renders-results.json"
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
