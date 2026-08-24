"""Live probe 5: multi-token literal name-prefix terms. One file, four queries."""
import json, sys, time
from datetime import datetime, timezone
from adapters.drive import _DRIVE_API, search_files
from adapters.http_client import get_sync_client

def run_query(raw, folder_id):
    res = search_files(f"trashed = false and ({raw})", max_results=50, folder_id=folder_id)
    return sorted(r.name for r in res.results)

def main():
    client = get_sync_client()
    out = {"probe": "mise-jefaki step 1 — battery 5 (multi-token literal name prefix)",
           "started_utc": datetime.now(timezone.utc).isoformat(), "queries": {}}
    created = []
    try:
        folder = client.post_json(_DRIVE_API, json_body={
            "name": "jefaki-arm2-probe-folder-5",
            "mimeType": "application/vnd.google-apps.folder"})
        created.append((folder["id"], "jefaki-arm2-probe-folder-5"))
        f = client.post_json(_DRIVE_API, json_body={
            "name": "jefaki-arm2-hyphen-probe.md", "mimeType": "text/plain",
            "parents": [folder["id"]]})
        created.append((f["id"], "jefaki-arm2-hyphen-probe.md"))
        deadline = time.time() + 120
        while time.time() < deadline:
            if run_query("name contains 'jefaki'", folder["id"]):
                break
            time.sleep(3)
        control = run_query("name contains 'jefaki'", folder["id"])
        out["control"] = control
        if not control:
            out["aborted"] = "control never indexed"; print("control never fired"); return
        for label, term in [
            ("literal_prefix_2tok", "jefaki-arm"),          # literal name prefix, 2nd token cut
            ("literal_prefix_3tok", "jefaki-arm2-hyph"),    # literal name prefix, 3rd token cut
            ("literal_prefix_ext",  "jefaki-arm2-hyphen-probe.m"),  # cut inside extension
            ("midname_multitok",    "efaki-arm2"),          # mid-name, expect 0
        ]:
            hits = run_query(f"name contains '{term}'", folder["id"])
            out["queries"][label] = {"term": term, "count": len(hits), "hits": hits}
            print(f"{label:22s} {len(hits)} hit(s): {hits}")
    finally:
        deleted, failed = [], []
        for fid, name in reversed(created):
            try:
                client.delete(f"{_DRIVE_API}/{fid}", params={"supportsAllDrives": "true"})
                deleted.append(name)
            except Exception as e:
                failed.append(f"{name} ({fid}): {e}")
        out["cleanup"] = {"deleted": deleted, "failed": failed}
        print(f"cleanup: deleted={deleted} failed={failed}")
        out["finished_utc"] = datetime.now(timezone.utc).isoformat()
        json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else
                  "/tmp/model-pilot/jefaki-probe/results5.json", "w"), indent=2)

if __name__ == "__main__":
    main()
