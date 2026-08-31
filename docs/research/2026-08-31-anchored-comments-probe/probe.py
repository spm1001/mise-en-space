#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""DPP anchored-comments probe (mise-picihi), 2026-08-31.

Establishes whether mise's OAuth client (mit-workspace-mcp-server, token
sameer.modha@itv.com) can reach the Developer Preview anchored-comments
endpoints on Docs, Slides and Sheets — read (commentsViewMode) and write
(batchUpdate insertComment / addCommentReply).

Stdlib only. Every HTTP exchange is saved to this directory as NN-name.json
(request minus auth header, status, response). Scratch artefacts are created
inside one Drive folder and left in place for a UI eyeball (the mikawi
visibility question), links printed at the end.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

EVIDENCE = Path(__file__).parent
TOKEN_PATH = Path.home() / ".claude/plugins/data/mise-batterie-de-savoir/token.json"

seq = 0
access_token = None


def record(name: str, method: str, url: str, body, status: int, response):
    global seq
    seq += 1
    out = EVIDENCE / f"{seq:02d}-{name}.json"
    out.write_text(json.dumps({
        "request": {"method": method, "url": url, "body": body},
        "status": status,
        "response": response,
    }, indent=2))
    return out.name


def api(name: str, method: str, url: str, body=None):
    """One API call; returns (status, parsed-json-or-text). Never raises."""
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
    fname = record(name, method, url, body, status, parsed)
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
    print(f"token refreshed for {t.get('_identity', t.get('account', '?'))}")


def drive_create(name: str, title: str, mime: str, parent: str | None):
    body = {"name": title, "mimeType": mime}
    if parent:
        body["parents"] = [parent]
    status, resp = api(name, "POST",
                       "https://www.googleapis.com/drive/v3/files?fields=id,name,webViewLink",
                       body)
    if status != 200:
        print(f"FATAL: could not create {title}: {resp}")
        sys.exit(1)
    return resp["id"], resp.get("webViewLink")


