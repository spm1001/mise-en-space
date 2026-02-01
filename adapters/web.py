"""
Web Content Adapter — Fetches web pages for extraction.

Handles HTTP fetching with intelligent fallback:
1. Try fast HTTP fetch
2. Detect if page needs browser rendering (JS-rendered content)
3. Fall back to browser if needed (requires webctl)

This adapter returns raw HTML. Extraction to markdown is done by extractors/web.py.
"""

import re
import subprocess
from functools import lru_cache
from urllib.parse import urlparse

import httpx

from models import MiseError, ErrorKind, WebData

__all__ = [
    "fetch_web_content",
    "is_web_url",
]

# Default timeout for HTTP requests (seconds)
HTTP_TIMEOUT = 30

# User agent that identifies as a reasonable bot
USER_AGENT = "mise-web/1.0 (Claude Code web fetcher; +https://github.com/anthropics)"

# Patterns that suggest JS-rendered content
JS_RENDER_PATTERNS = [
    r'id="__NEXT_DATA__"',  # Next.js
    r'__NUXT__',            # Nuxt
    r'window\.__INITIAL_STATE__',  # SSR hydration
    r'<noscript>.*?enable javascript',  # Explicit JS requirement
]

# Auth detection patterns
PAYWALL_MARKERS = [
    'subscribe to continue',
    'premium content',
    'members only',
    'paywall',
    'subscription required',
    'sign in to continue',
    'create an account',
]

LOGIN_URL_PATTERNS = [
    '/login',
    '/signin',
    '/auth',
    '/sso',
    '/oauth',
]

# Minimum characters for valid extraction
MIN_CONTENT_THRESHOLD = 100


def is_web_url(url: str) -> bool:
    """
    Check if string is a web URL (not a Google ID).

    Args:
        url: String to check

    Returns:
        True if it looks like a web URL
    """
    url = url.strip()
    return url.startswith(('http://', 'https://'))


def _detect_auth_required(response: httpx.Response, html: str) -> str | None:
    """
    Detect if page requires authentication.

    Returns:
        Error message if auth required, None otherwise
    """
    # HTTP 401/403
    if response.status_code == 401:
        return "Authentication required (401)"
    if response.status_code == 403:
        return "Access forbidden (403)"

    # Login redirect
    final_url = str(response.url).lower()
    for pattern in LOGIN_URL_PATTERNS:
        if pattern in final_url:
            return f"Redirected to login page: {response.url}"

    # Soft paywall detection (check HTML content)
    html_lower = html.lower()
    for marker in PAYWALL_MARKERS:
        if marker in html_lower:
            return f"Paywall detected: '{marker}'"

    return None


def _detect_captcha(html: str) -> bool:
    """Check if page shows CAPTCHA challenge."""
    html_lower = html.lower()
    return any(marker in html_lower for marker in [
        'cf-challenge',           # Cloudflare
        'captcha',
        'recaptcha',
        'hcaptcha',
        'challenge-running',      # Cloudflare
        'ddos-protection',
    ])


def _needs_browser_rendering(html: str) -> bool:
    """
    Check if page content suggests JS rendering is needed.

    Args:
        html: Raw HTML content

    Returns:
        True if content appears to need browser rendering
    """
    # Very short content often means JS-rendered
    if len(html.strip()) < 500:
        return True

    # Check for SPA framework patterns
    for pattern in JS_RENDER_PATTERNS:
        if re.search(pattern, html, re.IGNORECASE | re.DOTALL):
            return True

    return False


