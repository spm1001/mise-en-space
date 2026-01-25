"""
Create tool implementation.

Creates Google Workspace documents from markdown content.
"""

import io
from typing import Any

from googleapiclient.http import MediaIoBaseUpload

from adapters.services import get_drive_service
from adapters.drive import GOOGLE_DOC_MIME, GOOGLE_SHEET_MIME, GOOGLE_SLIDES_MIME
from models import CreateResult, CreateError
from retry import with_retry


# Supported doc types and their target MIME types
DOC_TYPE_TO_MIME = {
    "doc": GOOGLE_DOC_MIME,
    "sheet": GOOGLE_SHEET_MIME,
    "slides": GOOGLE_SLIDES_MIME,
}


def do_create(
    content: str,
    title: str,
    doc_type: str = "doc",
    folder_id: str | None = None,
) -> CreateResult | CreateError:
    """
    Create a Google Workspace document from markdown content.

    Args:
        content: Markdown content to convert
        title: Document title
        doc_type: 'doc' | 'sheet' | 'slides' (only 'doc' supported initially)
        folder_id: Optional destination folder ID

    Returns:
        CreateResult with file_id and web_link, or CreateError on failure
    """
    # Validate doc_type
    if doc_type not in DOC_TYPE_TO_MIME:
        return CreateError(
            kind="invalid_input",
            message=f"Unsupported doc_type: {doc_type}. Must be one of: {list(DOC_TYPE_TO_MIME.keys())}",
        )

    # Currently only supporting doc creation
    if doc_type != "doc":
        return CreateError(
            kind="not_implemented",
            message=f"Creating {doc_type} is not yet implemented. Only 'doc' is supported.",
        )

    return _create_doc(content, title, folder_id)


@with_retry(max_attempts=3, delay_ms=1000)
def _create_doc(
    content: str,
    title: str,
    folder_id: str | None = None,
) -> CreateResult | CreateError:
    """
    Create a Google Doc from markdown using Drive's native import.

    Drive automatically converts text/markdown to Google Doc format.
    This was discovered via about.get(fields='importFormats') - not in static docs!
    """
    service = get_drive_service()

    # File metadata
    file_metadata: dict[str, Any] = {
        "name": title,
        "mimeType": GOOGLE_DOC_MIME,
    }

    # Add parent folder if specified
    if folder_id:
        file_metadata["parents"] = [folder_id]

    # Create media with markdown content
    # Drive's import converts text/markdown -> Google Doc
    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")),
        mimetype="text/markdown",
        resumable=True,
    )

    # Create the file
    result = (
        service.files()
        .create(
            body=file_metadata,
            media_body=media,
            fields="id,webViewLink,name",
            supportsAllDrives=True,
        )
        .execute()
    )

    return CreateResult(
        file_id=result["id"],
        web_link=result["webViewLink"],
        title=result.get("name", title),
        doc_type="doc",
    )
