"""
Control characters Google Docs destroys on the way in (mise-melaso).

Every route into a Google Doc runs through one of two engines, and both
throw characters away under an HTTP 200 with nothing in the response to
say so. Measured live 2026-08-24 on five scratch docs
(``docs/research/2026-08-24-melaso-control-chars/``), read back through
BOTH ``documents.get`` and the ``text/plain`` export:

===============  =====================  ==========================
character        Drive markdown import  Docs API insertText /
                 (create, overwrite)    replaceAllText (prepend,
                                        append, append tab=,
                                        replace_text)
===============  =====================  ==========================
``\\f``           deleted                deleted
``\\x00``         **TRUNCATES the doc**  deleted
``\\r\\n``         joined to a space      normalised to ``\\n``
``\\t``           survives               survives
``\\v``           survives               survives
===============  =====================  ==========================

Two of those are worth acting on.

**``\\f`` is mise's own page marker.** ``pdftotext -layout`` separates PDF
pages with a form feed, mise counts them into ``cues.page_markers``
(``pdf_page_fidelity`` in ``tools/fetch/common.py``) and places exhibit
anchors on them (``extractors/pdf_anchors.py``). So the single commonest
thing a caller pastes into a Doc — a PDF deposit's ``content.md`` — loses
every page boundary, silently, and the fetch cue that promised page
citations is left pointing at a document that has none. An isolated form
feed on its own line is deleted too (probe A5): the import has no
page-break reading of it.

**``\\x00`` costs the whole tail of the document on the import path.**
``BEFORE\\x00AFTER`` imported as a doc ending at ``BEFORE`` — ``AFTER`` and
every later paragraph gone, HTTP 200, no cue (probe A3; the same shape
with a plain ``X`` kept its tail, probe A4, which is the control that makes
the absence mean something). On a ``do(overwrite)`` that is destruction:
the doc's old content is already replaced by the truncated head.

So the write paths convert rather than pass through, and always say so:
form feeds become a visible ``---`` boundary (a real horizontal rule after
the markdown import, a literal ``---`` line on the plain-text insert
paths), NULs are dropped before the upload can truncate on them. Both are
cued every time — a transformation the caller can't see is the thing this
module exists to prevent.

``\\r\\n`` and ``\\t``/``\\v`` are deliberately NOT touched. Line-ending
normalisation is ordinary markdown behaviour and cueing it would be noise;
``\\v`` in particular must keep flowing through untouched, because
``markdown_import.convert_fenced_blocks`` relies on Docs importing
backslash hard breaks AS ``\\v`` inside code paragraphs.
"""

from __future__ import annotations

FORM_FEED = "\f"
NUL = "\x00"

# (warnings, counts) — what one sanitise pass changed, carried from the
# transform to the cue-folding call the same way FootnoteState is.
SanitiseState = tuple[list[str], dict[str, int]]

# One marker for both engines: the markdown import renders a `---` line as a
# real horizontal rule (probed live), and on the plain-text insert paths it
# reads as a separator in the text itself. Blank lines either side so the
# import can't read it as a setext heading underline for the line above.
PAGE_BREAK_MARKER = "\n\n---\n\n"


def _form_feed_warning(count: int, *, rich: bool) -> str:
    plural = "" if count == 1 else "s"
    rendered = (
        "a horizontal rule" if rich else "a literal '---' line (this path "
        "writes plain text)"
    )
    return (
        f"{count} form feed{plural} (\\f) in the content — Google Docs "
        f"DELETES form feeds on every write path (measured 2026-08-24), so "
        f"each has been replaced with a '---' page-boundary marker, which "
        f"imports as {rendered}. Form feeds are how mise marks PDF page "
        f"boundaries in content.md, so these are almost certainly page "
        f"breaks; the deposit still holds the originals."
    )


def _nul_warning(count: int, *, rich: bool) -> str:
    plural = "" if count == 1 else "s"
    consequence = (
        "Drive's markdown import TRUNCATES the document at the first NUL "
        "(everything after it is silently discarded, HTTP 200)"
        if rich
        else "the Docs API deletes NULs from inserted text"
    )
    return (
        f"{count} NUL byte{plural} (\\x00) removed from the content before "
        f"writing — {consequence} (measured 2026-08-24). A NUL in text "
        f"content usually means the source was binary or mis-decoded; "
        f"check the content is what you meant to write."
    )


