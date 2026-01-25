"""
CDP adapter — Chrome DevTools Protocol utilities.

Provides cookie access via chrome-debug browser session.
Falls back gracefully if CDP is not available.
"""

import asyncio
import json
import urllib.request
from typing import Any

# Optional websockets import — only needed if CDP is available
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False


CDP_PORT = 9222
CDP_TIMEOUT = 5  # seconds


def is_cdp_available() -> bool:
    """
    Check if Chrome DevTools Protocol is available.

    Returns True if chrome-debug is running on port 9222.
    """
    if not WEBSOCKETS_AVAILABLE:
        return False

    try:
        url = f"http://localhost:{CDP_PORT}/json"
        with urllib.request.urlopen(url, timeout=CDP_TIMEOUT) as response:
            pages = json.loads(response.read())
            return len(pages) > 0
    except Exception:
        return False


async def _get_cookies_async(urls: list[str]) -> list[dict[str, Any]]:
    """Get cookies via CDP WebSocket connection."""
    # Get WebSocket URL from CDP
    pages_url = f"http://localhost:{CDP_PORT}/json"
    with urllib.request.urlopen(pages_url, timeout=CDP_TIMEOUT) as response:
        pages = json.loads(response.read())

    if not pages:
        return []

    ws_url = pages[0]["webSocketDebuggerUrl"]

    async with websockets.connect(ws_url) as ws:
        # Request cookies for specified URLs
        await ws.send(json.dumps({
            "id": 1,
            "method": "Network.getCookies",
            "params": {"urls": urls}
        }))

        response = json.loads(await asyncio.wait_for(ws.recv(), timeout=CDP_TIMEOUT))
        cookies: list[dict[str, Any]] = response.get("result", {}).get("cookies", [])
        return cookies


def get_google_cookies() -> dict[str, str] | None:
    """
    Get Google authentication cookies from chrome-debug session.

    Returns:
        Dict of cookie name -> value, or None if CDP not available.
        Includes SAPISID and session cookies needed for GenAI API.
    """
    if not is_cdp_available():
        return None

    try:
        urls = [
            "https://drive.google.com",
            "https://www.google.com",
            "https://appsgenaiserver-pa.clients6.google.com"
        ]
        cookies = asyncio.run(_get_cookies_async(urls))

        # Convert to dict
        return {c["name"]: c["value"] for c in cookies}

    except Exception:
        return None


def get_sapisid() -> str | None:
    """
    Get SAPISID cookie value for GenAI authentication.

    Returns:
        SAPISID value or None if not available.
    """
    cookies = get_google_cookies()
    if cookies:
        return cookies.get("SAPISID")
    return None
