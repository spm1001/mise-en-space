"""
Fetch routing — ID detection and do_fetch entry point.
"""

from pathlib import Path
from typing import Any

from adapters.web import is_web_url
from models import MiseError, FetchResult, FetchError
from validation import extract_drive_file_id, extract_gmail_id, is_gmail_api_id, GMAIL_WEB_ID_PREFIXES

from .gmail import fetch_gmail, fetch_attachment
from .drive import fetch_drive
from .web import fetch_web


def detect_id_type(input_id: str) -> tuple[str, str]:
    """
    Detect whether input is Gmail, Drive, or web URL, and normalize the ID.

    Returns:
        Tuple of (source, normalized_id) where source is 'gmail', 'drive', or 'web'
    """
    input_id = input_id.strip()

    # Gmail URL
    if "mail.google.com" in input_id:
        return ("gmail", extract_gmail_id(input_id))

    # Drive URL (docs, sheets, slides, drive)
    if any(domain in input_id for domain in ["docs.google.com", "sheets.google.com", "slides.google.com", "drive.google.com"]):
        return ("drive", extract_drive_file_id(input_id))

    # Web URL (non-Google HTTP/HTTPS)
    if is_web_url(input_id):
        return ("web", input_id)

    # Gmail API ID (16-char hex)
    if is_gmail_api_id(input_id):
        return ("gmail", input_id)

    # Gmail web ID (FMfcg..., KtbxL..., etc.) — needs conversion
    # Only match known prefixes; is_gmail_web_id fallback is too broad for bare IDs
    if input_id.startswith(GMAIL_WEB_ID_PREFIXES):
        return ("gmail", extract_gmail_id(input_id))

    # Default to Drive
    return ("drive", input_id)


def do_fetch(file_id: str, base_path: Path | None = None, attachment: str | None = None) -> FetchResult | FetchError:
    """
    Main fetch entry point.

    Detects ID type, routes to appropriate fetcher, handles errors.

    Args:
        file_id: Drive file ID, Gmail thread ID, or URL
        base_path: Base directory for deposits (defaults to cwd)
        attachment: Specific attachment filename to extract from Gmail thread
    """
    try:
        # Detect ID type and normalize
        source, normalized_id = detect_id_type(file_id)

        # Single-attachment fetch (Gmail only)
        if attachment:
            if source != "gmail":
                return FetchError(
                    kind="invalid_input",
                    message="attachment parameter only works with Gmail thread/message IDs",
                )
            return fetch_attachment(normalized_id, attachment, base_path=base_path)

        # Route to appropriate fetcher
        if source == "gmail":
            return fetch_gmail(normalized_id, base_path=base_path)
        elif source == "web":
            return fetch_web(normalized_id, base_path=base_path)
        else:
            return fetch_drive(normalized_id, base_path=base_path)

    except MiseError as e:
        return FetchError(kind=e.kind.value, message=e.message)
    except ValueError as e:
        return FetchError(kind="invalid_input", message=str(e))
    except Exception as e:
        return FetchError(kind="unknown", message=str(e))


