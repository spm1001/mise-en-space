#!/usr/bin/env python3
"""
Timing tests for Slides API thumbnail approaches.

Compares:
1. presentations().get() alone (text only)
2. Sequential getThumbnail (one shared service)
3. Concurrent getThumbnail (isolated service per thread) — what the adapter uses
4. HTTP batch (disabled by Google since 2022, kept for documentation)

Key finding (Feb 2026): Concurrent with isolated services is 2.5x faster than
sequential. Shared httplib2 connections cause SSL corruption under concurrency —
each thread needs its own service object via build().

Usage:
    uv run python scripts/slides_timing.py [presentation_id]
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import BatchHttpRequest


def _load_credentials() -> Credentials:
    import json
    with open("token.json") as f:
        token_data = json.load(f)
    return Credentials(
        token=token_data["token"],
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
    )


@lru_cache
def get_slides_service():
    """Cached service — NOT thread-safe (shared httplib2)."""
    return build("slides", "v1", credentials=_load_credentials())


def build_slides_service():
    """Fresh service per call — thread-safe (isolated httplib2)."""
    return build("slides", "v1", credentials=_load_credentials())


def time_get_only(service, presentation_id: str) -> tuple[float, int]:
    """Time just presentations().get() — text extraction only."""
    start = time.perf_counter()
    presentation = service.presentations().get(presentationId=presentation_id).execute()
    elapsed = time.perf_counter() - start
    return elapsed, len(presentation.get("slides", []))


def time_sequential(service, presentation_id: str, slide_ids: list[str]) -> float:
    """Sequential getThumbnail — one call at a time, shared service."""
    start = time.perf_counter()
    for slide_id in slide_ids:
        service.presentations().pages().getThumbnail(
            presentationId=presentation_id,
            pageObjectId=slide_id,
            thumbnailProperties_thumbnailSize="MEDIUM"
        ).execute()
    return time.perf_counter() - start


def time_concurrent(presentation_id: str, slide_ids: list[str], workers: int) -> tuple[float, int, int]:
    """Concurrent getThumbnail — isolated service per thread."""
    def fetch_one(slide_id: str) -> tuple[str, dict | None, str | None]:
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
            return slide_id, response, None
        except Exception as e:
            return slide_id, None, f"{type(e).__name__}: {e}"

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(fetch_one, slide_ids))
    elapsed = time.perf_counter() - start

    ok = len([r for r in results if r[1] is not None])
    err = len([r for r in results if r[2] is not None])
    return elapsed, ok, err


def time_batch(service, presentation_id: str, slide_ids: list[str]) -> tuple[float, int, int]:
    """HTTP batch — disabled by Google since 2022, kept for documentation."""
    results = {}

    def callback(request_id, response, exception):
        results[request_id] = {"error": str(exception)} if exception else response

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

    ok = len([r for r in results.values() if "error" not in r])
    err = len([r for r in results.values() if "error" in r])
    return elapsed, ok, err


def main():
    if len(sys.argv) < 2:
        presentation_id = "1ZrknZXSsyDtWuWq0cXV7UMZ-7WHClm3fJa61uZY2pwY"
    else:
        presentation_id = sys.argv[1]

    print(f"Testing presentation: {presentation_id}\n")

    service = get_slides_service()

    # 1. Text only
    get_time, slide_count = time_get_only(service, presentation_id)
    print(f"1. presentations().get() only:")
    print(f"   {get_time:.3f}s — {slide_count} slides")

    # Get slide IDs
    presentation = service.presentations().get(presentationId=presentation_id).execute()
    slide_ids = [slide["objectId"] for slide in presentation.get("slides", [])]
    if not slide_ids:
        print("\nNo slides found!")
        return

    # 2. Sequential
    seq_time = time_sequential(service, presentation_id, slide_ids)
    print(f"\n2. Sequential ({len(slide_ids)} slides):")
    print(f"   {seq_time:.3f}s ({seq_time/len(slide_ids):.3f}s/slide)")

    # 3. Concurrent at various worker counts
    print(f"\n3. Concurrent — isolated services ({len(slide_ids)} slides):")
    best_time, best_workers = float("inf"), 0
    for workers in [2, 3, 4, len(slide_ids)]:
        conc_time, ok, err = time_concurrent(presentation_id, slide_ids, workers)
        status = f"{ok} ok" + (f", {err} errors" if err else "")
        print(f"   workers={workers}: {conc_time:.3f}s ({status})")
        if conc_time < best_time and err == 0:
            best_time, best_workers = conc_time, workers

    # 4. Batch (expected to fail)
    print(f"\n4. HTTP batch ({len(slide_ids)} slides) — expected to fail:")
    batch_time, batch_ok, batch_err = time_batch(service, presentation_id, slide_ids)
    print(f"   {batch_time:.3f}s — {batch_ok} ok, {batch_err} errors")

    # Summary
    print(f"\n--- Summary ---")
    print(f"Text only:    {get_time:.3f}s")
    print(f"Sequential:   {seq_time:.3f}s ({seq_time/len(slide_ids):.3f}s/slide)")
    print(f"Concurrent:   {best_time:.3f}s (best at {best_workers} workers)")
    if best_time < seq_time:
        print(f"Speedup:      {seq_time/best_time:.1f}x ({(1 - best_time/seq_time) * 100:.0f}% reduction)")
    print(f"Batch:        {'FAILED' if batch_err else f'{batch_time:.3f}s'}")


if __name__ == "__main__":
    main()
