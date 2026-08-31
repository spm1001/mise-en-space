#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Suggest-mode probe (mise-picihi extension): writeMode SUGGEST + accept.

On the scratch probe doc: (1) insertText under writeControl.writeMode=SUGGEST,
twice (two separate suggestion threads); (2) read back with SUGGESTIONS_INLINE
and locate the suggestedInsertionIds; (3) accept ONE via acceptSuggestion,
leaving the other open for the UI eyeball. Evidence continues from 32.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

EVIDENCE = Path(__file__).parent
TOKEN_PATH = Path.home() / ".claude/plugins/data/mise-batterie-de-savoir/token.json"
DOC_ID = "1eS6SMV2kKwNW_uIm5Kez6V_6UVwmt0y9U_lZqCuioAo"

seq = 32
access_token = None


def api(name, method, url, body=None):
    global seq
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {access_token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode()
    parsed = json.loads(raw)
    seq += 1
    fname = f"{seq:02d}-{name}.json"
    (EVIDENCE / fname).write_text(json.dumps({
        "request": {"method": method, "url": url, "body": body},
        "status": status, "response": parsed}, indent=2))
    print(f"[{status}] {name} -> {fname}")
    return status, parsed


def refresh_token():
    global access_token
    t = json.loads(TOKEN_PATH.read_text())
    data = urllib.parse.urlencode({
        "client_id": t["client_id"], "client_secret": t["client_secret"],
        "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}).encode()
    req = urllib.request.Request(t["token_uri"], data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        access_token = json.loads(r.read())["access_token"]


def find_suggestions(doc):
    """Walk tabs/body text runs collecting suggestedInsertionIds."""
    found = {}

    def walk_content(content, where):
        for el in content or []:
            para = el.get("paragraph")
            if not para:
                continue
            for pe in para.get("elements", []):
                tr = pe.get("textRun")
                if tr and tr.get("suggestedInsertionIds"):
                    for sid in tr["suggestedInsertionIds"]:
                        found.setdefault(sid, []).append(
                            {"where": where, "text": tr.get("content", "")[:60]})

    body = (doc.get("body") or {}).get("content")
    walk_content(body, "body")
    for tab in doc.get("tabs", []):
        dt = (tab.get("documentTab") or {})
        walk_content((dt.get("body") or {}).get("content"), f"tab:{tab.get('tabProperties', {}).get('title')}")
    return found


def main():
    refresh_token()

    # Two suggested insertions, separate batches → separate suggestion threads.
    api("suggest-insert-1", "POST",
        f"https://docs.googleapis.com/v1/documents/{DOC_ID}:batchUpdate",
        {"requests": [{"insertText": {
            "location": {"index": 1},
            "text": "SUGGESTED-A: this sentence arrived as a suggestion and was ACCEPTED via the API.\n"}}],
         "writeControl": {"writeMode": "SUGGEST"}})
    api("suggest-insert-2", "POST",
        f"https://docs.googleapis.com/v1/documents/{DOC_ID}:batchUpdate",
        {"requests": [{"insertText": {
            "location": {"index": 1},
            "text": "SUGGESTED-B: this sentence arrived as a suggestion and is LEFT OPEN for the eyeball.\n"}}],
         "writeControl": {"writeMode": "SUGGEST"}})

    status, doc = api("suggest-read-back", "GET",
                      f"https://docs.googleapis.com/v1/documents/{DOC_ID}"
                      "?suggestionsViewMode=SUGGESTIONS_INLINE&includeTabsContent=true")
    sugg = find_suggestions(doc)
    print("\nsuggestion threads found:")
    print(json.dumps(sugg, indent=2))

    # Accept the one whose text is SUGGESTED-A.
    accept_id = next((sid for sid, occ in sugg.items()
                      if any("SUGGESTED-A" in o["text"] for o in occ)), None)
    if accept_id:
        api("suggest-accept-A", "POST",
            f"https://docs.googleapis.com/v1/documents/{DOC_ID}:batchUpdate",
            {"requests": [{"acceptSuggestion": {"suggestionId": accept_id}}]})
        status, doc2 = api("suggest-verify-accept", "GET",
                           f"https://docs.googleapis.com/v1/documents/{DOC_ID}"
                           "?suggestionsViewMode=SUGGESTIONS_INLINE&includeTabsContent=true")
        remaining = find_suggestions(doc2)
        print("\nremaining suggestion threads after accept:")
        print(json.dumps(remaining, indent=2))
    else:
        print("\nNO suggestion id found for SUGGESTED-A — check read-back evidence")


if __name__ == "__main__":
    main()
