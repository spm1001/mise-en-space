# Field report: mise cannot put a table into an existing Google Doc (22 Sep 2026)

**Where it bit.** The targeting-responses lane on the atelier, 21 and 22 Sep 2026, editing two live Docs that Sameer and three colleagues were commenting on ("Targeting data: what we buy…" and "Dunnhumby Deep Dive"). Four times the work needed a table added or a placeholder table filled in a Doc that already carried comments and tables. Each time mise had no move:

- `replace_text` and `append` write plain text, so a markdown table lands as pipes and dashes.
- `overwrite` renders markdown tables through the Drive import, but replaces the whole Doc, so every comment anchor and every existing table dies, and it refuses on multi-tab Docs anyway.
- `append` with `tab=` is plain text too.

So the session built "paste-pack" Docs (markdown rendered into a fresh Doc) and asked Sameer to copy tables across by hand, three times. On the fourth he asked why, and the answer was that the Docs API can do it directly, so the session hand-rolled it (below) in twenty minutes. Sameer's ruling: that is a project for mise, not a hand-roll.

**What the Docs API route does, measured live on `1qdYFK9PPy2U5AbzQLQQzvj9iY3ZiMjHWzjGaI_TZjbk`:**

1. Fill an existing placeholder table: `documents.get`, find the table whose first cell starts with a known string, compute each cell's text range from its paragraph elements (start of first paragraph to end of last paragraph minus the trailing newline), then one `batchUpdate` of `deleteContentRange` + `insertText` per cell, highest index first so earlier indexes stay valid.
2. Insert a new table after a paragraph: find the paragraph by its opening text, `insertText("\n")` at its end minus one, `insertTable{rows, columns}` at its end index, re-`get` the document, locate the new empty table, then `insertText` into every cell highest-index-first, then `updateTextStyle bold` on the header row's cell ranges.

Both took one script, ADC credentials with the `documents` scope, and `docs.googleapis.com` enabled on the quota project. Cells were populated correctly on first run; a `fetch` afterwards rendered both tables as markdown with the right values.

**The gap to close in mise, as a `do()` op or two:**

- `do(operation="insert_table", file_id, anchor="<opening words of the paragraph the table follows>" | "end", content="<markdown table>")` — parse the markdown table, insert after the anchor paragraph, fill cells, bold the header row, return `cues.restore_point` like every other Doc mutation and a `cues.table_index`.
- `do(operation="update_table", file_id, anchor="<opening words of the table's first cell>", content="<markdown table>")` — replace the cells of an existing table of the same shape (refuse with a teaching error if shapes differ), so a human-drafted placeholder table with X's can be filled by the agent.
- Keep the `replace_text` warning that a find string spanning a table cell will not match.

Nothing here is speculative: the two code paths above ran and the results were read back. The script that ran is `~/briefs/docs/dh_tables.py` on the atelier (Sameer's home); it is ninety lines and most of them are index bookkeeping, which is exactly what mise exists to hide.

**Cost of the gap this week:** three paste rounds on live Docs, one confused exchange about why, and a session that stopped using mise for the thing it is for.
