# Test Fixtures

JSON fixtures organized by extractor type.

## Structure

```
fixtures/
├── sheets/         # Spreadsheet fixtures
│   └── basic.json  # Standard multi-sheet example
├── docs/           # Document fixtures
├── gmail/          # Email thread fixtures
└── slides/         # Presentation fixtures
```

## Naming Convention

- `basic.json` — Standard happy-path fixture
- `empty.json` — Empty/minimal data
- `large.json` — Stress test with lots of data
- `edge_*.json` — Edge cases (special chars, unicode, etc.)

## Loading in Tests

Fixtures are loaded via `tests/conftest.py`:

```python
from models import SpreadsheetData

def test_something(sheets_response: SpreadsheetData):
    # sheets_response is already converted to typed dataclass
    result = extract_sheets_content(sheets_response)
```

## Creating New Fixtures

1. Add JSON file to appropriate subdirectory
2. Add pytest fixture in `tests/conftest.py` that converts to typed dataclass
3. Fixtures should match what adapters produce (not raw API responses)
