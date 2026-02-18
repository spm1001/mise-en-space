# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "websockets>=12.0",
#     "httpx>=0.27",
# ]
# ///
"""
Benchmark PDF page rendering via Chrome DevTools Protocol.

Opens PDF as file:// URL, uses CDP to screenshot each page.
Chrome's PDF viewer renders one page at a time — we scroll through.
"""
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import httpx
import websockets


async def get_ws_url() -> str:
    """Get Chrome's WebSocket debug URL."""
    resp = httpx.get("http://localhost:9222/json/version")
    return resp.json()["webSocketDebuggerUrl"]


async def send_cmd(ws, method: str, params: dict | None = None) -> dict:
    """Send CDP command and wait for response."""
    msg_id = id(method) % 100000
    payload = {"id": msg_id, "method": method}
    if params:
        payload["params"] = params
    await ws.send(json.dumps(payload))

    while True:
        resp = json.loads(await ws.recv())
        if resp.get("id") == msg_id:
            return resp.get("result", {})


async def render_pdf_pages(pdf_path: str, max_pages: int = 50) -> list[dict]:
    """Render PDF pages via Chrome CDP. Returns list of {page, bytes, size, time_ms}."""
    ws_url = await get_ws_url()

    # Create a new target (tab)
    resp = httpx.put(
        "http://localhost:9222/json/new",
        params={"url": f"file://{pdf_path}"},
    )
    target = resp.json()
    target_ws = target["webSocketDebuggerUrl"]

    results = []
    async with websockets.connect(target_ws, max_size=50 * 1024 * 1024) as ws:
        await send_cmd(ws, "Page.enable")

        # Wait for PDF to load
        await asyncio.sleep(1.0)

        # Get page dimensions
        layout = await send_cmd(ws, "Page.getLayoutMetrics")
        viewport_h = layout.get("cssVisualViewport", {}).get("clientHeight", 800)

        # Get the PDF page count via JavaScript
        page_count_result = await send_cmd(ws, "Runtime.evaluate", {
            "expression": "document.querySelector('embed')?.getAttribute('page-count') || document.querySelectorAll('.page').length || 0",
            "returnByValue": True,
        })

        # Try to get page count from PDF viewer
        # Chrome's PDF viewer uses an embed element
        pc_result = await send_cmd(ws, "Runtime.evaluate", {
            "expression": """
                (function() {
                    // Try PDF viewer API
                    const embed = document.querySelector('embed[type="application/pdf"]');
                    if (embed) {
                        // Chrome PDF viewer doesn't expose page count easily via DOM
                        // But we can check the scroll height vs viewport
                        return -1;  // Signal to use scroll-based approach
                    }
                    return 0;
                })()
            """,
            "returnByValue": True,
        })

        # Scroll-based approach: screenshot at each "page" position
        # First get total document height
        scroll_result = await send_cmd(ws, "Runtime.evaluate", {
            "expression": "document.documentElement.scrollHeight || document.body.scrollHeight",
            "returnByValue": True,
        })
        total_height = scroll_result.get("result", {}).get("value", 0)

        if total_height <= 0:
            # Fallback — just screenshot what we see
            total_height = viewport_h

        # Estimate page count from scroll height
        # Chrome PDF viewer: each page is roughly viewport height
        est_pages = max(1, min(max_pages, round(total_height / viewport_h)))

        print(f"  Viewport: {viewport_h}px, Total scroll: {total_height}px, Est pages: {est_pages}")

        for i in range(est_pages):
            # Scroll to page position
            scroll_y = i * viewport_h
            await send_cmd(ws, "Runtime.evaluate", {
                "expression": f"window.scrollTo(0, {scroll_y})",
            })
            await asyncio.sleep(0.1)  # Let render settle

            start = time.perf_counter()
            screenshot = await send_cmd(ws, "Page.captureScreenshot", {
                "format": "png",
                "clip": {
                    "x": 0,
                    "y": 0,
                    "width": layout.get("cssVisualViewport", {}).get("clientWidth", 800),
                    "height": viewport_h,
                    "scale": 1,
                },
            })
            elapsed = time.perf_counter() - start

            img_bytes = base64.b64decode(screenshot["data"])
            results.append({
                "page": i + 1,
                "size_kb": len(img_bytes) / 1024,
                "time_ms": round(elapsed * 1000, 1),
            })

    # Close the tab
    httpx.get(f"http://localhost:9222/json/close/{target['id']}")

    return results


async def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not pdf_path:
        print("Usage: uv run --script pdf_thumb_chrome.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = str(Path(pdf_path).resolve())
    print(f"PDF: {pdf_path}")
    print(f"Size: {Path(pdf_path).stat().st_size / 1024:.1f} KB")
    print()

    overall_start = time.perf_counter()
    results = await render_pdf_pages(pdf_path)
    overall_elapsed = time.perf_counter() - overall_start

    print(f"\n  Pages rendered: {len(results)}")
    print(f"  Total time: {overall_elapsed:.2f}s (incl 1s load wait + 0.1s/page settle)")
    if results:
        avg_ms = sum(r["time_ms"] for r in results) / len(results)
        avg_kb = sum(r["size_kb"] for r in results) / len(results)
        print(f"  Avg screenshot: {avg_ms:.1f}ms/page, {avg_kb:.1f}KB/page")
        pure_render = sum(r["time_ms"] for r in results)
        print(f"  Pure screenshot time: {pure_render:.0f}ms ({pure_render/len(results):.1f}ms/page)")

        # Show per-page
        print(f"\n  Per-page breakdown:")
        for r in results[:10]:
            print(f"    Page {r['page']}: {r['time_ms']}ms, {r['size_kb']:.1f}KB")
        if len(results) > 10:
            print(f"    ... ({len(results) - 10} more)")


asyncio.run(main())
