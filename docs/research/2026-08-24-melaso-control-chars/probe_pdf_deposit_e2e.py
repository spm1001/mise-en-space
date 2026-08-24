"""End-to-end: a REAL PDF deposit pasted into a Doc keeps its page boundaries.

The card's sharp edge is not an abstract control character — it is the one
mise writes itself. ``pdftotext -layout`` separates pages with a form feed,
``fetch`` counts those into ``cues.page_markers``, and ``do(create)`` used
to drop every one of them on the way into a Doc. So the closing proof runs
the whole scenario on a real document rather than on a synthetic string:

  fetch a Drive PDF → read its deposit's page_markers → create a Doc from
  that deposit with ``source=`` → count horizontalRule elements in the
  document Google actually built.

NB ``page_markers`` rides the deposit's **manifest.json**, not ``cues`` —
the first cut of this probe read ``cues`` and reported ``None`` for five
consecutive PDFs, which looks exactly like "no PDF here has page markers".
The manifest showed 58 form feeds on one of the same five.

Pass condition: rules == markers, and the create result cues the count.

Run:  MELASO_PROBE_TAG=<unique-token> uv run --all-extras python \\
          docs/research/2026-08-24-melaso-control-chars/probe_pdf_deposit_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mise_en_space import Mise  # noqa: E402
from probe_control_chars import read_doc, trash  # noqa: E402

TAG = os.environ.get("MELASO_PROBE_TAG", "melaso-probe")
SCRATCH = Path(os.environ.get("MELASO_PROBE_SCRATCH", "/tmp/melaso-probe"))


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    mise = Mise(base_path=SCRATCH)

    hits = mise.search("", type="pdf", max_results=5).drive_results
    if not hits:
        print("no PDF in Drive to test with — inconclusive, not a pass")
        return 2

    deposit = None
    for hit in hits:
        got = mise.fetch(hit["id"], thumbnails=False, crops=False)
        path = Path(got.path)
        manifest = json.loads((path / "manifest.json").read_text())
        markers = manifest.get("page_markers") or 0
        print(f"{hit['name'][:60]!r}: page_markers={markers} "
              f"pdf_pages={manifest.get('pdf_pages')}")
        if markers >= 2:  # a one-page PDF proves nothing about boundaries
            deposit = (path, markers, hit["name"])
            break

    if not deposit:
        print("no multi-page PDF with surviving markers — inconclusive")
        return 2

    path, markers, name = deposit
    raw = (path / "content.md").read_text(encoding="utf-8")
    print(f"\nusing {name!r}: {raw.count(chr(12))} form feeds in content.md")

    created = mise.do(
        "create", doc_type="doc", source=str(path),
        title=f"{TAG} PDF-deposit e2e (scratch, safe to trash)",
    )
    if created.get("error"):
        print("CREATE FAILED:", created)
        return 2
    doc_id = created["file_id"]
    print("scratch doc:", doc_id)
    print("cues.page_breaks_marked:", created["cues"].get("page_breaks_marked"))
    print("cues.warnings:", created["cues"].get("warnings"))

    try:
        text, elements = read_doc(doc_id)
        rules = text.count("<horizontalRule>")
        print(f"\nform feeds in deposit : {markers}")
        print(f"horizontal rules in doc: {rules}")
        print(f"form feeds in doc      : {text.count(chr(12))}")
        ok = (
            rules == markers
            and text.count(chr(12)) == 0
            and created["cues"].get("page_breaks_marked") == markers
        )
        print("\nVERDICT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        trash(doc_id)


if __name__ == "__main__":
    raise SystemExit(main())
