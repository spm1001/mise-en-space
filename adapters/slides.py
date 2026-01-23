"""
Slides adapter — Google Slides API wrapper.

Fetches presentation structure and thumbnails.
Uses extractor's parse_presentation() for response parsing.

NOTE: HTTP batch requests are NOT supported for Workspace editor APIs (Slides,
Sheets, Docs) — Google disabled this platform feature in 2022. Thumbnails must
be fetched sequentially. See: github.com/googleapis/google-api-python-client/issues/2085
"""

import urllib.request
from concurrent.futures import ThreadPoolExecutor

from googleapiclient.discovery import Resource

from models import PresentationData
from retry import with_retry
from adapters.services import get_slides_service
from extractors.slides import parse_presentation


# Fields to request — only what we need for extraction
# notesPage is nested under slideProperties
PRESENTATION_FIELDS = (
    "presentationId,"
    "title,"
    "locale,"
    "pageSize,"
    "slides(objectId,pageElements,slideProperties(notesPage))"
)


@with_retry(max_attempts=3, delay_ms=1000)
def fetch_presentation(
    presentation_id: str,
    include_thumbnails: bool = False,
) -> PresentationData:
    """
    Fetch complete presentation data.

    Calls:
    1. presentations().get() for structure and text
    2. Sequential pages().getThumbnail() for each slide (if include_thumbnails=True)

    NOTE: Thumbnail fetching is ~0.5s per slide (API calls must be sequential,
    but image downloads are parallelized). For a 43-slide deck, expect ~20s.
    Consider selective thumbnailing for large presentations.

    Args:
        presentation_id: The presentation ID (from URL or API)
        include_thumbnails: Whether to fetch slide thumbnails

    Returns:
        PresentationData ready for the extractor

    Raises:
        MiseError: On API failure (converted by @with_retry)
    """
    service = get_slides_service()

    # Fetch presentation structure
    response = (
        service.presentations()
        .get(presentationId=presentation_id, fields=PRESENTATION_FIELDS)
        .execute()
    )

    # Parse into typed model
    data = parse_presentation(response)

    # Fetch thumbnails if requested (sequential API calls, parallel downloads)
    if include_thumbnails and data.slides:
        _fetch_thumbnails(service, presentation_id, data)

    return data


def _fetch_thumbnails(
    service: Resource,
    presentation_id: str,
    data: PresentationData,
) -> None:
    """
    Fetch thumbnails for all slides.

    API calls are sequential (batch not supported for Workspace APIs).
    Image downloads are parallelized for speed.

    Updates data.slides[i].thumbnail_bytes in place.
    """
    # Step 1: Get thumbnail URLs (sequential API calls — no way around this)
    thumbnail_urls: list[tuple[str, str]] = []  # (slide_id, url)

    for slide in data.slides:
        if not slide.slide_id:
            continue
        try:
            response = (
                service.presentations()
                .pages()
                .getThumbnail(
                    presentationId=presentation_id,
                    pageObjectId=slide.slide_id,
                    thumbnailProperties_thumbnailSize="MEDIUM",
                )
                .execute()
            )
            url = response.get("contentUrl")
            if url:
                thumbnail_urls.append((slide.slide_id, url))
        except Exception:
            pass  # Skip failed thumbnails

    if not thumbnail_urls:
        return

    # Step 2: Download images in parallel
    def download(item: tuple[str, str]) -> tuple[str, bytes | None]:
        slide_id, url = item
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return slide_id, resp.read()
        except Exception:
            return slide_id, None

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(download, thumbnail_urls))

    # Step 3: Update slides with downloaded thumbnails
    thumbnail_map = {sid: data for sid, data in results if data is not None}

    for slide in data.slides:
        if slide.slide_id in thumbnail_map:
            slide.thumbnail_bytes = thumbnail_map[slide.slide_id]

    data.thumbnails_included = len(thumbnail_map) > 0
