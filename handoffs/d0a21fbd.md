# Handoff — 2026-03-22

session_id: d0a21fbd-1ad8-4914-9e51-d3fc4bbe0f10
purpose: Shipped file dates, binary uploads, image embedding (0.5.1) + hardening pass

## Done
- Reviewed and triaged 6 singleton bon items — closed mise-dodage, converted mise-jatadu under mise-zirupo
- Implemented mise-pudibu: createdTime/modifiedTime in search results, previews, and fetch manifests (~30 lines, 4 files)
- Implemented mise-likaba: file_path parameter for binary uploads via do(create, doc_type='file')
- Implemented mise-dulajo: image embedding in Google Docs via Docs API batchUpdate (post-creation injection)
- Hardening pass: made render_svg_to_png public (layer fix), UUID multipart boundary, permission ID from API response, text run concatenation for placeholder search, CLAUDE.md updated
- Filed mise-gozati for integration testing image embedding against real APIs
- Version bumped to 0.5.1, committed, pushed (2 commits: feat + fix)

## Gotchas
- Image embedding has never run against real Drive+Docs API — all mocked. The batchUpdate request format and permission lifecycle are the highest-risk areas. mise-gozati tracks this.
- `_render_svg_to_png` was renamed to `render_svg_to_png` — any code referencing the old private name will break.
- The understanding doc reference to "hardcoded boundary (mise_upload_boundary)" is now stale — boundary is UUID-based.
- `tools/create.py` is getting long (~760 lines). The image embedding section (~200 lines) could be extracted to `tools/embed.py` if it grows further.

## Risks
- Docs API `insertInlineImage` URI format (`https://drive.google.com/uc?export=view&id={id}`) is assumed correct but untested against real API. If Google rejects this URI format, all image embeddings will fail silently (reported in cues.image_errors).
- Enterprise Workspace accounts (ITV) will likely 403 on `_share_publicly` due to DLP policies. Graceful degradation is confirmed in code but not tested.
- `_find_placeholder_indices` assumes placeholders are within a single paragraph. If Drive's markdown import splits a placeholder across paragraphs (unlikely with Unicode sentinels), it won't be found.

## Next
- Integration test image embedding (mise-gozati) — highest priority before this feature is relied on
- mise-tagemu (Apps Script email extractor) — deferred again this session, third time running
- Remaining backlog: mise-heferu (image edge cases), mise-gubaci (meeting prep), mise-cadadi (latency), mise-zirupo (remote deployment chain)

## Commands
```bash
# Verify image embedding test coverage
uv run python -m pytest tests/unit/test_create.py -k "image" --no-cov -v

# Quick integration smoke test (needs OAuth token)
uv run python -c "from tools.create import _parse_image_refs; print(_parse_image_refs('![x](test.png)'))"
```

## Reflection
**Claude observed:** The orient phase caught real issues — the layer violation, boundary collision, and text run splitting were all genuine risks that would have been invisible to the next session. The biggest unknown remains the Docs API integration — every step of image embedding is mocked.

**User noted:** Corporate DLP blocking is expected for ITV account. GCS signed URLs is the clean fix if needed. Image integration testing needs a bon, not just a handoff note.
