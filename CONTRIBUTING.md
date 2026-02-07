# Contributing

You're probably a Claude. That's fine — most contributors here are. This guide works for humans too, but the defaults assume you're an AI agent working in someone's Claude Code session.

## Posture

**File issues, not PRs.** This repo has an owner who makes architectural decisions. If you find a bug or want a feature, file it via arc:

```bash
arc new "Short description" --for <parent-outcome> \
  --why "What's broken or missing" \
  --what "What needs to happen" \
  --done "How to verify it's fixed"
```

If you're not sure which outcome it belongs under, file it standalone — the owner will triage.

**Don't modify architecture without discussion.** The layer separation (extractors → adapters → tools) is load-bearing. If you think it needs changing, file an issue explaining why.

## The One Rule

**Extractors never import from adapters or tools.** They're pure functions — no I/O, no API calls, no file access. This is what makes them testable without credentials.

The full layer rules:
- Extractors NEVER import from adapters or tools (no I/O)
- Adapters NEVER import from tools
- Adapters MAY import parsing utilities from extractors
- Tools wire adapters → extractors → workspace

If you're adding code and aren't sure where it goes: if it does I/O, it's an adapter. If it transforms data, it's an extractor. If it orchestrates both and writes to disk, it's a tool.

## Quality Gates

Before any commit:

```bash
uv run pytest tests/unit -x -q          # 512+ unit tests, must all pass
uv run mypy models.py extractors/ adapters/ validation.py workspace/
```

Integration tests need real credentials and `-m integration`:
```bash
uv run pytest -m integration             # Requires token.json
```

## Adding a Content Type

The pattern is always: adapter (API call) → extractor (parse) → tool (wire + deposit).

1. **Adapter** in `adapters/` — thin wrapper that calls the API and returns raw data
2. **Model** in `models.py` — dataclass for the response (with `warnings: list[str]`)
3. **Extractor** in `extractors/` — pure function that transforms the model into markdown/CSV
4. **Tool wiring** in `tools/fetch.py` — route by MIME type, call adapter → extractor → deposit
5. **Tests** in `tests/unit/` — fixture JSON → model → extractor → expected output

Look at `adapters/image.py` + `extractors/image.py` for a clean small example.

## Filing a Good Bug Report

Include:
- **File paths and function names** — not "the sheets extractor is broken" but "`extract_sheets_content` in `extractors/sheets.py` returns empty string for..."
- **Input that triggered it** — file ID, URL, or a description of the content structure
- **Expected vs actual** — what you got, what you wanted
- **Error messages** — full traceback if available

Via arc:
```bash
arc new "extract_sheets_content returns empty for merged cells" \
  --why "Merged cells in row 3-5 of test spreadsheet 1abc123 produce no output" \
  --what "Handle rowSpan/colSpan in sheets extractor, add fixture for merged cells" \
  --done "Merged cell fixture extracts correctly, existing tests still pass"
```

## Key References

- **CLAUDE.md** — full architecture docs, design decisions, API patterns
- **docs/information-flow.md** — how data flows from APIs through extraction to deposit
- **README.md** — what this project is and why it exists
