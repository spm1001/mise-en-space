"""
Shared pytest fixtures for mise-en-space tests.

Fixtures are loaded from the fixtures/ directory at project root.
JSON is converted to typed dataclasses for type safety.

Adapter mocking infrastructure is also provided here for testing
adapters without hitting real Google APIs.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from models import (
    SpreadsheetData, SheetTab,
    DocData, DocTab,
    GmailThreadData, EmailMessage, EmailAttachment,
    PresentationData,
    FileCommentsData, CommentData, CommentReply,
)
from extractors.slides import parse_presentation
from extractors.gmail import parse_message_payload
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
# Comments Fixtures
# ============================================================================

@pytest.fixture
def comments_response() -> FileCommentsData:
    """Sample file comments data for testing."""
    raw = load_fixture("comments", "basic")
    return FileCommentsData(
        file_id=raw["file_id"],
        file_name=raw["file_name"],
        comments=[
            CommentData(
                id=c["id"],
                content=c["content"],
                author_name=c["author_name"],
                author_email=c.get("author_email"),
                created_time=c.get("created_time"),
                modified_time=c.get("modified_time"),
                resolved=c.get("resolved", False),
                quoted_text=c.get("quoted_text", ""),
                mentioned_emails=c.get("mentioned_emails", []),
                replies=[
                    CommentReply(
                        id=r["id"],
                        content=r["content"],
                        author_name=r["author_name"],
                        author_email=r.get("author_email"),
                        created_time=r.get("created_time"),
                        modified_time=r.get("modified_time"),
                        mentioned_emails=r.get("mentioned_emails", []),
                    )
                    for r in c.get("replies", [])
                ],
            )
            for c in raw["comments"]
        ],
    )


@pytest.fixture
def real_comments_response() -> FileCommentsData:
    """Real comments data with edge cases: rich text, resolved, long anchor, multi-mention, empty reply."""
    raw = load_fixture("comments", "real_comments")
    return FileCommentsData(
        file_id=raw["file_id"],
        file_name=raw["file_name"],
        comments=[
            CommentData(
                id=c["id"],
                content=c["content"],
                author_name=c["author_name"],
                author_email=c.get("author_email"),
                created_time=c.get("created_time"),
                modified_time=c.get("modified_time"),
                resolved=c.get("resolved", False),
                quoted_text=c.get("quoted_text", ""),
                mentioned_emails=c.get("mentioned_emails", []),
                replies=[
                    CommentReply(
                        id=r["id"],
                        content=r["content"],
                        author_name=r["author_name"],
                        author_email=r.get("author_email"),
                        created_time=r.get("created_time"),
                        modified_time=r.get("modified_time"),
                        mentioned_emails=r.get("mentioned_emails", []),
                    )
                    for r in c.get("replies", [])
                ],
            )
            for c in raw["comments"]
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

        body_text, body_html = parse_message_payload(payload)

        messages.append(EmailMessage(
            message_id=msg.get("id", ""),
            from_address=headers.get("From", ""),
            to_addresses=[headers.get("To", "")],
            cc_addresses=[headers.get("Cc", "")] if headers.get("Cc") else [],
            subject=headers.get("Subject", ""),
            date=None,  # Would need parsing
            body_text=body_text,
            body_html=body_html,
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




