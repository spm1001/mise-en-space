"""Drive search honesty cues (mise-jefaki).

Two ways a Drive result set lies by omission, each with its own cue:

- `drive_incomplete` — Google's incompleteSearch flag: corpora=allDrives lets
  the API stop before covering every drive and say so only here. Distinct from
  drive_truncated (OUR cap on a search that matched more; this is THEIR
  abandonment).
- `drive_name_semantics` — a zero on a punctuated `name contains` term reads
  as absence and often is not: Drive matches whole name tokens, never
  substrings. Measured live 2026-08-24; raw results in
  docs/research/2026-08-24-jefaki-name-probe/.

Sibling of tools/search.py (the search_calendar pattern) so the orchestrator
stays under the module-size ceiling.
"""

from validation import has_tokenising_separator, name_contains_terms


def drive_incomplete_cue() -> str:
    """Google stopped before covering every corpus — the zero may be partial."""
    return (
        "Google reported incompleteSearch=true — it stopped before covering "
        "every drive corpus, so this result set (including a zero) may be "
        "PARTIAL, not the population. Retry, narrow the query, or scope with "
        "folder_id before reading absence."
    )


def name_semantics_cue(raw_query: str) -> str | None:
    """The teaching cue for a zero-hit punctuated name-contains term.

    Returns None unless the raw query carries at least one `name contains`
    term with a separator Drive tokenises on — a single-token term already IS
    what Drive matches, and a cue there would cry wolf.
    """
    punctuated = [
        t for t in name_contains_terms(raw_query)
        if has_tokenising_separator(t)
    ]
    if not punctuated:
        return None
    return (
        f"0 Drive hits for the name term {punctuated[0]!r} — a null that is "
        "easy to over-read. Drive matches whole name TOKENS, not substrings: "
        "names split on punctuation, spaces and letter-digit boundaries "
        "(report-2026.pdf tokenises to report, 2026, pdf), a multi-token term "
        "is an AND of its whole tokens in any order, and the only substring "
        "honoured is a literal prefix of the ENTIRE name — so report-20 "
        "matches, eport-2026 never can. If the file may exist: retry with one "
        "whole token, exact equality (name = with the full filename), a "
        "fullText search, or a folder listing. Measured 2026-08-24 "
        "(mise-jefaki)."
    )
