"""
CDP proof-of-concept: fetch rendered HTML from Chrome Debug via DevTools Protocol.

Usage:
    uv run python scripts/cdp_poc.py "https://example.com"
    uv run python scripts/cdp_poc.py "https://drive.google.com"  # auth'd page

Requires Chrome Debug (forage) running on port 9222.
mise-en-space already has websockets as a dependency.
"""

import asyncio
import json
import sys

import websockets


CDP_PORT = 9222
CDP_HOST = "127.0.0.1"


async def fetch_rendered_html(url: str) -> str:
    """Navigate to URL in Chrome Debug and return rendered HTML."""

    # 1. Get available targets
    import urllib.request
    targets_url = f"http://{CDP_HOST}:{CDP_PORT}/json"
    with urllib.request.urlopen(targets_url) as resp:
        targets = json.loads(resp.read())

    # Find a reusable tab or create one
    page_targets = [t for t in targets if t["type"] == "page" and "chrome://" not in t["url"]]

    if page_targets:
        ws_url = page_targets[0]["webSocketDebuggerUrl"]
    else:
        # Create new tab
        new_url = f"http://{CDP_HOST}:{CDP_PORT}/json/new"
        with urllib.request.urlopen(new_url) as resp:
            tab = json.loads(resp.read())
        ws_url = tab["webSocketDebuggerUrl"]

    # 2. Connect via WebSocket and navigate
    msg_id = 0

    async def send(ws, method, params=None):
        nonlocal msg_id
        msg_id += 1
        payload = {"id": msg_id, "method": method, "params": params or {}}
        await ws.send(json.dumps(payload))

        # Wait for matching response (skip events)
        while True:
            raw = await ws.recv()
            data = json.loads(raw)
            if data.get("id") == msg_id:
                return data

    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        # Enable page events
        await send(ws, "Page.enable")

        # Navigate
        result = await send(ws, "Page.navigate", {"url": url})
        if "error" in result:
            raise RuntimeError(f"Navigation failed: {result['error']}")

        # Wait for load event
        while True:
            raw = await ws.recv()
            data = json.loads(raw)
            if data.get("method") == "Page.loadEventFired":
                break

        # Small delay for JS rendering
        await asyncio.sleep(1)

        # 3. Get rendered HTML
        doc = await send(ws, "DOM.getDocument", {"depth": 0})
        root_id = doc["result"]["root"]["nodeId"]

        html = await send(ws, "DOM.getOuterHTML", {"nodeId": root_id})
        return html["result"]["outerHTML"]


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"

    try:
        html = await fetch_rendered_html(url)
        print(f"Fetched {len(html)} chars of rendered HTML from: {url}")
        print(f"Title: ", end="")
        # Quick title extract
        if "<title>" in html:
            title = html.split("<title>")[1].split("</title>")[0]
            print(title)
        else:
            print("(no title)")
        print(f"\nFirst 500 chars:\n{html[:500]}")
    except ConnectionRefusedError:
        print("Chrome Debug not running. Start with: chrome-debug (or forage)")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
