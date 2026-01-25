"""
Slides adapter — Google Slides API wrapper.

Fetches presentation structure and thumbnails.
Uses extractor's parse_presentation() for response parsing.

NOTE: HTTP batch requests are NOT supported for Workspace editor APIs (Slides,
Sheets, Docs) — Google disabled this platform feature in 2022. Thumbnails must
be fetched sequentially. See: github.com/googleapis/google-api-python-client/issues/2085
"""

import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

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

    # Fetch thumbnails selectively (only for slides that need them)
    if include_thumbnails and data.slides:
        _fetch_thumbnails_selective(service, presentation_id, data)

    return data


def _fetch_thumbnails_selective(
    service: Resource,
    presentation_id: str,
    data: PresentationData,
) -> None:
    """
    Fetch thumbnails selectively based on slide.needs_thumbnail.

    Skips slides where:
    - needs_thumbnail=False (stock photos, text-only)
    - slide_id is missing

    API calls are sequential (batch not supported for Workspace APIs).
    Image downloads are parallelized for speed.

    Updates data.slides[i].thumbnail_bytes in place.
    """
    # Step 1: Get thumbnail URLs only for slides that need them
    thumbnail_urls: list[tuple[str, str]] = []  # (slide_id, url)

    for slide in data.slides:
        if not slide.slide_id:
            continue
        if not slide.needs_thumbnail:
            continue  # Skip based on selective logic

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
        except HttpError as e:
            status = e.resp.status if e.resp else "unknown"
            if status == 403:
                slide.warnings.append("Thumbnail unavailable: permission denied")
            elif status == 404:
                slide.warnings.append("Thumbnail unavailable: not found")
            else:
                slide.warnings.append(f"Thumbnail unavailable: HTTP {status}")
        except Exception as e:
            slide.warnings.append(f"Thumbnail fetch failed: {type(e).__name__}")

    if not thumbnail_urls:
        return

    # Step 2: Download images in parallel
    def download(item: tuple[str, str]) -> tuple[str, bytes | None, str | None]:
        slide_id, url = item
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return slide_id, resp.read(), None
        except urllib.error.URLError as e:
            return slide_id, None, f"Download failed: {e.reason}"
        except TimeoutError:
            return slide_id, None, "Download failed: timeout"
        except Exception as e:
            return slide_id, None, f"Download failed: {type(e).__name__}"

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(download, thumbnail_urls))

    # Step 3: Update slides with downloaded thumbnails and track failures
    slide_by_id = {s.slide_id: s for s in data.slides if s.slide_id}

    for slide_id, image_data, error in results:
        if slide_id not in slide_by_id:
            continue
        slide = slide_by_id[slide_id]
        if image_data is not None:
            slide.thumbnail_bytes = image_data
        elif error:
            slide.warnings.append(error)

    data.thumbnails_included = any(s.thumbnail_bytes for s in data.slides)
