# Inline-reply detector: real-mail census (mise-lopune, 27 Sep 2026)

`census.py` runs the Gmail extractor over real threads and prints every thread where the inline-reply recovery fired, with the passages it kept. The gates in `extractors/inline_replies.py` came from reading those firings, not from the synthetic tests. Re-run it before loosening a gate. For a positive control, point it at the live repro thread cited on mise-lopune's card: a query for that thread's subject must fire. The subject stays off this public repo.

| Run | Sample | Multi-message threads | Firings | True answer-in-quote |
|---|---|---|---|---|
| First cut: novelty + trailing-footer rule | 300 threads, last 120 days, no promotions/social | 43 | 10 | 1 |
| Gated | same 300 | 43 | 1 | 1 |
| Gated | 150 threads matching "response below", "see below", "inline", "in red" and similar, last 2 years | 97 | 3 | 3 |

The nine false firings in the first cut, by class, each now answered by a gate:

- **Gateway legal footers.** Added in transit, so the quote carries them and the earlier message as fetched does not. Answered by treating any paragraph with a footer phrase as neutral.
- **Links the quoting client added:** maps links under addresses, `<+44…>` phone links, `<name@host>`. Answered by counting link targets as neither old nor new.
- **Forward header blocks** (`From:` / `Date:` / `To:` lines). Answered by treating header lines as neutral.
- **Chains of messages that never reached this thread.** Answered by the insertion rule: the quoted words either side of a passage must sit side by side in an earlier message.
- **Notification plain-text renderings.** Answered by the pointer gate: the reply's own text must say "below", "inline" or similar, or be empty.

Recall is unmeasured: the targeted run shows the gated detector finds replies that point into their quote, and nothing here counts the ones it misses. One true case was mixed-format: only the first line of each inline comment was `>`-quoted and the rest sat unquoted in the reply body. The recovered passages are therefore fragments, and the remainder was already in the deposit.
