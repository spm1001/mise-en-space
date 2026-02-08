# Test Design

How mise-en-space is tested, what's covered, and where the gaps are.

## Philosophy

Tests here follow Simon Willison's principle: **inline snapshots over abstraction**. Expected output lives in the test file, not in external golden files. A failing test should tell you what changed without chasing references.

The architecture helps: extractors are pure functions (data in → string out), so most tests are straightforward fixture-to-output assertions with no mocking needed.

## Two Tiers

| Tier | Command | What it needs | Speed |
|------|---------|---------------|-------|
| **Unit** | `uv run pytest tests/unit -x -q` | Nothing — fixtures are JSON | ~1s for 450+ tests |
| **Integration** | `uv run pytest -m integration` | `token.json` + real Google resources | ~30s, hits live APIs |

Unit tests run by default. Integration tests require `-m integration` and IDs configured in `fixtures/integration_ids.json`.

## Fixture Strategy

### Synthetic Fixtures

Hand-crafted JSON in `fixtures/` designed to exercise specific edge cases:

| Fixture | Tests |
|---------|-------|
| `sheets/basic.json` | CSV escaping: commas, quotes, newlines in cells |
| `docs/basic.json` | Multi-tab with all paragraph element types |
| `gmail/thread.json` | 3-message thread with signatures and quotes |
| `comments/basic.json` | Threaded replies, resolved comments, mentions |
| `malformed/*.json` | Missing fields, null values, empty bodies |

### Real Fixtures

Captured from live APIs via `scripts/capture_fixtures.py`, sanitized via `scripts/sanitize_fixtures.py`. These catch issues synthetic fixtures miss — real API quirks, unexpected field combinations.

| Fixture | Source |
|---------|--------|
| `docs/real_multi_tab.json` | 3-tab test document |
| `docs/real_single_tab.json` | Single tab document |
| `sheets/real_spreadsheet.json` | Test spreadsheet |
| `gmail/real_thread.json` | 2-message thread (sanitized) |
| `slides/real_presentation.json` | 7-slide presentation |
| `comments/real_comments.json` | Comments from test document |

`tests/conftest.py` loads these and converts to typed dataclasses from `models.py`.

### Mocking

`tests/mock_utils.py` provides `make_http_error(status, message)` for simulating Google API errors (403, 404, 429, 500).

`conftest.py` provides patched service fixtures (`patch_drive_service`, `patch_slides_service`, etc.) that replace `get_*_service()` calls.

## What's Covered

### Extractors (pure functions) — strong coverage

Every extractor has dedicated unit tests plus cross-cutting tests:

| Extractor | Dedicated | Also tested by |
|-----------|-----------|----------------|
| `extractors/sheets.py` | `test_sheets` (7) | `test_warning_behaviors`, `test_real_fixtures`, `test_negative_paths` |
| `extractors/docs.py` | `test_docs` (22) | `test_warning_behaviors`, `test_real_fixtures`, `test_negative_paths` |
| `extractors/slides.py` | `test_slides` (33) | `test_real_fixtures`, `test_negative_paths` |
| `extractors/gmail.py` | `test_gmail_extractor` (25) | `test_warning_behaviors`, `test_negative_paths` |
| `extractors/comments.py` | `test_comments` (13) | — |
| `extractors/web.py` | `test_web` (70) | — |

### Cross-cutting test files

