"""
Integration tests for the search MCP tool.

Run with: uv run pytest tests/integration/test_search_tool.py -v -m integration

Rewritten 2026-09-23 (mise-pirusu): these tests predated the deposit model.
search() now requires base_path, answers with counts + a preview, and writes
the full results to a JSON deposit. The old tests asserted inline
drive_results/gmail_results, so four failed outright and the format test
passed VACUOUSLY — its `if result.get("drive_results")` guard was never true.
"""

import json
from pathlib import Path

import pytest

from server import search


def _search(tmp_path: Path, query: str, **kwargs) -> tuple[dict, dict]:
    """(response, deposited full results) for one search."""
    result = search(query, base_path=str(tmp_path), **kwargs)
    assert "error" not in result, result
    full = json.loads(Path(result["path"]).read_text())
    return result, full


@pytest.mark.integration
def test_search_drive_only(tmp_path: Path) -> None:
    result, full = _search(tmp_path, "test", sources=["drive"], max_results=5)
    assert result["query"] == "test" and result["sources"] == ["drive"]
    assert isinstance(full["drive_results"], list)
    assert result["drive_count"] == len(full["drive_results"])


@pytest.mark.integration
def test_search_gmail_only(tmp_path: Path) -> None:
    result, full = _search(tmp_path, "test", sources=["gmail"], max_results=5)
    assert isinstance(full["gmail_results"], list)
    assert result["gmail_count"] == len(full["gmail_results"])


@pytest.mark.integration
def test_search_both_sources(tmp_path: Path) -> None:
    result, full = _search(tmp_path, "meeting", max_results=3)
    assert result["sources"] == ["drive", "gmail"]
    assert "drive_results" in full and "gmail_results" in full


@pytest.mark.integration
def test_search_result_format(tmp_path: Path) -> None:
    """A common word must return rows in both sources, so the field checks
    cannot pass vacuously (the old version's guard never fired)."""
    _, full = _search(tmp_path, "meeting", max_results=5)
    assert full["drive_results"] and full["gmail_results"], "no rows to check the format on"
    drive = full["drive_results"][0]
    for key in ("id", "name", "mimeType"):
        assert key in drive, key
    gmail = full["gmail_results"][0]
    for key in ("thread_id", "subject", "snippet"):
        assert key in gmail, key


@pytest.mark.integration
def test_search_no_results(tmp_path: Path) -> None:
    result, full = _search(tmp_path, "xyzzy12345nosuchterm98765", max_results=5)
    assert result["drive_count"] == 0 and result["gmail_count"] == 0
    assert full["drive_results"] == [] and full["gmail_results"] == []
    assert not full.get("errors")