def main():
    refresh_token()
    verdicts = {}

    print("\n== scratch artefacts ==")
    folder_id, folder_link = drive_create(
        "create-folder", "anchored-comments-probe scratch (2026-08-31, mise-picihi)",
        "application/vnd.google-apps.folder", None)
    doc_id, doc_link = drive_create(
        "create-doc", "probe doc — anchored comments",
        "application/vnd.google-apps.document", folder_id)
    deck_id, deck_link = drive_create(
        "create-deck", "probe deck — anchored comments",
        "application/vnd.google-apps.presentation", folder_id)
    sheet_id_file, sheet_link = drive_create(
        "create-sheet", "probe sheet — anchored comments",
        "application/vnd.google-apps.spreadsheet", folder_id)

    # ---------- DOCS ----------
    print("\n== docs leg ==")
    api("docs-insert-text", "POST",
        f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
        {"requests": [{"insertText": {
            "location": {"index": 1},
            "text": "This paragraph exists to carry an anchored probe comment. Delete freely.\n"}}]})

    # THE enrollment discriminator: preview-only query param on the read.
    status, resp = api("docs-read-commentsViewMode", "GET",
                       f"https://docs.googleapis.com/v1/documents/{doc_id}"
                       "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED&includeTabsContent=true")
    verdicts["docs_read"] = status

    status, resp = api("docs-insertComment", "POST",
                       f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
                       {"requests": [{"insertComment": {
                           "content": "Anchored probe comment via Docs API (mise-picihi).",
                           "range": {"startIndex": 6, "endIndex": 15}}}]})
    verdicts["docs_insert"] = status
    docs_comment_id = None
    if status == 200:
        for reply in resp.get("replies", []):
            ic = reply.get("insertComment") or {}
            docs_comment_id = ic.get("commentId") or docs_comment_id

    if docs_comment_id:
        api("docs-addCommentReply", "POST",
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            {"requests": [{"addCommentReply": {
                "commentId": docs_comment_id,
                "post": {"content": "Reply via Docs API — left open for UI eyeball."}}}]})
        api("docs-read-back", "GET",
            f"https://docs.googleapis.com/v1/documents/{doc_id}"
            "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED&includeTabsContent=true"
            "&fields=documentId,comments")
        # Cross-plane: does the Drive comments API see the Docs-API comment?
        api("drive-comments-list-on-doc", "GET",
            f"https://www.googleapis.com/drive/v3/files/{doc_id}/comments"
            "?fields=comments(id,content,anchor,resolved,author/displayName)&pageSize=20")

    # ---------- SLIDES ----------
    print("\n== slides leg ==")
    status, resp = api("slides-get-plain", "GET",
                       f"https://slides.googleapis.com/v1/presentations/{deck_id}"
                       "?fields=presentationId,slides(objectId)")
    slide_object_id = None
    if status == 200 and resp.get("slides"):
        slide_object_id = resp["slides"][0]["objectId"]

    status, resp = api("slides-read-commentsViewMode", "GET",
                       f"https://slides.googleapis.com/v1/presentations/{deck_id}"
                       "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
                       "&fields=presentationId,comments,slides(objectId,commentAnchors)")
    verdicts["slides_read"] = status

    slides_comment_id = None
    if slide_object_id:
        status, resp = api("slides-insertComment", "POST",
                           f"https://slides.googleapis.com/v1/presentations/{deck_id}:batchUpdate",
                           {"requests": [{"insertComment": {
                               "content": "Anchored probe comment on slide 1 via Slides API (mise-picihi).",
                               "objectId": slide_object_id}}]})
        verdicts["slides_insert"] = status
        if status == 200:
            for reply in resp.get("replies", []):
                ic = reply.get("insertComment") or {}
                slides_comment_id = ic.get("commentId") or slides_comment_id

    if slides_comment_id:
        api("slides-addCommentReply", "POST",
            f"https://slides.googleapis.com/v1/presentations/{deck_id}:batchUpdate",
            {"requests": [{"addCommentReply": {
                "commentId": slides_comment_id,
                "post": {"content": "Reply via Slides API — left open for UI eyeball."}}}]})
        api("slides-read-back", "GET",
            f"https://slides.googleapis.com/v1/presentations/{deck_id}"
            "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
            "&fields=presentationId,comments,slides(objectId,commentAnchors)")

    # ---------- SHEETS ----------
    print("\n== sheets leg ==")
    status, resp = api("sheets-get-plain", "GET",
                       f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id_file}"
                       "?fields=spreadsheetId,sheets(properties(sheetId,title))")
    grid_sheet_id = None
    if status == 200 and resp.get("sheets"):
        grid_sheet_id = resp["sheets"][0]["properties"]["sheetId"]

    status, resp = api("sheets-read-commentsViewMode", "GET",
                       f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id_file}"
                       "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
                       "&fields=spreadsheetId,comments,sheets(properties(sheetId,title),commentAnchors)")
    verdicts["sheets_read"] = status

    if grid_sheet_id is not None:
        status, resp = api("sheets-insertComment", "POST",
                           f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id_file}:batchUpdate",
                           {"requests": [{"insertComment": {
                               "content": "Anchored probe comment on B2 via Sheets API (mise-picihi).",
                               "coordinate": {"sheetId": grid_sheet_id,
                                              "rowIndex": 1, "columnIndex": 1}}}]})
        verdicts["sheets_insert"] = status
        if status == 200:
            api("sheets-read-back", "GET",
                f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id_file}"
                "?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED"
                "&fields=spreadsheetId,comments,sheets(properties(sheetId,title),commentAnchors)")

    print("\n== verdicts (HTTP status per probe) ==")
    print(json.dumps(verdicts, indent=2))
    print("\n== artefacts (left in place for UI eyeball) ==")
    for label, link in [("folder", folder_link), ("doc", doc_link),
                        ("deck", deck_link), ("sheet", sheet_link)]:
        print(f"  {label}: {link}")


if __name__ == "__main__":
    main()
