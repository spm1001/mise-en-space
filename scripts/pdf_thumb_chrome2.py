# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "websockets>=12.0",
#     "httpx>=0.27",
#     "pymupdf>=1.25.0",
# ]
# ///
"""
Benchmark PDF page rendering via Chrome CDP — per-page navigation approach.

Uses Chrome's PDF viewer with #page=N fragment for each page.
Also uses PyMuPDF just for page count (lightweight metadata read).
"""
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import httpx
import websockets


CDP = "http://localhost:9222"
_cmd_id = 0


async def send(ws, method: str, params: dict | None = None) -> dict:
    global _cmd_id
    _cmd_id += 1
    payload = {"id": _cmd_id, "method": method}
    if params:
        payload["params"] = params
    await ws.send(json.dumps(payload))
    while True:
        resp = json.loads(await ws.recv())
        if resp.get("id") == _cmd_id:
            if "error" in resp:
                raise RuntimeError(f"CDP error: {resp['error']}")
            return resp.get("result", {})


def get_page_count(pdf_path: str) -> int:
    """Get page count without rendering (fast metadata read)."""
    import fitz
    doc = fitz.open(pdf_path)
    count = len(doc)
    doc.close()
    return count


async def render_pages_chrome(pdf_path: str, page_count: int) -> list[dict]:
    """Render each page by navigating to file://path#page=N."""
    # Create new tab
    resp = httpx.put(f"{CDP}/json/new", params={"url": "about:blank"})
    target = resp.json()
    results = []

    try:
        async with websockets.connect(
            target["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024
        ) as ws:
            await send(ws, "Page.enable")

            # Set a reasonable viewport
            await send(ws, "Emulation.setDeviceMetricsOverride", {
                "width": 1200,
                "height": 1600,
                "deviceScaleFactor": 1,
                "mobile": False,
            })

            for i in range(page_count):
                page_num = i + 1
                url = f"file://{pdf_path}#page={page_num}"

                start = time.perf_counter()

                # Navigate to page
                await send(ws, "Page.navigate", {"url": url})
                # Wait for load
                try:
                    while True:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                        if msg.get("method") == "Page.loadEventFired":
                            break
                except asyncio.TimeoutError:
                    pass

                # Brief settle for PDF renderer
                await asyncio.sleep(0.3)

                # Screenshot
                screenshot = await send(ws, "Page.captureScreenshot", {
                    "format": "png",
                    "captureBeyondViewport": False,
                })
                elapsed = time.perf_counter() - start

                img_bytes = base64.b64decode(screenshot["data"])
                results.append({
                    "page": page_num,
                    "size_kb": round(len(img_bytes) / 1024, 1),
                    "time_ms": round(elapsed * 1000, 1),
                })

    finally:
        httpx.get(f"{CDP}/json/close/{target['id']}")

    return results


async def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not pdf_path:
        print("Usage: uv run --script pdf_thumb_chrome2.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = str(Path(pdf_path).resolve())
    page_count = get_page_count(pdf_path)
    max_pages = min(page_count, 10)  # Cap for benchmarking

    print(f"PDF: {Path(pdf_path).name}")
    print(f"Size: {Path(pdf_path).stat().st_size / 1024:.1f} KB, {page_count} pages (testing {max_pages})")
    print()

    overall_start = time.perf_counter()
    results = await render_pages_chrome(pdf_path, max_pages)
    overall_elapsed = time.perf_counter() - overall_start

    if results:
        avg_ms = sum(r["time_ms"] for r in results) / len(results)
        avg_kb = sum(r["size_kb"] for r in results) / len(results)
        print(f"  Pages: {len(results)}")
        print(f"  Total: {overall_elapsed:.2f}s")
        print(f"  Avg: {avg_ms:.0f}ms/page, {avg_kb:.0f}KB/page")
        print()
        for r in results:
            print(f"    Page {r['page']:2d}: {r['time_ms']:6.0f}ms  {r['size_kb']:6.1f}KB")


asyncio.run(main())
