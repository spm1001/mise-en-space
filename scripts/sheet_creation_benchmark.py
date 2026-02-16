"""
Benchmark three Sheet creation paths.

Tests: Drive CSV upload, Sheets API create, openpyxl+upload.
Measures: timing, type detection accuracy, formatting quality.

Usage:
    uv run python scripts/sheet_creation_benchmark.py
"""

import csv
import io
import json
import sys
import time
from pathlib import Path

# Ensure repo root is on sys.path (scripts/ may not inherit it)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from googleapiclient.http import MediaIoBaseUpload

from adapters.services import get_drive_service, get_sheets_service
from adapters.drive import GOOGLE_SHEET_MIME


# ---------------------------------------------------------------------------
# Test data: each row is (label, value, expected_type, notes)
# expected_type is what we WANT Google to store it as
# ---------------------------------------------------------------------------

TEST_ROWS = [
    # Header row handled separately
    # --- Plain text ---
    ("Plain text", "Hello world", "string", "Simple text"),
    ("Long text", "The quick brown fox jumps over the lazy dog and keeps running for quite a while longer than you might expect", "string", "Long string"),
    ("Unicode", "Héllo wörld café résumé", "string", "Accented chars"),
    ("Emoji", "Budget 📊 approved ✅", "string", "Emoji in text"),
    ("Quoted commas", "Smith, John & Partners", "string", "Comma in value"),
    # --- Numbers ---
    ("Integer", "42", "number", "Plain integer"),
    ("Large integer", "1245000", "number", "No separators"),
    ("Formatted integer", "1,245,000", "number", "Comma-separated"),
    ("Decimal", "3.14159", "number", "Float"),
    ("Negative", "-500", "number", "Negative integer"),
    ("Negative decimal", "-12.50", "number", "Negative float"),
    ("Zero", "0", "number", "Zero"),
    # --- Currency ---
    ("GBP", "£12,450.00", "number/currency", "UK pounds"),
    ("GBP no commas", "£500", "number/currency", "Simple GBP"),
    ("USD", "$8,200.50", "number/currency", "US dollars"),
    ("EUR", "€15,600.00", "number/currency", "Euros"),
    ("Negative GBP", "-£3,000.00", "number/currency", "Negative currency"),
    # --- Percentages ---
    ("Percent", "3.63%", "number/percent", "Percentage with decimal"),
    ("Percent whole", "45%", "number/percent", "Whole percentage"),
    ("Percent small", "0.26%", "number/percent", "Small percentage"),
    # --- Dates ---
    ("ISO date", "2026-01-15", "date", "ISO 8601"),
    ("UK date", "15/01/2026", "date", "DD/MM/YYYY"),
    ("US date", "01/15/2026", "date", "MM/DD/YYYY (ambiguous)"),
    ("Date with time", "2026-01-15 14:30:00", "date", "ISO with time"),
    ("Short date", "15-Jan-2026", "date", "Human-readable"),
    # --- Booleans ---
    ("Boolean true", "TRUE", "boolean", "Uppercase TRUE"),
    ("Boolean false", "FALSE", "boolean", "Uppercase FALSE"),
    # --- Formulas ---
    ("Formula SUM", "=SUM(B2:B12)", "formula", "SUM formula"),
    ("Formula AVG", "=AVERAGE(B2:B12)", "formula", "AVERAGE formula"),
    ("Formula IF", '=IF(B2>100,"High","Low")', "formula", "IF formula"),
    # --- Tricky: text that looks like numbers ---
    ("Phone number", "07700900123", "ambiguous", "UK mobile - could be number or text"),
    ("Postcode", "SW1A 1AA", "string", "UK postcode"),
    ("Leading zeros", "007", "ambiguous", "Leading zeros - should be text but often becomes 7"),
    ("ID number", "00012345", "ambiguous", "Leading zeros in ID"),
    ("Year-like", "2026", "ambiguous", "Could be year or number"),
    # --- Empty ---
    ("Empty cell", "", "empty", "Empty string"),
]

