#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Follow-up legs for the anchored-comments probe (mise-picihi).

probe.py established enrollment + insertComment on all three surfaces. This
covers what its commentId-extraction bug skipped, plus the corrected Docs read:
  - Docs read with suggestionsViewMode explicitly set (the 400 asked for it)
  - addCommentReply on Docs and Slides (left OPEN for UI eyeball)
  - post-insert read-backs with commentAnchors
  - cross-plane: Drive comments.list on all three files (what mise's current
    comments.md machinery would see)
Evidence files continue the probe.py numbering from 15.
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
DOCS_COMMENT_ID = "AAACGWxxk2k"
SLIDES_COMMENT_ID = "AAACGW2am2w"

seq = 14
access_token = None


def api(name: str, method: str, url: str, body=None):
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
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    seq += 1
    fname = f"{seq:02d}-{name}.json"
    (EVIDENCE / fname).write_text(json.dumps({
        "request": {"method": method, "url": url, "body": body},
        "status": status,
        "response": parsed,
    }, indent=2))
    print(f"  [{status}] {name} -> {fname}")
    return status, parsed


def refresh_token():
    global access_token
    t = json.loads(TOKEN_PATH.read_text())
    data = urllib.parse.urlencode({
        "client_id": t["client_id"],
        "client_secret": t["client_secret"],
        "refresh_token": t["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(t["token_uri"], data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        access_token = json.loads(r.read())["access_token"]


def main():
    refresh_token()

    print("== docs: corrected read (explicit suggestionsViewMode) ==")
    api("docs-read-corrected", "GET",
        f"https://docs.googleapis.com/v1/documents/{DOC_ID}"
        "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
        "&suggestionsViewMode=SUGGESTIONS_INLINE"
        "&includeTabsContent=true&fields=documentId,comments")

    print("== docs: reply (left open) ==")
    api("docs-addCommentReply", "POST",
        f"https://docs.googleapis.com/v1/documents/{DOC_ID}:batchUpdate",
        {"requests": [{"addCommentReply": {
            "commentId": DOCS_COMMENT_ID,
            "post": {"content": "Reply via Docs API — left open for UI eyeball."}}}]})

    print("== slides: reply + read-back with anchors ==")
    api("slides-addCommentReply", "POST",
        f"https://slides.googleapis.com/v1/presentations/{DECK_ID}:batchUpdate",
        {"requests": [{"addCommentReply": {
            "commentId": SLIDES_COMMENT_ID,
            "post": {"content": "Reply via Slides API — left open for UI eyeball."}}}]})
    api("slides-read-back", "GET",
        f"https://slides.googleapis.com/v1/presentations/{DECK_ID}"
        "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
        "&fields=presentationId,comments,slides(objectId,commentAnchors)")

    print("== cross-plane: Drive comments.list on each file ==")
    for label, fid in [("doc", DOC_ID), ("deck", DECK_ID), ("sheet", SHEET_ID)]:
        api(f"drive-comments-list-{label}", "GET",
            f"https://www.googleapis.com/drive/v3/files/{fid}/comments"
            "?fields=comments(id,content,anchor,resolved,author/displayName,replies(content))"
            "&pageSize=20")


if __name__ == "__main__":
    main()
