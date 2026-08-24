"""Live probe 4: (a) digit-boundary tokenisation ('2' as a bare token);
(b) whether SELF-created Shared Drive files are visible to default-corpora
name search (pins the user-corpus definition: created-by counts).

Creates: 1 probe file in My Drive scratch folder (for a);
         1 fresh Shared Drive + 1 probe file (for b) — all deleted after.
"""

import json
import sys
import time
import uuid
from datetime import datetime, timezone

from adapters.drive import _DRIVE_API, search_files
from adapters.http_client import get_sync_client

_DRIVES_API = "https://www.googleapis.com/drive/v3/drives"


def run_query(raw: str, folder_id: str | None) -> list[str]:
    res = search_files(f"trashed = false and ({raw})", max_results=50,
                       folder_id=folder_id)
    return sorted(r.name for r in res.results)


def main() -> None:
    client = get_sync_client()
    out: dict = {
        "probe": "mise-jefaki step 1 — battery 4 (digit boundary + self-created shared-drive visibility)",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "part_a": {}, "part_b": {},
    }
    created_files: list[tuple[str, str]] = []
    shared_drive_id: str | None = None

    try:
        # ---- Part (a): My Drive, digit-boundary discriminator ----
        folder = client.post_json(_DRIVE_API, json_body={
            "name": "jefaki-arm2-probe-folder-4",
            "mimeType": "application/vnd.google-apps.folder"})
        folder_id = folder["id"]
        created_files.append((folder_id, "jefaki-arm2-probe-folder-4"))
        f = client.post_json(_DRIVE_API, json_body={
            "name": "jefaki-arm2-hyphen-probe.md", "mimeType": "text/plain",
            "parents": [folder_id]})
        created_files.append((f["id"], "jefaki-arm2-hyphen-probe.md"))

        deadline = time.time() + 120
        while time.time() < deadline:
            if run_query("name contains 'jefaki'", folder_id):
                break
            time.sleep(3)
        control = run_query("name contains 'jefaki'", folder_id)
        out["part_a"]["control"] = control
        if not control:
            out["part_a"]["aborted"] = "control never indexed"
            print("part (a) control never fired — aborting")
        else:
            for label, term in [
                ("bare_digit_2", "2"),          # 3 hits iff arm2 -> [arm, 2]
                ("arm_prefix", "arm"),          # re-confirm on this file
                ("rm2_substring", "rm2"),       # substring control, expect 0
                ("jefak_first_prefix", "jefak"),
                ("hyph_later_prefix", "hyph"),
            ]:
                hits = run_query(f"name contains '{term}'", folder_id)
                out["part_a"][label] = {"term": term, "count": len(hits),
                                        "hits": hits}
                print(f"a.{label:20s} {len(hits)} hit(s): {hits}")

        # ---- Part (b): fresh Shared Drive, self-created file ----
        try:
            drive = client.post_json(
                _DRIVES_API, json_body={"name": "jefaki-arm2-probe-drive"},
                params={"requestId": str(uuid.uuid4())})
            shared_drive_id = drive["id"]
            print(f"shared drive created: {shared_drive_id}")
        except Exception as e:
            out["part_b"]["skipped"] = f"drives.create refused: {e}"
            print(f"part (b) skipped — drives.create refused: {e}")

        if shared_drive_id:
            sf = client.post_json(_DRIVE_API, params={"supportsAllDrives": "true"},
                                  json_body={
                "name": "jefaki-arm2-shared-probe.md", "mimeType": "text/plain",
                "parents": [shared_drive_id]})
            created_files.append((sf["id"], "jefaki-arm2-shared-probe.md"))

            # Poll via drive-scoped listing (known-positive control).
            deadline = time.time() + 120
            listed: list[str] = []
            while time.time() < deadline:
                resp = client.get_json(_DRIVE_API, params={
                    "q": f"'{shared_drive_id}' in parents and trashed = false",
                    "fields": "files(name)", "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true"})
                listed = [x["name"] for x in resp.get("files", [])]
                if listed:
                    break
                time.sleep(3)
            out["part_b"]["parents_listing"] = listed
            print(f"b.parents_listing: {listed}")

            for label, extra in [
                ("name_default_corpora", None),
                ("name_allDrives", {"corpora": "allDrives"}),
            ]:
                params = {
                    "q": "name contains 'jefaki-arm2-shared-probe' and trashed = false",
                    "fields": "files(name,driveId)", "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true"}
                if extra:
                    params.update(extra)
                resp = client.get_json(_DRIVE_API, params=params)
                hits = [x["name"] for x in resp.get("files", [])]
                out["part_b"][label] = {"count": len(hits), "hits": hits}
                print(f"b.{label:22s} {len(hits)} hit(s): {hits}")

    finally:
        deleted, failed = [], []
        for fid, name in reversed(created_files):
            try:
                client.delete(f"{_DRIVE_API}/{fid}",
                              params={"supportsAllDrives": "true"})
                deleted.append(name)
            except Exception as e:
                failed.append(f"{name} ({fid}): {e}")
        if shared_drive_id:
            try:
                client.delete(f"{_DRIVES_API}/{shared_drive_id}")
                deleted.append("jefaki-arm2-probe-drive (shared drive)")
            except Exception as e:
                failed.append(f"shared drive {shared_drive_id}: {e}")
        out["cleanup"] = {"deleted": deleted, "failed": failed}
        print(f"cleanup: deleted={deleted} failed={failed}")
        out["finished_utc"] = datetime.now(timezone.utc).isoformat()
        with open(sys.argv[1] if len(sys.argv) > 1 else
                  "/tmp/model-pilot/jefaki-probe/results4.json", "w") as fh:
            json.dump(out, fh, indent=2)


if __name__ == "__main__":
    main()
