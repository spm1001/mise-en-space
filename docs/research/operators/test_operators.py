#!/usr/bin/env python3
"""
Test search operators against Drive and Gmail APIs.

Probes which operators work via API (not just UI).
Run with: uv run python scripts/test_operators.py
"""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.services import get_drive_service, get_gmail_service
from googleapiclient.errors import HttpError

# =============================================================================
# DRIVE OPERATORS TO TEST
# =============================================================================

DRIVE_OPERATORS = {
    # Core operators (should work)
    "fullText contains 'budget'": "fullText search",
    "name contains 'meeting'": "name search",
    "mimeType = 'application/vnd.google-apps.document'": "mimeType exact",
    "mimeType contains 'document'": "mimeType partial",
    "modifiedTime > '2024-01-01'": "modifiedTime filter",
    "createdTime > '2024-01-01'": "createdTime filter",

    # Starred/trashed
    "starred = true": "starred filter",
    "trashed = false": "trashed filter (default)",
    "trashed = true": "trashed filter (show trash)",

    # Owner/sharing operators
    "'me' in owners": "owned by me",
    "'me' in writers": "I have write access",
    "'me' in readers": "I have read access",

    # Specific email owner (replace with real email to test)
    # "'someone@example.com' in owners": "specific owner",
    # "'someone@example.com' in writers": "specific writer",
    # "'someone@example.com' in readers": "specific reader",

    # Folder operators
    "parents in '1234567890'": "specific folder (invalid id, expect error)",

    # Property-based
    "visibility = 'anyoneWithLink'": "public visibility",
    "visibility = 'limited'": "limited visibility",

    # Shared drive
    "sharedWithMe = true": "shared with me",

    # UI-only operators (expected to fail)
    "followup:actionitems": "followup:actionitems (UI-only)",
    "from:john": "from: operator (UI-only?)",
    "to:jane": "to: operator (UI-only?)",
    "type:document": "type: operator (UI-only?)",
    "type:pdf": "type: pdf (UI-only?)",
    "is:starred": "is:starred (UI-only?)",
    "owner:me": "owner:me (UI-only?)",
    "creator:me": "creator:me (UI-only?)",
    "before:2024-01-01": "before: (UI-only?)",
    "after:2024-01-01": "after: (UI-only?)",
    "title:meeting": "title: (UI-only?)",
    "app:docs": "app: (UI-only?)",
    "source:domain": "source: (UI-only?)",
    "sharedwith:me": "sharedwith: (UI-only?)",

    # Combined queries
    "fullText contains 'budget' and mimeType = 'application/vnd.google-apps.document'": "combined query",
}

# =============================================================================
# GMAIL OPERATORS TO TEST
# =============================================================================

GMAIL_OPERATORS = {
    # Core operators (should work)
    "budget": "simple term",
    "from:john": "from: operator",
    "to:jane": "to: operator",
    "cc:mike": "cc: operator",
    "bcc:test": "bcc: operator",
    "subject:meeting": "subject: operator",

    # Label/category
    "is:unread": "is:unread",
    "is:read": "is:read",
    "is:starred": "is:starred",
    "is:important": "is:important",
    "is:snoozed": "is:snoozed",
    "in:inbox": "in:inbox",
    "in:sent": "in:sent",
    "in:draft": "in:draft",
    "in:spam": "in:spam",
    "in:trash": "in:trash",
    "in:anywhere": "in:anywhere",
    "label:inbox": "label:inbox",
    "category:primary": "category:primary",
    "category:social": "category:social",
    "category:promotions": "category:promotions",
    "category:updates": "category:updates",
    "category:forums": "category:forums",

    # Attachment operators
    "has:attachment": "has:attachment",
    "has:drive": "has:drive",
    "has:document": "has:document",
    "has:spreadsheet": "has:spreadsheet",
    "has:presentation": "has:presentation",
    "has:youtube": "has:youtube",
    "filename:pdf": "filename:pdf",
    "filename:*.pdf": "filename:*.pdf (wildcard)",

    # Size operators
    "larger:10M": "larger:10M",
    "smaller:1M": "smaller:1M",
    "size:10000": "size:10000 (bytes)",

    # Date operators
    "after:2024/01/01": "after:YYYY/MM/DD",
    "before:2024/12/31": "before:YYYY/MM/DD",
    "older:1d": "older:1d (relative)",
    "older:1w": "older:1w (weeks)",
    "older:1m": "older:1m (months)",
    "older:1y": "older:1y (years)",
    "older_than:1d": "older_than:1d",
    "newer:1d": "newer:1d",
    "newer_than:1d": "newer_than:1d",

    # Boolean operators
    "budget OR report": "OR operator",
    "budget AND report": "AND (implicit)",
    "-spam": "NOT (-) operator",
    '"exact phrase"': "exact phrase",

    # Advanced
    "AROUND 5 budget report": "AROUND (proximity)",
    "list:info@example.com": "list: (mailing list)",
    "rfc822msgid:id": "rfc822msgid:",
    "deliveredto:me": "deliveredto:",

    # UI-only? (testing)
    "is:chat": "is:chat",
    "has:yellow-star": "has:yellow-star",
    "has:blue-info": "has:blue-info",
}


