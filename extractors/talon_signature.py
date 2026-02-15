"""
Signature extraction using talon's bruteforce approach.

Inlined from: https://github.com/mailgun/talon
Source files: talon/signature/bruteforce.py, talon/signature/constants.py, talon/utils.py
Commit: 71d9b6eb78e985bcdfbf99b69c20c001b4b818c4 (2022-02-07)

MIT License - Copyright (c) Mailgun Technologies Inc.
"""

import logging
from dataclasses import dataclass

# Why regex, not re?
# talon's RE_SIGNATURE_CANDIDATE uses duplicate named groups (?P<candidate>...)
# which re (stdlib) doesn't support. The regex module does. If this becomes a perf
# concern (backtracking), we'd need to rewrite the pattern without named groups.
import regex as re

log = logging.getLogger(__name__)

# --- Constants (from talon/signature/constants.py) ---

SIGNATURE_MAX_LINES = 11
TOO_LONG_SIGNATURE_LINE = 60

# --- Utils (from talon/utils.py) ---

RE_DELIMITER = re.compile(r'\r?\n')


def get_delimiter(msg_body: str) -> str:
    """Detect line delimiter used in message."""
    match = RE_DELIMITER.search(msg_body)
    if match:
        return str(match.group())
    return '\n'


# --- Regex patterns (from talon/signature/bruteforce.py) ---

# Regex to fetch signature based on common signature words
RE_SIGNATURE = re.compile(r'''
    (
        (?:
            ^[\s]*--*[\s]*[a-z \.]*$
            |
            ^thanks[\s,!]*$
            |
            ^regards[\s,!]*$
            |
            ^cheers[\s,!]*$
            |
            ^best[ a-z]*[\s,!]*$
        )
        .*
    )
''', re.I | re.X | re.M | re.S)

# Signatures appended by phone email clients
RE_PHONE_SIGNATURE = re.compile(r'''
    (
        (?:
            ^sent[ ]{1}from[ ]{1}my[\s,!\w]*$
            |
            ^sent[ ]from[ ]Mailbox[ ]for[ ]iPhone.*$
            |
            ^sent[ ]([\S]*[ ])?from[ ]my[ ]BlackBerry.*$
            |
            ^Enviado[ ]desde[ ]mi[ ]([\S]+[ ]){0,2}BlackBerry.*$
        )
        .*
    )
''', re.I | re.X | re.M | re.S)

# Candidate marking pattern
# c - could be signature line
# d - line starts with dashes (could be signature or list item)
# l - long line
RE_SIGNATURE_CANDIDATE = re.compile(r'''
    (?P<candidate>c+d)[^d]
    |
    (?P<candidate>c+d)$
    |
    (?P<candidate>c+)
    |
    (?P<candidate>d)[^d]
    |
    (?P<candidate>d)$
''', re.I | re.X | re.M | re.S)


# --- Core functions (from talon/signature/bruteforce.py) ---

def extract_signature(msg_body: str) -> tuple[str, str | None]:
    """
    Analyzes message for a presence of signature block (by common patterns)
    and returns tuple with two elements: message text without signature block
    and the signature itself.

    >>> extract_signature('Hey man! How r u?\\n\\n--\\nRegards,\\nRoman')
    ('Hey man! How r u?', '--\\nRegards,\\nRoman')

    >>> extract_signature('Hey man!')
    ('Hey man!', None)
    """
    try:
        # Identify line delimiter first
        delimiter = get_delimiter(msg_body)

        stripped_body = msg_body.strip()
        phone_signature = None

        # Strip off phone signature
        phone_match = RE_PHONE_SIGNATURE.search(msg_body)
        if phone_match:
            stripped_body = stripped_body[:phone_match.start()]
            phone_signature = phone_match.group()

        # Decide on signature candidate
        lines = stripped_body.splitlines()
        candidate_lines = get_signature_candidate(lines)
        candidate_text = delimiter.join(candidate_lines)

        # Try to extract signature
        signature_match = RE_SIGNATURE.search(candidate_text)
        if not signature_match:
            return (stripped_body.strip(), phone_signature)
        else:
            signature = signature_match.group()
            # When we splitlines() and then join them
            # we can lose a new line at the end
            # we did it when identifying a candidate
            # so we had to do it for stripped_body now
            stripped_body = delimiter.join(lines)
            stripped_body = stripped_body[:-len(signature)]

            if phone_signature:
                signature = delimiter.join([signature, phone_signature])

            return (stripped_body.strip(), signature.strip())
    except Exception:
        log.exception('ERROR extracting signature')
        return (msg_body, None)


