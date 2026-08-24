"""mise-tanoti before/after measurement: Garni corpus walk, crops on vs off.

Surface (named, per identity discipline): TUBE, sameer.modha@itv.com (own
token via normal resolution — the corpus tree lists freely to this identity,
established during the dehebi evidence close), direct mise LIBRARY fetch
(working tree), serially over the 10 corpus PDFs, thumbnails=False both arms
(Garni's hydrate shape). Arm A = crops on (today's default), arm B = crops
off (the new opt-out). Banked comparables: ~5min30 tube cold walk (impersonated
SA, core install) and 416s Cloud Run ambient (cudoba handoff, 2026-08-24).

Usage:  uv run python docs/research/2026-08-24-tanoti-measure/measure_corpus_walk.py --arm A|B
Writes /tmp/tanoti-arm-<ARM>.json with per-file seconds + fidelity fields.
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

CORPUS_ROOT = "1rb4ZKoQo2lgbnWoq8sGs6vNqUqQnMcoA"  # Garni corpus (Shared Drive)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["A", "B"], required=True)
    args = ap.parse_args()
    crops = args.arm == "A"

    from mise_en_space import Mise
    from adapters.drive import list_folder
    import cues_util

    base = Path(tempfile.mkdtemp(prefix=f"tanoti-{args.arm}-"))
    m = Mise(base_path=base)

    listing = list_folder(CORPUS_ROOT)
    pdfs = sorted(
        (f for f in listing.files if f.mime_type == "application/pdf"),
        key=lambda f: f.name,
    )
    print(f"identity={identity} arm={args.arm} crops={crops} pdfs={len(pdfs)}")

    per_file = []
    t_all = time.monotonic()
    for f in pdfs:
        t0 = time.monotonic()
        r = m.fetch(f.id, thumbnails=False, crops=crops)
        dt = time.monotonic() - t0
        err = getattr(r, "error", None)
        if err:
            per_file.append({"name": f.name, "error": str(err), "secs": round(dt, 1)})
            print(f"  FAIL {f.name}: {err}")
            continue
        manifest = json.loads((Path(r.path) / "manifest.json").read_text())
        content = Path(r.content_file).read_text()
        per_file.append({
            "name": f.name,
            "secs": round(dt, 1),
            "pdf_pages": manifest.get("pdf_pages"),
            "page_markers": manifest.get("page_markers"),
            "last_modified_by": manifest.get("last_modified_by"),
            "crop_count": manifest.get("crop_count", 0),
            "anchor_lines": content.count("exhibit:"),
            # Anchor insertion adds blank lines around each anchor, so the
            # comparable stream drops anchor lines AND blank lines — the
            # first cut kept the blanks and its own artefacts showed
            # differing shas on every crop-bearing file (essayeur catch,
            # 2026-08-24: substance was equal, the instrument said not).
            "content_sha": __import__("hashlib").sha256(
                "\n".join(
                    l for l in content.splitlines()
                    if "exhibit:" not in l and l.strip()
                ).encode()
            ).hexdigest()[:16],
            "deposit_bytes": sum(
                p.stat().st_size for p in Path(r.path).iterdir() if p.is_file()
            ),
        })
        print(f"  {f.name}: {dt:.1f}s pages={manifest.get('pdf_pages')} "
              f"crops={manifest.get('crop_count', 0)} by={manifest.get('last_modified_by')!r}")
    total = time.monotonic() - t_all
    # Identity resolves lazily at first API call — read it AFTER the walk
    # (the first cut read it before and committed identity: null).
    identity = cues_util.current_user_email()

    out = {
        "arm": args.arm, "crops": crops, "identity": identity,
        "surface": "tube, mise working-tree library, serial, thumbnails=False",
        "total_secs": round(total, 1), "files": per_file,
    }
    out_path = Path(f"/tmp/tanoti-arm-{args.arm}.json")
    out_path.write_text(json.dumps(out, indent=2))
    print(f"TOTAL {args.arm}: {total:.1f}s -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
