"""
Gmail a-family URL resolution via a logged-in Chrome (CDP).

The a-family web tokens (KtbxL…, QgrcJHs… → a:r-N) are client-assigned ids
that reach no public API surface — no arithmetic transform, no drafts.get,
no permmsgid (docs/2026-08-07-gilojo-a-family-verdict.md has the evidence
and the matched pair). The only stable mapping is the one Gmail's own UI
performs when it opens the permalink — so, where a logged-in CDP Chrome
exists, open the URL in a background tab and read the ids Gmail plants in
its rendered DOM (`data-legacy-thread-id`).

Fail-open EVERYWHERE, by design: no CDP endpoint, no websockets package, a
lapsed session (the page bounces to an SSO wall and the attribute never
appears), a timeout — all return None, and the caller falls back to the
teaching error + candidates. This adapter can add a route; it must never
add a failure mode.

Posture matches genai.py/cdp.py: a cookie/browser-dependent bonus, never a
primary path. The primary paths are the deterministic ones in mise-lerulo
(Message-IDs via rfc822msgid:, msg-f Show-original URLs).
"""

import asyncio
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from validation import GMAIL_API_ID_PATTERN

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False


# How long to wait for Gmail's SPA to render the thread. Boot is typically
# 5–8s in a warm profile; the budget covers a cold one. A lapsed session
# never renders the attribute, so this is also the worst-case cost of an
# unauthenticated browser — bounded, then the caller falls back.
RESOLVE_TIMEOUT_S = 15.0
_POLL_INTERVAL_S = 0.5
_HTTP_TIMEOUT_S = 3.0

# The JS Gmail's rendered DOM answers. Returns null while the tab is still
# booting (about:blank has no http href), "AUTH_WALL" when it sits on a
# non-Gmail page, and the ids once the thread view exists. AUTH_WALL is only
# believed after several CONSECUTIVE observations (see _drive_tab): a silent
# SSO refresh legitimately bounces through accounts.google.com and back, and
# a single mid-bounce sample must not abort a resolution that was about to
# succeed.
_RESOLVE_JS = """
(() => {
  if (!location.href.startsWith('http')) return null;
  if (!location.host.includes('mail.google.com')) return 'AUTH_WALL';
  const t = document.querySelector('[data-legacy-thread-id]');
  if (!t) return null;
  return JSON.stringify({
    thread_id: t.getAttribute('data-legacy-thread-id'),
    subject: ((document.querySelector('h2.hP') || {}).innerText || '').trim(),
  });
})()
"""

# Consecutive AUTH_WALL observations before giving up — a real SSO wall
# persists; a silent-refresh bounce clears within a poll or two.
_AUTH_WALL_PATIENCE = 5


@dataclass
class BrowserResolution:
    """What the rendered DOM yielded for an otherwise-unresolvable URL."""
    thread_id: str
    subject: str


def _candidate_endpoints() -> list[str]:
    """CDP endpoints to try, first answer wins.

    Env first (MISE_CDP_ENDPOINT, then the estate-conventional PASSE_CDP),
    then the two local defaults: 9223 (a session Chrome) and 9222 (the
    older chrome-debug/tunnel convention cdp.py already uses).
    """
    endpoints = []
    for var in ("MISE_CDP_ENDPOINT", "PASSE_CDP"):
        value = os.environ.get(var, "").strip().rstrip("/")
        if value:
            endpoints.append(value)
    for default in ("http://localhost:9223", "http://localhost:9222"):
        if default not in endpoints:
            endpoints.append(default)
    return endpoints


def _parse_resolution(raw: Any) -> BrowserResolution | None:
    """Validate what the page script returned. None unless a well-formed
    16-hex thread id came back — the DOM is an outside system, so validate
    at the edge and trust nothing past it."""
    if not isinstance(raw, str) or raw == "AUTH_WALL":
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    thread_id = data.get("thread_id") or ""
    if not GMAIL_API_ID_PATTERN.match(thread_id):
        return None
    return BrowserResolution(thread_id=thread_id, subject=data.get("subject") or "")


def _http_json(url: str, method: str = "GET") -> Any:
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:
        body = response.read()
    return json.loads(body) if body.strip() else None


def _live_endpoint() -> str | None:
    for endpoint in _candidate_endpoints():
        try:
            _http_json(f"{endpoint}/json/version")
            return endpoint
        except Exception:
            continue
    return None


async def _drive_tab(ws_url: str, url: str, deadline: float) -> BrowserResolution | None:
    """Navigate the tab to the permalink, then poll until Gmail renders the
    ids, an SSO wall persists, or the deadline passes.

    Navigation happens HERE via Page.navigate, not via /json/new?url=…: the
    DevTools HTTP endpoint takes its URL raw after the '?' (not as a url=
    key-value), a quirk that silently leaves the tab on about:blank when got
    wrong — measured live 2026-08-07. Page.navigate is the documented form.
    """
    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "id": 1, "method": "Page.navigate", "params": {"url": url},
        }))
        msg_id = 1
        auth_wall_streak = 0
        while time.monotonic() < deadline:
            msg_id += 1
            await ws.send(json.dumps({
                "id": msg_id,
                "method": "Runtime.evaluate",
                "params": {"expression": _RESOLVE_JS, "returnByValue": True},
            }))
            # Skip CDP event notifications until our reply arrives.
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                if reply.get("id") == msg_id:
                    break
            value = reply.get("result", {}).get("result", {}).get("value")
            if value == "AUTH_WALL":
                auth_wall_streak += 1
                if auth_wall_streak >= _AUTH_WALL_PATIENCE:
                    return None
            else:
                auth_wall_streak = 0
                resolution = _parse_resolution(value)
                if resolution:
                    return resolution
            await asyncio.sleep(_POLL_INTERVAL_S)
    return None


def resolve_gmail_url_via_browser(
    url: str, timeout_s: float = RESOLVE_TIMEOUT_S
) -> BrowserResolution | None:
    """Open an unresolvable Gmail permalink in a logged-in Chrome and read
    the thread id from the rendered DOM. None on ANY failure — the caller
    always has a fallback and this route must never become a second error.

    The tab is opened in the background (Chrome's /json/new does not steal
    focus) and closed in a finally, so a human watching the browser sees at
    most a brief extra tab.
    """
    if not WEBSOCKETS_AVAILABLE:
        return None
    endpoint = _live_endpoint()
    if endpoint is None:
        return None

    target_id: str | None = None
    try:
        target = _http_json(f"{endpoint}/json/new", method="PUT")
        if not isinstance(target, dict):
            return None
        target_id = target.get("id")
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            return None
        deadline = time.monotonic() + timeout_s
        return asyncio.run(_drive_tab(ws_url, url, deadline))
    except Exception:
        return None
    finally:
        if target_id:
            try:
                _http_json(f"{endpoint}/json/close/{target_id}")
            except Exception:
                pass
