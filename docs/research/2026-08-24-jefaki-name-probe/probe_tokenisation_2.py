"""Live probe 2: discriminate phrase-vs-AND, order, token-prefix position,
cross-separator neutrality; plus corpus-wide re-checks of the original
incident's file names (somiho-probe / cudoba-probe), read-only.

Card mise-jefaki, step 1 (second battery).
"""

import json
import sys
import time
from datetime import datetime, timezone

from adapters.drive import _DRIVE_API, search_files
from adapters.http_client import get_sync_client

FOLDER_NAME = "jefaki-arm2-probe-folder-2"
FILE_NAMES = [
    "jefaki-arm2-hyphen-probe.md",
    "jefaki-arm2_underscore_probe.md",
    "jefaki-arm2.dot.probe.md",
]

CONTAINS_BATTERY: list[tuple[str, str]] = [
    ("control_first_token",       "jefaki"),
    # token-prefix by position
    ("first_token_prefix",        "jefak"),
    ("second_token_prefix",       "arm"),
    ("last_token_prefix_single",  "prob"),      # prefix of a late token, alone
    # phrase vs AND: non-consecutive token pairs
    ("nonconsecutive_pair",       "jefaki-hyphen"),
    ("nonconsecutive_space",      "jefaki probe"),
    # order sensitivity: reversed consecutive pair
    ("reversed_pair",             "arm2-jefaki"),
    # cross-separator: query separator differs from the name's
    ("cross_separator_dot",       "arm2.hyphen"),
    ("cross_separator_space",     "arm2 hyphen"),
    ("cross_sep_underscore",      "jefaki_arm2"),
    # phrase-prefix: multi-token term whose LAST token is a prefix
    ("phrase_last_prefix",        "hyphen-pro"),
    ("phrase_first_prefix",       "jefak-arm2"),
    # dot pair (extension phrase)
    ("dot_pair_ext",              "probe.md"),
    # whole-name-minus-extension-dot check on underscore file
    ("underscore_cross_hyphen",   "arm2-underscore"),
]

def run_query(raw: str, folder_id: str | None) -> list[str]:
    res = search_files(f"trashed = false and ({raw})", max_results=50,
                       folder_id=folder_id)
    return sorted(r.name for r in res.results)


def main() -> None:
    client = get_sync_client()
    out: dict = {
        "probe": "mise-jefaki step 1 — battery 2 (phrase/order/prefix/cross-separator)",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "queries": {},
        "incident_recheck": {},
    }

    # Read-only first: do the ORIGINAL incident's names match today, corpus-wide?
    for label, raw in [
        ("somiho_token",        "name contains 'somiho'"),
        ("somiho_hyphenated",   "name contains 'somiho-probe'"),
        ("cudoba_token",        "name contains 'cudoba'"),
        ("cudoba_hyphenated",   "name contains 'cudoba-probe'"),
    ]:
        hits = run_query(raw, None)
        out["incident_recheck"][label] = {"clause": raw, "hits": hits,
                                          "count": len(hits)}
        print(f"incident {label:20s} {len(hits)} hit(s): {hits}")

    created: list[tuple[str, str]] = []
    try:
        folder = client.post_json(_DRIVE_API, json_body={
            "name": FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
        })
        folder_id = folder["id"]
        created.append((folder_id, FOLDER_NAME))

        for name in FILE_NAMES:
            f = client.post_json(_DRIVE_API, json_body={
                "name": name, "mimeType": "text/plain",
                "parents": [folder_id],
            })
            created.append((f["id"], name))

        # Known-positive control with poll-count recorded.
        deadline = time.time() + 120
        polls = 0
        control_hits: list[str] = []
        while time.time() < deadline:
            polls += 1
            control_hits = run_query("name contains 'jefaki'", folder_id)
            if len(control_hits) == 3:
                break
            time.sleep(3)
        out["control_poll"] = {"polls": polls, "hits": control_hits,
                               "all_three_indexed": len(control_hits) == 3}
        print(f"control poll: {len(control_hits)}/3 after {polls} poll(s)")
        if len(control_hits) != 3:
            out["aborted"] = "control never reached 3/3 within 120s"
            print("CONTROL DID NOT FIRE — aborting battery.")
            return

        for label, term in CONTAINS_BATTERY:
            hits = run_query(f"name contains '{term}'", folder_id)
            out["queries"][label] = {
                "clause": f"name contains '{term}'", "hits": hits,
                "count": len(hits),
            }
            print(f"{label:26s} {len(hits)} hit(s): {hits}")

    finally:
        deleted, failed = [], []
        for fid, name in reversed(created):
            try:
                client.delete(f"{_DRIVE_API}/{fid}",
                              params={"supportsAllDrives": "true"})
                deleted.append(name)
            except Exception as e:
                failed.append(f"{name} ({fid}): {e}")
        out["cleanup"] = {"deleted": deleted, "failed": failed}
        print(f"cleanup: deleted={deleted} failed={failed}")
        out["finished_utc"] = datetime.now(timezone.utc).isoformat()
        with open(sys.argv[1] if len(sys.argv) > 1 else
                  "/tmp/model-pilot/jefaki-probe/results2.json", "w") as fh:
            json.dump(out, fh, indent=2)


if __name__ == "__main__":
    main()
