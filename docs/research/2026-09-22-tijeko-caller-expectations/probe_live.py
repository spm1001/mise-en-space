"""Live feasibility probes for the two 'consume' verdicts that need an API the
code doesn't use yet (mise-tijeko, rule 4 of the pre-registration).

  C6: can Drive re-parent a Google Form right after the Forms API mints it?
  C9: does patching conferenceData to null remove an event's Meet link?

Scratch artefacts only, on the authed account's own Drive and primary calendar:
no attendees, sendUpdates=none, and everything minted is DELETED at the end
(permanent delete — nothing here was ever shared). Run from the repo root:

    uv run --all-extras python docs/research/2026-09-22-tijeko-caller-expectations/probe_live.py
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from adapters.calendar import _CALENDAR_API, get_event, insert_event, patch_event  # noqa: E402
from adapters.http_client import get_sync_client  # noqa: E402
from tools.events_util import meet_request  # noqa: E402
from tools.form_create import _api_create_form  # noqa: E402
from tools.move import _DRIVE_API, _move_file  # noqa: E402

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
client = get_sync_client()
out: dict = {"stamp": stamp}
minted_files: list[str] = []
minted_event: str | None = None


def parents(file_id: str) -> list[str]:
    meta = client.get_json(f"{_DRIVE_API}/{file_id}",
                           params={"fields": "parents,mimeType,name", "supportsAllDrives": "true"})
    return meta.get("parents", [])


WHICH = set(sys.argv[1:]) or {"c6", "c9"}
try:
  if "c6" in WHICH:
    # --- C6 -----------------------------------------------------------------
    folder = client.post_json(_DRIVE_API, params={"supportsAllDrives": "true", "fields": "id"},
                              json_body={"name": f"tijeko probe folder {stamp} (scratch)",
                                         "mimeType": "application/vnd.google-apps.folder"})
    minted_files.append(folder["id"])
    form = _api_create_form(f"tijeko probe form {stamp} (scratch)")
    form_id = form["formId"]
    minted_files.append(form_id)
    before = parents(form_id)
    moved = _move_file(form_id, folder["id"])
    after = parents(form_id)
    out["C6"] = {"parents_before": before, "folder": folder["id"], "parents_after": after,
                 "move_result": moved.to_dict() if hasattr(moved, "to_dict") else moved,
                 "landed": after == [folder["id"]]}

  if "c9" in WHICH:
    # --- C9 -----------------------------------------------------------------
    ev = insert_event({
        "summary": f"tijeko probe event {stamp} (scratch)",
        "start": {"dateTime": "2026-12-30T07:00:00Z"},
        "end": {"dateTime": "2026-12-30T07:15:00Z"},
        "conferenceData": meet_request(),
    }, send_updates="none")
    minted_event = ev["id"]
    had = {"hangoutLink": ev.get("hangoutLink"), "has_conferenceData": "conferenceData" in ev}
    time.sleep(20)  # run 1: an immediate patch drew 403 rateLimitExceeded
    patched = patch_event(minted_event, {"conferenceData": None}, send_updates="none")
    reread = get_event(minted_event)
    out["C9"] = {
        "at_insert": had,
        "patch_response": {"hangoutLink": patched.get("hangoutLink"),
                           "has_conferenceData": "conferenceData" in patched},
        "reread": {"hangoutLink": reread.get("hangoutLink"),
                   "has_conferenceData": "conferenceData" in reread},
        "removed": "conferenceData" not in reread and not reread.get("hangoutLink"),
    }
finally:
    cleanup = []
    if minted_event:
        client.delete(f"{_CALENDAR_API}/primary/events/{minted_event}", params={"sendUpdates": "none"})
        cleanup.append(("event", minted_event))
    for fid in reversed(minted_files):
        client.delete(f"{_DRIVE_API}/{fid}", params={"supportsAllDrives": "true"})
        cleanup.append(("drive", fid))
    out["deleted"] = cleanup
    print(json.dumps(out, indent=2, default=str))
