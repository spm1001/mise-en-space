"""
Fetch routing — ID detection and do_fetch entry point.
"""

from pathlib import Path

from adapters.gmail import search_threads
from adapters.gmail_browser import resolve_gmail_url_via_browser
from adapters.gmail_ids import get_thread_id_for_rfc822_message_id
from models import MiseError, ErrorKind, FetchResult, FetchError
from validation import extract_drive_file_id, extract_gmail_id, extract_gmail_permmsgid, extract_rfc822_message_id, is_gmail_api_id, is_self_sent_gmail_url, GMAIL_WEB_ID_PREFIXES, detect_fetch_input_problem, diagnose_fetch_404

from .decorations import UrlDecorations, apply_url_decorations, parse_drive_url_decorations
from .gmail import fetch_gmail, fetch_attachment
from .drive import fetch_drive


def detect_id_type(input_id: str) -> tuple[str, str, UrlDecorations]:
    """
    Detect whether input is Gmail or Drive, and normalize the ID.

    Returns:
        Tuple of (source, normalized_id, decorations). This 3-tuple IS the
        seam that used to destroy URL decorations (mise-dogape): the id
        extraction keeps only /d/{id}, so anything the URL's tail named —
        ?gid, ?tab, #heading, #slide, ?disco — must be carried from here or
        it is unrecoverable one line into the fetch. Gmail URLs and bare ids
        carry an empty UrlDecorations.
    """
    input_id = input_id.strip()

    # Gmail URL
    if "mail.google.com" in input_id:
        return ("gmail", extract_gmail_id(input_id), UrlDecorations())

    # Drive URL (docs, sheets, slides, drive)
    if any(domain in input_id for domain in ["docs.google.com", "sheets.google.com", "slides.google.com", "drive.google.com"]):
        return ("drive", extract_drive_file_id(input_id), parse_drive_url_decorations(input_id))

    # Gmail API ID (16-char hex)
    if is_gmail_api_id(input_id):
        return ("gmail", input_id, UrlDecorations())

    # Gmail web ID (FMfcg..., KtbxL..., etc.) — needs conversion
    # Only match known prefixes; is_gmail_web_id fallback is too broad for bare IDs
    if input_id.startswith(GMAIL_WEB_ID_PREFIXES):
        return ("gmail", extract_gmail_id(input_id), UrlDecorations())

    # Default to Drive
    return ("drive", input_id, UrlDecorations())


def _self_sent_candidates() -> list[dict[str, str]] | None:
    """
    Recent sent threads, as candidates for an unreachable self-sent URL.

    The anti-freelancing rail (mise-lerulo): on 2026-07-31 a bare (correct)
    refusal led a session to search the inbox, pick the newest unread thread,
    and analyse the wrong email as the requested one — while the right thread
    sat at rank 2 of that same search. Candidates make the next move explicit:
    confirm one, or say you cannot. Self-sent means the user wrote it, so
    in:sent is where it lives.

    Fail-open by design: candidates are a bonus on an error path, and a failed
    search must never turn one error into two.
    """
    try:
        results = search_threads("in:sent", max_results=10)
    except Exception:
        return None
    candidates = [
        {
            "thread_id": r.thread_id,
            "subject": r.subject,
            "from": r.from_address or "",
            "date": r.date.strftime("%Y-%m-%d") if r.date else "",
        }
        for r in results.results
    ]
    return candidates or None


