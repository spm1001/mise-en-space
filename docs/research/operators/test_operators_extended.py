#!/usr/bin/env python3
"""
Extended operator testing - edge cases and less common operators.

Run with: uv run python scripts/test_operators_extended.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.services import get_drive_service, get_gmail_service
from googleapiclient.errors import HttpError


def test_drive(service, query: str, description: str):
    """Test Drive operator."""
    try:
        result = service.files().list(
            q=query,
            pageSize=3,
            fields="files(id,name,mimeType,modifiedTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = result.get("files", [])
        print(f"✅ {description}")
        print(f"   Query: {query}")
        print(f"   Results: {len(files)}")
        for f in files[:3]:
            print(f"     - {f['name']} ({f['mimeType'].split('.')[-1]})")
    except HttpError as e:
        print(f"❌ {description}")
        print(f"   Query: {query}")
        print(f"   Error: {e.resp.status} - {str(e)[:80]}")
    print()


def test_gmail(service, query: str, description: str):
    """Test Gmail operator."""
    try:
        result = service.users().threads().list(
            userId="me",
            q=query,
            maxResults=3,
        ).execute()
        threads = result.get("threads", [])
        count = result.get("resultSizeEstimate", len(threads))
        print(f"✅ {description}")
        print(f"   Query: {query}")
        print(f"   Estimated: {count}")
    except HttpError as e:
        print(f"❌ {description}")
        print(f"   Query: {query}")
        print(f"   Error: {e.resp.status}")
    print()


def main():
    drive = get_drive_service()
    gmail = get_gmail_service()

    print("=" * 70)
    print("DRIVE EXTENDED TESTS")
    print("=" * 70)
    print()

    # Date format variations
    test_drive(drive, "modifiedTime > '2025-01-01T00:00:00'", "modifiedTime with full ISO")
    test_drive(drive, "modifiedTime > '2025-01-01T00:00:00Z'", "modifiedTime with Z suffix")
    test_drive(drive, "modifiedTime >= '2025-01-01'", "modifiedTime >= (not just >)")
    test_drive(drive, "modifiedTime < '2024-01-01'", "modifiedTime < (older than)")

    # OR operator (valid in Drive?)
    test_drive(drive, "name contains 'budget' or name contains 'report'", "OR operator")

    # AND operator (implicit vs explicit)
    test_drive(drive, "name contains 'budget' and name contains '2025'", "explicit AND")

    # NOT operator
    test_drive(drive, "mimeType != 'application/vnd.google-apps.folder'", "NOT folder (!=)")
    test_drive(drive, "not mimeType = 'application/vnd.google-apps.folder'", "NOT folder (not)")

    # Folder search
    test_drive(drive, "mimeType = 'application/vnd.google-apps.folder' and name contains 'project'", "Find folders by name")

    # Properties (custom metadata)
    test_drive(drive, "properties has { key='foo' and value='bar' }", "custom properties (exotic)")

    # App-specific MIME types
    test_drive(drive, "mimeType = 'application/pdf'", "PDF files")
    test_drive(drive, "mimeType = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'", "XLSX files")
    test_drive(drive, "mimeType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'", "DOCX files")
    test_drive(drive, "mimeType = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'", "PPTX files")

    # MIME type shortcuts that might work
    test_drive(drive, "mimeType = 'application/vnd.google-apps.drawing'", "Google Drawings")
    test_drive(drive, "mimeType = 'application/vnd.google-apps.form'", "Google Forms")
    test_drive(drive, "mimeType = 'application/vnd.google-apps.site'", "Google Sites")

    # Recency patterns for smart defaults
    print()
    print("=" * 70)
    print("RECENCY PATTERNS (for smart defaults)")
    print("=" * 70)
    print()

    # Count files by age - useful for understanding typical recency
    from datetime import datetime, timedelta

    for days, label in [(7, "1 week"), (30, "1 month"), (90, "3 months"), (365, "1 year")]:
        date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        query = f"modifiedTime > '{date}' and mimeType = 'application/vnd.google-apps.document'"
        try:
            result = drive.files().list(
                q=query,
                pageSize=1000,
                fields="files(id)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            count = len(result.get("files", []))
            print(f"Docs modified in last {label}: {count}")
        except Exception as e:
            print(f"Error for {label}: {e}")

    print()
    print("=" * 70)
    print("GMAIL EXTENDED TESTS")
    print("=" * 70)
    print()

    # Gmail date arithmetic edge cases
    test_gmail(gmail, "newer_than:1d older_than:7d", "Between 1-7 days old")
    test_gmail(gmail, "after:2025/01/01 before:2025/01/31", "January 2025")

    # Combinations
    test_gmail(gmail, "from:noreply@google.com has:attachment", "Google automated with attachments")
    test_gmail(gmail, "subject:(weekly report) has:document", "Weekly reports with Docs")

    # Negation patterns
    test_gmail(gmail, "is:unread -category:promotions", "Unread non-promotional")
    test_gmail(gmail, "has:attachment -filename:ics -filename:pdf", "Attachments but not ICS or PDF")

    # Size combinations
    test_gmail(gmail, "larger:5M smaller:20M", "Medium-size emails (5-20MB)")

    # Advanced
    test_gmail(gmail, "deliveredto:sameer@itv.com", "Delivered to specific address")


if __name__ == "__main__":
    main()
