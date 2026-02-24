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
from pathlib import Path
from typing import Any


# ============================================================================
# ERROR TYPES
# ============================================================================

class ErrorKind(Enum):
    """Categories of errors for consistent handling."""
    AUTH_EXPIRED = "auth_expired"        # Token needs refresh
    AUTH_REQUIRED = "auth_required"      # Web page requires authentication
    NOT_FOUND = "not_found"              # Resource doesn't exist
    PERMISSION_DENIED = "permission_denied"  # No access to resource
    RATE_LIMITED = "rate_limited"        # Hit API quota
    NETWORK_ERROR = "network_error"      # Connection failed
    TIMEOUT = "timeout"                  # Request timed out
    CAPTCHA = "captcha"                  # CAPTCHA challenge detected
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
    sheet_type: str = "GRID"  # GRID, OBJECT (chart sheet), or DATA_SOURCE


@dataclass
class ChartData:
    """
    A chart from a spreadsheet.

    Charts can be:
    - Floating (embedded on a GRID sheet)
    - Sheet charts (on their own OBJECT sheet)

    Rendering happens via Slides API - see adapters/charts.py.
    """
    chart_id: int
    title: str | None = None
    sheet_name: str | None = None  # Where the chart lives
    chart_type: str | None = None  # COLUMN, LINE, PIE, etc.

    # Rendered PNG (populated by chart rendering)
    png_bytes: bytes | None = None

    # Source data range (for metadata)
    source_ranges: list[str] = field(default_factory=list)


@dataclass
class SpreadsheetData:
    """
    Assembled spreadsheet data for the sheets extractor.

    Adapter calls:
    1. spreadsheets().get() for metadata + chart info
    2. spreadsheets().values().batchGet() for all sheet values
    3. Chart rendering via Slides API (if charts present)
    Then assembles this structure.
    """
    title: str
    spreadsheet_id: str
    sheets: list[SheetTab]

    # Charts from the spreadsheet (populated by chart rendering)
    charts: list[ChartData] = field(default_factory=list)

    # Optional metadata
    locale: str | None = None
    time_zone: str | None = None

    # Chart rendering timing (ms)
    chart_render_time_ms: int = 0

    # Formula cell count (cells where FORMULA differs from FORMATTED_VALUE)
    formula_count: int = 0

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
class ForwardedMessage:
    """A message forwarded as a MIME message/rfc822 attachment."""
    from_address: str = ""
    date: str = ""
    subject: str = ""
    body_text: str = ""
    body_html: str | None = None  # Raw HTML when no plain text; adapter converts


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

    # Forwarded messages (MIME message/rfc822 parts)
    forwarded_messages: list[ForwardedMessage] = field(default_factory=list)


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
# WEB TYPES
# ============================================================================

@dataclass
class WebData:
    """
    Assembled web page data for the web extractor.

    Adapter fetches HTML (via HTTP or browser rendering), assembles this structure.
    Extractor receives this, returns clean markdown.

    For non-HTML responses (PDFs, etc.), raw_bytes carries the binary content
    and html will be empty. The tool layer checks content_type to route
    binary content to the appropriate extractor.
    """
    url: str
    html: str
    final_url: str  # After redirects
    status_code: int
    content_type: str
    cookies_used: bool
    render_method: str  # 'http' or 'browser'

    # Warnings during fetch (redirects, fallbacks, etc.)
    warnings: list[str] = field(default_factory=list)
    # Raw bytes for non-HTML responses (PDFs, images, etc.)
    # Only populated for small responses (below STREAMING_THRESHOLD_BYTES).
    raw_bytes: bytes | None = None
    # Temp file path for large binary responses (above STREAMING_THRESHOLD_BYTES).
    # Caller is responsible for cleanup (unlink when done).
    temp_path: Path | None = None
    # Markdown from browser extraction (passe read).
    # When set, the tool layer skips trafilatura and uses this directly.
    # The html field still holds the original HTTP response for title extraction.
    pre_extracted_content: str | None = None


# ============================================================================
# DRIVE FOLDER TYPES
# ============================================================================

@dataclass
class FolderItem:
    """A subfolder entry in a Drive folder listing."""
    id: str
    name: str


@dataclass
class FolderFile:
    """A file entry in a Drive folder listing."""
    id: str
    name: str
    mime_type: str


@dataclass
class FolderListing:
    """Result of list_folder() — direct children of a Drive folder."""
    subfolders: list[FolderItem]
    files: list[FolderFile]
    file_count: int
    folder_count: int
    item_count: int
    types: list[str]          # Distinct MIME types from files (not folders)
    truncated: bool           # True if more than 300 items exist


# ============================================================================
# SEARCH RESULT TYPES
# ============================================================================

