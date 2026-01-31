#!/usr/bin/env python3
"""
Capture real Google API responses as test fixtures.

Usage:
    uv run python scripts/capture_fixtures.py              # Capture only
    uv run python scripts/capture_fixtures.py --sanitize   # Capture + sanitize PII

Fetches from the test folder and optionally sanitizes PII.
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.services import (
    get_docs_service,
    get_sheets_service,
    get_slides_service,
    get_gmail_service,
    get_drive_service,
)
from validation import extract_gmail_id

FIXTURES_DIR = PROJECT_ROOT / "fixtures"

# Test document IDs from Google Docs Test Suite folder
TEST_IDS = {
    "docs_multi_tab": "1iBsJHoqza53_r5FxqKX-FIh7ctW2RD8J_8xqOd6Uyxo",
    "docs_single_tab": "1bREiVmvgSsRKJLjamTOE0Wasq1ze7R3bIPOdtJMomKM",
    "sheets": "1UlWoEsfjzqbuS_tKD6Drm4wmbPLeGOKWVBVip5AI-xw",
    "slides": "1ZrknZXSsyDtWuWq0cXV7UMZ-7WHClm3fJa61uZY2pwY",
    # Use the Innovid doc which has good comment examples
    "comments": "1u5HAOwh0dGQPdELj4OSHIWoJURvK9o1m",
}


def capture_docs(doc_id: str, output_name: str) -> dict:
    """Capture Google Docs API response."""
    print(f"Fetching Docs: {doc_id}...")
    service = get_docs_service()

    # Fetch with all tabs content
    doc = service.documents().get(
        documentId=doc_id,
        includeTabsContent=True,
    ).execute()

    # Save raw response
    output_path = FIXTURES_DIR / "docs" / f"{output_name}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(doc, f, indent=2)

    print(f"  Saved: {output_path}")
    print(f"  Title: {doc.get('title')}")
    print(f"  Tabs: {len(doc.get('tabs', []))}")

    return doc


def capture_sheets(spreadsheet_id: str, output_name: str) -> dict:
    """Capture Google Sheets API response (metadata + values)."""
    print(f"Fetching Sheets: {spreadsheet_id}...")
    service = get_sheets_service()

    # Get metadata first
    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
    ).execute()

    # Get sheet names, filtering out chart sheets (sheetType != GRID)
    data_sheets = [
        s for s in metadata.get("sheets", [])
        if s.get("properties", {}).get("sheetType", "GRID") == "GRID"
    ]
    sheet_names = [s["properties"]["title"] for s in data_sheets]

    # Get values for all data sheets in one batch call
    ranges = [f"'{name}'!A:ZZ" for name in sheet_names]
    values_response = service.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=ranges,
    ).execute()

    # Combine into fixture format
    fixture = {
        "spreadsheet_id": spreadsheet_id,
        "title": metadata.get("properties", {}).get("title", ""),
        "locale": metadata.get("properties", {}).get("locale"),
        "time_zone": metadata.get("properties", {}).get("timeZone"),
        "sheets": [],
    }

    value_ranges = values_response.get("valueRanges", [])
    for i, sheet_meta in enumerate(data_sheets):
        sheet_name = sheet_meta["properties"]["title"]
        values = value_ranges[i].get("values", []) if i < len(value_ranges) else []
        fixture["sheets"].append({
            "name": sheet_name,
            "values": values,
        })

    # Save
    output_path = FIXTURES_DIR / "sheets" / f"{output_name}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(fixture, f, indent=2)

    print(f"  Saved: {output_path}")
    print(f"  Title: {fixture['title']}")
    print(f"  Sheets: {len(fixture['sheets'])}")

    return fixture


def capture_slides(presentation_id: str, output_name: str) -> dict:
    """Capture Google Slides API response."""
    print(f"Fetching Slides: {presentation_id}...")
    service = get_slides_service()

    # Fetch presentation
    presentation = service.presentations().get(
        presentationId=presentation_id,
    ).execute()

    # Save raw response
    output_path = FIXTURES_DIR / "slides" / f"{output_name}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(presentation, f, indent=2)

    print(f"  Saved: {output_path}")
    print(f"  Title: {presentation.get('title')}")
    print(f"  Slides: {len(presentation.get('slides', []))}")

    return presentation


def capture_gmail_thread(thread_id_or_url: str, output_name: str) -> dict:
    """Capture Gmail thread API response. Accepts URL, web ID, or API ID."""
    # Convert URL/web ID to API ID
    api_id = extract_gmail_id(thread_id_or_url)
    print(f"Fetching Gmail thread: {api_id} (from {thread_id_or_url[:40]}...)")

    service = get_gmail_service()

    # Fetch thread with full message content
    thread = service.users().threads().get(
        userId="me",
        id=api_id,
        format="full",
    ).execute()

    # Save raw response
    output_path = FIXTURES_DIR / "gmail" / f"{output_name}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(thread, f, indent=2)

    print(f"  Saved: {output_path}")
    print(f"  Messages: {len(thread.get('messages', []))}")

    return thread


def capture_comments(file_id: str, output_name: str) -> dict:
    """Capture Drive Comments API response."""
    print(f"Fetching comments: {file_id}...")
    service = get_drive_service()

    # Also get file metadata for the name
    metadata = service.files().get(
        fileId=file_id,
        fields="id,name,mimeType",
        supportsAllDrives=True,
    ).execute()

    # Fetch comments with full author and reply info
    comments_response = service.comments().list(
        fileId=file_id,
        fields=(
            "comments("
            "id,content,"
            "author(displayName,emailAddress),"
            "createdTime,modifiedTime,"
            "resolved,quotedFileContent,"
            "replies(id,content,author(displayName,emailAddress),createdTime,modifiedTime)"
            ")"
        ),
        pageSize=100,
    ).execute()

    # Combine into fixture format matching our models
    fixture = {
        "file_id": file_id,
        "file_name": metadata.get("name", ""),
        "mime_type": metadata.get("mimeType", ""),
        "comments": [],
    }

    for comment in comments_response.get("comments", []):
        author = comment.get("author", {})
        parsed_comment = {
            "id": comment.get("id", ""),
            "content": comment.get("content", ""),
            "author_name": author.get("displayName", "Unknown"),
            "author_email": author.get("emailAddress"),
            "created_time": comment.get("createdTime"),
            "modified_time": comment.get("modifiedTime"),
            "resolved": comment.get("resolved", False),
            "quoted_text": comment.get("quotedFileContent", {}).get("value", ""),
            "replies": [],
        }

        for reply in comment.get("replies", []):
            reply_author = reply.get("author", {})
            parsed_comment["replies"].append({
                "id": reply.get("id", ""),
                "content": reply.get("content", ""),
                "author_name": reply_author.get("displayName", "Unknown"),
                "author_email": reply_author.get("emailAddress"),
                "created_time": reply.get("createdTime"),
                "modified_time": reply.get("modifiedTime"),
            })

        fixture["comments"].append(parsed_comment)

    # Save
    output_path = FIXTURES_DIR / "comments" / f"{output_name}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(fixture, f, indent=2)

    print(f"  Saved: {output_path}")
    print(f"  File: {fixture['file_name']}")
    print(f"  Comments: {len(fixture['comments'])}")

    return fixture


def run_sanitize():
    """Run sanitization on captured fixtures."""
    from scripts.sanitize_fixtures import main as sanitize_main
    print("\n=== Sanitizing Fixtures ===\n")
    sanitize_main()


def main():
    """Capture all fixtures."""
    parser = argparse.ArgumentParser(description="Capture real Google API responses as fixtures")
    parser.add_argument(
        "--sanitize", "-s",
        action="store_true",
        help="Sanitize PII after capturing (replace emails/names with generic values)"
    )
    args = parser.parse_args()

    print("=== Capturing Real API Fixtures ===\n")

    # Docs
    capture_docs(TEST_IDS["docs_multi_tab"], "real_multi_tab")
    capture_docs(TEST_IDS["docs_single_tab"], "real_single_tab")
    print()

    # Sheets
    capture_sheets(TEST_IDS["sheets"], "real_spreadsheet")
    print()

    # Slides
    capture_slides(TEST_IDS["slides"], "real_presentation")
    print()

    # Gmail - accepts URL, web ID, or API ID
    capture_gmail_thread(
        "https://mail.google.com/mail/u/0/#sent/FMfcgzQfBZdVqDtDZnXwMRWvRZjGhdWN",
        "real_thread"
    )
    print()

    # Comments
    capture_comments(TEST_IDS["comments"], "real_comments")

    print("\n=== Capture Done ===")

    if args.sanitize:
        run_sanitize()
        print("\n=== All Done (captured + sanitized) ===")


if __name__ == "__main__":
    main()