def get_signature_candidate(lines: list[str]) -> list[str]:
    """
    Return lines that could hold signature.

    The lines should:
    * be among last SIGNATURE_MAX_LINES non-empty lines
    * not include first line
    * be shorter than TOO_LONG_SIGNATURE_LINE
    * not include more than one line that starts with dashes
    """
    # Non empty lines indexes
    non_empty = [i for i, line in enumerate(lines) if line.strip()]

    # If message is empty or just one line then there is no signature
    if len(non_empty) <= 1:
        return []

    # We don't expect signature to start at the 1st line
    candidate_indexes = non_empty[1:]
    # Signature shouldn't be longer than SIGNATURE_MAX_LINES
    candidate_indexes = candidate_indexes[-SIGNATURE_MAX_LINES:]

    markers = _mark_candidate_indexes(lines, candidate_indexes)
    candidate_indexes = _process_marked_candidate_indexes(candidate_indexes, markers)

    # Get actual lines for the candidate instead of indexes
    if candidate_indexes:
        return lines[candidate_indexes[0]:]

    return []


def _mark_candidate_indexes(lines: list[str], candidate: list[int]) -> str:
    """
    Mark candidate indexes with markers.

    Markers:
    * c - line that could be a signature line
    * l - long line
    * d - line that starts with dashes but has other chars as well

    >>> _mark_candidate_indexes(['Some text', '', '-', 'Bob'], [0, 2, 3])
    'cdc'
    """
    # At first consider everything to be potential signature lines
    markers = list('c' * len(candidate))

    # Mark lines starting from bottom up
    for i, line_idx in reversed(list(enumerate(candidate))):
        if len(lines[line_idx].strip()) > TOO_LONG_SIGNATURE_LINE:
            markers[i] = 'l'
        else:
            line = lines[line_idx].strip()
            if line.startswith('-') and line.strip("-"):
                markers[i] = 'd'

    return "".join(markers)


def _process_marked_candidate_indexes(candidate: list[int], markers: str) -> list[int]:
    """
    Run regexes against candidate's marked indexes to strip
    signature candidate.

    >>> _process_marked_candidate_indexes([9, 12, 14, 15, 17], 'clddc')
    [15, 17]
    """
    match = RE_SIGNATURE_CANDIDATE.match(markers[::-1])
    return candidate[-match.end('candidate'):] if match else []


# --- Forward detection ---
# Forwarded messages contain unique content from outside the thread.
# They must be preserved, unlike reply quotes (which duplicate earlier content).

@dataclass
class ForwardedSection:
    """A forwarded message section extracted from an email body."""
    attribution: str  # "From: ... Date: ... Subject: ..." or raw marker line
    body: str

# Gmail and Apple Mail forward markers (anchored to start of line)
_RE_FORWARD_MARKER = re.compile(
    r'^-{5,}\s*Forwarded message\s*-{5,}\s*$'
    r'|'
    r'^Begin forwarded message:\s*$',
    re.MULTILINE | re.IGNORECASE
)


def split_forward_sections(msg_body: str) -> tuple[str, list[ForwardedSection]]:
    """
    Split message body into own content and forwarded sections.

    Scans for forward markers (Gmail: '---------- Forwarded message ---------',
    Apple: 'Begin forwarded message:') and splits the body at the first marker.
    Content before the marker is "own content" (will be stripped normally).
    Content after each marker is a forwarded section (preserved as-is).

    Quoted forwards inside reply quotes (> ---------- Forwarded message)
    won't match because the regex is anchored to ^ and strip_quoted_lines
    removes >-prefixed lines first. This is correct: quoted forwards are
    part of the reply chain, not standalone forwarded content.

    Returns:
        Tuple of (own_content, list_of_forwarded_sections)
    """
    match = _RE_FORWARD_MARKER.search(msg_body)
    if not match:
        return msg_body, []

    own_content = msg_body[:match.start()].rstrip()
    remaining = msg_body[match.end():].lstrip('\n')

    sections: list[ForwardedSection] = []

    # Split remaining text on subsequent forward markers
    parts = _RE_FORWARD_MARKER.split(remaining)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Try to extract attribution block (From:/Date:/Subject: lines at top)
        attribution, body = _parse_forward_attribution(part)
        sections.append(ForwardedSection(attribution=attribution, body=body))

    return own_content, sections


