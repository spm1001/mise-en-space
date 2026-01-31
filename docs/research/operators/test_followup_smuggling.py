#!/usr/bin/env python3
"""
Test followup:actionitems smuggling - can we make this UI-only feature work via API?

Run with: uv run python scripts/test_followup_smuggling.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.services import get_drive_service
from googleapiclient.errors import HttpError


def test_query(service, query: str, description: str, **kwargs):
    """Test a Drive query with optional extra params."""
    print(f"\n{'='*60}")
    print(f"Test: {description}")
    print(f"Query: {query}")
    if kwargs:
        print(f"Extra params: {kwargs}")
    print("-" * 60)

    try:
        params = {
            "q": query,
            "pageSize": 3,
            "fields": "files(id,name,mimeType)",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        params.update(kwargs)

        result = service.files().list(**params).execute()
        files = result.get("files", [])
        print(f"✅ SUCCESS - {len(files)} results")
        for f in files[:3]:
            print(f"   - {f['name']}")
        return True
    except HttpError as e:
        print(f"❌ FAILED - {e.resp.status}")
        error_content = e.content.decode() if hasattr(e, 'content') else str(e)
        print(f"   {error_content[:200]}")
        return False
    except Exception as e:
        print(f"❌ EXCEPTION - {type(e).__name__}: {e}")
        return False


def main():
    service = get_drive_service()

    print("\n" + "=" * 60)
    print("FOLLOWUP:ACTIONITEMS SMUGGLING EXPERIMENTS")
    print("=" * 60)

    # Baseline - known working query
    test_query(service, "fullText contains 'action'", "Baseline - fullText contains 'action'")

    # Original failing query
    test_query(service, "followup:actionitems", "Direct followup:actionitems (expected to fail)")

    # Smuggling attempts
    test_query(service, "'followup:actionitems'", "Quoted as string")
    test_query(service, "\"followup:actionitems\"", "Double-quoted")
    test_query(service, "fullText contains 'followup:actionitems'", "As fullText search term")
    test_query(service, "name contains 'followup:actionitems'", "As name search term")

    # Try with properties
    test_query(service, "properties has { key='followup' and value='actionitems' }", "As custom property")

    # Try appProperties (app-specific)
    test_query(service, "appProperties has { key='followup' and value='actionitems' }", "As app property")

    # Try as a label
    test_query(service, "labels has { key='followup' and value='actionitems' }", "As label (new Drive labels API)")

    # Try in description (metadata field)
    test_query(service, "description contains 'action items'", "description contains (if that field exists)")

    # Try searching by viewedByMeTime (maybe action items are recently viewed?)
    test_query(service, "viewedByMeTime > '2025-01-01'", "viewedByMeTime filter")

    # Try starred (action items might correlate with starred?)
    test_query(service, "starred = true and fullText contains 'action'", "Starred + action text")

    # Check if there are special corpora for action items
    for corpus in ['user', 'allDrives', 'domain']:
        try:
            result = service.files().list(
                q="fullText contains 'action'",
                corpora=corpus,
                pageSize=1,
                fields="files(id)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            print(f"Corpus '{corpus}': works")
        except Exception as e:
            print(f"Corpus '{corpus}': {e}")

    # Check about.get for any hints about supported features
    print("\n" + "=" * 60)
    print("CHECKING DRIVE API CAPABILITIES")
    print("=" * 60)

    try:
        about = service.about().get(fields="*").execute()

        # Check for any mention of followup or action items
        import json
        about_str = json.dumps(about, indent=2)

        # Look for interesting keys
        interesting_keys = [k for k in about.keys() if 'follow' in k.lower() or 'action' in k.lower() or 'item' in k.lower()]
        if interesting_keys:
            print(f"Interesting keys found: {interesting_keys}")
        else:
            print("No followup/action-related keys in about response")

        # Print import/export formats for reference
        if 'importFormats' in about:
            print(f"\nImport formats available: {len(about['importFormats'])} types")
        if 'exportFormats' in about:
            print(f"Export formats available: {len(about['exportFormats'])} types")

    except Exception as e:
        print(f"Error getting about: {e}")

    # Try the newer Drive labels API
    print("\n" + "=" * 60)
    print("CHECKING DRIVE LABELS API")
    print("=" * 60)

    try:
        # This is Drive Labels API v2 - separate from files API
        from googleapiclient.discovery import build
        from adapters.services import _get_cached_credentials

        # Try to build labels service
        creds = _get_cached_credentials()
        labels_service = build('drivelabels', 'v2', credentials=creds)

        # List labels
        result = labels_service.labels().list(
            view='LABEL_VIEW_FULL',
            pageSize=10,
        ).execute()

        labels = result.get('labels', [])
        print(f"Found {len(labels)} labels:")
        for label in labels[:5]:
            print(f"  - {label.get('name', 'unnamed')}: {label.get('labelType', '?')}")

    except Exception as e:
        print(f"Labels API error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
