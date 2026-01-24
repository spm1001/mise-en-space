#!/usr/bin/env python3
"""
Bakeoff Part 2: Drive copy-with-conversion extraction for Office files.

Converts XLSX → Google Sheets → our extractor
Converts PPTX → Google Slides → our extractor

Usage:
    uv run python bakeoff/test_drive_conversion.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.drive import get_drive_service
from adapters.sheets import fetch_spreadsheet
from adapters.slides import fetch_presentation
from extractors.sheets import extract_sheets_content
from extractors.slides import extract_slides_content

# Test files (same as markitdown test)
XLSX_ID = "1AQq5yv2hzNWSr13oUtWL4N7KEBXnN077"  # Commercial Outcomes
PPTX_ID = "1c9xgom5U8ALvPhvKMZSIx36LwngMhBeG"  # BHF x ITV Geo Experiment


def copy_with_conversion(file_id: str, target_mime: str) -> str:
    """Copy file with conversion to Google native format."""
    service = get_drive_service()

    # Get source metadata
    meta = service.files().get(fileId=file_id, fields="name,mimeType").execute()
    print(f"Converting: {meta['name']}")
    print(f"  From: {meta['mimeType']}")
    print(f"  To: {target_mime}")

    # Copy with conversion
    copy_body = {
        "name": f"[TEMP] {meta['name']} - Bakeoff",
        "mimeType": target_mime,
    }
    copied = service.files().copy(fileId=file_id, body=copy_body).execute()
    new_id = copied["id"]
    print(f"  → Created: {new_id}")

    return new_id


def delete_file(file_id: str):
    """Delete file from Drive."""
    service = get_drive_service()
    service.files().delete(fileId=file_id).execute()
    print(f"  → Deleted temp file: {file_id}")


def main():
    print("=" * 60)
    print("BAKEOFF: Drive conversion + our extractors")
    print("=" * 60)

    # Test XLSX → Google Sheets
    print("\n" + "=" * 60)
    print("XLSX → Google Sheets → Our Extractor")
    print("=" * 60)

    sheets_id = copy_with_conversion(
        XLSX_ID, "application/vnd.google-apps.spreadsheet"
    )

    try:
        sheet_data = fetch_spreadsheet(sheets_id)
        xlsx_content = extract_sheets_content(sheet_data)

        print(f"\n--- Our extractor output ({len(xlsx_content):,} chars) ---")
        print(xlsx_content[:3000])
        if len(xlsx_content) > 3000:
            print(f"\n... [{len(xlsx_content) - 3000:,} more chars] ...")
    finally:
        delete_file(sheets_id)

    # Test PPTX → Google Slides
    print("\n" + "=" * 60)
    print("PPTX → Google Slides → Our Extractor")
    print("=" * 60)

    slides_id = copy_with_conversion(
        PPTX_ID, "application/vnd.google-apps.presentation"
    )

    try:
        slides_data = fetch_presentation(slides_id)
        pptx_content = extract_slides_content(slides_data)

        print(f"\n--- Our extractor output ({len(pptx_content):,} chars) ---")
        print(pptx_content[:3000])
        if len(pptx_content) > 3000:
            print(f"\n... [{len(pptx_content) - 3000:,} more chars] ...")
    finally:
        delete_file(slides_id)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"XLSX via Drive conversion: {len(xlsx_content):,} chars")
    print(f"PPTX via Drive conversion: {len(pptx_content):,} chars")


if __name__ == "__main__":
    main()