HEADERS = ["Type", "Value", "Expected", "Notes"]


def generate_test_csv() -> str:
    """Generate test CSV from TEST_ROWS."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    for label, value, expected, notes in TEST_ROWS:
        writer.writerow([label, value, expected, notes])
    return output.getvalue()


def generate_large_csv(rows: int = 500, cols: int = 10) -> str:
    """Generate a larger CSV for timing tests."""
    output = io.StringIO()
    writer = csv.writer(output)
    headers = [f"Col_{i}" for i in range(cols)]
    headers[0] = "Category"
    headers[1] = "Revenue"
    headers[2] = "Cost"
    headers[3] = "Margin"
    headers[4] = "Date"
    headers[5] = "Region"
    writer.writerow(headers)

    import random
    random.seed(42)
    categories = ["Product A", "Product B", "Product C", "Service X", "Service Y"]
    regions = ["UK", "US", "EU", "APAC", "LATAM"]

    for i in range(rows):
        row = [""] * cols
        row[0] = random.choice(categories)
        rev = round(random.uniform(1000, 100000), 2)
        cost = round(rev * random.uniform(0.3, 0.8), 2)
        row[1] = f"£{rev:,.2f}"
        row[2] = f"£{cost:,.2f}"
        row[3] = f"{((rev - cost) / rev * 100):.1f}%"
        row[4] = f"2026-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
        row[5] = random.choice(regions)
        for j in range(6, cols):
            row[j] = str(random.randint(1, 1000))
        writer.writerow(row)

    return output.getvalue()


# ---------------------------------------------------------------------------
# Path A: Drive CSV upload
# ---------------------------------------------------------------------------

def path_a_drive_csv(csv_content: str, title: str) -> dict:
    """Upload CSV to Drive, let Google convert to Sheet."""
    service = get_drive_service()

    media = MediaIoBaseUpload(
        io.BytesIO(csv_content.encode("utf-8")),
        mimetype="text/csv",
        resumable=True,
    )
    file_metadata = {
        "name": title,
        "mimeType": GOOGLE_SHEET_MIME,
    }

    start = time.time()
    result = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,webViewLink,name",
        supportsAllDrives=True,
    ).execute()
    elapsed = time.time() - start

    return {
        "file_id": result["id"],
        "web_link": result.get("webViewLink", ""),
        "title": result.get("name", title),
        "elapsed_s": round(elapsed, 2),
        "path": "A: Drive CSV",
    }


# ---------------------------------------------------------------------------
# Path A+: Drive CSV upload + formatting batchUpdate
# ---------------------------------------------------------------------------

def path_a_plus_formatted(csv_content: str, title: str) -> dict:
    """Upload CSV, then apply formatting (bold headers, freeze, auto-resize)."""
    # Step 1: upload
    upload_result = path_a_drive_csv(csv_content, title)
    upload_time = upload_result["elapsed_s"]

    spreadsheet_id = upload_result["file_id"]
    sheets_service = get_sheets_service()

    # Get sheet ID
    ss = sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties"
    ).execute()
    sheet_id = ss["sheets"][0]["properties"]["sheetId"]

    # Step 2: formatting
    start = time.time()
    requests = [
        # Bold header row
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
        # Freeze header row
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        # Auto-resize all columns
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                }
            }
        },
    ]

    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()
    format_time = round(time.time() - start, 2)

    return {
        "file_id": spreadsheet_id,
        "web_link": upload_result["web_link"],
        "title": upload_result["title"],
        "upload_time_s": upload_time,
        "format_time_s": format_time,
        "elapsed_s": round(upload_time + format_time, 2),
        "path": "A+: Drive CSV + format",
    }


# ---------------------------------------------------------------------------
# Path B: Sheets API spreadsheets.create
# ---------------------------------------------------------------------------

def path_b_sheets_create(csv_content: str, title: str) -> dict:
    """Create Sheet via Sheets API with full formatting."""
    sheets_service = get_sheets_service()

    # Parse CSV
    reader = csv.reader(io.StringIO(csv_content))
    rows = list(reader)
    headers = rows[0]
    data_rows = rows[1:]

    def make_cell_value(val: str) -> dict:
        """Convert string to appropriate ExtendedValue."""
        if not val:
            return {}
        if val.startswith("="):
            return {"userEnteredValue": {"formulaValue": val}}
        # Try number
        try:
            # Strip currency/percent for number detection
            cleaned = val.replace(",", "").replace("£", "").replace("$", "").replace("€", "").replace("%", "")
            if cleaned.lstrip("-"):
                num = float(cleaned)
                return {"userEnteredValue": {"numberValue": num}}
        except (ValueError, AttributeError):
            pass
        # Boolean
        if val.upper() == "TRUE":
            return {"userEnteredValue": {"boolValue": True}}
        if val.upper() == "FALSE":
            return {"userEnteredValue": {"boolValue": False}}
        # String
        return {"userEnteredValue": {"stringValue": val}}

    # Build header row with bold formatting
    header_row_data = {
        "values": [
            {
                "userEnteredValue": {"stringValue": h},
                "userEnteredFormat": {
                    "textFormat": {"bold": True},
                },
            }
            for h in headers
        ]
    }

    # Build data rows
    body_rows = []
    for row in data_rows:
        body_rows.append({
            "values": [make_cell_value(val) for val in row]
        })

    all_rows = [header_row_data] + body_rows

    body = {
        "properties": {
            "title": title,
            "locale": "en_GB",
            "autoRecalc": "ON_CHANGE",
        },
        "sheets": [
            {
                "properties": {
                    "sheetId": 0,
                    "title": "Sheet1",
                    "gridProperties": {
                        "frozenRowCount": 1,
                    },
                },
                "data": [
                    {
                        "startRow": 0,
                        "startColumn": 0,
                        "rowData": all_rows,
                    }
                ],
            }
        ],
    }

    start = time.time()
    result = sheets_service.spreadsheets().create(
        body=body,
        fields="spreadsheetId,spreadsheetUrl,properties.title",
    ).execute()
    create_time = round(time.time() - start, 2)

    spreadsheet_id = result["spreadsheetId"]

    # Auto-resize (can't do in create call)
    resize_start = time.time()
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": 0,
                            "dimension": "COLUMNS",
                        }
                    }
                }
            ]
        },
    ).execute()
    resize_time = round(time.time() - resize_start, 2)

    # Move to same location (Drive root) — spreadsheets.create doesn't support parents
    # (it always creates in root, which is fine for benchmark)

    return {
        "file_id": spreadsheet_id,
        "web_link": result.get("spreadsheetUrl", ""),
        "title": result["properties"]["title"],
        "create_time_s": create_time,
        "resize_time_s": resize_time,
        "elapsed_s": round(create_time + resize_time, 2),
        "path": "B: Sheets API create",
    }


# ---------------------------------------------------------------------------
# Path C: openpyxl + upload
# ---------------------------------------------------------------------------

def path_c_openpyxl(csv_content: str, title: str) -> dict:
    """Build XLSX with openpyxl, upload+convert via Drive."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        return {"error": "openpyxl not installed", "path": "C: openpyxl+upload"}

    # Parse CSV
    reader = csv.reader(io.StringIO(csv_content))
    rows = list(reader)

    # Build workbook
    build_start = time.time()
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    for row in rows:
        ws.append(row)

    # Bold headers
    for cell in ws[1]:
        cell.font = Font(bold=True)

    # Freeze header row
    ws.freeze_panes = "A2"

    # Save to bytes
    xlsx_buffer = io.BytesIO()
    wb.save(xlsx_buffer)
    xlsx_bytes = xlsx_buffer.getvalue()
    build_time = round(time.time() - build_start, 3)

    # Upload+convert
    service = get_drive_service()
    media = MediaIoBaseUpload(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=True,
    )
    file_metadata = {
        "name": title,
        "mimeType": GOOGLE_SHEET_MIME,
    }

    upload_start = time.time()
    result = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,webViewLink,name",
        supportsAllDrives=True,
    ).execute()
    upload_time = round(time.time() - upload_start, 2)

    return {
        "file_id": result["id"],
        "web_link": result.get("webViewLink", ""),
        "title": result.get("name", title),
        "build_time_s": build_time,
        "upload_time_s": upload_time,
        "elapsed_s": round(build_time + upload_time, 2),
        "path": "C: openpyxl+upload",
    }


