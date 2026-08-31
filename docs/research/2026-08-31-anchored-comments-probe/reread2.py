#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""UI-authored anchor check against LIVE files (mise-picihi, check #2 proper).

Subjects: two real files with colleague-authored comments (found via Drive
activity) — the ADR 044A draft Doc and the Melt SoW Reconciliation file.
Read-only. This repo is PUBLIC, so saved evidence is REDACTED to structure:
comment text, quotes and author names are reduced to presence/length; ids,
anchor ids and grid ranges are kept (anchors are opaque or coordinates).
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

EVIDENCE = Path(__file__).parent
TOKEN_PATH = Path.home() / ".claude/plugins/data/mise-batterie-de-savoir/token.json"

ADR_DOC = "19QwOvicYYO_A4dHhTB7WoG8qWakOJBQ4xgfNjhJ7m-A"
MELT = "1ima2XhARDK4V89T2zpvxrPj8AIGHJ1ft5DYYoruvTIc"

seq = 30
access_token = None


def get(url):
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {access_token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def save(name, url, status, redacted):
    global seq
    seq += 1
    fname = f"{seq:02d}-{name}.json"
    (EVIDENCE / fname).write_text(json.dumps({
        "request": {"method": "GET", "url": url,
                    "note": "REDACTED evidence — live third-party file, public repo"},
        "status": status,
        "response_redacted": redacted,
    }, indent=2))
    print(f"[{status}] {name} -> {fname}")


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


def redact_thread(c):
    head = c.get("headPost") or {}
    posts = c.get("replies") or []
    return {
        "commentId": c.get("commentId"),
        "status": c.get("status"),
        "anchorId_present": "anchorId" in c,
        "anchorId": c.get("anchorId"),          # opaque kix.* / JSON coords — not content
        "plainTextQuote_present": "plainTextQuote" in c,
        "plainTextQuote_len": len(c.get("plainTextQuote", "") or ""),
        "head_content_len": len(head.get("content", "") or ""),
        "reply_count": len(posts) if isinstance(posts, list) else None,
    }


def main():
    refresh_token()

    # What is Melt?
    status, meta = get(f"https://www.googleapis.com/drive/v3/files/{MELT}?fields=mimeType")
    melt_mime = meta.get("mimeType", "?")
    print(f"Melt mimeType: {melt_mime}")

    # ADR: Docs preview read
    url = (f"https://docs.googleapis.com/v1/documents/{ADR_DOC}"
           "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
           "&suggestionsViewMode=SUGGESTIONS_INLINE&includeTabsContent=true"
           "&fields=documentId,comments")
    status, resp = get(url)
    threads = [redact_thread(c) for c in resp.get("comments", [])] if status == 200 else resp
    save("adr-doc-ui-comments", url, status, threads)
    print(json.dumps(threads, indent=2))

    # Melt: per type
    if "spreadsheet" in melt_mime:
        url = (f"https://sheets.googleapis.com/v4/spreadsheets/{MELT}"
               "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
               "&fields=spreadsheetId,comments,sheets(properties(sheetId),commentAnchors)")
        status, resp = get(url)
        if status == 200:
            redacted = {
                "threads": [redact_thread(c) for c in resp.get("comments", [])],
                "commentAnchors_by_sheet": [
                    {"sheetId": (s.get("properties") or {}).get("sheetId"),
                     "commentAnchors": s.get("commentAnchors", [])}
                    for s in resp.get("sheets", [])],
            }
        else:
            redacted = resp
        save("melt-sheet-ui-comments", url, status, redacted)
        print(json.dumps(redacted, indent=2))
    else:
        url = (f"https://docs.googleapis.com/v1/documents/{MELT}"
               "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
               "&suggestionsViewMode=SUGGESTIONS_INLINE&includeTabsContent=true"
               "&fields=documentId,comments")
        status, resp = get(url)
        threads = [redact_thread(c) for c in resp.get("comments", [])] if status == 200 else resp
        save("melt-doc-ui-comments", url, status, threads)
        print(json.dumps(threads, indent=2))


if __name__ == "__main__":
    main()
