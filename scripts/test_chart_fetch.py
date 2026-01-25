#!/usr/bin/env python3
"""
Test chart rendering with the fixture spreadsheet.

This tests the full fetch flow: Sheets API → chart rendering → deposit.
"""

import json
from pathlib import Path

# Add parent to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.fetch import fetch_sheet

# Test fixture from mise-w6f design
TEST_SPREADSHEET_ID = "1UlWoEsfjzqbuS_tKD6Drm4wmbPLeGOKWVBVip5AI-xw"


def main():
    print("=" * 60)
    print("CHART FETCH TEST")
    print("=" * 60)
    print(f"Spreadsheet: {TEST_SPREADSHEET_ID}")

    # Fetch the spreadsheet (this will render charts)
    print("\nFetching spreadsheet with charts...")
    result = fetch_sheet(TEST_SPREADSHEET_ID, "Chart Test Fixture", {})

    print(f"\nResult:")
    print(f"  Path: {result.path}")
    print(f"  Content file: {result.content_file}")
    print(f"  Format: {result.format}")
    print(f"  Type: {result.type}")
    print(f"  Metadata: {result.metadata}")

    # Check the deposit folder
    folder = Path(result.path)
    print(f"\nDeposit folder contents:")
    for f in sorted(folder.iterdir()):
        size = f.stat().st_size
        print(f"  {f.name}: {size:,} bytes")

    # Read manifest
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        print(f"\nManifest:")
        for k, v in manifest.items():
            print(f"  {k}: {v}")

    # Read charts.json if exists
    charts_path = folder / "charts.json"
    if charts_path.exists():
        charts = json.loads(charts_path.read_text())
        print(f"\nCharts metadata ({len(charts)} charts):")
        for i, chart in enumerate(charts):
            has_png = chart.get("has_png", False)
            status = "✓" if has_png else "✗"
            print(f"  {status} Chart {i+1}: {chart.get('title', 'Untitled')} ({chart.get('chart_type', 'unknown')})")
    else:
        print("\n⚠️  No charts.json found")

    # Verify chart PNGs
    chart_pngs = list(folder.glob("chart_*.png"))
    if chart_pngs:
        print(f"\nChart PNGs ({len(chart_pngs)}):")
        for png in sorted(chart_pngs):
            size = png.stat().st_size
            # Check it's actually a PNG
            with open(png, 'rb') as f:
                header = f.read(8)
            is_png = header.startswith(b'\x89PNG')
            status = "✓ PNG" if is_png else "✗ NOT PNG"
            print(f"  {png.name}: {size:,} bytes ({status})")
    else:
        print("\n⚠️  No chart PNG files found")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
