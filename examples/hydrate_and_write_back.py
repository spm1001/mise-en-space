"""Hydrate a Drive folder into a local workspace, then write a Doc back.

The worked example for mise's library door (mise-jabeka) — the exact shape
an agent service like Garni runs on Cloud Run: read a corpus folder the
service account can see, convert every file to markdown on local disk,
do some work, and land an artefact back in a Shared Drive as a real
Google Doc. Follow this file; you should never need to read mise
internals — if you do, that is a bug in this example, please report it.

Install
-------
mise is not on PyPI, and its jeton dependency is declared bare
(`Requires-Dist: jeton` — uv source maps don't ride wheel metadata), so
a consumer installs both explicitly:

    uv build --wheel ~/repos/spm1001/mise-en-space -o dist/
    uv pip install dist/mise_en_space-*.whl "jeton @ git+https://github.com/spm1001/jeton.git"

(Or depend on the repo directly: `mise-en-space @ git+https://github.com/spm1001/mise-en-space.git`
with the same jeton line.)

Credentials
-----------
`Mise(ambient=True)` resolves Application Default Credentials — on Cloud
Run that is the service's own service account from the metadata server,
with zero files and zero env vars. Ambient is explicit opt-in by design:
mise never falls back to it silently, so a missing token teaches rather
than switching identity. Two facts about running as a service account:

- An SA owns no Drive storage, so writes only land in a SHARED DRIVE the
  SA can write to (content-writer or better). A My Drive create returns
  403 storageQuotaExceeded, and mise's error message teaches exactly this.
- Search defaults to Drive-only and mailbox/calendar ops refuse with the
  reason — a service account has no inbox.

To prove this locally before deploying, impersonate the SA (needs
roles/iam.serviceAccountTokenCreator on it):

    gcloud auth application-default login --impersonate-service-account=<sa-email>

and run with GOOGLE_APPLICATION_CREDENTIALS pointing at the file that
writes — same ambient code path the deployment uses. The scope tier is
per-deployment: set MISE_SCOPES=readonly for a consumer that never
writes; unset means read-write.

The deposit shape
-----------------
Every fetch lands a PER-FILE FOLDER under `<base_path>/.mise/`:

    .mise/{type}--{title-slug}--{id-prefix}/
        manifest.json    # metadata: source id, title, tabs, skips, failures
        content.md       # the document as markdown (or per-tab CSVs for sheets)
        comments.md      # open comments, when any

Consume that shape directly (the manifest names anything that was
skipped, so it replaces any hand-rolled MANIFEST.md), or flatten to
taste — the copy-out loop at the bottom of hydrate() shows the flatten.

Run
---
    GARNI_CORPUS_FOLDER=<drive-folder-id> \
    GARNI_OUTPUT_FOLDER=<shared-drive-folder-id> \
    python examples/hydrate_and_write_back.py
"""

import json
import os
import sys
from pathlib import Path

from mise_en_space import FetchResult, Mise

GOOGLE_DOC = "application/vnd.google-apps.document"
FOLDER = "application/vnd.google-apps.folder"


def hydrate(ws: Mise, folder_id: str, flat_dir: Path) -> list[dict]:
    """Fetch every file in a Drive folder; return what landed where.

    One level, like Garni's corpus. For a tree, a single
    ws.fetch(folder_id, recursive=True) deposits the full indented
    listing instead — walk that if your corpus nests.
    """
    listing = ws.search(folder_id=folder_id, max_results=100)
    if listing.errors:
        raise RuntimeError(f"corpus listing failed: {listing.errors}")

    hydrated: list[dict] = []
    for entry in listing.drive_results:
        if entry["mimeType"] == FOLDER:
            continue
        # Print BEFORE each fetch: PDF conversion can take minutes per
        # file on report-sized documents, and a silent walk is
        # indistinguishable from a hung one.
        print(f"  hydrating {entry['name']} ...", flush=True)
        result = ws.fetch(entry["id"])
        if not isinstance(result, FetchResult):
            # A teaching error object — message says what to do. One bad
            # file shouldn't abort a corpus walk; record it and move on.
            hydrated.append({"id": entry["id"], "name": entry["name"],
                             "error": result.message})
            continue
        deposit = Path(result.path)
        record = {
            "id": entry["id"],
            "name": entry["name"],
            "deposit": deposit,                      # the per-file folder
            "content": Path(result.content_file),    # markdown inside it
            "warnings": result.cues.get("warnings", []),
        }
        # The flatten, for consumers who want one flat dir of .md files.
        # Line 1 is provenance: agents citing the corpus link back to the
        # source document (the Garni field handbook relies on this), and
        # the deposit keeps identity in manifest.json — so the flatten
        # carries it forward rather than stripping it.
        manifest = json.loads((deposit / "manifest.json").read_text())
        flat_dir.mkdir(parents=True, exist_ok=True)
        flat = flat_dir / f"{entry['name']}.md"
        header = (
            f"source: https://drive.google.com/file/d/{manifest['id']}/view\n"
            f"fetched: {manifest['fetched_at']} via mise ({manifest['type']})\n\n"
        )
        flat.write_text(header + record["content"].read_text())
        record["flat"] = flat
        hydrated.append(record)
    return hydrated


def write_back(ws: Mise, folder_id: str, title: str, markdown: str) -> dict:
    """Land markdown in Drive as a real Google Doc (headings, tables render).

    folder_id must be inside a Shared Drive when running as a service
    account. To UPDATE an existing Doc instead, use
    ws.do("overwrite", file_id=..., content=...) — same markdown-in
    contract, and the result's cues carry a restore point.
    """
    result = ws.do("create", title=title, content=markdown, folder_id=folder_id)
    if result.get("error"):
        raise RuntimeError(f"create refused: {result['message']}")
    return result


def main() -> int:
    corpus = os.environ["GARNI_CORPUS_FOLDER"]
    output = os.environ["GARNI_OUTPUT_FOLDER"]

    workdir = Path.cwd() / "workspace"
    ws = Mise(ambient=True, base_path=workdir)

    hydrated = hydrate(ws, corpus, flat_dir=workdir / "flat")
    for h in hydrated:
        status = h.get("error") or h["content"]
        print(f"  {h['name']} -> {status}", flush=True)

    digest = "\n".join(
        ["# Corpus digest", "", f"Hydrated {len(hydrated)} files:", ""]
        + [f"- **{h['name']}**" + (f" — FAILED: {h['error']}" if h.get("error") else "")
           for h in hydrated]
    )
    created = write_back(ws, output, "Corpus digest (worked example)", digest)
    print(f"Doc created: {created['web_link']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
