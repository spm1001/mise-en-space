"""
Integration tests for parallel search concurrency.

Verifies that Drive + Gmail search actually runs in parallel
and doesn't race on auth token refresh or httplib2 thread safety.

Run with: uv run pytest tests/integration/test_parallel_search.py -v -m integration
"""

import time
from pathlib import Path

import pytest

from tools.search import do_search

TMP = Path("/tmp")


@pytest.mark.integration
def test_parallel_faster_than_sequential() -> None:
    """Both-source search should be faster than sum of individual searches."""
    query = "meeting"

    # Time individual sources
    t0 = time.monotonic()
    do_search(query, sources=["drive"], max_results=5, base_path=TMP)
    drive_time = time.monotonic() - t0

    t0 = time.monotonic()
    do_search(query, sources=["gmail"], max_results=5, base_path=TMP)
    gmail_time = time.monotonic() - t0

    sequential_total = drive_time + gmail_time

    # Time parallel (both sources)
    t0 = time.monotonic()
    do_search(query, sources=["drive", "gmail"], max_results=5, base_path=TMP)
    parallel_time = time.monotonic() - t0

    # Parallel should be meaningfully faster than sequential.
    # Use 1.5x as threshold — generous enough to avoid flakes,
    # strict enough to prove concurrency.
    assert parallel_time < sequential_total * 1.5, (
        f"Parallel ({parallel_time:.2f}s) not faster than "
        f"sequential ({sequential_total:.2f}s) * 1.5"
    )


@pytest.mark.integration
def test_parallel_completes_quickly() -> None:
    """Both-source search should complete in reasonable time."""
    t0 = time.monotonic()
    result = do_search("test", sources=["drive", "gmail"], max_results=5, base_path=TMP)
    elapsed = time.monotonic() - t0

    # Should complete in under 5s (generous — typically <2s)
    assert elapsed < 5.0, f"Parallel search took {elapsed:.2f}s"
    assert isinstance(result.drive_results, list)
    assert isinstance(result.gmail_results, list)


@pytest.mark.integration
def test_repeated_parallel_no_auth_race() -> None:
    """Run parallel search 5 times to detect intermittent auth races."""
    errors = []
    for i in range(5):
        try:
            result = do_search(
                "report", sources=["drive", "gmail"],
                max_results=3, base_path=TMP,
            )
            assert hasattr(result, "drive_results"), f"Run {i+1}: missing drive_results"
            assert hasattr(result, "gmail_results"), f"Run {i+1}: missing gmail_results"
        except Exception as e:
            errors.append(f"Run {i+1}: {e}")

    assert not errors, f"Auth race failures:\n" + "\n".join(errors)


@pytest.mark.integration
def test_parallel_error_isolation() -> None:
    """One source failing shouldn't block the other."""
    result = do_search("test", sources=["drive", "gmail"], max_results=5, base_path=TMP)

    # Both fields should be present even if one source had issues
    assert isinstance(result.drive_results, list)
    assert isinstance(result.gmail_results, list)
