# Integration Tests

Tests that hit real Google APIs with real credentials.

## Prerequisites

1. Valid `token.json` (symlinked from mcp-google-workspace)
2. Test files in your Google account (see Test Data section)
3. Run with: `uv run pytest tests/integration -v`

## Running

```bash
# Skip integration tests (default in CI)
uv run pytest tests/unit -v

# Run integration tests only
uv run pytest tests/integration -v

# Run all tests
uv run pytest tests/ -v
```

## Markers

Integration tests are marked with `@pytest.mark.integration`:

```python
import pytest

@pytest.mark.integration
def test_fetch_real_doc():
    ...
```

To skip integration tests by default, add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = ["integration: tests that hit real APIs"]
addopts = "-m 'not integration'"  # Skip by default
```

Then run with `-m integration` to include them.

## Test Data

Integration tests use real files in the test account. Document IDs are stored in:
- `fixtures/integration_ids.json` (gitignored)

Example:
```json
{
  "test_doc_id": "1abc...",
  "test_sheet_id": "1def...",
  "test_thread_id": "189a..."
}
```

Create test files manually, then record their IDs.

## Best Practices

1. **Don't create files** — Tests should read, not write
2. **Use stable test data** — Don't depend on inbox state
3. **Handle rate limits** — Add delays between API calls if needed
4. **Clean up nothing** — Read-only operations only
5. **Skip gracefully** — If credentials missing, skip with clear message

Example:
```python
import pytest
from pathlib import Path

IDS_FILE = Path(__file__).parent.parent.parent / "fixtures" / "integration_ids.json"

@pytest.fixture
def integration_ids():
    if not IDS_FILE.exists():
        pytest.skip("Integration IDs not configured")
    with open(IDS_FILE) as f:
        return json.load(f)

@pytest.mark.integration
def test_fetch_real_sheet(integration_ids):
    sheet_id = integration_ids["test_sheet_id"]
    # ... test with real API
```

## What to Test

- **Adapters:** Do API calls return expected shapes?
- **End-to-end:** Does search → fetch → extract produce valid output?
- **Error handling:** What happens with invalid IDs, no permissions?

Don't duplicate unit test logic — unit tests cover extraction, integration tests cover API interaction.
