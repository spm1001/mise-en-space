"""
Charts adapter — Render Sheets charts as PNGs via Slides API.

The Sheets API has no direct chart export. The standard workaround:
1. Create temporary Slides presentation
2. Embed charts via createSheetsChart
3. Fetch contentUrl for rendered PNG
4. Download PNGs
5. Delete presentation

Benchmarks (Jan 2026):
- Create presentation: ~3s (fixed overhead)
- Per chart: ~2s insert + ~0.3s fetch PNG
- Fully batched (slides+charts in one batchUpdate) is ~20% faster

Use LINKED mode (not NOT_LINKED_IMAGE) - counterintuitively faster.

Benchmarked Jan 2026: LINKED is ~10-15% faster than NOT_LINKED_IMAGE.
Theory: NOT_LINKED_IMAGE does extra work to "freeze" the chart as a static
image, while LINKED just renders and links. The contentUrl works either way.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests  # type: ignore[import-untyped]

from models import ChartData
from adapters.services import get_slides_service, get_drive_service


def get_charts_from_spreadsheet(spreadsheet_response: dict[str, Any]) -> list[ChartData]:
    """
    Extract chart metadata from a spreadsheet().get() response.

    Args:
        spreadsheet_response: Response from sheets.spreadsheets().get()

    Returns:
        List of ChartData with metadata (no PNG yet)
    """
    charts: list[ChartData] = []

    for sheet in spreadsheet_response.get("sheets", []):
        sheet_props = sheet.get("properties", {})
        sheet_name = sheet_props.get("title", "")

        for chart in sheet.get("charts", []):
            chart_id = chart.get("chartId")
            if not chart_id:
                continue

            # Extract chart spec
            spec = chart.get("spec", {})
            title = spec.get("title")

            # Try to determine chart type from spec
            chart_type = None
            if "basicChart" in spec:
                chart_type = spec["basicChart"].get("chartType")
            elif "pieChart" in spec:
                chart_type = "PIE"
            elif "histogramChart" in spec:
                chart_type = "HISTOGRAM"

            charts.append(ChartData(
                chart_id=chart_id,
                title=title,
                sheet_name=sheet_name,
                chart_type=chart_type,
            ))

    return charts


def render_charts_as_pngs(
    spreadsheet_id: str,
    charts: list[ChartData],
    timeout_seconds: int = 60,
) -> tuple[list[ChartData], int]:
    """
    Render charts as PNGs via Slides API.

    Uses the fully-batched approach for efficiency:
    - Single batchUpdate for all slides + chart insertions
    - Single get() to retrieve all contentUrls
    - Parallel PNG fetches

    Args:
        spreadsheet_id: The source spreadsheet ID
        charts: List of ChartData with chart_ids populated
        timeout_seconds: Max time for the entire operation

    Returns:
        Tuple of (charts with png_bytes populated, render_time_ms)
    """
    if not charts:
        return charts, 0

    render_start = time.perf_counter()

    slides_service = get_slides_service()
    drive_service = get_drive_service()

    presentation_id = None

    try:
        # Step 1: Create temporary presentation
        pres = slides_service.presentations().create(
            body={"title": f"mise-chart-render-{int(time.time())}"}
        ).execute()
        presentation_id = pres.get("presentationId")

        if not presentation_id:
            raise RuntimeError("Failed to create presentation")

        # Step 2: Build batched requests for all slides + charts
        requests_batch: list[dict[str, Any]] = []
        slide_ids: list[str] = []
        chart_obj_ids: list[str] = []

        for i, chart in enumerate(charts):
            slide_id = f"slide_{i}_{int(time.time())}"
            chart_obj_id = f"chart_{i}_{int(time.time())}"
            slide_ids.append(slide_id)
            chart_obj_ids.append(chart_obj_id)

            # Create slide
            requests_batch.append({
                "createSlide": {
                    "objectId": slide_id,
                    "insertionIndex": i
                }
            })

            # Insert chart
            requests_batch.append({
                "createSheetsChart": {
                    "objectId": chart_obj_id,
                    "spreadsheetId": spreadsheet_id,
                    "chartId": chart.chart_id,
                    "linkingMode": "LINKED",  # Faster than NOT_LINKED_IMAGE
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {
                            "width": {"magnitude": 6000000, "unit": "EMU"},
                            "height": {"magnitude": 4000000, "unit": "EMU"},
                        },
                        "transform": {
                            "scaleX": 1, "scaleY": 1,
                            "translateX": 0, "translateY": 0,
                            "unit": "EMU"
                        }
                    }
                }
            })

        # Execute all slide+chart creations in one call
        slides_service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": requests_batch}
        ).execute()

        # Step 3: Fetch presentation to get all contentUrls
        pres_data = slides_service.presentations().get(
            presentationId=presentation_id
        ).execute()

        # Map object IDs to contentUrls
        obj_id_to_url: dict[str, str] = {}
        for slide in pres_data.get("slides", []):
            for elem in slide.get("pageElements", []):
                obj_id = elem.get("objectId")
                if obj_id in chart_obj_ids:
                    url = elem.get("sheetsChart", {}).get("contentUrl")
                    if url:
                        obj_id_to_url[obj_id] = url

        # Step 4: Fetch PNGs in parallel
        def fetch_png(chart_obj_id: str) -> tuple[str, bytes | None]:
            """Fetch a single PNG, return (obj_id, bytes or None)."""
            url = obj_id_to_url.get(chart_obj_id)
            if not url:
                return chart_obj_id, None
            try:
                response = requests.get(url, timeout=30)
                if response.status_code == 200 and len(response.content) > 100:
                    return chart_obj_id, response.content
            except requests.RequestException:
                pass
            return chart_obj_id, None

        # Fetch all PNGs concurrently
        png_results: dict[str, bytes | None] = {}
        with ThreadPoolExecutor(max_workers=min(len(chart_obj_ids), 10)) as executor:
            futures = {executor.submit(fetch_png, obj_id): obj_id for obj_id in chart_obj_ids}
            for future in as_completed(futures):
                obj_id, png_data = future.result()
                png_results[obj_id] = png_data

        # Assign results back to charts
        for chart, obj_id in zip(charts, chart_obj_ids):
            png_data = png_results.get(obj_id)
            if png_data:
                chart.png_bytes = png_data

        render_time_ms = int((time.perf_counter() - render_start) * 1000)
        return charts, render_time_ms

    finally:
        # Always clean up the presentation
        if presentation_id:
            try:
                drive_service.files().delete(fileId=presentation_id).execute()
            except Exception:
                pass  # Best effort cleanup