def sanitise_doc_content(
    content: str | None, *, rich: bool
) -> tuple[str | None, list[str], dict[str, int]]:
    """Make content survive a Google Docs write visibly, or say what changed.

    ``rich=True`` for the Drive markdown import path (``do(create)`` on a
    doc, ``do(overwrite)`` on a Google Doc), ``rich=False`` for the Docs
    API insert paths (``prepend``, ``append``, ``append tab=``,
    ``replace_text``). The split changes only the wording and the NUL's
    consequence — the transformation itself is identical, so the same
    content lands the same way whichever door it came through.

    Returns ``(content, warnings, cues)``. ``cues`` carries
    ``page_breaks_marked`` and ``nuls_removed`` counts, present only when
    non-zero, for a caller that wants to branch rather than read prose.
    Clean content is returned unchanged with empty warnings and cues, so
    the call is free to make unconditionally.
    """
    if not content:
        return content, [], {}

    warnings: list[str] = []
    cues: dict[str, int] = {}

    form_feeds = content.count(FORM_FEED)
    if form_feeds:
        content = content.replace(FORM_FEED, PAGE_BREAK_MARKER)
        warnings.append(_form_feed_warning(form_feeds, rich=rich))
        cues["page_breaks_marked"] = form_feeds

    nuls = content.count(NUL)
    if nuls:
        content = content.replace(NUL, "")
        warnings.append(_nul_warning(nuls, rich=rich))
        cues["nuls_removed"] = nuls

    return content, warnings, cues


def sanitise_for_import(
    doc_type: str | None, content: str | None
) -> tuple[str | None, SanitiseState]:
    """One-line call-site wrapper for the Drive markdown IMPORT path.

    Gates on ``doc_type`` the way ``footnotes_for_import`` does, so
    ``do(create)`` can call it once without a branch: only ``doc='doc'``
    rides the import engine. ``doc_type='file'`` is a plain byte upload
    that preserves both characters (measured), and a sheet goes up as
    ``text/csv`` through a different engine that has not been measured —
    neither is silently transformed on a guess.
    """
    if doc_type != "doc":
        return content, ([], {})
    content, warnings, counts = sanitise_doc_content(content, rich=True)
    return content, (warnings, counts)


def sanitise_for_insert(content: str | None) -> tuple[str | None, SanitiseState]:
    """One-line call-site wrapper for the Docs API insert paths.

    ``prepend``, ``append``, ``append tab=`` and ``replace_text``'s
    replacement text all ride ``insertText``/``replaceAllText``, which eat
    the same two characters. Call it only AFTER the plain-file routing:
    a plain text file keeps both, and transforming its content would be
    the very silent change this module exists to stop.
    """
    content, warnings, counts = sanitise_doc_content(content, rich=False)
    return content, (warnings, counts)


def apply_sanitise_cues(cues: dict[str, object], state: SanitiseState) -> None:
    """Fold a sanitise pass's disclosure into a result's cues, in place.

    Kept beside the transform so no call site has to remember the shape:
    the counts go in as their own keys (``page_breaks_marked``,
    ``nuls_removed``) and the prose joins the standard ``warnings`` list
    every do() result already uses.
    """
    warnings, counts = state
    if warnings:
        existing = cues.setdefault("warnings", [])
        if isinstance(existing, list):
            existing.extend(warnings)
    for key, value in counts.items():
        cues[key] = value


def find_string_warning(find: str | None) -> str | None:
    """Warn when a replace_text ``find`` carries a char no Doc can hold.

    Not a write path, but the same measured fact reaching the caller from
    the other side: because every write path deletes ``\\f`` and ``\\x00``,
    a ``find`` copied out of a PDF deposit that includes one can never
    match anything in the document — a guaranteed silent no-op, which is
    the failure class ``NO_MATCH_WARNING`` exists for.
    """
    if not find:
        return None
    present = []
    if FORM_FEED in find:
        present.append("a form feed (\\f)")
    if NUL in find:
        present.append("a NUL (\\x00)")
    if not present:
        return None
    return (
        f"`find` contains {' and '.join(present)}, which no Google Doc can "
        "contain — Docs deletes both on every write path (measured "
        "2026-08-24), so this find can never match. In a PDF deposit a form "
        "feed is the page boundary; search text from within one page."
    )