def _parse_forward_attribution(text: str) -> tuple[str, str]:
    """
    Parse attribution headers from the top of a forwarded section.

    Gmail forwards typically have:
        From: Name <email>
        Date: ...
        Subject: ...
        To: ...

    Returns (attribution_block, remaining_body).
    If no attribution headers found, returns ("", full_text).
    """
    lines = text.split('\n')
    attr_lines: list[str] = []
    body_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Attribution lines: "Key: value" pattern (From, Date, Subject, To, Cc)
        if re.match(r'^(From|Date|Subject|To|Cc|Sent):\s', stripped, re.IGNORECASE):
            attr_lines.append(stripped)
            body_start = i + 1
        elif stripped == '' and attr_lines:
            # Blank line after attribution = end of attribution block
            body_start = i + 1
            break
        elif attr_lines:
            # Non-attribution line after seeing some — attribution block is done
            body_start = i
            break
        else:
            # No attribution found at all — whole thing is body
            break

    attribution = '\n'.join(attr_lines)
    body = '\n'.join(lines[body_start:]).strip()

    return attribution, body


# --- Convenience functions for our use case ---

def strip_quoted_lines(msg_body: str) -> str:
    """
    Strip lines starting with '>' (quoted reply content).

    This is a simple but effective way to remove quoted reply chains
    that typically appear after signatures in email threads.
    """
    lines = msg_body.split('\n')
    # Keep lines that don't start with '>' (allowing leading whitespace)
    kept = [line for line in lines if not line.lstrip().startswith('>')]
    return '\n'.join(kept)


def strip_signature(msg_body: str) -> str:
    """
    Strip signature from message body, returning just the content.

    This is a convenience wrapper around extract_signature() that
    returns only the body (discarding the signature).
    """
    body, _ = extract_signature(msg_body)
    return body


_RE_REPLY_PREAMBLE = re.compile(
    r'^On .{10,80} wrote:\s*$', re.MULTILINE
)

_RE_URL = re.compile(r'https?://|<http')


def _strip_trailing_contact_block(body: str) -> str:
    """
    Strip trailing URL-dense blocks that look like contact signatures.

    Catches modern corporate signatures (name/title/links) that talon
    misses because they lack explicit markers like '--' or 'Thanks'.

    Detection: finds a "name block" pattern — a short line (bare name)
    preceded by a blank line, followed by another short text line
    (full name/title), with 3+ URLs in the text below. This avoids
    false positives on content that happens to contain links.

    Also strips orphaned reply preambles ("On ... wrote:").
    """
    # First: strip orphaned reply preamble left after quote removal
    body = _RE_REPLY_PREAMBLE.sub('', body).rstrip()

    lines = body.split('\n')
    if len(lines) < 5:
        return body

    # Look for name-block signature start: blank → short name → text
    for i in range(1, len(lines) - 2):
        if lines[i - 1].strip():
            continue  # Need a blank line before

        line = lines[i].strip()
        if not line or len(line) >= 30 or _RE_URL.search(line):
            continue  # Not a short name line

        # Find next non-blank line — should be text, not a URL
        next_text = None
        for j in range(i + 1, min(i + 4, len(lines))):
            if lines[j].strip():
                next_text = lines[j].strip()
                break

        if not next_text or _RE_URL.search(next_text) or len(next_text) >= 60:
            continue  # Next line is a URL or too long — not a name block

        # Check URL density below this point
        trailing = '\n'.join(lines[i:])
        url_count = len(_RE_URL.findall(trailing))
        if url_count >= 3:
            return '\n'.join(lines[:i]).rstrip()

    return body


def strip_signature_and_quotes(msg_body: str) -> str:
    """
    Strip signatures and quoted replies, preserving forwarded messages.

    Pipeline:
    0. Split off forwarded sections (unique content from outside the thread)
    1. Strip quoted lines (>) from own content only
    2. Detect and strip signature (talon) from own content
    3. Strip trailing URL-dense contact blocks from own content
    4. Reassemble with forwarded sections using delimiter

    Forwarded messages are preserved because they contain unique content
    from outside the thread — unlike reply quotes which duplicate earlier
    messages. The split happens first so >-prefixed lines inside forwarded
    content aren't mistakenly stripped.

    Edge case: reply-to-forward (> ---------- Forwarded message) inside
    reply quotes won't match the regex (anchored to ^, quote-prefixed lines
    stripped first), so it's correctly removed as part of the reply chain.
    """
    # Step 0: split off forwarded sections before any stripping
    own_content, forwarded = split_forward_sections(msg_body)

    # Steps 1-3: strip own content only
    without_quotes = strip_quoted_lines(own_content)
    body, _ = extract_signature(without_quotes)
    body = _strip_trailing_contact_block(body)

    # Step 4: reassemble with forwarded sections
    if forwarded:
        parts = [body.rstrip()]
        for section in forwarded:
            parts.append('\n\n--- Forwarded message ---')
            if section.attribution:
                parts.append(section.attribution)
            if section.body:
                parts.append('')
                parts.append(section.body)
        body = '\n'.join(parts)

    return body