def do_fetch(file_id: str, base_path: Path | None = None, attachment: str | None = None, recursive: bool = False, tabs: list[str] | None = None, suggestions: str = "accepted", raw: bool = False) -> FetchResult | FetchError:
    """
    Main fetch entry point.

    Detects ID type, routes to appropriate fetcher, handles errors.

    Args:
        file_id: Drive file ID or Gmail thread ID
        base_path: Base directory for deposits (defaults to cwd)
        attachment: Specific attachment filename to extract from Gmail thread
        raw: With attachment=, also deposit the untouched original bytes (PDFs and
            Office files are otherwise converted and the original discarded)
        recursive: For folder fetches, traverse subfolders recursively
        suggestions: For Google Docs with suggested edits — 'accepted'
            (default: suggestions applied), 'original' (suggestions ignored),
            'markup' (explicit {++ins++}/{--del--} spans)
    """
    try:
        if suggestions not in ("accepted", "original", "markup"):
            return FetchError(
                kind="invalid_input",
                message=(
                    "suggestions must be one of 'accepted', 'original', "
                    f"'markup' — got {suggestions!r}"
                ),
            )
        # Pre-flight: catch the two input shapes agents reliably get wrong (a 12-char
        # deposit-folder prefix; a non-fetchable URL) with a teaching error, before they
        # fall through to a bare Google 404 (mise-dizupe).
        resolution_note: str | None = None
        source: str | None = None
        normalized_id: str | None = None
        decorations = UrlDecorations()

        problem = detect_fetch_input_problem(file_id)
        if problem:
            # Self-sent (a-family) FRAGMENT permalinks name a thread that
            # exists but is unreachable from the URL alone. Where a logged-in
            # CDP browser is available, Gmail's own UI performs the mapping —
            # open the URL there and read the rendered thread id (mise-johata;
            # evidence in docs/2026-08-07-gilojo-a-family-verdict.md).
            # Fail-open: no browser, lapsed session, timeout — all fall
            # through to the teaching error + candidates. msg-a Show-original
            # URLs are excluded: the UI renders an error page for those, so
            # an attempt could only burn the timeout.
            resolution = None
            if is_self_sent_gmail_url(file_id) and extract_gmail_permmsgid(file_id) is None:
                resolution = resolve_gmail_url_via_browser(file_id)
            if resolution is None:
                error = FetchError(kind="invalid_input", message=problem)
                # Attach recent sent threads so the caller confirms a
                # candidate (or says they can't) instead of silently
                # substituting.
                if is_self_sent_gmail_url(file_id):
                    error.candidates = _self_sent_candidates()
                    if error.candidates:
                        error.message += (
                            " Recent sent threads are attached as `candidates` — "
                            "if one of them is clearly this thread, fetch it by "
                            "its thread_id; if you cannot tell which, say so "
                            "rather than picking."
                        )
                return error
            source, normalized_id = "gmail", resolution.thread_id
            subject_part = f" ('{resolution.subject}')" if resolution.subject else ""
            resolution_note = (
                f"This URL carries a self-sent (a-family) token that no API can "
                f"convert, so mise resolved it by opening the URL in the logged-in "
                f"browser and reading Gmail's rendered thread id: "
                f"'{resolution.thread_id}'{subject_part}. If this route stops "
                f"working, the browser session has likely lapsed — the Message-ID "
                f"route (More > Show original) always works."
            )

        # A Message-ID (from Gmail's Show original view) resolves via one
        # exact-match rfc822msgid: search — the deterministic route into
        # self-sent threads whose web tokens cannot be converted (mise-lerulo).
        # Runs after the URL pre-flight, so URL-shaped inputs never reach it.
        if source is None:
            rfc822_id = extract_rfc822_message_id(file_id)
            if rfc822_id:
                thread_id = get_thread_id_for_rfc822_message_id(rfc822_id)
                source, normalized_id = "gmail", thread_id
                resolution_note = (
                    f"Resolved Message-ID '<{rfc822_id}>' to thread "
                    f"'{thread_id}' via an exact-match rfc822msgid: search."
                )
            else:
                # Detect ID type and normalize
                source, normalized_id, decorations = detect_id_type(file_id)

        # Single-attachment fetch (Gmail only)
        if attachment:
            if source != "gmail":
                return FetchError(
                    kind="invalid_input",
                    message="attachment parameter only works with Gmail thread/message IDs",
                )
            result = fetch_attachment(normalized_id, attachment, base_path=base_path, raw=raw)
        elif source == "gmail":
            result = fetch_gmail(normalized_id, base_path=base_path)
        else:
            result = fetch_drive(normalized_id, base_path=base_path, recursive=recursive, tabs=tabs, suggestions=suggestions)

        # Disclose any resolution (Message-ID→thread, or browser-resolved
        # a-family URL) as a cue — resolve-and-cue, never silently (the
        # mise-saroca discipline).
        if resolution_note and isinstance(result, FetchResult):
            result.cues.setdefault("warnings", []).append(resolution_note)
        # Point at what the URL's tail named (mise-dogape). Post-fetch and
        # deposit-driven, so a decorated and a bare fetch deposit identically.
        # Point at what the URL's tail named (mise-dogape). Post-fetch and
        # deposit-driven, so a decorated and a bare fetch deposit identically.
        if decorations and isinstance(result, FetchResult):
            apply_url_decorations(result, decorations)
        return result

    except MiseError as e:
        # A 404 reaching here is the largest untaught failure class in the call log —
        # 6 of 23 lifetime failures, and the only class with no recovery route in the
        # error, so callers either retry a permanent 404 or detour by hand (mise-tuveda).
        # Name the likely id type and the next move, keyed on the shape of what was
        # actually passed. Deliberately additive: Google's own text stays, because it
        # sometimes carries detail the shape cannot know.
        if e.kind is ErrorKind.NOT_FOUND and not e.details.get("diagnosed"):
            advice = diagnose_fetch_404(file_id)
            if advice:
                return FetchError(kind=e.kind.value, message=f"{e.message} {advice}")
        return FetchError(kind=e.kind.value, message=e.message)
    except ValueError as e:
        return FetchError(kind="invalid_input", message=str(e))
    except Exception as e:
        return FetchError(kind="unknown", message=str(e))


