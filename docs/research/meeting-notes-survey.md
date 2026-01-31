# Meeting Notes Survey

Research for `mise-curuci`: understanding meeting note patterns in Drive.

## Observed Patterns

### Pattern 1: Gemini Notes (Google Meet) — **Primary target**

Most common pattern. Generated automatically from Meet recordings.

**Structure:**
- Email notification with inline summary (~500 words)
- Linked Doc with 2 tabs:
  - **Notes tab**: Summary + timestamped discussion bullets (~12k chars)
  - **Transcript tab**: Verbatim transcript (~58k chars) — the real context buster

**Naming convention:** `{Meeting Title} – YYYY/MM/DD HH:MM GMT – Notes by Gemini`

**Sample sizes (recent docs):**
| Doc | Notes tab | Transcript tab | Total |
|-----|-----------|----------------|-------|
| Desired Outcomes and StW Update | 12,338 | 58,518 | 70,942 |
| MIT Speedrun (Jan 26) | ~10k | ~39k | 49,265 |
| MIT Speedrun (Jan 19) | ~10k | ~37k | 47,351 |
| Snowflake Lantern chat | ~8k | ~23k | 31,275 |

**Signal density:**
- Email summary: HIGH (LLM-generated, actionable)
- Notes tab bullets: MEDIUM (timestamped but repetitive, lots of filler phrases)
- Transcript tab: LOW (verbatim speech, "um", "yeah", tangents, repetition)

**Edge case:** Coworking/silent meetings produce empty summaries:
> "A summary wasn't produced for this meeting because there wasn't enough conversation in a supported language."

### Pattern 2: Fathom Notes — **Structured, email-only**

Third-party meeting assistant. Content is in email body, links to hosted recording.

**Structure:**
- Action items with assignees (~200 words)
- Key takeaways as bullets (~300 words)
- Topics with nested sub-bullets (~500 words)
- Links to fathom.video (often inaccessible to others)

**Signal density:** HIGH — already distilled, attributed actions

**Limitation:** Linked recordings/summaries require Fathom account access.

### Pattern 3: Human Notes in Reference Docs — **Accumulated, not per-meeting**

Long-running docs organized by entity (client or supplier).

**Examples:**
- Client Reference: 14 tabs, ~12k chars total
- Supplier Reference: 22 tabs, ~66k chars total

**Structure per meeting entry:**
```
## {Date} |
Attendees: ...

Notes
- bullet points

Action items
- ...
```

**Signal density:** MEDIUM-HIGH — human-filtered, but variable quality

### Pattern 4: Ad-hoc Human Notes

Standalone docs, often in Work Notes folder. Infrequent, varied format.

## Size Distribution

From search results (recent 3 months):

| Size bucket | Count | Examples |
|-------------|-------|----------|
| <10k chars | 3 | Short meetings, failed transcriptions |
| 10-30k chars | 4 | 1:1s, focused discussions |
| 30-50k chars | 5 | Team meetings, multi-topic |
| >50k chars | 3 | Long strategy sessions |

**Median Gemini doc: ~30k chars** — too large for naive inclusion in context.

## Key Insights

1. **The transcript tab is the problem** — 50-60k chars of low-signal verbatim speech. The Notes tab and email summary are already decent.

2. **Gemini's summary is passable** — captures key points and action items. Not knowledge-graph aware, but serviceable for basic retrieval.

3. **Fathom's structure is extraction-friendly** — action items with assignees, nested topics. Better signal density than Gemini.

4. **Human notes are sparse but high-signal** — when they exist, they're already distilled.

5. **Recurring meetings accumulate** — MIT Speedrun generates ~50k chars/week. Context busting is cumulative.

## Implications for Extraction Strategy

### What needs distillation:
- Gemini transcript tabs (primary target)
- Gemini Notes tabs (secondary — already semi-structured)

### What can pass through:
- Gemini email summaries (already LLM-distilled)
- Fathom email content (already structured)
- Human notes (already filtered)

### Potential approaches:
1. **Tab-aware fetch**: Skip transcript tab by default, include only on request
2. **Selective distillation**: Only run LLM on transcript when Notes tab is insufficient
3. **Pattern-based extraction**: Regex for action items, decisions, @mentions
4. **Hybrid**: Pattern extraction first, LLM only for complex content

## Test Corpus

Representative samples for prototype testing:

| ID | Type | Size | Characteristics |
|----|------|------|-----------------|
| `13plDyWniPlYSdv3EyyB4txV2hekENk_VmDfhelY6NKQ` | Gemini 2-tab | 71k | Strategy meeting, clear decisions |
| `1-MK3pETvT-vGcvHWNIgtD3UiBjV0WEce4PX_DN1qC0w` | Gemini 2-tab | 49k | Team standup, many participants |
| `1YzvNK-6CPn2fq6UwynYNJrecqT_1QRKKNjnVMPiZ09k` | Gemini empty | 5k | Coworking session (edge case) |
| `19aef5261bd9bcc3` | Fathom email | 2k | NIQ sync, structured output |
| `1owM77DjcTwHjrTfHs9zEfQfNyeFfmtheokBGvkPD8II` | Human reference | 12k | Client notes, accumulated |

---

*Survey completed 2026-01-30 for mise-curuci*
