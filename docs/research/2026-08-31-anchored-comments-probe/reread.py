#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Re-read after Sameer's UI comments (mise-picihi, eyeball check #2).

Reads all three surfaces via the preview endpoints AND the Drive comments
plane, then prints a compact analysis: which threads are UI-authored (i.e.
not one of the ids our probes minted) and what anchor data each carries.
Evidence numbering continues from 24.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

EVIDENCE = Path(__file__).parent
TOKEN_PATH = Path.home() / ".claude/plugins/data/mise-batterie-de-savoir/token.json"

DOC_ID = "1eS6SMV2kKwNW_uIm5Kez6V_6UVwmt0y9U_lZqCuioAo"
DECK_ID = "1wkQSlFk0ey8Asa6-Z4fptwBXpDNjRWaQPCR4Jw_A5NM"
SHEET_ID = "1HLogE6ENzSAHsFdGMI_HxpRFK__2AKZw1ngEYlPkIDg"
API_MINTED = {"AAACGWxxk2k", "AAACGW2am2w", "AAACGWrtgyk", "AAACGWxxk2w"}

seq = 24
access_token = None


def api(name: str, url: str):
    global seq
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {access_token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode()
    parsed = json.loads(raw)
    seq += 1
    fname = f"{seq:02d}-{name}.json"
    (EVIDENCE / fname).write_text(json.dumps({
        "request": {"method": "GET", "url": url},
        "status": status,
        "response": parsed,
    }, indent=2))
    print(f"[{status}] {name} -> {fname}")
    return parsed


def refresh_token():
    global access_token
    t = json.loads(TOKEN_PATH.read_text())
    data = urllib.parse.urlencode({
        "client_id": t["client_id"], "client_secret": t["client_secret"],
        "refresh_token": t["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(t["token_uri"], data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        access_token = json.loads(r.read())["access_token"]


def describe_thread(c):
    origin = "API-minted" if c.get("commentId") in API_MINTED else "UI-AUTHORED"
    posts = c.get("replies") or c.get("posts") or []
    return {
        "origin": origin,
        "commentId": c.get("commentId"),
        "status": c.get("status"),
        "anchorId": c.get("anchorId", "(ABSENT)"),
        "quote": c.get("plainTextQuote", "(ABSENT)"),
        "head": (c.get("headPost") or {}).get("content"),
        "reply_contents": [(p.get("content")) for p in posts] if isinstance(posts, list) else posts,
    }


def main():
    refresh_token()

    docs = api("docs-reread",
               f"https://docs.googleapis.com/v1/documents/{DOC_ID}"
               "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
               "&suggestionsViewMode=SUGGESTIONS_INLINE&includeTabsContent=true"
               "&fields=documentId,comments")
    slides = api("slides-reread",
                 f"https://slides.googleapis.com/v1/presentations/{DECK_ID}"
                 "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
                 "&fields=presentationId,comments,slides(objectId,commentAnchors)")
    sheets = api("sheets-reread",
                 f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
                 "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
                 "&fields=spreadsheetId,comments,sheets(properties(sheetId,title),commentAnchors)")

    drive = {}
    for label, fid in [("doc", DOC_ID), ("deck", DECK_ID), ("sheet", SHEET_ID)]:
        drive[label] = api(f"drive-reread-{label}",
                           f"https://www.googleapis.com/drive/v3/files/{fid}/comments"
                           "?fields=comments(id,content,anchor,resolved,replies(content))&pageSize=50")

    print("\n==== ANALYSIS ====")
    print("\n-- DOCS threads (preview read) --")
    print(json.dumps([describe_thread(c) for c in docs.get("comments", [])], indent=2))

    print("\n-- SLIDES threads (preview read) --")
    print(json.dumps([describe_thread(c) for c in slides.get("comments", [])], indent=2))
    print("-- SLIDES per-slide commentAnchors --")
    print(json.dumps(slides.get("slides", []), indent=2))

    print("\n-- SHEETS threads (preview read) --")
    print(json.dumps([describe_thread(c) for c in sheets.get("comments", [])], indent=2))
    print("-- SHEETS per-sheet commentAnchors --")
    print(json.dumps([s.get("commentAnchors") for s in sheets.get("sheets", [])], indent=2))

    print("\n-- DRIVE plane: anchor field per comment --")
    for label, resp in drive.items():
        for c in resp.get("comments", []):
            origin = "API-minted" if c.get("id") in API_MINTED else "UI-AUTHORED"
            print(f"  {label}: [{origin}] id={c.get('id')} anchor={c.get('anchor', '(ABSENT)')!r} "
                  f"resolved={c.get('resolved', '(ABSENT)')} content={c.get('content', '')[:60]!r}")


if __name__ == "__main__":
    main()
