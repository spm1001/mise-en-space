"""Live probe 3 (READ-ONLY): reproduce the incident on its own artefact.

cudoba-probe.md (1SklXLZ1fhUFBC6E_2KC7B6vdbvZDu0gmd_XWyTiRPek) was created
2026-08-24 ~09:16Z by the agent-spike SA in the Garni Shared Drive corpus.
From this user token, `name contains 'cudoba'` returned 0 hours later
(battery 2) — so indexing lag is dead. Hypothesis: the default `corpora=user`
(files created by / opened by / shared directly with the user) excludes it,
while `includeItemsFromAllDrives=true` alone does not widen a name query.

All calls read-only: files.get, files.list. Nothing created.
"""

import json
import sys
from datetime import datetime, timezone

from adapters.drive import _DRIVE_API, search_files
from adapters.http_client import get_sync_client

CUDOBA_ID = "1SklXLZ1fhUFBC6E_2KC7B6vdbvZDu0gmd_XWyTiRPek"
FIELDS = "files(id,name,driveId,parents),nextPageToken"


def main() -> None:
    client = get_sync_client()
    out: dict = {
        "probe": "mise-jefaki step 1 — battery 3 (corpora mechanism, read-only)",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "steps": {},
    }

    def record(label: str, data) -> None:
        out["steps"][label] = data
        print(f"{label}: {json.dumps(data)[:300]}")

    # 1. The file exists and is reachable by id.
    meta = client.get_json(
        f"{_DRIVE_API}/{CUDOBA_ID}",
        params={"supportsAllDrives": "true",
                "fields": "id,name,driveId,parents,createdTime,"
                          "lastModifyingUser(displayName),mimeType"},
    )
    record("files_get_by_id", meta)
    drive_id = meta.get("driveId")
    parent = (meta.get("parents") or [None])[0]

    def list_q(label: str, q: str, extra: dict | None = None) -> None:
        params = {
            "q": q,
            "pageSize": "20",
            "fields": FIELDS,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if extra:
            params.update(extra)
        resp = client.get_json(_DRIVE_API, params=params)
        hits = [f["name"] for f in resp.get("files", [])]
        record(label, {"q": q, "extra": extra or {}, "count": len(hits),
                       "hits": hits})

    # 2. The mise instrument as-shipped (search_files → default corpora).
    res = search_files("trashed = false and (name contains 'cudoba-probe')",
                       max_results=20)
    record("mise_search_files_default", {
        "count": len(res.results), "hits": [r.name for r in res.results]})

    # 3. Same query, direct API, default corpora (control for step 2).
    list_q("direct_default_corpora", "name contains 'cudoba-probe' and trashed = false")

    # 4. Same query with corpora=allDrives — the hypothesis test.
    list_q("direct_allDrives", "name contains 'cudoba-probe' and trashed = false",
           {"corpora": "allDrives"})

    # 5. Scoped to the file's own drive.
    if drive_id:
        list_q("direct_drive_scoped", "name contains 'cudoba-probe' and trashed = false",
               {"corpora": "drive", "driveId": drive_id})

    # 6. Parents listing through the SAME default-corpora path — the incident's
    #    "found by folder-scoped listing" half.
    if parent:
        list_q("parents_default_corpora",
               f"'{parent}' in parents and trashed = false")

    # 7. Exact-name equality, default vs allDrives.
    list_q("exact_name_default", "name = 'cudoba-probe.md' and trashed = false")
    list_q("exact_name_allDrives", "name = 'cudoba-probe.md' and trashed = false",
           {"corpora": "allDrives"})

    # 8. The known-positive from the incident: somiho-probe, allDrives.
    list_q("somiho_allDrives", "name contains 'somiho' and trashed = false",
           {"corpora": "allDrives"})

    out["finished_utc"] = datetime.now(timezone.utc).isoformat()
    with open(sys.argv[1] if len(sys.argv) > 1 else
              "/tmp/model-pilot/jefaki-probe/results3.json", "w") as fh:
        json.dump(out, fh, indent=2)


if __name__ == "__main__":
    main()
