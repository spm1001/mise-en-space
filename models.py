"""
Type definitions for mise-en-space.

Dataclasses defining the contracts between layers:
- Adapters produce these structures from API responses
- Extractors consume these structures and return content strings
- Tools wire everything together

These types make the adapter→extractor contract explicit and IDE-checkable.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ============================================================================
# ERROR TYPES
# ============================================================================

class ErrorKind(Enum):
    """Categories of errors for consistent handling."""
    AUTH_EXPIRED = "auth_expired"        # Token needs refresh
    NOT_FOUND = "not_found"              # Resource doesn't exist
    PERMISSION_DENIED = "permission_denied"  # No access to resource
    RATE_LIMITED = "rate_limited"        # Hit API quota
    NETWORK_ERROR = "network_error"      # Connection failed
    INVALID_INPUT = "invalid_input"      # Bad parameters
    EXTRACTION_FAILED = "extraction_failed"  # Couldn't process content
    UNKNOWN = "unknown"                  # Unexpected error


class MiseError(Exception):
    """
    Structured error for consistent handling across layers.

    Adapters raise these on API failures.
    Tools catch and format for MCP response.

    Inherits from Exception so it can be raised.
    """

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details or {}
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for MCP response."""
        return {
            "error": True,
            "kind": self.kind.value,
            "message": self.message,
            "retryable": self.retryable,
            **self.details,
        }


# ============================================================================
# SHEETS TYPES
# ============================================================================

# Cell values from Sheets API are strings, numbers, booleans, or None
CellValue = str | int | float | bool | None


@dataclass
class SheetTab:
    """A single sheet/tab within a spreadsheet."""
    name: str
    values: list[list[CellValue]]  # Rows of cells


@dataclass
class SpreadsheetData:
    """
    Assembled spreadsheet data for the sheets extractor.

    Adapter calls:
    1. spreadsheets().get() for metadata
    2. spreadsheets().values().get() for each sheet
    Then assembles this structure.
    """
    title: str
    spreadsheet_id: str
    sheets: list[SheetTab]

    # Optional metadata
    locale: str | None = None
    time_zone: str | None = None

    # Warnings during extraction (empty sheets, truncation, etc.)
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# DOCS TYPES
# ============================================================================

@dataclass
class DocTab:
    """A single tab within a Google Doc."""
    title: str
    tab_id: str
    index: int
    body: dict[str, Any]  # The 'body' field from documentTab
    footnotes: dict[str, Any] = field(default_factory=dict)  # Tab-specific footnotes
    lists: dict[str, Any] = field(default_factory=dict)  # List definitions
    inline_objects: dict[str, Any] = field(default_factory=dict)  # Images, drawings, charts


@dataclass
class DocData:
    """
    Assembled document data for the docs extractor.

    Adapter calls documents().get() and assembles this structure.
    Both legacy single-tab and modern multi-tab docs are normalized
    to a list of DocTab for consistent extractor interface.
    """
    title: str
    document_id: str
    tabs: list[DocTab]

    # Optional metadata
    revision_id: str | None = None

    # Warnings during extraction (unknown elements, truncation, etc.)
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# GMAIL TYPES
# ============================================================================

@dataclass
class EmailAttachment:
    """An attachment on an email message."""
    filename: str
    mime_type: str
    size: int
    attachment_id: str  # For fetching content later

    # If already fetched
    content: bytes | None = None


@dataclass
class EmailMessage:
    """A single email message within a thread."""
    message_id: str
    from_address: str
    to_addresses: list[str]
    cc_addresses: list[str] = field(default_factory=list)
    subject: str = ""
    date: datetime | None = None

    # Content - at least one should be present
    body_text: str | None = None  # Plain text version
    body_html: str | None = None  # HTML version

    # Attachments
    attachments: list[EmailAttachment] = field(default_factory=list)

    # Drive links mentioned in body (people say "attached" when they mean "linked")
    drive_links: list[dict[str, str]] = field(default_factory=list)


@dataclass
class GmailThreadData:
    """
    Assembled Gmail thread data for the gmail extractor.

    Adapter calls:
    1. threads().get() with format=FULL
    2. Processes each message
    Then assembles this structure.
    """
    thread_id: str
    subject: str
    messages: list[EmailMessage]

    # Summary metadata
    message_count: int = 0
    has_attachments: bool = False

    # Warnings during extraction (signature stripping issues, encoding, etc.)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.message_count = len(self.messages)
        self.has_attachments = any(
            m.attachments for m in self.messages
        )


