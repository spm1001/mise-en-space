"""
Slides adapter — Google Slides API wrapper.

Fetches presentation structure and thumbnails.
Uses extractor's parse_presentation() for response parsing.

NOTE: HTTP batch requests are NOT supported for Workspace editor APIs (Slides,
Sheets, Docs) — Google disabled this in 2022. However, concurrent individual
getThumbnail requests DO work when each thread has its own service object
(isolated httplib2 connections). Shared connections cause SSL corruption.
"""

import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from googleapiclient.errors import HttpError

from models import PresentationData
from retry import with_retry
from adapters.services import get_slides_service, build_slides_service
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
    2. Concurrent pages().getThumbnail() for slides needing thumbnails

    Thumbnail API calls use isolated service objects per thread (shared httplib2
    connections cause SSL corruption). Capped at 2 concurrent workers — Google
    rate-limits at 3+. Benchmarked: 3.2x faster for 43 slides (22s vs 71s).
    Image downloads are also parallelized.

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
        _fetch_thumbnails_selective(presentation_id, data)

    return data


def _fetch_thumbnails_selective(
    presentation_id: str,
    data: PresentationData,
) -> None:
    """
    Fetch thumbnails selectively based on slide.needs_thumbnail.

    Skips slides where:
    - needs_thumbnail=False (stock photos, text-only)
    - slide_id is missing

    getThumbnail API calls run concurrently using isolated service objects
    (one per thread — shared httplib2 connections cause SSL corruption).
    Image downloads are also parallelized.

    Updates data.slides[i].thumbnail_bytes in place.
    """
    # Collect slides that need thumbnails
    target_slides = [
        s for s in data.slides if s.slide_id and s.needs_thumbnail
    ]
    if not target_slides:
        return

    slide_by_id = {s.slide_id: s for s in data.slides if s.slide_id}

    # Step 1: Get thumbnail URLs — parallel with isolated services
    # Each thread gets its own Slides service (own httplib2 connection)
    # to avoid SSL corruption that occurs with shared connections.
    def get_thumbnail_url(
        slide_id: str,
    ) -> tuple[str, str | None, str | None]:
        try:
            svc = build_slides_service()
            response = (
                svc.presentations()
                .pages()
                .getThumbnail(
                    presentationId=presentation_id,
                    pageObjectId=slide_id,
                    thumbnailProperties_thumbnailSize="MEDIUM",
                )
                .execute()
            )
            return slide_id, response.get("contentUrl"), None
        except HttpError as e:
            status = e.resp.status if e.resp else "unknown"
            if status == 403:
                return slide_id, None, "Thumbnail unavailable: permission denied"
            elif status == 404:
                return slide_id, None, "Thumbnail unavailable: not found"
            return slide_id, None, f"Thumbnail unavailable: HTTP {status}"
        except Exception as e:
            return slide_id, None, f"Thumbnail fetch failed: {type(e).__name__}"

    # Cap at 2 workers — Google rate-limits at 3+ concurrent getThumbnail
    # calls (tested: 7-slide deck works at any concurrency, 43-slide deck
    # fails at 3+ workers). 2 workers gives 3.2x speedup on large decks.
    slide_ids = [s.slide_id for s in target_slides]
    with ThreadPoolExecutor(max_workers=min(2, len(slide_ids))) as executor:
        url_results = list(executor.map(get_thumbnail_url, slide_ids))

    # Collect URLs, record errors
    thumbnail_urls: list[tuple[str, str]] = []
    for slide_id, url, error in url_results:
        if url:
            thumbnail_urls.append((slide_id, url))
        elif error and slide_id in slide_by_id:
            slide_by_id[slide_id].warnings.append(error)

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
    for slide_id, image_data, error in results:
        if slide_id not in slide_by_id:
            continue
        slide = slide_by_id[slide_id]
        if image_data is not None:
            slide.thumbnail_bytes = image_data
        elif error:
            slide.warnings.append(error)

    data.thumbnails_included = any(s.thumbnail_bytes for s in data.slides)