| File | Tests | What it exercises |
|------|------:|-------------------|
| `test_negative_paths` | 17 | Malformed input across all extractors — missing fields, null values, empty bodies |
| `test_real_fixtures` | 28 | Smoke tests: real API responses through full extraction pipeline |
| `test_warning_behaviors` | 16 | Warnings populated correctly across extractors |
| `test_architecture` | 4 | Layer boundary enforcement via AST parsing (extractors can't import adapters) |
| `test_security` | 4 | Query escaping regression, raw interpolation scanning |

### Adapters — mixed coverage

| Adapter | Unit tests | Integration tests |
|---------|:----------:|:-----------------:|
| `adapters/slides.py` | `test_slides_adapter` (7) — thumbnail failures | `test_slides_adapter` |
| `adapters/pdf.py` | `test_pdf` (16) — markitdown/Drive fallback | `test_pdf_adapter` |
| `adapters/office.py` | `test_office` (10) — MIME mapping, conversion | `test_office_adapter` |
| `adapters/image.py` | `test_image_fetch` (20) — MIME, SVG, deposit | — |
| `adapters/web.py` | `test_web` (70) — auth, fallback, binary | — |
| `adapters/charts.py` | `test_charts_adapter` (16) — metadata, PNG | — |
| `adapters/cdp.py` | `test_cdp` (7) — availability, cookies | — |
| `adapters/genai.py` | `test_genai` (16) — SAPISIDHASH, summaries | — |
| `adapters/activity.py` | `test_activity` (12) — models only | — |
| `adapters/drive.py` | Partial — `_parse_email_context` only | `test_drive_adapter` |
| `adapters/docs.py` | **None** | `test_docs_adapter` |
| `adapters/sheets.py` | **None** | `test_sheets_adapter` |
| `adapters/gmail.py` | **None** | `test_gmail_adapter` |
| `adapters/conversion.py` | Indirect (via pdf/office) | — |
| `adapters/services.py` | **None** | — |

### Tools — routing tested, not much else

| Tool | Unit tests | Integration tests |
|------|:----------:|:-----------------:|
| `tools/fetch.py` | `test_fetch` (11) — ID routing | `test_fetch_tool` (13) |
| `tools/search.py` | `test_cross_source` (10) — formatting | `test_search_tool` (5) |
| `tools/create.py` | **None** | `test_create_tool` (5) |

### Infrastructure — tested

| Module | Tests | What |
|--------|------:|------|
| `validation.py` | 35 | URL/ID parsing, Gmail ID conversion |
| `retry.py` | 40 | Status extraction, retry decisions, backoff, MiseError conversion |
| `filters.py` | 30 | Trivial attachment detection (calendar, vcard, small images) |
| `workspace/manager.py` | 22 | Slugify, deposit folders, content/thumbnail/manifest writing |

### Infrastructure — not tested

| Module | Why it's OK (or not) |
|--------|---------------------|
| `auth.py` | OAuth flow — hard to unit test, verified by integration tests existing |
| `oauth_config.py` | Config constants — nothing to test |
| `server.py` | MCP registration — thin wrapper, tested indirectly by integration tests |
| `cli.py` | CLI entry point — same tools as MCP, not independently tested |
| `logging_config.py` | Setup only — nothing to test |

## Known Gaps

Organized by the arc outcome **mise-3uu** (confident deployability via comprehensive test coverage).

### High value — adapter unit tests

The core Google API adapters (docs, sheets, gmail, drive) have **no unit tests**. They're only tested via integration tests that hit live APIs. This means:
- Can't test error paths without provoking real errors
- CI can't run them (no credentials)
- Slow feedback loop

The pattern exists — `test_slides_adapter.py` and `test_pdf.py` show how to mock `get_*_service()` and test adapter logic. Extending this to docs/sheets/gmail/drive is mechanical.

**Related arc items:** mise-3uu.2 (adapter integration tests), mise-rufile (capture fixtures for untested adapters)

### Medium value — round-trip and edge cases

| Gap | Arc item | Notes |
|-----|----------|-------|
| Gmail body round-trip (HTML→text→markdown) | mise-3uu.1 | Signature stripping + quote removal + HTML conversion chain |
| Charts adapter integration | mise-rohali | Unit tests exist but no real API fixture |
| Activity API adapter functions | mise-lusome, mise-DiZaje | Models tested, adapter functions not |
| HTML body with PDF Content-Type | mise-nofifu | CDN serves HTML with `application/pdf` header |
| Raw text unit tests | mise-sujuKo | `test_text_fetch` exists (8 tests) but may not cover all MIME types |

### Lower value — completeness

| Gap | Arc item | Notes |
|-----|----------|-------|
| Sanitized test fixtures | mise-kaRuro | Some real fixtures may have PII — systematic sanitization pass needed |
| Purpose-built test doc with rich comments | mise-jutike | Current comment fixtures are thin |
| New API services test | mise-HoWeKe | Activity API, charts — exercise in real environment |

## Adding Tests

### Unit test for an extractor

Easiest category. Add fixture JSON, write assertion:

```python
def test_my_edge_case(basic_sheets_data):
    """Describe the specific edge case."""
    # Modify fixture for the scenario
    basic_sheets_data.values = {"Sheet1": [["a", "b"], ["c", ""]]}

    content = extract_sheets_content(basic_sheets_data)

    assert "a,b" in content
    assert content.endswith("\n")
```

### Unit test for an adapter

Mock the Google service, test the wiring:

```python
def test_fetch_document_multi_tab(patch_docs_service):
    """Multi-tab documents return all tabs."""
    mock_service = patch_docs_service
    mock_service.documents().get().execute.return_value = {
        "documentId": "abc",
        "tabs": [{"documentTab": {"body": {...}}}]
    }

    result = fetch_document("abc")

    assert len(result.tabs) == 1
    mock_service.documents().get.assert_called_with(
        documentId="abc", includeTabsContent=True
    )
```

### Integration test

Needs real IDs in `fixtures/integration_ids.json`:

```python
@pytest.mark.integration
def test_fetch_real_spreadsheet():
    ids = json.load(open("fixtures/integration_ids.json"))
    result = fetch_spreadsheet(ids["sheets"]["test_spreadsheet"])
    assert result.title
    assert len(result.values) > 0
```

## Test Counts (as of Feb 2026)

| Category | Files | Tests |
|----------|------:|------:|
| Unit | 26 | ~450 |
| Integration | 10 | ~62 |
| **Total** | **36** | **~512** |

Run time: unit ~1s, integration ~30s.