# ============================================================================
# SLIDES TYPES
# ============================================================================

@dataclass
class SlideTable:
    """A table within a slide."""
    rows: list[list[str]]  # 2D grid of cell values


@dataclass
class SlideData:
    """A single slide within a presentation."""
    slide_id: str
    index: int  # 0-based position

    # Slide title (from TITLE or CENTERED_TITLE placeholder)
    title: str | None = None

    # Text content extracted from shapes (excluding title)
    text_content: list[str] = field(default_factory=list)

    # Tables extracted from slide
    tables: list[SlideTable] = field(default_factory=list)

    # Speaker notes
    notes: str | None = None

    # Visual elements descriptions (for context without thumbnails)
    visual_elements: list[str] = field(default_factory=list)

    # Thumbnail (populated by adapter if requested)
    thumbnail_bytes: bytes | None = None

    # Thumbnail decision (populated during parsing)
    needs_thumbnail: bool = False
    thumbnail_reason: str | None = None  # 'chart', 'image', 'fragmented_text'
    skip_thumbnail_reason: str | None = None  # 'single_large_image', 'text_only'

    # Warnings during extraction (missing objectId, etc.)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PresentationData:
    """
    Assembled presentation data for the slides extractor.

    Adapter calls:
    1. presentations().get() for structure and text
    2. batch pages().getThumbnail() for thumbnails (optional)
    Then assembles this structure.
    """
    title: str
    presentation_id: str
    slides: list[SlideData]

    # Whether thumbnails were fetched
    thumbnails_included: bool = False

    # Optional metadata
    page_size: dict[str, Any] | None = None  # Width/height in EMU
    locale: str | None = None

    # Warnings aggregated from all slides
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# SEARCH RESULT TYPES
# ============================================================================

@dataclass
class DriveSearchResult:
    """A single result from Drive search."""
    file_id: str
    name: str
    mime_type: str
    modified_time: datetime | None = None

    # For triage
    snippet: str | None = None
    owners: list[str] = field(default_factory=list)
    web_view_link: str | None = None


@dataclass
class GmailSearchResult:
    """A single result from Gmail search."""
    thread_id: str
    subject: str
    snippet: str
    date: datetime | None = None

    # For triage
    from_address: str | None = None
    message_count: int = 1
    has_attachments: bool = False
    attachment_names: list[str] = field(default_factory=list)


# ============================================================================
# TOOL RESPONSE TYPES
# ============================================================================

@dataclass
class FetchResult:
    """Successful fetch result."""
    path: str                    # Folder path (mise-fetch/...)
    content_file: str            # Full path to content file
    format: str                  # 'markdown', 'csv'
    type: str                    # 'doc', 'sheet', 'slides', 'gmail'
    metadata: dict[str, Any]     # Type-specific metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content_file": self.content_file,
            "format": self.format,
            "type": self.type,
            "metadata": self.metadata,
        }


@dataclass
class FetchError:
    """Fetch error result."""
    error: bool = True
    kind: str = "unknown"
    message: str = ""
    file_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {"error": self.error, "kind": self.kind, "message": self.message}
        if self.file_id:
            result["file_id"] = self.file_id
        if self.name:
            result["name"] = self.name
        return result


@dataclass
class SearchResult:
    """Search result across sources."""
    query: str
    sources: list[str]
    drive_results: list[dict[str, Any]] = field(default_factory=list)
    gmail_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"query": self.query, "sources": self.sources}
        if "drive" in self.sources:
            result["drive_results"] = self.drive_results
        if "gmail" in self.sources:
            result["gmail_results"] = self.gmail_results
        if self.errors:
            result["errors"] = self.errors
        return result


@dataclass
class CreateResult:
    """Successful create result."""
    file_id: str
    web_link: str
    title: str
    doc_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "web_link": self.web_link,
            "title": self.title,
            "type": self.doc_type,
        }


@dataclass
class CreateError:
    """Create error result."""
    error: bool = True
    kind: str = "unknown"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.error,
            "kind": self.kind,
            "message": self.message,
        }
