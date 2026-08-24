# mise-huwubi v2-surface probes — 2026-08-24 session artefacts

Referenced by mise-huwubi's closing verdicts and the four adoption items they minted
(mise-wagina, mise-gojoku, mise-fowabo, mise-vubeku). Candidate 1 (elicitation) was
probed the previous day — its artefacts live in `../2026-08-23-mcp2-probes/`.

| File | What it proved |
|---|---|
| `otel_version_probe.py` | One in-process server+client over memory streams, four wire-level answers: **C2** the default `OpenTelemetryMiddleware` emits per-call SERVER spans with real durations once a TracerProvider is wired (120ms sleep → 121.3ms span, `gen_ai.tool.name` attached); **C3** `instructions=` lands in the initialize result; **C5** `version=` lands in serverInfo; **C4** a typed-return tool gets an auto-detected outputSchema and the result rides the wire TWICE (structuredContent + JSON text block). Also a live specimen of the python-name/wire-name trap (`serverInfo` vs `server_info`) — the same family as the zidipo harvester instrument trap. |
| `cc_probe_server.py` + `cc-probe-config.json` | The CC-side halves, via `claude -p --strict-mcp-config`: **C3** CC injects server instructions into the model's context under an "MCP Server Instructions" heading (canary echoed verbatim, headless; corroborated interactively by this estate's own airtable block); **C4** the model receives ONE representation only — the compact JSON text block; structuredContent never reaches it. |
| (inline, no file) | mise's own wire, working-tree server over real stdio: `version=''`, `instructions=None` (both gaps live), and all three tools now carry a permissive ~75-byte auto-detected outputSchema post-2.x-migration — noted on mise-zidipo for the next Tier-0 re-harvest. |
