#!/usr/bin/env python3
"""
Bakeoff: Compare markitdown extraction vs Drive conversion for Office files.

Usage:
    uv run python bakeoff/test_markitdown.py
"""

import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from markitdown import MarkItDown
from adapters.drive import get_drive_service

# Test files
XLSX_ID = "1AQq5yv2hzNWSr13oUtWL4N7KEBXnN077"  # Commercial Outcomes
PPTX_ID = "1c9xgom5U8ALvPhvKMZSIx36LwngMhBeG"  # BHF x ITV Geo Experiment


def download_file(file_id: str, suffix: str) -> Path:
    """Download file from Drive to temp location."""
    service = get_drive_service()

    # Get metadata for filename
    meta = service.files().get(fileId=file_id, fields="name,mimeType").execute()
    print(f"Downloading: {meta['name']} ({meta['mimeType']})")

    # Download raw content
    content = service.files().get_media(fileId=file_id).execute()

    # Write to temp file
    tmp = Path(tempfile.mktemp(suffix=suffix))
    tmp.write_bytes(content)
    print(f"  → Saved to: {tmp} ({len(content):,} bytes)")
    return tmp


def test_markitdown(file_path: Path) -> str:
    """Run markitdown on file."""
    md = MarkItDown()
    result = md.convert(str(file_path))
    return result.text_content


def main():
    print("=" * 60)
    print("BAKEOFF: markitdown extraction for Office files")
    print("=" * 60)

    # Test XLSX
    print("\n" + "=" * 60)
    print("XLSX TEST")
    print("=" * 60)

    xlsx_path = download_file(XLSX_ID, ".xlsx")
    xlsx_content = test_markitdown(xlsx_path)

    print(f"\n--- markitdown output ({len(xlsx_content):,} chars) ---")
    # Show first 3000 chars
    print(xlsx_content[:3000])
    if len(xlsx_content) > 3000:
        print(f"\n... [{len(xlsx_content) - 3000:,} more chars] ...")

    # Test PPTX
    print("\n" + "=" * 60)
    print("PPTX TEST")
    print("=" * 60)

    pptx_path = download_file(PPTX_ID, ".pptx")
    pptx_content = test_markitdown(pptx_path)

    print(f"\n--- markitdown output ({len(pptx_content):,} chars) ---")
    # Show first 3000 chars
    print(pptx_content[:3000])
    if len(pptx_content) > 3000:
        print(f"\n... [{len(pptx_content) - 3000:,} more chars] ...")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"XLSX: {len(xlsx_content):,} chars extracted")
    print(f"PPTX: {len(pptx_content):,} chars extracted")

    # Cleanup
    xlsx_path.unlink()
    pptx_path.unlink()


if __name__ == "__main__":
    main()