# ---------------------------------------------------------------------------
# Read-back: verify what Google actually stored
# ---------------------------------------------------------------------------

def read_back_sheet(spreadsheet_id: str) -> list[list[dict]]:
    """Read back cell data to verify types."""
    sheets_service = get_sheets_service()

    # Get full cell data including effective format and value
    result = sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        includeGridData=True,
        fields="sheets.data.rowData.values(userEnteredValue,effectiveValue,effectiveFormat.numberFormat,formattedValue)",
    ).execute()

    rows = []
    for row_data in result["sheets"][0]["data"][0].get("rowData", []):
        cells = []
        for cell in row_data.get("values", []):
            cells.append({
                "entered": cell.get("userEnteredValue", {}),
                "effective": cell.get("effectiveValue", {}),
                "format": cell.get("effectiveFormat", {}).get("numberFormat", {}),
                "display": cell.get("formattedValue", ""),
            })
        rows.append(cells)
    return rows


def classify_cell(cell: dict) -> str:
    """Classify what Google stored a cell as."""
    entered = cell.get("entered", {})
    fmt = cell.get("format", {})

    if "formulaValue" in entered:
        return "formula"
    if "boolValue" in entered:
        return "boolean"
    if "numberValue" in entered:
        fmt_type = fmt.get("type", "")
        if fmt_type == "PERCENT":
            return "number/percent"
        if fmt_type == "CURRENCY":
            return "number/currency"
        if fmt_type in ("DATE", "DATE_TIME", "TIME"):
            return "date"
        return "number"
    if "stringValue" in entered:
        return "string"
    if not entered:
        return "empty"
    return "unknown"


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def run_benchmark():
    """Run all paths and compare."""
    test_csv = generate_test_csv()
    large_csv = generate_large_csv(500, 10)

    print("=" * 70)
    print("SHEET CREATION BENCHMARK")
    print("=" * 70)

    # --- Type detection test (small CSV) ---
    print("\n--- TYPE DETECTION TEST (diverse cell types) ---\n")

    results = {}
    created_ids = []

    for label, func in [
        ("A: Drive CSV", lambda: path_a_drive_csv(test_csv, "Benchmark-A-types")),
        ("A+: Drive CSV + format", lambda: path_a_plus_formatted(test_csv, "Benchmark-A+-types")),
        ("B: Sheets API create", lambda: path_b_sheets_create(test_csv, "Benchmark-B-types")),
        ("C: openpyxl+upload", lambda: path_c_openpyxl(test_csv, "Benchmark-C-types")),
    ]:
        print(f"  Running {label}...", end=" ", flush=True)
        result = func()
        print(f"{result['elapsed_s']}s")
        if "error" not in result:
            results[label] = result
            created_ids.append(result["file_id"])

    # Read back and compare type detection
    print("\n--- TYPE DETECTION RESULTS ---\n")
    print(f"{'Cell Type':<25} {'Expected':<18} ", end="")
    for label in results:
        print(f"{label:<20} ", end="")
    print()
    print("-" * (25 + 18 + 20 * len(results)))

    readbacks = {}
    for label, result in results.items():
        readbacks[label] = read_back_sheet(result["file_id"])

    # Compare each test row (skip header row = index 0)
    match_counts = {label: 0 for label in results}
    total_testable = 0

    for i, (row_label, value, expected, notes) in enumerate(TEST_ROWS):
        row_idx = i + 1  # +1 for header

        # Skip ambiguous expectations
        if expected == "ambiguous":
            print(f"  {row_label:<23} {'(ambiguous)':<18} ", end="")
            for label in results:
                if row_idx < len(readbacks[label]):
                    cells = readbacks[label][row_idx]
                    if len(cells) > 1:
                        actual = classify_cell(cells[1])  # Column B = value
                        display = cells[1].get("display", "")
                        print(f"{actual} [{display}]"[:19].ljust(20), end=" ")
                    else:
                        print(f"{'(missing)':<20} ", end="")
                else:
                    print(f"{'(no row)':<20} ", end="")
            print()
            continue

        total_testable += 1
        print(f"  {row_label:<23} {expected:<18} ", end="")

        for label in results:
            if row_idx < len(readbacks[label]):
                cells = readbacks[label][row_idx]
                if len(cells) > 1:
                    actual = classify_cell(cells[1])
                    display = cells[1].get("display", "")
                    match = "✓" if actual == expected else "✗"
                    if match == "✓":
                        match_counts[label] += 1
                    info = f"{match} {actual}"
                    print(f"{info[:19]:<20} ", end="")
                else:
                    print(f"{'(missing)':<20} ", end="")
            else:
                print(f"{'(no row)':<20} ", end="")
        print()

    print()
    print(f"{'ACCURACY':<25} {'':<18} ", end="")
    for label in results:
        pct = match_counts[label] / total_testable * 100 if total_testable else 0
        print(f"{match_counts[label]}/{total_testable} ({pct:.0f}%)".ljust(20), end=" ")
    print()

    # --- Timing test (large CSV) ---
    print("\n\n--- TIMING TEST (500 rows × 10 cols) ---\n")

    for label, func in [
        ("A: Drive CSV", lambda: path_a_drive_csv(large_csv, "Benchmark-A-large")),
        ("A+: Drive CSV + format", lambda: path_a_plus_formatted(large_csv, "Benchmark-A+-large")),
        ("B: Sheets API create", lambda: path_b_sheets_create(large_csv, "Benchmark-B-large")),
        ("C: openpyxl+upload", lambda: path_c_openpyxl(large_csv, "Benchmark-C-large")),
    ]:
        print(f"  Running {label}...", end=" ", flush=True)
        result = func()
        created_ids.append(result.get("file_id", ""))
        # Print timing breakdown
        timing_parts = []
        for k, v in result.items():
            if k.endswith("_s") and k != "elapsed_s":
                timing_parts.append(f"{k.replace('_s','')}: {v}s")
        breakdown = " | ".join(timing_parts) if timing_parts else ""
        print(f"total: {result['elapsed_s']}s  ({breakdown})")

    # --- Cleanup ---
    print(f"\n--- CLEANUP ---\n")
    print(f"Created {len(created_ids)} test spreadsheets.")
    print("File IDs for manual cleanup:")
    drive_service = get_drive_service()
    for fid in created_ids:
        if fid:
            try:
                meta = drive_service.files().get(fileId=fid, fields="name").execute()
                print(f"  {fid} — {meta['name']}")
            except Exception:
                print(f"  {fid} — (couldn't read)")

    # Auto-delete benchmark files
    print("\nDeleting benchmark files...")
    for fid in created_ids:
        if fid:
            try:
                drive_service.files().delete(fileId=fid).execute()
                print(f"  Deleted {fid}")
            except Exception as e:
                print(f"  Failed to delete {fid}: {e}")


if __name__ == "__main__":
    run_benchmark()
