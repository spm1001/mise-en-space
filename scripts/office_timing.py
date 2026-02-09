#!/usr/bin/env python3
"""
Per-step timing for Office file conversion pipeline.

Measures: download → upload+convert → export → delete for DOCX and XLSX.
Goal: identify which step dominates the ~9s DOCX / ~6s XLSX total.

Usage:
    uv run python scripts/office_timing.py
"""

import json
import sys
import time

# Add project root to path
sys.path.insert(0, ".")

from adapters.services import get_drive_service
from adapters.drive import download_file
from googleapiclient.http import MediaInMemoryUpload


def load_test_ids() -> dict:
    with open("fixtures/integration_ids.json") as f:
        return json.load(f)


def time_office_pipeline(file_id: str, label: str, source_mime: str, target_mime: str, export_mime: str) -> None:
    """Time each step of the Office conversion pipeline."""
    service = get_drive_service()

    print(f"\n{'='*60}")
    print(f"{label} (file_id: {file_id})")
    print(f"{'='*60}")

    total_start = time.perf_counter()

    # Step 1: Download from Drive
    t0 = time.perf_counter()
    file_bytes = download_file(file_id)
    t_download = time.perf_counter() - t0
    print(f"  1. Download:        {t_download:.3f}s  ({len(file_bytes):,} bytes)")

    # Step 2: Upload with conversion
    t0 = time.perf_counter()
    media = MediaInMemoryUpload(file_bytes, mimetype=source_mime)
    uploaded = (
        service.files()
        .create(
            body={"name": "_mise_timing_temp", "mimeType": target_mime},
            media_body=media,
            fields="id",
        )
        .execute()
    )
    temp_id = uploaded["id"]
    t_upload = time.perf_counter() - t0
    print(f"  2. Upload+convert:  {t_upload:.3f}s")

    # Step 3: Export
    t0 = time.perf_counter()
    content = (
        service.files()
        .export(fileId=temp_id, mimeType=export_mime)
        .execute()
    )
    t_export = time.perf_counter() - t0
    content_len = len(content) if isinstance(content, (str, bytes)) else 0
    print(f"  3. Export:          {t_export:.3f}s  ({content_len:,} bytes)")

    # Step 4: Delete temp
    t0 = time.perf_counter()
    service.files().delete(fileId=temp_id).execute()
    t_delete = time.perf_counter() - t0
    print(f"  4. Delete temp:     {t_delete:.3f}s")

    total = time.perf_counter() - total_start

    print(f"\n  Total:              {total:.3f}s")
    print(f"\n  Breakdown:")
    for name, t in [("Download", t_download), ("Upload+convert", t_upload), ("Export", t_export), ("Delete", t_delete)]:
        pct = (t / total) * 100
        bar = "#" * int(pct / 2)
        print(f"    {name:<16} {t:5.3f}s  {pct:4.1f}%  {bar}")


def main():
    ids = load_test_ids()

    # DOCX → Google Doc → markdown
    time_office_pipeline(
        file_id=ids["test_docx_id"],
        label="DOCX (→ Doc → markdown)",
        source_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        target_mime="application/vnd.google-apps.document",
        export_mime="text/markdown",
    )

    # XLSX → Google Sheet → CSV
    time_office_pipeline(
        file_id=ids["test_xlsx_id"],
        label="XLSX (→ Sheet → CSV)",
        source_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        target_mime="application/vnd.google-apps.spreadsheet",
        export_mime="text/csv",
    )

    # Run each a second time (warm creds, connection reuse)
    print(f"\n\n{'*'*60}")
    print("Second run (warm connections)")
    print(f"{'*'*60}")

    time_office_pipeline(
        file_id=ids["test_docx_id"],
        label="DOCX (warm)",
        source_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        target_mime="application/vnd.google-apps.document",
        export_mime="text/markdown",
    )

    time_office_pipeline(
        file_id=ids["test_xlsx_id"],
        label="XLSX (warm)",
        source_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        target_mime="application/vnd.google-apps.spreadsheet",
        export_mime="text/csv",
    )


if __name__ == "__main__":
    main()