def test_drive_operator(service, query: str, description: str) -> dict:
    """Test a single Drive operator."""
    try:
        result = service.files().list(
            q=query,
            pageSize=1,
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        files = result.get("files", [])
        return {
            "status": "OK",
            "count": len(files),
            "sample": files[0]["name"] if files else None,
        }
    except HttpError as e:
        return {
            "status": "ERROR",
            "code": e.resp.status,
            "error": str(e.error_details) if hasattr(e, 'error_details') else str(e)[:100],
        }
    except Exception as e:
        return {
            "status": "EXCEPTION",
            "error": str(e)[:100],
        }


def test_gmail_operator(service, query: str, description: str) -> dict:
    """Test a single Gmail operator."""
    try:
        result = service.users().threads().list(
            userId="me",
            q=query,
            maxResults=1,
        ).execute()

        threads = result.get("threads", [])
        return {
            "status": "OK",
            "count": result.get("resultSizeEstimate", len(threads)),
        }
    except HttpError as e:
        return {
            "status": "ERROR",
            "code": e.resp.status,
            "error": str(e.error_details) if hasattr(e, 'error_details') else str(e)[:100],
        }
    except Exception as e:
        return {
            "status": "EXCEPTION",
            "error": str(e)[:100],
        }


def main():
    print("=" * 70)
    print("DRIVE OPERATOR TESTING")
    print("=" * 70)

    drive_service = get_drive_service()

    drive_results = {}
    for query, description in DRIVE_OPERATORS.items():
        result = test_drive_operator(drive_service, query, description)
        drive_results[query] = result

        status_icon = "✅" if result["status"] == "OK" else "❌"
        if result["status"] == "OK":
            print(f"{status_icon} {description}")
            print(f"   Query: {query}")
            if result.get("sample"):
                print(f"   Sample: {result['sample']}")
        else:
            print(f"{status_icon} {description}")
            print(f"   Query: {query}")
            print(f"   Error: {result.get('error', 'Unknown')[:60]}")
        print()

    print()
    print("=" * 70)
    print("GMAIL OPERATOR TESTING")
    print("=" * 70)

    gmail_service = get_gmail_service()

    gmail_results = {}
    for query, description in GMAIL_OPERATORS.items():
        result = test_gmail_operator(gmail_service, query, description)
        gmail_results[query] = result

        status_icon = "✅" if result["status"] == "OK" else "❌"
        if result["status"] == "OK":
            print(f"{status_icon} {description}")
            print(f"   Query: {query}")
            print(f"   Estimated results: {result.get('count', 0)}")
        else:
            print(f"{status_icon} {description}")
            print(f"   Query: {query}")
            print(f"   Error: {result.get('error', 'Unknown')[:60]}")
        print()

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    drive_ok = sum(1 for r in drive_results.values() if r["status"] == "OK")
    drive_fail = len(drive_results) - drive_ok
    print(f"Drive: {drive_ok}/{len(drive_results)} operators work ({drive_fail} failed)")

    gmail_ok = sum(1 for r in gmail_results.values() if r["status"] == "OK")
    gmail_fail = len(gmail_results) - gmail_ok
    print(f"Gmail: {gmail_ok}/{len(gmail_results)} operators work ({gmail_fail} failed)")

    # List failures
    print()
    print("DRIVE FAILURES:")
    for query, result in drive_results.items():
        if result["status"] != "OK":
            print(f"  - {query}")

    print()
    print("GMAIL FAILURES:")
    for query, result in gmail_results.items():
        if result["status"] != "OK":
            print(f"  - {query}")


if __name__ == "__main__":
    main()
