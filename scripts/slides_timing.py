#!/usr/bin/env python3
"""
Timing tests for Slides API approaches.

Compares:
1. presentations().get() alone (text only)
2. Sequential thumbnail fetches (v1 approach)
3. Batch thumbnail fetches (new approach)

Usage:
    uv run python scripts/slides_timing.py <presentation_id>
"""

import sys
import time
from functools import lru_cache

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import BatchHttpRequest


@lru_cache
def get_slides_service():
    """Get authenticated Slides service."""
    import json
    with open("token.json") as f:
        token_data = json.load(f)
    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
    )
    return build("slides", "v1", credentials=creds)


def time_get_only(service, presentation_id: str) -> tuple[float, int]:
    """Time just presentations().get() — text extraction only."""
    start = time.perf_counter()
    presentation = service.presentations().get(presentationId=presentation_id).execute()
    elapsed = time.perf_counter() - start
    slide_count = len(presentation.get("slides", []))
    return elapsed, slide_count


def time_sequential_thumbnails(service, presentation_id: str, slide_ids: list[str]) -> float:
    """Time sequential thumbnail fetches (v1 approach)."""
    start = time.perf_counter()
    for slide_id in slide_ids:
        service.presentations().pages().getThumbnail(
            presentationId=presentation_id,
            pageObjectId=slide_id,
            thumbnailProperties_thumbnailSize="MEDIUM"
        ).execute()
    elapsed = time.perf_counter() - start
    return elapsed


def time_batch_thumbnails(service, presentation_id: str, slide_ids: list[str]) -> float:
    """Time batch thumbnail fetches (new approach)."""
    results = {}

    def callback(request_id, response, exception):
        if exception:
            results[request_id] = {"error": str(exception)}
        else:
            results[request_id] = response

    start = time.perf_counter()
    batch: BatchHttpRequest = service.new_batch_http_request(callback=callback)
    for slide_id in slide_ids:
        batch.add(
            service.presentations().pages().getThumbnail(
                presentationId=presentation_id,
                pageObjectId=slide_id,
                thumbnailProperties_thumbnailSize="MEDIUM"
            ),
            request_id=slide_id
        )
    batch.execute()
    elapsed = time.perf_counter() - start
    return elapsed


def main():
    if len(sys.argv) < 2:
        # Default to the test presentation from fixtures
        presentation_id = "1ZrknZXSsyDtWuWq0cXV7UMZ-7WHClm3fJa61uZY2pwY"
    else:
        presentation_id = sys.argv[1]

    print(f"Testing presentation: {presentation_id}\n")

    service = get_slides_service()

    # Test 1: Get only
    get_time, slide_count = time_get_only(service, presentation_id)
    print(f"1. presentations().get() only:")
    print(f"   Time: {get_time:.3f}s")
    print(f"   Slides: {slide_count}")

    # Get slide IDs for thumbnail tests
    presentation = service.presentations().get(presentationId=presentation_id).execute()
    slide_ids = [slide["objectId"] for slide in presentation.get("slides", [])]

    if not slide_ids:
        print("\nNo slides found!")
        return

    print(f"\n2. Sequential thumbnails ({len(slide_ids)} slides):")
    seq_time = time_sequential_thumbnails(service, presentation_id, slide_ids)
    print(f"   Time: {seq_time:.3f}s")
    print(f"   Per slide: {seq_time/len(slide_ids):.3f}s")

    print(f"\n3. Batch thumbnails ({len(slide_ids)} slides):")
    batch_time = time_batch_thumbnails(service, presentation_id, slide_ids)
    print(f"   Time: {batch_time:.3f}s")
    print(f"   Per slide: {batch_time/len(slide_ids):.3f}s")

    print(f"\n--- Summary ---")
    print(f"Text only:   {get_time:.3f}s")
    print(f"Sequential:  {get_time + seq_time:.3f}s total ({seq_time:.3f}s for thumbnails)")
    print(f"Batch:       {get_time + batch_time:.3f}s total ({batch_time:.3f}s for thumbnails)")
    print(f"Batch speedup: {seq_time/batch_time:.1f}x faster than sequential")


if __name__ == "__main__":
    main()
