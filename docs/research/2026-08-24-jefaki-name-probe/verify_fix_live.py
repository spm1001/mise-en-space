"""Live verification of the fix: the incident query through the real instrument."""
import json, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
from adapters.drive import search_files
from tools.search import do_search

out = {"started_utc": datetime.now(timezone.utc).isoformat(), "checks": {}}

# 1. THE incident query, adapter level — must now find the file.
res = search_files("trashed = false and (name contains 'cudoba-probe')", max_results=20)
out["checks"]["adapter_cudoba"] = {
    "hits": [r.name for r in res.results], "incomplete": res.incomplete}
print("adapter cudoba-probe:", [r.name for r in res.results], "incomplete:", res.incomplete)

# 2. The incident's known-positive, tool level (full funnel, raw_query path).
with tempfile.TemporaryDirectory() as td:
    r = do_search(raw_query="name contains 'somiho-probe'", base_path=Path(td))
    out["checks"]["tool_somiho"] = {
        "drive_results": [d["name"] for d in r.drive_results],
        "cues": {k: v[:120] for k, v in r.cues.items()},
        "errors": r.errors}
    print("tool somiho-probe:", [d["name"] for d in r.drive_results])
    print("  cues:", list(r.cues.keys()))

    # 3. Zero-hit punctuated term through the funnel — cue must fire live.
    r2 = do_search(raw_query="name contains 'jefaki-arm2-nonexistent'", base_path=Path(td))
    out["checks"]["tool_cue_on_null"] = {
        "drive_results": r2.drive_results,
        "has_semantics_cue": "drive_name_semantics" in r2.cues,
        "cue_text": r2.cues.get("drive_name_semantics", "")}
    print("null search cue fired:", "drive_name_semantics" in r2.cues)

    # 4. My Drive regression control: a name search that worked pre-fix still works.
    #    (jeton/mise setup files live in My Drive; find any file by listing root first)
    from adapters.http_client import get_sync_client
    from adapters.drive import _DRIVE_API
    client = get_sync_client()
    root = client.get_json(_DRIVE_API, params={
        "q": "'root' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'",
        "pageSize": "5", "fields": "files(name)"})
    names = [f["name"] for f in root.get("files", [])]
    out["checks"]["mydrive_sample"] = names
    print("my drive sample:", names)
    if names:
        # first whole token of the first name
        import re as _re
        tok = _re.split(r"[^A-Za-z]+", names[0])
        tok = next((t for t in tok if len(t) > 2), None)
        if tok:
            res2 = search_files(f"trashed = false and (name contains '{tok}')", max_results=30)
            found = any(names[0] == r.name for r in res2.results)
            out["checks"]["mydrive_name_search"] = {
                "token": tok, "target": names[0], "found": found,
                "n_hits": len(res2.results)}
            print(f"my drive control: token '{tok}' target '{names[0]}' found={found}")

out["finished_utc"] = datetime.now(timezone.utc).isoformat()
json.dump(out, open(sys.argv[1], "w"), indent=2)