@dataclass
class EmailContext:
    """Email context extracted from exfil'd file description."""
    message_id: str
    from_address: str | None = None
    subject: str | None = None
    date: str | None = None


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

    # For cross-source linkage
    description: str | None = None
    email_context: EmailContext | None = None  # Populated for exfil'd files


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
    path: str                    # Folder path (mise/...)
    content_file: str            # Full path to content file
    format: str                  # 'markdown', 'csv'
    type: str                    # 'doc', 'sheet', 'slides', 'gmail'
    metadata: dict[str, Any]     # Type-specific metadata
    cues: dict[str, Any] = field(default_factory=dict)  # Decision-tree signals

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "path": self.path,
            "content_file": self.content_file,
            "format": self.format,
            "type": self.type,
            "metadata": self.metadata,
        }
        # Always include cues — explicit-null principle: empty cues means
        # "we checked, nothing to signal" not "cues not implemented"
        result["cues"] = self.cues
        return result


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
    activity_results: list[dict[str, Any]] = field(default_factory=list)
    calendar_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Path to deposited results file (filesystem-first pattern)
    path: str | None = None
    # Decision-tree signals (scope notes, warnings, etc.)
    cues: dict[str, Any] = field(default_factory=dict)

    def full_results(self) -> dict[str, Any]:
        """Get full results dict (for writing to file)."""
        result: dict[str, Any] = {"query": self.query, "sources": self.sources}
        if "drive" in self.sources:
            result["drive_results"] = self.drive_results
        if "gmail" in self.sources:
            result["gmail_results"] = self.gmail_results
        if "activity" in self.sources:
            result["activity_results"] = self.activity_results
        if "calendar" in self.sources:
            result["calendar_results"] = self.calendar_results
        if self.errors:
            result["errors"] = self.errors
        return result

    def _build_preview(self, max_per_source: int = 5) -> dict[str, Any]:
        """Build compact preview of top results for each source."""
        preview: dict[str, Any] = {}
        if self.drive_results:
            drive_items = []
            for r in self.drive_results[:max_per_source]:
                item: dict[str, Any] = {
                    "name": r.get("name", ""),
                    "id": r.get("id", ""),
                    "mimeType": r.get("mimeType", ""),
                }
                if r.get("email_context"):
                    item["email_context"] = r["email_context"]
                drive_items.append(item)
            preview["drive"] = drive_items
        if self.gmail_results:
            gmail_items = []
            for r in self.gmail_results[:max_per_source]:
                item = {
                    "subject": r.get("subject", ""),
                    "thread_id": r.get("thread_id", ""),
                    "from": r.get("from", ""),
                    "message_count": r.get("message_count", 1),
                }
                att_names = r.get("attachment_names")
                if att_names:
                    item["attachment_names"] = att_names
                gmail_items.append(item)
            preview["gmail"] = gmail_items
        if self.activity_results:
            activity_items = []
            for r in self.activity_results[:max_per_source]:
                item = {
                    "file_name": r.get("file_name", ""),
                    "file_id": r.get("file_id", ""),
                    "action_type": r.get("action_type", ""),
                    "actor": r.get("actor", ""),
                    "timestamp": r.get("timestamp", ""),
                }
                if r.get("mentioned_users"):
                    item["mentioned_users"] = r["mentioned_users"]
                activity_items.append(item)
            preview["activity"] = activity_items
        if self.calendar_results:
            calendar_items = []
            for r in self.calendar_results[:max_per_source]:
                item = {
                    "summary": r.get("summary", ""),
                    "start_time": r.get("start_time", ""),
                    "attendee_count": r.get("attendee_count", 0),
                }
                if r.get("attachment_count"):
                    item["attachment_count"] = r["attachment_count"]
                if r.get("meet_link"):
                    item["has_meet"] = True
                calendar_items.append(item)
            preview["calendar"] = calendar_items
        return preview

    def to_dict(self) -> dict[str, Any]:
        """
        Get MCP response dict.

        If path is set, returns path + summary + preview (filesystem-first pattern).
        Otherwise returns full results inline (legacy/testing).
        """
        if self.path:
            # Filesystem-first: return path + summary + preview
            result: dict[str, Any] = {
                "path": self.path,
                "query": self.query,
                "sources": self.sources,
                "drive_count": len(self.drive_results) if "drive" in self.sources else 0,
                "gmail_count": len(self.gmail_results) if "gmail" in self.sources else 0,
                "activity_count": len(self.activity_results) if "activity" in self.sources else 0,
                "calendar_count": len(self.calendar_results) if "calendar" in self.sources else 0,
            }
            preview = self._build_preview()
            if preview:
                result["preview"] = preview
            if self.errors:
                result["errors"] = self.errors
            if self.cues:
                result["cues"] = self.cues
            return result
        else:
            # Legacy: return full results inline
            return self.full_results()


