#!/usr/bin/env python3
"""
Chart rendering timing experiments.

Benchmarks different approaches to rendering Sheets charts as PNGs.
IMPORTANT: Verifies success at each step to avoid timing error paths.
"""

import time
import json
import requests
from pathlib import Path
from dataclasses import dataclass

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Test fixture from mise-w6f design
TEST_SPREADSHEET_ID = "1UlWoEsfjzqbuS_tKD6Drm4wmbPLeGOKWVBVip5AI-xw"


@dataclass
class TimingResult:
    """Result of a timed operation."""
    operation: str
    duration_ms: float
    success: bool
    details: str = ""


def get_credentials():
    """Load credentials from token.json."""
    token_path = Path(__file__).parent.parent / "token.json"
    if not token_path.exists():
        raise FileNotFoundError(f"No token.json found at {token_path}. Run auth first.")

    creds = Credentials.from_authorized_user_file(str(token_path))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    return creds


def timed(operation_name: str):
    """Decorator to time an operation and return TimingResult."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000

                # Check if result indicates success
                if result is None:
                    return TimingResult(operation_name, duration_ms, False, "Returned None")
                if isinstance(result, dict) and result.get("error"):
                    return TimingResult(operation_name, duration_ms, False, str(result.get("error")))

                return TimingResult(operation_name, duration_ms, True, str(result)[:100])
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                return TimingResult(operation_name, duration_ms, False, str(e)[:200])
        return wrapper
    return decorator


def get_charts_in_spreadsheet(sheets_service, spreadsheet_id: str) -> list[dict]:
    """Get all charts from a spreadsheet."""
    ss = sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        includeGridData=False
    ).execute()

    charts = []
    for sheet in ss.get("sheets", []):
        sheet_props = sheet.get("properties", {})
        for chart in sheet.get("charts", []):
            charts.append({
                "chart_id": chart["chartId"],
                "title": chart.get("spec", {}).get("title", "Untitled"),
                "sheet_name": sheet_props.get("title"),
                "sheet_type": sheet_props.get("sheetType", "GRID"),
            })
    return charts


class ChartTimingExperiment:
    """Run timing experiments for chart rendering."""

    def __init__(self):
        self.creds = get_credentials()
        self.slides_service = build("slides", "v1", credentials=self.creds)
        self.sheets_service = build("sheets", "v4", credentials=self.creds)
        self.drive_service = build("drive", "v3", credentials=self.creds)
        self.results: list[TimingResult] = []

        # Track created resources for cleanup
        self.presentation_id = None

    def log(self, result: TimingResult):
        """Log and store a timing result."""
        status = "✓" if result.success else "✗"
        print(f"  {status} {result.operation}: {result.duration_ms:.0f}ms")
        if not result.success:
            print(f"    ERROR: {result.details}")
        self.results.append(result)

    def create_presentation(self) -> str | None:
        """Create a new presentation and return its ID."""
        start = time.perf_counter()
        try:
            pres = self.slides_service.presentations().create(
                body={"title": f"Chart Timing Test {int(time.time())}"}
            ).execute()
            duration_ms = (time.perf_counter() - start) * 1000

            pres_id = pres.get("presentationId")
            if not pres_id:
                self.log(TimingResult("create_presentation", duration_ms, False, "No presentationId"))
                return None

            self.presentation_id = pres_id
            self.log(TimingResult("create_presentation", duration_ms, True, pres_id))
            return pres_id
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("create_presentation", duration_ms, False, str(e)))
            return None

    def create_slide(self, presentation_id: str) -> str | None:
        """Create a blank slide and return its ID."""
        start = time.perf_counter()
        try:
            response = self.slides_service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={"requests": [{"createSlide": {"insertionIndex": 0}}]}
            ).execute()
            duration_ms = (time.perf_counter() - start) * 1000

            slide_id = response.get("replies", [{}])[0].get("createSlide", {}).get("objectId")
            if not slide_id:
                self.log(TimingResult("create_slide", duration_ms, False, "No slide objectId"))
                return None

            self.log(TimingResult("create_slide", duration_ms, True, slide_id))
            return slide_id
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("create_slide", duration_ms, False, str(e)))
            return None

    def insert_chart(self, presentation_id: str, slide_id: str,
                     spreadsheet_id: str, chart_id: int,
                     linking_mode: str = "LINKED") -> str | None:
        """Insert a chart and return the object ID."""
        op_name = f"insert_chart_{linking_mode}"
        start = time.perf_counter()
        try:
            response = self.slides_service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={"requests": [{
                    "createSheetsChart": {
                        "spreadsheetId": spreadsheet_id,
                        "chartId": chart_id,
                        "linkingMode": linking_mode,
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {
                                "width": {"magnitude": 4000000, "unit": "EMU"},
                                "height": {"magnitude": 3000000, "unit": "EMU"},
                            },
                            "transform": {
                                "scaleX": 1, "scaleY": 1,
                                "translateX": 100000, "translateY": 100000,
                                "unit": "EMU"
                            }
                        }
                    }
                }]}
            ).execute()
            duration_ms = (time.perf_counter() - start) * 1000

            obj_id = response.get("replies", [{}])[0].get("createSheetsChart", {}).get("objectId")
            if not obj_id:
                self.log(TimingResult(op_name, duration_ms, False, "No chart objectId"))
                return None

            self.log(TimingResult(op_name, duration_ms, True, obj_id))
            return obj_id
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult(op_name, duration_ms, False, str(e)))
            return None

    def get_content_url(self, presentation_id: str, chart_object_id: str,
                        linking_mode: str = "LINKED") -> str | None:
        """Fetch presentation to get the contentUrl for a chart."""
        start = time.perf_counter()
        try:
            pres = self.slides_service.presentations().get(
                presentationId=presentation_id
            ).execute()
            duration_ms = (time.perf_counter() - start) * 1000

            # Find the chart element
            content_url = None
            elem_type = None
            for slide in pres.get("slides", []):
                for elem in slide.get("pageElements", []):
                    if elem.get("objectId") == chart_object_id:
                        # LINKED mode: sheetsChart with contentUrl
                        if "sheetsChart" in elem:
                            content_url = elem.get("sheetsChart", {}).get("contentUrl")
                            elem_type = "sheetsChart"
                        # NOT_LINKED_IMAGE mode: image with contentUrl
                        elif "image" in elem:
                            content_url = elem.get("image", {}).get("contentUrl")
                            elem_type = "image"
                        break

            if not content_url:
                # Debug: show what we found
                details = f"No contentUrl. elem_type={elem_type}"
                self.log(TimingResult("get_content_url", duration_ms, False, details))
                return None

            self.log(TimingResult("get_content_url", duration_ms, True,
                                  f"{elem_type}: {content_url[:50]}..."))
            return content_url
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("get_content_url", duration_ms, False, str(e)))
            return None

    def fetch_png(self, content_url: str) -> bytes | None:
        """Fetch the actual PNG from contentUrl."""
        start = time.perf_counter()
        try:
            response = requests.get(content_url, timeout=30)
            duration_ms = (time.perf_counter() - start) * 1000

            if response.status_code != 200:
                self.log(TimingResult("fetch_png", duration_ms, False, f"HTTP {response.status_code}"))
                return None

            # Verify it's actually an image
            content_type = response.headers.get("content-type", "")
            content_length = len(response.content)

            if content_length < 1000:  # Suspiciously small
                self.log(TimingResult("fetch_png", duration_ms, False,
                                      f"Too small: {content_length} bytes"))
                return None

            if "image" not in content_type and not response.content[:8].startswith(b'\x89PNG'):
                self.log(TimingResult("fetch_png", duration_ms, False,
                                      f"Not an image: {content_type}"))
                return None

            self.log(TimingResult("fetch_png", duration_ms, True,
                                  f"{content_length} bytes, {content_type}"))
            return response.content
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("fetch_png", duration_ms, False, str(e)))
            return None

    def delete_presentation(self, presentation_id: str) -> bool:
        """Delete the presentation."""
        start = time.perf_counter()
        try:
            self.drive_service.files().delete(fileId=presentation_id).execute()
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("delete_presentation", duration_ms, True, presentation_id))
            return True
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("delete_presentation", duration_ms, False, str(e)))
            return False

    def cleanup(self):
        """Clean up any created resources."""
        if self.presentation_id:
            try:
                self.drive_service.files().delete(fileId=self.presentation_id).execute()
                print(f"  Cleaned up presentation: {self.presentation_id}")
            except Exception as e:
                print(f"  Failed to cleanup: {e}")
            self.presentation_id = None

    def run_single_chart_experiment(self, spreadsheet_id: str, chart_id: int,
                                    linking_mode: str = "LINKED") -> dict:
        """
        Run full cycle for a single chart.
        Returns timing breakdown and success status.
        """
        print(f"\n--- Single Chart: {linking_mode} mode ---")
        self.results = []

        total_start = time.perf_counter()

        # Step 1: Create presentation
        pres_id = self.create_presentation()
        if not pres_id:
            return {"success": False, "error": "Failed to create presentation"}

        # Step 2: Create slide
        slide_id = self.create_slide(pres_id)
        if not slide_id:
            self.cleanup()
            return {"success": False, "error": "Failed to create slide"}

        # Step 3: Insert chart
        chart_obj_id = self.insert_chart(pres_id, slide_id, spreadsheet_id, chart_id, linking_mode)
        if not chart_obj_id:
            self.cleanup()
            return {"success": False, "error": "Failed to insert chart"}

        # Step 4: Get contentUrl
        content_url = self.get_content_url(pres_id, chart_obj_id)
        if not content_url:
            self.cleanup()
            return {"success": False, "error": "Failed to get contentUrl"}

        # Step 5: Fetch PNG
        png_data = self.fetch_png(content_url)
        if not png_data:
            self.cleanup()
            return {"success": False, "error": "Failed to fetch PNG"}

        # Step 6: Delete presentation
        self.delete_presentation(pres_id)
        self.presentation_id = None

        total_ms = (time.perf_counter() - total_start) * 1000

        return {
            "success": True,
            "total_ms": total_ms,
            "linking_mode": linking_mode,
            "png_size": len(png_data),
            "steps": [{"op": r.operation, "ms": r.duration_ms, "ok": r.success}
                      for r in self.results]
        }

    def run_batch_experiment(self, spreadsheet_id: str, chart_ids: list[int],
                            linking_mode: str = "LINKED") -> dict:
        """
        Insert multiple charts in one presentation.
        Returns timing breakdown.
        """
        print(f"\n--- Batch Charts ({len(chart_ids)}): {linking_mode} mode ---")
        self.results = []

        total_start = time.perf_counter()

        # Step 1: Create presentation
        pres_id = self.create_presentation()
        if not pres_id:
            return {"success": False, "error": "Failed to create presentation"}

        # Step 2: Create slides (one per chart)
        slide_ids = []
        for i in range(len(chart_ids)):
            slide_id = self.create_slide(pres_id)
            if not slide_id:
                self.cleanup()
                return {"success": False, "error": f"Failed to create slide {i}"}
            slide_ids.append(slide_id)

        # Step 3: Insert all charts
        chart_obj_ids = []
        for i, (slide_id, chart_id) in enumerate(zip(slide_ids, chart_ids)):
            obj_id = self.insert_chart(pres_id, slide_id, spreadsheet_id, chart_id, linking_mode)
            if not obj_id:
                self.cleanup()
                return {"success": False, "error": f"Failed to insert chart {i}"}
            chart_obj_ids.append(obj_id)

        # Step 4: Single fetch to get all contentUrls
        start = time.perf_counter()
        try:
            pres = self.slides_service.presentations().get(presentationId=pres_id).execute()
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("get_all_content_urls", duration_ms, True, f"{len(chart_ids)} charts"))
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("get_all_content_urls", duration_ms, False, str(e)))
            self.cleanup()
            return {"success": False, "error": "Failed to get contentUrls"}

        # Extract contentUrls (handle both LINKED and NOT_LINKED_IMAGE)
        content_urls = []
        for slide in pres.get("slides", []):
            for elem in slide.get("pageElements", []):
                if elem.get("objectId") in chart_obj_ids:
                    # Try sheetsChart first (LINKED), then image (NOT_LINKED_IMAGE)
                    url = elem.get("sheetsChart", {}).get("contentUrl")
                    if not url:
                        url = elem.get("image", {}).get("contentUrl")
                    if url:
                        content_urls.append(url)

        if len(content_urls) != len(chart_ids):
            self.cleanup()
            return {"success": False, "error": f"Only found {len(content_urls)}/{len(chart_ids)} contentUrls"}

        # Step 5: Fetch all PNGs
        png_sizes = []
        for url in content_urls:
            png_data = self.fetch_png(url)
            if not png_data:
                self.cleanup()
                return {"success": False, "error": "Failed to fetch a PNG"}
            png_sizes.append(len(png_data))

        # Step 6: Delete presentation
        self.delete_presentation(pres_id)
        self.presentation_id = None

        total_ms = (time.perf_counter() - total_start) * 1000

        return {
            "success": True,
            "total_ms": total_ms,
            "chart_count": len(chart_ids),
            "linking_mode": linking_mode,
            "png_sizes": png_sizes,
            "steps": [{"op": r.operation, "ms": r.duration_ms, "ok": r.success}
                      for r in self.results]
        }


    def run_fully_batched_experiment(self, spreadsheet_id: str, chart_ids: list[int],
                                      linking_mode: str = "LINKED") -> dict:
        """
        Maximum batching: one batchUpdate for all slides + charts.
        """
        print(f"\n--- Fully Batched ({len(chart_ids)} charts): {linking_mode} mode ---")
        self.results = []

        total_start = time.perf_counter()

        # Step 1: Create presentation
        pres_id = self.create_presentation()
        if not pres_id:
            return {"success": False, "error": "Failed to create presentation"}

        # Step 2: Build all requests in one batch
        # Create slides and insert charts in a single batchUpdate
        requests = []
        slide_ids = []
        chart_obj_ids = []

        for i, chart_id in enumerate(chart_ids):
            slide_id = f"slide_{i}_{int(time.time())}"
            chart_obj_id = f"chart_{i}_{int(time.time())}"
            slide_ids.append(slide_id)
            chart_obj_ids.append(chart_obj_id)

            # Create slide
            requests.append({
                "createSlide": {
                    "objectId": slide_id,
                    "insertionIndex": i
                }
            })

            # Insert chart onto that slide
            requests.append({
                "createSheetsChart": {
                    "objectId": chart_obj_id,
                    "spreadsheetId": spreadsheet_id,
                    "chartId": chart_id,
                    "linkingMode": linking_mode,
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {
                            "width": {"magnitude": 4000000, "unit": "EMU"},
                            "height": {"magnitude": 3000000, "unit": "EMU"},
                        },
                        "transform": {
                            "scaleX": 1, "scaleY": 1,
                            "translateX": 100000, "translateY": 100000,
                            "unit": "EMU"
                        }
                    }
                }
            })

        # Execute all at once
        start = time.perf_counter()
        try:
            response = self.slides_service.presentations().batchUpdate(
                presentationId=pres_id,
                body={"requests": requests}
            ).execute()
            duration_ms = (time.perf_counter() - start) * 1000

            # Verify all succeeded
            replies = response.get("replies", [])
            if len(replies) != len(requests):
                self.log(TimingResult("batch_create_all", duration_ms, False,
                                      f"Expected {len(requests)} replies, got {len(replies)}"))
                self.cleanup()
                return {"success": False, "error": "Batch incomplete"}

            self.log(TimingResult("batch_create_all", duration_ms, True,
                                  f"{len(chart_ids)} slides + charts"))
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("batch_create_all", duration_ms, False, str(e)[:200]))
            self.cleanup()
            return {"success": False, "error": str(e)[:100]}

        # Step 3: Fetch all contentUrls
        start = time.perf_counter()
        try:
            pres = self.slides_service.presentations().get(presentationId=pres_id).execute()
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("get_all_content_urls", duration_ms, True, f"{len(chart_ids)} charts"))
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            self.log(TimingResult("get_all_content_urls", duration_ms, False, str(e)))
            self.cleanup()
            return {"success": False, "error": "Failed to get contentUrls"}

        # Extract contentUrls
        content_urls = []
        for slide in pres.get("slides", []):
            for elem in slide.get("pageElements", []):
                if elem.get("objectId") in chart_obj_ids:
                    url = elem.get("sheetsChart", {}).get("contentUrl")
                    if not url:
                        url = elem.get("image", {}).get("contentUrl")
                    if url:
                        content_urls.append(url)

        if len(content_urls) != len(chart_ids):
            self.cleanup()
            return {"success": False, "error": f"Only found {len(content_urls)}/{len(chart_ids)} contentUrls"}

        # Step 4: Fetch all PNGs
        png_sizes = []
        for url in content_urls:
            png_data = self.fetch_png(url)
            if not png_data:
                self.cleanup()
                return {"success": False, "error": "Failed to fetch a PNG"}
            png_sizes.append(len(png_data))

        # Step 5: Delete presentation
        self.delete_presentation(pres_id)
        self.presentation_id = None

        total_ms = (time.perf_counter() - total_start) * 1000

        return {
            "success": True,
            "total_ms": total_ms,
            "chart_count": len(chart_ids),
            "linking_mode": linking_mode,
            "png_sizes": png_sizes,
            "steps": [{"op": r.operation, "ms": r.duration_ms, "ok": r.success}
                      for r in self.results]
        }


def main():
    print("=" * 60)
    print("CHART RENDERING TIMING EXPERIMENTS")
    print("=" * 60)

    exp = ChartTimingExperiment()

    # First, find charts in the test spreadsheet
    print(f"\nFetching charts from test spreadsheet...")
    charts = get_charts_in_spreadsheet(exp.sheets_service, TEST_SPREADSHEET_ID)

    if not charts:
        print("ERROR: No charts found in test spreadsheet!")
        print(f"  Spreadsheet: {TEST_SPREADSHEET_ID}")
        return

    print(f"Found {len(charts)} charts:")
    for c in charts:
        print(f"  - {c['chart_id']}: {c['title']} (sheet: {c['sheet_name']}, type: {c['sheet_type']})")

    first_chart_id = charts[0]["chart_id"]
    all_chart_ids = [c["chart_id"] for c in charts]

    # Experiment 1: Single chart, LINKED mode
    result1 = exp.run_single_chart_experiment(TEST_SPREADSHEET_ID, first_chart_id, "LINKED")

    # Experiment 2: Single chart, NOT_LINKED_IMAGE mode
    result2 = exp.run_single_chart_experiment(TEST_SPREADSHEET_ID, first_chart_id, "NOT_LINKED_IMAGE")

    # Experiment 3: Batch all charts, LINKED mode
    if len(charts) > 1:
        result3 = exp.run_batch_experiment(TEST_SPREADSHEET_ID, all_chart_ids, "LINKED")
    else:
        result3 = {"skipped": True, "reason": "Only 1 chart"}

    # Experiment 4: Batch all charts, NOT_LINKED_IMAGE mode
    if len(charts) > 1:
        result4 = exp.run_batch_experiment(TEST_SPREADSHEET_ID, all_chart_ids, "NOT_LINKED_IMAGE")
    else:
        result4 = {"skipped": True, "reason": "Only 1 chart"}

    # Experiment 5: Fully batched (slides + charts in one call)
    if len(charts) > 1:
        result5 = exp.run_fully_batched_experiment(TEST_SPREADSHEET_ID, all_chart_ids, "LINKED")
    else:
        result5 = {"skipped": True, "reason": "Only 1 chart"}

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    def summarize(name: str, result: dict):
        if result.get("skipped"):
            print(f"\n{name}: SKIPPED ({result.get('reason')})")
            return
        if not result.get("success"):
            print(f"\n{name}: FAILED ({result.get('error')})")
            return

        print(f"\n{name}:")
        print(f"  Total: {result['total_ms']:.0f}ms")
        if "chart_count" in result:
            per_chart = result['total_ms'] / result['chart_count']
            print(f"  Per chart: {per_chart:.0f}ms")

        # Break down by operation type
        steps = result.get("steps", [])
        by_op = {}
        for s in steps:
            op = s["op"].replace("_LINKED", "").replace("_NOT_LINKED_IMAGE", "")
            by_op[op] = by_op.get(op, 0) + s["ms"]

        print("  Breakdown:")
        for op, ms in by_op.items():
            print(f"    {op}: {ms:.0f}ms")

    summarize("Single LINKED", result1)
    summarize("Single NOT_LINKED_IMAGE", result2)
    summarize("Batch LINKED", result3)
    summarize("Batch NOT_LINKED_IMAGE", result4)
    summarize("Fully Batched LINKED", result5)

    # Comparison
    if result1.get("success") and result2.get("success"):
        diff = result1["total_ms"] - result2["total_ms"]
        faster = "NOT_LINKED_IMAGE" if diff > 0 else "LINKED"
        print(f"\n→ Single chart: {faster} is {abs(diff):.0f}ms faster")

    if result3.get("success") and result4.get("success"):
        diff = result3["total_ms"] - result4["total_ms"]
        faster = "NOT_LINKED_IMAGE" if diff > 0 else "LINKED"
        print(f"→ Batch: {faster} is {abs(diff):.0f}ms faster")

    # Cleanup on error
    exp.cleanup()


if __name__ == "__main__":
    main()
