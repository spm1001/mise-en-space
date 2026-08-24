# mise-jefaki probes — Drive `name contains` semantics + the corpus a name search covers

Five live batteries against the ITV corpus (user OAuth, 2026-08-24), all probe files created in scratch and deleted after measurement (cleanup recorded in each results JSON). Battery 3 is read-only, run on the original incident's own artefact.

## The headline: the hyphen was innocent

The incident (mise-dehebi evidence-close essayeur, 2026-08-24): `raw_query="name contains 'cudoba-probe'"` returned ZERO while the file existed and a folder-scoped listing found it. The folklore blamed hyphen tokenisation. Measured, the mechanism is the **corpus, not the term**:

- `includeItemsFromAllDrives=true` does NOT widen the searched corpus — it only admits shared-drive items into whatever corpus the query already covers, and the default is `user` (files **created by, opened by, or shared directly with** the user).
- `cudoba-probe.md` was created by the agent-spike service account in a Shared Drive and never opened by the searching user → outside the `user` corpus → invisible to every bare-filter query, `name contains` and exact `name =` alike (`results3.json`: default corpus 0/0, `corpora=allDrives` 1/1).
- A parents-scoped listing widens the effective corpus implicitly (the Drive docs' "this can change depending on the filter set through the q parameter") — which is exactly why the incident's folder listing found the file and made the name search's zero look like term semantics.
- The incident's "known-positive that also nulled" (`somiho-probe.md`) sits in the same Shared Drive: same mechanism, not a second fault.
- Indexing lag is dead as an explanation: the corpus-wide null reproduced **hours** after the file's creation, and in batteries 1/2/4/5 fresh My-Drive files were name-indexed within seconds (control poll green on the first attempt).

Fix shipped with this probe: `search_files` sends `corpora=allDrives` whenever `include_shared_drives=True`, reads `incompleteSearch` back (new mask field + `DriveSearchResults.incomplete`), and the search tool cues `drive_incomplete` when Google abandons corpora coverage. Live re-verify in `verify_fix.json`: the incident query now returns `cudoba-probe.md` through the unmodified call path, and a My-Drive control still matches.

## The tokenisation model (40 queries, zero exceptions)

`name contains 'T'` matches file F iff:

- **(A) whole-token AND**: every token of T equals a whole token of F's name — case-insensitive, any order, any position. Names and terms tokenise identically on punctuation (`-`, `_`, `.`), whitespace, AND letter↔digit boundaries (`arm2` → `arm`, `2` — pinned by the bare-digit query `'2'` matching, battery 4); the separator characters themselves are interchangeable (`'arm2.hyphen'`, `'arm2 hyphen'`, `'arm2-hyphen'` all match a name containing `-arm2-hyphen-`); **or**
- **(B) literal name prefix**: T is a literal (case-insensitive) prefix of F's full name string — `'jefaki-arm2-hyph'` and `'jefaki-arm2-hyphen-probe.m'` both match `jefaki-arm2-hyphen-probe.md` (battery 5).

What does NOT match: substrings (`'efaki'`, `'rm2'`), prefixes of non-leading tokens (`'hyph'`, `'prob'` — 0 hits while `'hyphen'`, `'probe'` hit), and multi-token terms that are neither all-whole-tokens nor a literal name prefix (`'jefak-arm2'`, `'hyphen-pro'` → 0).

Consequences the `drive_name_semantics` cue teaches on a zero: a hyphenated FULL filename term is findable as typed (both A and B); a mid-name fragment cut mid-token is not, and its null lies.

## Files

| File | What |
|---|---|
| `probe_tokenisation.py` / `results.json` | Battery 1 — 22 queries, 3 files (hyphen/underscore/dot), folder-scoped exact counts |
| `probe_tokenisation_2.py` / `results2.json` | Battery 2 — phrase-vs-AND, order, cross-separator, + corpus-wide incident recheck (somiho/cudoba nulls from default corpus) |
| `probe_corpora_3.py` / `results3.json` | Battery 3 — READ-ONLY corpora discrimination on the incident artefact |
| `probe_battery_4.py` / `results4.json` | Battery 4 — digit-boundary pin; shared-drive self-created half SKIPPED (drives.create 403, org policy — that cell stays unmeasured) |
| `probe_battery_5.py` / `results5.json` | Battery 5 — multi-token literal-name-prefix terms |
| `verify_fix_live.py` / `verify_fix.json` | Post-fix live green: incident query finds the file; cue fires on a real null; My-Drive control unregressed |
| `verify_fix_post_refactor.json` | Same script re-run after the size-ceiling refactor (cues → `tools/search_drive_cues.py`, formatters → `tools/search_format.py`) — the live green re-earned through the shipped call path |

Unmeasured cells, named: whether a file the user *created themselves* in a Shared Drive is inside their `user` corpus (probe blocked by drives.create 403 — irrelevant to the fix, which covers both cases); fullText-null behaviour on fresh files (recorded in battery 1 but not load-bearing — content indexing lag makes fullText nulls inconclusive by design).