def _is_webctl_available() -> bool:
    """Check if webctl daemon is available for browser rendering."""
    try:
        result = subprocess.run(
            ['webctl', 'status'],
            capture_output=True,
            text=True,
            timeout=5
        )
        # webctl status returns 0 if daemon is running
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _fetch_with_browser(url: str) -> tuple[str, str]:
    """
    Fetch page using browser rendering via webctl.

    Returns:
        Tuple of (html, final_url)

    Raises:
        MiseError: If browser fetch fails
    """
    try:
        # Ensure daemon is running
        start_result = subprocess.run(
            ['webctl', 'start'],
            capture_output=True,
            text=True,
            timeout=30
        )

        # Navigate with network-idle wait
        nav_result = subprocess.run(
            ['webctl', 'navigate', url, '--wait', 'network-idle', '--timeout', '30000'],
            capture_output=True,
            text=True,
            timeout=45
        )
        if nav_result.returncode != 0:
            raise MiseError(
                ErrorKind.NETWORK_ERROR,
                f"Browser navigation failed: {nav_result.stderr}"
            )

        # Get rendered HTML
        html_result = subprocess.run(
            ['webctl', 'eval', 'document.documentElement.outerHTML'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if html_result.returncode != 0:
            raise MiseError(
                ErrorKind.EXTRACTION_FAILED,
                f"Failed to get rendered HTML: {html_result.stderr}"
            )

        # Get final URL (after redirects)
        url_result = subprocess.run(
            ['webctl', 'eval', 'window.location.href'],
            capture_output=True,
            text=True,
            timeout=5
        )
        final_url = url_result.stdout.strip() if url_result.returncode == 0 else url

        return (html_result.stdout, final_url)

    except subprocess.TimeoutExpired:
        raise MiseError(ErrorKind.TIMEOUT, f"Browser rendering timed out for {url}")
    except FileNotFoundError:
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            "webctl not found. Install webctl for browser rendering support."
        )


def fetch_web_content(url: str, use_browser: bool = False) -> WebData:
    """
    Fetch web page content with intelligent fallback.

    Strategy:
    1. Try HTTP fetch (fast path)
    2. Detect if JS-rendered content needs browser
    3. Fall back to browser if available and needed

    Args:
        url: URL to fetch
        use_browser: Force browser rendering (skip HTTP)

    Returns:
        WebData with HTML content ready for extraction

    Raises:
        MiseError: On fetch failure
    """
    # Validate URL
    if not is_web_url(url):
        raise MiseError(
            ErrorKind.INVALID_INPUT,
            f"Invalid URL: {url}. Must start with http:// or https://"
        )

    warnings: list[str] = []
    domain = urlparse(url).netloc

    # === BROWSER PATH (forced or fallback) ===
    if use_browser:
        if not _is_webctl_available():
            raise MiseError(
                ErrorKind.INVALID_INPUT,
                f"Browser rendering requested but webctl is not running. "
                f"Start it with: webctl start"
            )

        html, final_url = _fetch_with_browser(url)
        return WebData(
            url=url,
            html=html,
            final_url=final_url,
            status_code=200,  # Browser always returns rendered page
            content_type='text/html',
            cookies_used=True,  # Browser uses session cookies
            render_method='browser',
            warnings=warnings,
        )

    # === HTTP PATH (fast) ===
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(HTTP_TIMEOUT),
        ) as client:
            response = client.get(
                url,
                headers={
                    'User-Agent': USER_AGENT,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                }
            )

    except httpx.TimeoutException:
        raise MiseError(ErrorKind.TIMEOUT, f"Request timed out: {url}")
    except httpx.ConnectError as e:
        raise MiseError(ErrorKind.NETWORK_ERROR, f"Connection failed: {url} - {e}")
    except httpx.RequestError as e:
        raise MiseError(ErrorKind.NETWORK_ERROR, f"Request failed: {url} - {e}")

    html = response.text
    final_url = str(response.url)

    # Track redirect
    if final_url != url:
        warnings.append(f"Redirected: {url} → {final_url}")

    # Check for CAPTCHA
    if _detect_captcha(html):
        raise MiseError(
            ErrorKind.CAPTCHA,
            f"CAPTCHA detected at {final_url}. Needs human intervention."
        )

    # Check for auth requirement
    auth_error = _detect_auth_required(response, html)
    if auth_error:
        raise MiseError(ErrorKind.AUTH_REQUIRED, auth_error)

    # Check if content needs browser rendering
    if _needs_browser_rendering(html):
        warnings.append("Content appears JS-rendered")

        if _is_webctl_available():
            warnings.append("Falling back to browser rendering")
            html, final_url = _fetch_with_browser(url)
            return WebData(
                url=url,
                html=html,
                final_url=final_url,
                status_code=200,
                content_type='text/html',
                cookies_used=True,
                render_method='browser',
                warnings=warnings,
            )
        else:
            warnings.append(
                "Browser rendering recommended but webctl not available. "
                "Content may be incomplete."
            )

    # Check for rate limiting
    if response.status_code == 429:
        raise MiseError(
            ErrorKind.RATE_LIMITED,
            f"Rate limited by {domain}. Try again later.",
            retryable=True
        )

    # Check for server errors
    if response.status_code >= 500:
        raise MiseError(
            ErrorKind.NETWORK_ERROR,
            f"Server error ({response.status_code}) from {domain}"
        )

    # Check for not found
    if response.status_code == 404:
        raise MiseError(ErrorKind.NOT_FOUND, f"Page not found: {url}")

    return WebData(
        url=url,
        html=html,
        final_url=final_url,
        status_code=response.status_code,
        content_type=response.headers.get('content-type', ''),
        cookies_used=False,
        render_method='http',
        warnings=warnings,
    )
