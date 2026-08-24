"""Live probe: Drive `name contains` tokenisation vs hyphen / underscore / dot.

Card mise-jefaki, step 1. Creates three empty probe files in a dedicated
scratch folder, polls a known-positive control until name indexing is live,
runs a folder-scoped query battery (counts are exact, 0..3), then deletes
every created resource by explicit id.

Run from the mise-en-space clone:
    PYTHONPATH=<clone> uv run python probe_tokenisation.py
"""

import json
import sys
import time
from datetime import datetime, timezone

from adapters.drive import _DRIVE_API, search_files
from adapters.http_client import get_sync_client

FOLDER_NAME = "jefaki-arm2-probe-folder"
FILE_NAMES = [
    "jefaki-arm2-hyphen-probe.md",       # hyphens only
    "jefaki-arm2_underscore_probe.md",   # underscores after the required token
    "jefaki-arm2.dot.probe.md",          # dots after the required token
]

# (label, name-contains term) — each run folder-scoped. None-term entries
# carry a full raw clause instead.
CONTAINS_BATTERY: list[tuple[str, str]] = [
    ("control_first_token",     "jefaki"),
    ("full_hyphen_name",        "jefaki-arm2-hyphen-probe.md"),
    ("hyphen_name_no_ext",      "jefaki-arm2-hyphen-probe"),
    ("hyphen_pair",             "jefaki-arm2"),
    ("second_token",            "arm2"),
    ("inner_token_hyphen",      "hyphen"),
    ("inner_token_prefix",      "hyph"),
    ("mid_token_substring",     "efaki"),
    ("trailing_hyphen",         "jefaki-"),
    ("underscore_inner",        "underscore"),
    ("underscore_chunk",        "arm2_underscore_probe"),
    ("full_underscore_name",    "jefaki-arm2_underscore_probe.md"),
    ("dot_inner",               "dot"),
    ("extension_token",         "md"),
    ("full_dot_name",           "jefaki-arm2.dot.probe.md"),
    ("space_multiword",         "jefaki arm2"),
    ("case_upper",              "JEFAKI"),
    ("probe_token",             "probe"),
]

RAW_BATTERY: list[tuple[str, str]] = [
    ("anded_tokens_hyphen",
     "name contains 'jefaki' and name contains 'arm2' and name contains 'hyphen' and name contains 'probe'"),
    ("exact_equals_hyphen",
     "name = 'jefaki-arm2-hyphen-probe.md'"),
    # fullText nulls are inconclusive (content indexing lag on fresh files);
    # recorded for interest, never load-bearing.
    ("fulltext_hyphen_name",
     "fullText contains 'jefaki-arm2-hyphen-probe'"),
    ("fulltext_control",
     "fullText contains 'jefaki'"),
]


def run_query(raw: str, folder_id: str) -> list[str]:
    res = search_files(f"trashed = false and ({raw})", max_results=50,
                       folder_id=folder_id)
    return sorted(r.name for r in res.results)


def main() -> None:
    client = get_sync_client()
    out: dict = {
        "probe": "mise-jefaki step 1 — Drive name-contains tokenisation",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "files": FILE_NAMES,
        "queries": {},
    }

    created: list[tuple[str, str]] = []  # (id, name) — deletion targets
    try:
        folder = client.post_json(_DRIVE_API, json_body={
            "name": FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
        })
        folder_id = folder["id"]
        created.append((folder_id, FOLDER_NAME))
        print(f"folder created: {FOLDER_NAME} ({folder_id})")

        for name in FILE_NAMES:
            f = client.post_json(_DRIVE_API, json_body={
                "name": name,
                "mimeType": "text/plain",
                "parents": [folder_id],
            })
            created.append((f["id"], name))
            print(f"file created: {name} ({f['id']})")

        # Known-positive control: poll until name indexing sees all 3.
        # Nulls in the battery count only after this fires (verification.md).
        deadline = time.time() + 120
        control_hits: list[str] = []
        while time.time() < deadline:
            control_hits = run_query("name contains 'jefaki'", folder_id)
            if len(control_hits) == 3:
                break
            time.sleep(3)
        out["control_poll"] = {
            "hits": control_hits,
            "all_three_indexed": len(control_hits) == 3,
        }
        print(f"control poll: {len(control_hits)}/3 indexed -> {control_hits}")
        if len(control_hits) != 3:
            print("CONTROL DID NOT FIRE — battery nulls would be meaningless; aborting.")
            out["aborted"] = "control never reached 3/3 within 120s"
            return

        for label, term in CONTAINS_BATTERY:
            hits = run_query(f"name contains '{term}'", folder_id)
            out["queries"][label] = {
                "clause": f"name contains '{term}'", "hits": hits,
                "count": len(hits),
            }
            print(f"{label:24s} {len(hits)} hit(s): {hits}")

        for label, raw in RAW_BATTERY:
            hits = run_query(raw, folder_id)
            out["queries"][label] = {"clause": raw, "hits": hits,
                                     "count": len(hits)}
            print(f"{label:24s} {len(hits)} hit(s): {hits}")

    finally:
        # Delete children first, folder last — every target by explicit id.
        deleted, failed = [], []
        for fid, name in reversed(created):
            try:
                client.delete(f"{_DRIVE_API}/{fid}",
                              params={"supportsAllDrives": "true"})
                deleted.append(name)
            except Exception as e:  # record, keep deleting the rest
                failed.append(f"{name} ({fid}): {e}")
        out["cleanup"] = {"deleted": deleted, "failed": failed}
        print(f"cleanup: deleted={deleted} failed={failed}")
        out["finished_utc"] = datetime.now(timezone.utc).isoformat()
        with open(sys.argv[1] if len(sys.argv) > 1 else
                  "/tmp/model-pilot/jefaki-probe/results.json", "w") as fh:
            json.dump(out, fh, indent=2)


if __name__ == "__main__":
    main()
