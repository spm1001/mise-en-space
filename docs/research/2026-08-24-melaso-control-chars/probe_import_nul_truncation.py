"""Follow-up: the Drive markdown import TRUNCATES the document at ``\\x00``.

probe_control_chars.py's path A read back as
``'MELASOPROBE START\\n\\nFFAFFB\\n\\nNULA\\n\\n'`` — every sentinel after the
NUL simply absent, under an HTTP 200.  That is a bigger claim than the card
made (silent truncation, not silent thinning), so it gets its own controlled
run before anything is written down:

  A2  sentinels with the NUL REMOVED  — isolates the cause: if the tail
      arrives, the NUL is what cut it, not length or the CRLF
  A3  HEAD / BEFORE\\x00AFTER / TAIL   — where exactly does the cut fall?
  A4  the same shape with the NUL replaced by a plain 'X'  — the
      known-positive control, because a probe that reports absence is
      only checked once it has fired on something present
  A5  a lone ``\\f`` on its own line   — does the import read an isolated
      form feed as a page break (the "survives visibly" outcome) rather
      than deleting it?

Run:  MELASO_PROBE_TAG=<unique-token> uv run --all-extras python \\
          docs/research/2026-08-24-melaso-control-chars/probe_import_nul_truncation.py
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
from probe_control_chars import (  # noqa: E402
    SENTINELS,
    read_doc,
    trash,
    verdicts,
)

TAG = os.environ.get("MELASO_PROBE_TAG", "melaso-probe")
SCRATCH = Path(os.environ.get("MELASO_PROBE_SCRATCH", "/tmp/melaso-probe"))


def sentinels_without(skip: str) -> str:
    lines = ["MELASOPROBE START", ""]
    for name, ch, shape in SENTINELS:
        if name == skip:
            continue
        lines.append(shape.format(ch))
        lines.append("")
    lines.append("MELASOPROBE END")
    return "\n".join(lines) + "\n"


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

    cut_shape = "HEADMARKER\n\nmiddle text\n\nBEFORE{}AFTER\n\nTAILMARKER\n"

    try:
        # A2 — same sentinels, NUL removed
        body = sentinels_without("nul")
        text, _ = read_doc(make("A2-no-nul", body))
        v = verdicts(body, text)
        print("\n=== A2 import, NUL removed ===")
        print("  verdicts:", json.dumps({k: x for k, x in v.items() if k != "nul"}))
        print("  read-back:", repr(text))
        rows.append({"case": "A2 import without NUL", "verdicts": v, "text": text})

        # A3 — where does the cut fall?
        text, _ = read_doc(make("A3-nul-cut", cut_shape.format("\x00")))
        print("\n=== A3 import, BEFORE\\x00AFTER ===")
        print("  read-back:", repr(text))
        print("  HEADMARKER present:", "HEADMARKER" in text)
        print("  BEFORE present    :", "BEFORE" in text)
        print("  AFTER present     :", "AFTER" in text)
        print("  TAILMARKER present:", "TAILMARKER" in text)
        rows.append({"case": "A3 import with NUL mid-document", "text": text})

        # A4 — known-positive control for the same instrument
        text, _ = read_doc(make("A4-control-x", cut_shape.format("X")))
        print("\n=== A4 CONTROL, same shape with 'X' ===")
        print("  read-back:", repr(text))
        print("  TAILMARKER present:", "TAILMARKER" in text)
        rows.append({"case": "A4 control (X instead of NUL)", "text": text})

        # A5 — a lone form feed: page break, or deleted?
        text, elements = read_doc(make("A5-lone-ff", "PAGEONE\n\n\f\n\nPAGETWO\n"))
        print("\n=== A5 import, lone form feed on its own line ===")
        print("  read-back:", repr(text))
        print("  elements :", elements)
        rows.append({"case": "A5 lone form feed", "text": text, "elements": elements})

        out = SCRATCH / "melaso-nul-truncation-results.json"
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
