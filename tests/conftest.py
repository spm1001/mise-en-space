"""
Shared pytest fixtures for mise-en-space tests.

Fixtures are loaded from the fixtures/ directory at project root.
JSON is converted to typed dataclasses for type safety.
"""

import json
from pathlib import Path

import pytest

from models import (
    SpreadsheetData, SheetTab,
    DocData, DocTab,
    GmailThreadData, EmailMessage, EmailAttachment,
    PresentationData,
)
from extractors.slides import parse_presentation
from datetime import datetime

# Project root for fixture loading
PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = PROJECT_ROOT / "fixtures"


def load_fixture(category: str, name: str) -> dict:
    """
    Load a JSON fixture by category and name.

    Args:
        category: Subdirectory (sheets, docs, gmail, slides)
        name: Fixture name without extension

    Example:
        load_fixture("sheets", "basic")  # loads fixtures/sheets/basic.json
    """
    fixture_path = FIXTURES_DIR / category / f"{name}.json"
    with open(fixture_path) as f:
        return json.load(f)


# ============================================================================
# Sheets Fixtures
# ============================================================================

@pytest.fixture
def sheets_response() -> SpreadsheetData:
    """Sample Google Sheets data for testing."""
    raw = load_fixture("sheets", "basic")
    return SpreadsheetData(
        title=raw["title"],
        spreadsheet_id="test-spreadsheet-id",
        sheets=[
            SheetTab(name=s["name"], values=s["values"])
            for s in raw["sheets"]
        ],
    )


# ============================================================================
# Docs Fixtures
# ============================================================================

@pytest.fixture
def docs_response() -> DocData:
    """Sample Google Docs data for testing."""
    raw = load_fixture("docs", "basic")
    return DocData(
        title=raw["title"],
        document_id=raw["document_id"],
        tabs=[
            DocTab(
                title=t["title"],
                tab_id=t["tab_id"],
                index=t["index"],
                body=t["body"],
                footnotes=t.get("footnotes", {}),
                lists=t.get("lists", {}),
                inline_objects=t.get("inline_objects", {}),
            )
            for t in raw["tabs"]
        ],
    )


# ============================================================================
# Gmail Fixtures
# ============================================================================

@pytest.fixture
def gmail_thread_response() -> GmailThreadData:
    """Sample Gmail thread data for testing."""
    raw = load_fixture("gmail", "thread")
    return GmailThreadData(
        thread_id=raw["thread_id"],
        subject=raw["subject"],
        messages=[
            EmailMessage(
                message_id=m["message_id"],
                from_address=m["from_address"],
                to_addresses=m["to_addresses"],
                cc_addresses=m.get("cc_addresses", []),
                subject=m.get("subject", ""),
                date=datetime.fromisoformat(m["date"].replace("Z", "+00:00")) if m.get("date") else None,
                body_text=m.get("body_text"),
                body_html=m.get("body_html"),
                attachments=[
                    EmailAttachment(
                        filename=a["filename"],
                        mime_type=a["mime_type"],
                        size=a["size"],
                        attachment_id=a["attachment_id"],
                    )
                    for a in m.get("attachments", [])
                ],
                drive_links=m.get("drive_links", []),
            )
            for m in raw["messages"]
        ],
    )


# ============================================================================
# Real API Response Fixtures (captured from Google APIs)
# ============================================================================

@pytest.fixture
def real_docs_multi_tab() -> DocData:
    """Real Google Docs response with multiple tabs."""
    raw = load_fixture("docs", "real_multi_tab")
    tabs = raw.get("tabs", [])
    return DocData(
        title=raw.get("title", ""),
        document_id=raw.get("documentId", ""),
        tabs=[
            DocTab(
                title=t.get("tabProperties", {}).get("title", f"Tab {i}"),
                tab_id=t.get("tabProperties", {}).get("tabId", f"t{i}"),
                index=t.get("tabProperties", {}).get("index", i),
                body=t.get("documentTab", {}).get("body", {}),
                footnotes=t.get("documentTab", {}).get("footnotes", {}),
                lists=t.get("documentTab", {}).get("lists", {}),
                inline_objects=t.get("documentTab", {}).get("inlineObjects", {}),
            )
            for i, t in enumerate(tabs)
        ],
    )


@pytest.fixture
def real_sheets() -> SpreadsheetData:
    """Real Google Sheets response."""
    raw = load_fixture("sheets", "real_spreadsheet")
    return SpreadsheetData(
        title=raw.get("title", ""),
        spreadsheet_id=raw.get("spreadsheet_id", ""),
        sheets=[
            SheetTab(name=s["name"], values=s["values"])
            for s in raw.get("sheets", [])
        ],
        locale=raw.get("locale"),
        time_zone=raw.get("time_zone"),
    )


@pytest.fixture
def real_gmail_thread() -> GmailThreadData:
    """Real Gmail thread response (sanitized)."""
    raw = load_fixture("gmail", "real_thread")
    # Parse from raw API format
    messages = []
    for msg in raw.get("messages", []):
        # Extract headers
        headers = {}
        payload = msg.get("payload", {})
        for h in payload.get("headers", []):
            headers[h["name"]] = h["value"]

        messages.append(EmailMessage(
            message_id=msg.get("id", ""),
            from_address=headers.get("From", ""),
            to_addresses=[headers.get("To", "")],
            cc_addresses=[headers.get("Cc", "")] if headers.get("Cc") else [],
            subject=headers.get("Subject", ""),
            date=None,  # Would need parsing
            body_text=None,  # Extracted by adapter
            body_html=None,  # Extracted by adapter
            attachments=[],
            drive_links=[],
        ))

    return GmailThreadData(
        thread_id=raw.get("id", ""),
        subject=messages[0].subject if messages else "",
        messages=messages,
    )


# ============================================================================
# Slides Fixtures
# ============================================================================

@pytest.fixture
def real_slides() -> PresentationData:
    """Real Google Slides response."""
    raw = load_fixture("slides", "real_presentation")
    return parse_presentation(raw)