@dataclass
class DoResult:
    """Successful result from a do() operation.

    Shared return type for all 6 operations (create, move, overwrite,
    prepend, append, replace_text). Operation-specific fields go in extras.
    """
    file_id: str
    title: str
    web_link: str
    operation: str
    cues: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "file_id": self.file_id,
            "title": self.title,
            "web_link": self.web_link,
            "operation": self.operation,
            "cues": self.cues,
        }
        result.update(self.extras)
        return result


@dataclass
class CreateResult:
    """Successful create result."""
    file_id: str
    web_link: str
    title: str
    doc_type: str
    cues: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "title": self.title,
            "web_link": self.web_link,
            "operation": "create",
            "type": self.doc_type,
            "cues": self.cues or {},
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


# ============================================================================
# COMMENTS TYPES
# ============================================================================

@dataclass
class CommentReply:
    """A reply to a comment on a Drive file."""
    id: str
    content: str
    author_name: str
    author_email: str | None = None
    created_time: str | None = None
    modified_time: str | None = None
    mentioned_emails: list[str] = field(default_factory=list)  # @mentions in reply


@dataclass
class CommentData:
    """A comment on a Drive file."""
    id: str
    content: str
    author_name: str
    author_email: str | None = None
    created_time: str | None = None
    modified_time: str | None = None
    resolved: bool = False
    quoted_text: str = ""  # From quotedFileContent.value (human-readable for Docs)
    mentioned_emails: list[str] = field(default_factory=list)  # @mentions in comment
    replies: list[CommentReply] = field(default_factory=list)


@dataclass
class FileCommentsData:
    """
    Assembled comments data for the comments extractor.

    Adapter calls comments().list() with pagination and assembles this structure.
    """
    file_id: str
    file_name: str
    comments: list[CommentData]
    comment_count: int = 0

    # Warnings during extraction (missing authors, truncation, etc.)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.comment_count = len(self.comments)


# ============================================================================
# ACTIVITY TYPES
# ============================================================================

@dataclass
class ActivityActor:
    """Actor who performed an activity."""
    name: str
    email: str | None = None


@dataclass
class ActivityTarget:
    """Target file/folder of an activity."""
    file_id: str
    file_name: str
    mime_type: str | None = None
    web_link: str | None = None


@dataclass
class CommentActivity:
    """A comment-related activity (create, reply, resolve, etc.)."""
    activity_id: str
    timestamp: str  # ISO format
    actor: ActivityActor
    target: ActivityTarget
    action_type: str  # "comment", "reply", "resolve", "reopen", "delete", etc.
    mentioned_users: list[str] = field(default_factory=list)  # Emails mentioned
    comment_content: str | None = None  # May be truncated by API


@dataclass
class ActivitySearchResult:
    """Results from Activity API query."""
    activities: list[CommentActivity]
    next_page_token: str | None = None
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# CALENDAR TYPES
# ============================================================================

@dataclass
class CalendarAttachment:
    """A file attached to a calendar event."""
    file_id: str
    title: str
    mime_type: str | None = None
    file_url: str | None = None


@dataclass
class CalendarAttendee:
    """An attendee of a calendar event."""
    email: str
    display_name: str | None = None
    response_status: str = "needsAction"  # needsAction, declined, tentative, accepted
    is_self: bool = False
    is_resource: bool = False  # Room/equipment booking


@dataclass
class CalendarEvent:
    """A calendar event with meeting context."""
    event_id: str
    summary: str
    start_time: str  # ISO format
    end_time: str  # ISO format
    html_link: str | None = None
    attendees: list[CalendarAttendee] = field(default_factory=list)
    attachments: list[CalendarAttachment] = field(default_factory=list)
    meet_link: str | None = None
    description: str | None = None
    organizer_email: str | None = None


@dataclass
class CalendarSearchResult:
    """Results from Calendar API query."""
    events: list[CalendarEvent]
    next_page_token: str | None = None
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# TASKS TYPES
# ============================================================================

@dataclass
class TaskItem:
    """A Google Tasks task."""
    task_id: str
    title: str
    status: str  # "needsAction" or "completed"
    due: str | None = None  # RFC3339 date
    notes: str | None = None
    updated: str | None = None  # RFC3339 timestamp
    completed: str | None = None  # RFC3339 timestamp
    parent_id: str | None = None  # For subtasks
    web_link: str | None = None


@dataclass
class TaskList:
    """A Google Tasks task list."""
    list_id: str
    title: str
    updated: str | None = None


@dataclass
class TaskSearchResult:
    """Results from Tasks API query."""
    tasks: list[TaskItem]
    task_list_title: str | None = None
    next_page_token: str | None = None
    warnings: list[str] = field(default_factory=list)
