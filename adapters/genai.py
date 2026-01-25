"""
GenAI adapter — Google's internal GenAI API for video summaries.

Leverages pre-computed AI summaries for Drive videos.
Requires browser cookies via CDP (chrome-debug).

IMPORTANT: This uses an UNDOCUMENTED internal Google endpoint:
    https://appsgenaiserver-pa.clients6.google.com/v1/genai/streamGenerate

This endpoint:
- Is NOT part of any public Google API
- Has NO OAuth access — requires browser session cookies
- May change or break without notice
- Was reverse-engineered from Chrome network logs (Jan 2026)

The request format is protobuf-as-JSON (not standard REST). The response is
a streaming protobuf that builds up incrementally — the same text may appear
multiple times at different completion stages.

If this breaks, check:
- skill-chrome-log/references/DRIVE_VIDEO_SUMMARY_HOWTO.md for updated docs
- Chrome network logs for current request format
"""

import hashlib
import json
import os
import random
import re
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any

from adapters.cdp import get_google_cookies


# GenAI endpoint (internal Google API)
GENAI_ENDPOINT = "https://appsgenaiserver-pa.clients6.google.com/v1/genai/streamGenerate"

# API key loaded from environment - DO NOT HARDCODE
# Get from Chrome network logs: drive.google.com video page → filter "streamGenerate"
GENAI_API_KEY = os.environ.get("GENAI_API_KEY", "")


@dataclass
class VideoSummary:
    """Video summary result."""
    summary: str
    transcript_snippets: list[str]
    has_content: bool
    error: str | None = None  # Set if there was an error (e.g., stale cookies)


def _compute_sapisidhash(sapisid: str, origin: str = "https://drive.google.com") -> str:
    """
    Compute SAPISIDHASH authentication header.

    Google's internal auth scheme: timestamp + SHA1(timestamp + sapisid + origin)
    """
    timestamp = int(time.time())
    hash_input = f"{timestamp} {sapisid} {origin}"
    hash_value = hashlib.sha1(hash_input.encode()).hexdigest()
    return f"{timestamp}_{hash_value}"


def _build_request_body(file_id: str) -> list[Any]:
    """Build protobuf-as-JSON request body for GenAI API."""
    request_id = f"goog_{random.randint(-999999999, -1)}"
    return [
        [134, None, [
            24, None, None, None, request_id,
            [None, None, None, None, None, None, [0]], "0", None,
            [None, None, None, [[[None, None, None, None, None, None, None,
                [None, None, [None, None, None, [file_id]]]]]]],
            None, None, [29], None, "en-GB",
            None, None, None, None, None, 0,
            None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None,
            None, None, 0
        ], None, None, [1], 1],
        [1, None, 1]
    ]


def _extract_summary_text(response_text: str) -> tuple[str, list[str]]:
    """
    Extract summary and transcript snippets from GenAI response.

    Returns:
        Tuple of (summary, transcript_snippets)
    """
    summary = ""
    snippets: list[str] = []

    # Look for summary text (sentences starting with common patterns)
    summary_patterns = [
        r'This video[^"\\]{50,500}',
        r'The video[^"\\]{50,500}',
    ]

    for pattern in summary_patterns:
        matches = re.findall(pattern, response_text)
        if matches:
            summary = max(matches, key=len)
            break

    # Look for transcript-like snippets (substantial text blocks)
    seen_snippets: set[str] = set()
    text_matches = re.findall(r'"([A-Z][a-z][^"\\]{80,300})"', response_text)
    for match in text_matches:
        # Skip if it looks like the summary we already found
        if summary and match in summary:
            continue
        # Skip code-like or structured content
        if any(x in match for x in ['{', '}', 'http', 'null', '\\']):
            continue
        # Skip duplicates
        if match in seen_snippets:
            continue
        seen_snippets.add(match)
        snippets.append(match)

    return summary, snippets[:5]  # Limit snippets


def get_video_summary(file_id: str) -> VideoSummary | None:
    """
    Get AI-generated summary for a Google Drive video.

    Requires:
    - GENAI_API_KEY env var (get from Chrome network logs on drive.google.com)
    - chrome-debug running with an authenticated Google session

    Args:
        file_id: Drive file ID of the video

    Returns:
        VideoSummary with summary and transcript snippets, or None if unavailable.
    """
    # Check API key is configured
    if not GENAI_API_KEY:
        return None

    # Get cookies from browser session
    cookies = get_google_cookies()
    if not cookies:
        return None

    sapisid = cookies.get("SAPISID")
    if not sapisid:
        return None

    # Compute auth header (all three variants use same hash)
    hash_value = _compute_sapisidhash(sapisid)
    auth_header = (
        f"SAPISIDHASH {hash_value} "
        f"SAPISID1PHASH {hash_value} "
        f"SAPISID3PHASH {hash_value}"
    )

    # Build cookie header
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

    # Build request
    url = f"{GENAI_ENDPOINT}?key={GENAI_API_KEY}"
    body = _build_request_body(file_id)

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json+protobuf",
            "Authorization": auth_header,
            "X-Goog-AuthUser": "0",
            "Referer": "https://drive.google.com/",
            "Origin": "https://drive.google.com",
            "Cookie": cookie_header,
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            text = response.read().decode()

            if len(text) < 100:
                # Empty or minimal response — video may not have summary
                return VideoSummary(
                    summary="",
                    transcript_snippets=[],
                    has_content=False,
                )

            summary, snippets = _extract_summary_text(text)
            return VideoSummary(
                summary=summary,
                transcript_snippets=snippets,
                has_content=bool(summary or snippets),
            )

    except urllib.error.HTTPError as e:
        if e.code == 401:
            # Stale cookies — browser session may have expired
            return VideoSummary(
                summary="",
                transcript_snippets=[],
                has_content=False,
                error="stale_cookies",
            )
        elif e.code == 403:
            # Permission denied — may not have access to video
            return VideoSummary(
                summary="",
                transcript_snippets=[],
                has_content=False,
                error="permission_denied",
            )
        # Other errors (404 = video not processed, etc.)
        return None
    except Exception:
        return None


# Video MIME type patterns
VIDEO_MIME_PREFIXES = ("video/",)
AUDIO_MIME_PREFIXES = ("audio/",)


def is_video_file(mime_type: str) -> bool:
    """Check if MIME type is a video."""
    return mime_type.startswith(VIDEO_MIME_PREFIXES)


def is_audio_file(mime_type: str) -> bool:
    """Check if MIME type is audio."""
    return mime_type.startswith(AUDIO_MIME_PREFIXES)


def is_media_file(mime_type: str) -> bool:
    """Check if MIME type is video or audio."""
    return is_video_file(mime_type) or is_audio_file(mime_type)
