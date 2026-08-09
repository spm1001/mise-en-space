"""
Input validation and ID conversion utilities.

Handles:
- Gmail web URL/ID → API ID conversion
- Google Drive URL → file ID extraction

Patterns adopted from mcp-google-workspace.
"""

import re
from base64 import b64decode, b64encode
from urllib.parse import parse_qs, unquote_plus, urlsplit

# =============================================================================
# PATTERNS
# =============================================================================

# Google Drive URL patterns
GOOGLE_DRIVE_ID_PATTERN = re.compile(r'/(?:d|folders)/([a-zA-Z0-9_-]+)')
GOOGLE_DRIVE_QUERY_PATTERN = re.compile(r'[?&]id=([a-zA-Z0-9_-]+)')
GOOGLE_FILE_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

# Gmail patterns
GMAIL_API_ID_PATTERN = re.compile(r'^[0-9a-f]{16}$')
GMAIL_WEB_ID_PREFIXES = ('FM', 'KtbxL', 'QgrcJHs', 'CLL', 'Gtj')
# Gmail draft ids, as they appear in mise's own draft links and in drafts.list.
GMAIL_DRAFT_ID_PATTERN = re.compile(r'^r-?\d+$')



# =============================================================================
# DRIVE ID EXTRACTION
# =============================================================================

def extract_drive_file_id(input_value: str) -> str:
    """
    Extract Google Drive file ID from URL or validate bare ID.

    Accepts:
    - Full URL: https://docs.google.com/document/d/1abc.../edit
    - Full URL: https://drive.google.com/file/d/1abc.../view
    - Full URL: https://drive.google.com/open?id=1abc...
    - Bare ID: 1abc...

    Returns:
        Extracted file ID

    Raises:
        ValueError: If input doesn't contain a valid Google file ID
    """
    if not input_value:
        raise ValueError("File ID or URL is required")

    input_value = input_value.strip()

    # If it looks like a URL, extract the ID
    if input_value.startswith('http://') or input_value.startswith('https://'):
        # Try /d/{id} pattern first (most common)
        match = GOOGLE_DRIVE_ID_PATTERN.search(input_value)
        if match:
            return match.group(1)

        # Try ?id={id} query parameter
        match = GOOGLE_DRIVE_QUERY_PATTERN.search(input_value)
        if match:
            return match.group(1)

        raise ValueError(
            f"Could not extract file ID from URL: {input_value}\n"
            "Expected format: https://docs.google.com/document/d/{id}/... or "
            "https://drive.google.com/open?id={id}"
        )

    # Validate as bare ID
    if not GOOGLE_FILE_ID_PATTERN.match(input_value):
        raise ValueError(
            f"Invalid file ID format: {input_value}\n"
            "File IDs contain only letters, numbers, hyphens, and underscores"
        )

    return input_value


# =============================================================================
# GMAIL ID CONVERSION
# =============================================================================

def _decode_gmail_web_token(token: str) -> str | None:
    """
    Decode Gmail web URL token to internal format.

    Gmail web tokens use a vowel-less character set that transforms to base64.
    Algorithm reverse-engineered by Arsenal Recon.

    Args:
        token: Gmail web token (e.g., "FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph")

    Returns:
        Decoded string like "thread-f:1851234526825889641" or None if decoding fails

    Reference:
        https://github.com/ArsenalRecon/GmailURLDecoder

    Negative results, recorded so nobody re-derives them (mise-lerulo step 6):
    naive base64 does NOT decode these tokens — neither the standard nor the
    urlsafe alphabet, at offsets 0/4/6, yields bytes containing the known API
    thread id (probed 2026-07-31 against a matched pair). The vowel-less
    transform below is the only known decode. And for thread-a tokens, the
    decoded r-number is NOT a draft id in disguise: drafts.get on a live
    example's r-number 404s (probed 2026-08-07). thread-a numbers reach no
    API surface at all — the Message-ID via rfc822msgid: is the way in.
    """
    charset_full = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    charset_reduced = "BCDFGHJKLMNPQRSTVWXZbcdfghjklmnpqrstvwxz"

    try:
        size_in = len(charset_reduced)
        size_out = len(charset_full)
        alph_map = {charset_reduced[i]: i for i in range(size_in)}

        in_str_idx: list[int] = []
        for i in reversed(range(len(token))):
            if token[i] not in alph_map:
                return None  # Invalid character
            in_str_idx.append(alph_map[token[i]])

        out_str_idx: list[int] = []
        for i in reversed(range(len(in_str_idx))):
            offset = 0
            for j in range(len(out_str_idx)):
                idx = size_in * out_str_idx[j] + offset
                if idx >= size_out:
                    rest = idx % size_out
                    offset = (idx - rest) // size_out
                    idx = rest
                else:
                    offset = 0
                out_str_idx[j] = idx

            while offset:
                rest = offset % size_out
                out_str_idx.append(rest)
                offset = (offset - rest) // size_out

            offset = in_str_idx[i]
            j = 0
            while offset:
                if j >= len(out_str_idx):
                    out_str_idx.append(0)
                idx = out_str_idx[j] + offset
                if idx >= size_out:
                    rest = idx % size_out
                    offset = (idx - rest) // size_out
                    idx = rest
                else:
                    offset = 0
                out_str_idx[j] = idx
                j += 1

        out_str = "".join(
            charset_full[out_str_idx[i]] for i in reversed(range(len(out_str_idx)))
        )

        # Base64 decode
        padding = '=' * (-len(out_str) % 4)
        result = b64decode(out_str + padding).decode("utf-8")

        # Add thread- prefix if missing
        if "thread-" not in result:
            result = "thread-" + result

        return result

    except Exception:
        return None


def _extract_api_id_from_decoded(decoded: str) -> str | None:
    """
    Extract API thread/message ID from decoded Gmail token.

    For thread-f: format, the decimal number is the API ID in decimal.
    For thread-a: format, there's no simple mapping (used for self-sent emails).

    Args:
        decoded: Decoded token like "thread-f:1851234526825889641"

    Returns:
        API thread ID (16-char hex) or None if not extractable
    """
    # Look for thread-f:DECIMAL or msg-f:DECIMAL pattern
    match = re.search(r'(?:thread|msg)-f:(\d+)', decoded)
    if match:
        decimal_id = int(match.group(1))
        hex_id = format(decimal_id, 'x')
        # Pad to 16 chars (API IDs are always 16 hex chars)
        return hex_id.zfill(16)[-16:]

    return None


def convert_gmail_web_id(web_id: str) -> str | None:
    """
    Convert Gmail web UI ID to API thread/message ID.

    Gmail web URLs use a different ID format than the API. This function
    decodes the web format and extracts the API ID when possible.

    Args:
        web_id: Gmail web token (e.g., "FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph")

    Returns:
        API thread/message ID (16-char hex) or None if conversion fails

    Note:
        - thread-f: tokens CAN be converted (normal received emails)
        - thread-a: tokens CANNOT be converted (self-sent emails, ~2018+)

    Reference:
        Algorithm by Arsenal Recon: https://github.com/ArsenalRecon/GmailURLDecoder
    """
    decoded = _decode_gmail_web_token(web_id)
    if not decoded:
        return None

    return _extract_api_id_from_decoded(decoded)


def encode_gmail_web_token(api_id: str) -> str | None:
    """
    Encode an API thread/message ID as a Gmail web URL token — the decoder's inverse.

    Encodes the BARE 'f:<decimal>' form, NOT 'thread-f:...' — the prefixed form
    encodes to a token ('NHgv...') that fails the GMAIL_WEB_ID_PREFIXES shape
    gate, while the bare form yields the familiar 'FMfcgz...' (probed 2026-08-09).
    Only the f-family is encodable: self-sent (thread-a) threads have no
    arithmetic transform between their r-number and the API id (mise-lerulo),
    so callers must not mint links for threads that may be self-sent —
    that judgement lives at the emit sites, not here.

    Args:
        api_id: API thread/message ID (16-char hex, e.g. "19b0c2d3e4f5a6b7")

    Returns:
        Web token (e.g. "FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph") or None on non-hex input
    """
    charset_full = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    charset_reduced = "BCDFGHJKLMNPQRSTVWXZbcdfghjklmnpqrstvwxz"
    try:
        decimal_id = int(api_id, 16)
    except (ValueError, TypeError):
        return None
    b64 = b64encode(f"f:{decimal_id}".encode()).decode().rstrip("=")
    # Interpret the base64 string as a number over the full alphabet...
    n = 0
    for ch in b64:
        n = n * 64 + charset_full.index(ch)
    # ...and re-express it over the 40-char vowel-less alphabet.
    digits: list[str] = []
    while n:
        n, rest = divmod(n, 40)
        digits.append(charset_reduced[rest])
    return "".join(reversed(digits))


def gmail_thread_web_url(thread_id: str) -> str | None:
    """Clickable Gmail web URL for an API thread id, or None if not encodable.

    Fragment-only form (no /u/N) — same shape as the draft links do() already
    emits; Gmail resolves it against the browser's default account.
    """
    token = encode_gmail_web_token(thread_id)
    if not token:
        return None
    return f"https://mail.google.com/mail/#all/{token}"


RFC822_MESSAGE_ID_BODY_RE = re.compile(r'^[^\s<>@]+@[^\s<>@/]+\.[^\s<>@/]+$')


def extract_rfc822_message_id(input_value: str) -> str | None:
    """
    Recognise an RFC 822 Message-ID passed as a fetch input, bare or <bracketed>.

    The Message-ID (from Gmail's Show original view) is the one deterministic
    route into threads whose web tokens decode to thread-a — self-sent mail,
    where no arithmetic transform to an API id exists. fetch() resolves it via
    an exact-match rfc822msgid: search so callers don't need to know the
    operator (mise-lerulo).

    Deliberately shape-only and pure: a plain email address also matches, and
    that is fine — an email address is never a valid fetch input, and the
    NOT_FOUND from the lookup names that possibility. Brackets must balance;
    the domain part must carry a dot and no '/', so URL fragments containing
    a bare '@' never match.

    Returns:
        The Message-ID without angle brackets, or None if the shape says
        this isn't one.
    """
    if not input_value:
        return None
    s = input_value.strip()
    if s.startswith('<') != s.endswith('>'):
        return None  # unbalanced brackets
    if s.startswith('<'):
        s = s[1:-1]
    if not RFC822_MESSAGE_ID_BODY_RE.match(s):
        return None
    return s


def extract_gmail_permmsgid(url: str) -> tuple[str, str] | None:
    """
    Parse the permmsgid parameter from a Gmail Show-original URL.

    Show original's own URL (?view=om&permmsgid=…) carries a second
    machine-usable identifier alongside the Message-ID the page displays:
    msg-f:<decimal> converts to the hex API MESSAGE id by the same transform
    as thread-f (confirmed live 2026-08-07: msg-f:1872845353272970994 →
    19fdaeed11138ef2, the very thread its FMfcgz token names). msg-a:r-…
    (self-sent) has no transform — same family as thread-a.

    Returns:
        (family, value) — ('f', '1872…') or ('a', 'r-812…') — or None when
        the URL carries no parseable permmsgid.
    """
    if not url or 'mail.google.com' not in url:
        return None
    query = urlsplit(url).query
    values = parse_qs(query).get('permmsgid')
    if not values:
        return None
    match = re.match(r'^msg-([fa]):(.+)$', values[0])
    if not match:
        return None
    return (match.group(1), match.group(2))


def gmail_fragment_segments(url: str) -> list[str]:
    """
    Split a Gmail web URL's fragment into its '/'-separated segments.

    The thread token is always the LAST segment, however many precede it:
    '#all/FMfcgz…' has two, '#search/from%3Aalice+lantern/FMfcgz…' has three,
    '#chat/space/<id>/<topic>/<msg>' has five. Splitting is deliberate — the
    previous implementation used a regex capturing the *second* segment, so
    every 3+ segment fragment extracted the wrong thing, and widening the
    regex is the kind of fix that silently re-breaks on the next shape.
    """
    _, _, fragment = url.partition('#')
    return [seg for seg in fragment.split('/') if seg]


def extract_gmail_draft_id(url: str) -> str | None:
    """
    Extract the draft id from a Gmail #drafts URL — the link mise itself writes.

    adapters/gmail.py emits https://mail.google.com/mail/#drafts/<draft-id> on
    every draft it creates, and until mise-jujoti closed the loop mise refused
    to read the URL it had itself written. Drafts are a separate object in a
    separate id space (r + digits), so this returns the draft id for the
    caller to resolve via drafts.get — a URL whose last segment is instead a
    web thread token (#drafts/FMfcgz…, the UI's own shape) returns None and
    takes the normal thread route.

    Pure function, no I/O.
    """
    if not url or 'mail.google.com' not in url:
        return None
    segments = gmail_fragment_segments(url)
    if len(segments) < 2 or segments[0].lower() != 'drafts':
        return None
    token = segments[-1]
    if GMAIL_DRAFT_ID_PATTERN.match(token):
        return token
    return None


GMAIL_ACCOUNT_INDEX_PATTERN = re.compile(r'/mail/u/(\d+)')


def extract_gmail_url_context(url: str) -> dict[str, object] | None:
    """
    Provenance a Gmail web URL carries BESIDE the thread token.

    All of it used to be accepted-and-dropped (mise-jujoti): the search query
    or label the thread was reached through — genuine provenance about why the
    thread was being looked at — and the /u/N account index, which matters
    because mise always reads the one account it is authed to, not the URL's.

    Returns a dict with any of 'search_query', 'label' (both unquote_plus'd,
    so from%3Aalice+lantern renders as a real query), and 'account_index'
    (only when non-zero — /u/0/ is the default account and carries no signal).
    None when the URL carries no such context. Pure function, no I/O.
    """
    if not url or 'mail.google.com' not in url:
        return None

    context: dict[str, object] = {}

    match = GMAIL_ACCOUNT_INDEX_PATTERN.search(url)
    if match and match.group(1) != '0':
        context['account_index'] = int(match.group(1))

    segments = gmail_fragment_segments(url)
    if len(segments) >= 3:
        view = segments[0].lower()
        if view == 'search':
            context['search_query'] = unquote_plus(segments[1])
        elif view == 'label':
            context['label'] = unquote_plus(segments[1])

    return context or None


def extract_gmail_id_from_url(url: str) -> str | None:
    """
    Extract and convert Gmail thread/message ID from a Gmail web URL.

    Combines URL parsing with web ID conversion to get an API-usable ID.

    Args:
        url: Gmail web URL (e.g., "https://mail.google.com/mail/u/0/#inbox/FMfcgz...")

    Returns:
        API thread/message ID (16-char hex) or None if extraction/conversion fails.
        Use diagnose_gmail_url() to find out WHY a None happened — the reason is
        the difference between a refusal a caller can act on and a dead end.

    Example:
        >>> extract_gmail_id_from_url("https://mail.google.com/mail/u/0/#inbox/FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph")
        '19b0e7fe6f653f69'
    """
    if not url or 'mail.google.com' not in url:
        return None

    # Show-original URLs carry the id in the QUERY, not the fragment. msg-f
    # decimals convert like thread-f; the result is a MESSAGE id, which the
    # gmail fetcher's message→thread machinery resolves (head message ids ARE
    # their thread id, so the common case costs no extra call).
    permmsgid = extract_gmail_permmsgid(url)
    if permmsgid:
        family, value = permmsgid
        if family == 'f' and value.isdigit():
            return _extract_api_id_from_decoded(f"msg-f:{value}")
        return None  # msg-a (self-sent) — diagnose_gmail_url names the route

    segments = gmail_fragment_segments(url)
    if not segments:
        return None

    # Google Chat is served from mail.google.com but is a different product with
    # its own id space — its ids must never reach the mail thread decoder.
    if segments[0].lower() == 'chat':
        return None

    token = segments[-1]
    # The prefix allowlist is the validity gate on the captured token, so a label
    # name or a search term landing last never gets decoded as a thread.
    if not token.startswith(GMAIL_WEB_ID_PREFIXES):
        return None

    return convert_gmail_web_id(token)


_SHOW_ORIGINAL_ROUTE = (
    "The reliable route is the Message-ID: open the message in Gmail, choose "
    "More > Show original, copy the Message-ID, and pass it to fetch() — or "
    "search('rfc822msgid:<message-id>'), which resolves to exactly one thread."
)


def diagnose_gmail_url(url: str) -> str | None:
    """
    Explain WHY a Gmail web URL cannot be resolved, naming a concrete next move.

    Returns None when the URL does resolve. Otherwise returns teaching text for
    the specific class of failure, because the classes have different remedies
    and one generic refusal invites the caller to freelance: on 2026-07-31 a bare
    (correct) refusal led a session to search the inbox, pick the newest unread
    thread, and analyse the wrong email as though it were the requested one —
    while the right thread sat at rank 2 of that same search. Measured the same
    week: refusals that teach get obeyed in seconds; refusals that don't cost
    minutes.

    Pure function, no I/O — it runs before any API call.
    """
    if not url or 'mail.google.com' not in url:
        return None

    permmsgid = extract_gmail_permmsgid(url)
    if permmsgid and permmsgid[0] == 'a':
        return (
            "This Show-original URL carries permmsgid=msg-a:…, which marks a "
            "SELF-SENT message — msg-a numbers have no known transform to an "
            "API id (msg-f ones convert; msg-a is the same dead end as "
            "thread-a). But the page this URL opens displays the answer: copy "
            "the Message-ID header shown there and pass it to fetch(), or run "
            "search('rfc822msgid:<message-id>')."
        )
    if permmsgid:
        # family 'f' reaching diagnosis means the decimal didn't convert —
        # malformed value rather than a known class.
        return (
            f"This Show-original URL carries permmsgid=msg-{permmsgid[0]}:"
            f"{permmsgid[1][:25]}, which could not be converted to an API id. "
            f"{_SHOW_ORIGINAL_ROUTE}"
        )

    segments = gmail_fragment_segments(url)
    if not segments:
        return (
            "This Gmail URL has no fragment, so it names a mailbox view rather "
            "than a thread. fetch() retrieves one specific thread. "
            "Use search('from:… subject:…') to find it."
        )

    if segments[0].lower() == 'chat':
        return (
            "This is a Google Chat link, not a mail thread. Chat is served from "
            "mail.google.com but is a different product with its own id space, so "
            "mise cannot read it — there is no mail thread behind this URL."
        )

    token = segments[-1]

    if GMAIL_DRAFT_ID_PATTERN.match(token):
        return (
            f"This is a Gmail draft link (draft id '{token}'). fetch() resolves "
            f"it to the thread holding the draft automatically; for other "
            f"operations pass that thread id, or edit the draft itself with "
            f"do(draft, file_id='{token}')."
        )

    if not token.startswith(GMAIL_WEB_ID_PREFIXES):
        if len(segments) == 1:
            what = f"names the '{token[:30]}' mailbox view"
        else:
            what = (
                f"ends in '{token[:30]}', which is not a Gmail thread token — it is "
                f"still the '{segments[0]}' view"
            )
        return (
            f"This URL {what} rather than an open conversation. Open the message "
            f"itself so the URL ends in a thread token, or use "
            f"search('from:… subject:…') to find the thread."
        )

    # The token IS a thread token. Decode it to tell a convertible thread-f from a
    # thread-a, rather than pattern-matching the prefix: resolve, don't validate.
    decoded = _decode_gmail_web_token(token)
    if decoded and 'thread-a:' in decoded:
        return (
            f"This is a self-sent thread (thread-a format, roughly 2018 onward). "
            f"Its web token decodes cleanly but the number it carries is not the "
            f"API thread id and no transform is known, so the URL alone cannot "
            f"reach it. {_SHOW_ORIGINAL_ROUTE} If this session has browser tools "
            f"attached to a Chrome where this Gmail account is signed in (e.g. "
            f"Claude in Chrome), you can also open the URL there yourself, read "
            f"the thread's data-legacy-thread-id attribute from the page, and "
            f"fetch that id directly."
        )

    if convert_gmail_web_id(token) is None:
        return (
            f"This Gmail token ('{token[:25]}…') could not be decoded to an API "
            f"thread id. {_SHOW_ORIGINAL_ROUTE}"
        )

    return None


def is_self_sent_gmail_url(url: str) -> bool:
    """
    True when a Gmail URL names a thread that EXISTS but cannot be reached from
    the URL alone — the self-sent class (thread-a token, or msg-a permmsgid).

    This is the one refusal class that earns a candidates search: the thread is
    real and probably recent, so recency in the sent folder finds it. Chat
    links, mailbox views, and draft links stay out — there is no single mail
    thread behind those, and candidates would only dignify the wrong question.
    """
    if not url or 'mail.google.com' not in url:
        return False
    permmsgid = extract_gmail_permmsgid(url)
    if permmsgid and permmsgid[0] == 'a':
        return True
    segments = gmail_fragment_segments(url)
    if not segments or segments[0].lower() == 'chat':
        return False
    token = segments[-1]
    if not token.startswith(GMAIL_WEB_ID_PREFIXES):
        return False
    decoded = _decode_gmail_web_token(token)
    return bool(decoded and 'thread-a:' in decoded)


def is_gmail_web_id(id_value: str) -> bool:
    """
    Check if a string looks like a Gmail web UI ID (not API format).

    Useful for early detection before making API calls.

    Args:
        id_value: Potential Gmail ID

    Returns:
        True if it appears to be a web UI format ID
    """
    if not id_value:
        return False
    # Web IDs are longer and have specific prefixes
    return (
        id_value.startswith(GMAIL_WEB_ID_PREFIXES) or
        (len(id_value) > 20 and not GMAIL_API_ID_PATTERN.match(id_value))
    )


def is_gmail_api_id(id_value: str) -> bool:
    """
    Check if a string is a valid Gmail API ID (16-char hex).

    Args:
        id_value: Potential Gmail ID

    Returns:
        True if it's a valid API format ID
    """
    if not id_value:
        return False
    return bool(GMAIL_API_ID_PATTERN.match(id_value))


_GMAIL_ID_SAFE_RE = re.compile(r'^[0-9a-zA-Z]+$')


def validate_gmail_id(gmail_id: str, param_name: str = "thread_id") -> None:
    """
    Raise ValueError if gmail_id looks malformed — URLs, control chars, spaces, etc.

    Gmail API thread/message IDs are alphanumeric. This rejects URLs,
    query strings, control characters, and other obviously wrong inputs
    without being overly strict about exact format.

    Args:
        gmail_id: The Gmail thread or message ID to validate
        param_name: Name used in the error message

    Raises:
        ValueError: If gmail_id contains non-alphanumeric characters
    """
    if not _GMAIL_ID_SAFE_RE.match(gmail_id):
        raise ValueError(
            f"Invalid {param_name}: must contain only alphanumeric characters, "
            f"got '{gmail_id[:30]}'"
        )


def extract_gmail_id(input_value: str) -> str:
    """
    Extract Gmail thread/message ID from URL, web ID, or validate API ID.

    Accepts:
    - Gmail URL: https://mail.google.com/mail/u/0/#inbox/FMfcgz...
    - Web ID: FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph (converts automatically)
    - API ID: 19b0e7fe6f653f69 (returned as-is)

    Returns:
        Valid API-format Gmail ID (16-char hex)

    Raises:
        ValueError: If ID cannot be converted or is invalid
    """
    if not input_value:
        raise ValueError("Gmail ID or URL is required")

    input_value = input_value.strip()

    # Try to extract ID from Gmail web URL
    if input_value.startswith('http://') or input_value.startswith('https://'):
        if 'mail.google.com' in input_value:
            api_id = extract_gmail_id_from_url(input_value)
            if api_id:
                return api_id
            raise ValueError(
                diagnose_gmail_url(input_value)
                or "Could not convert this Gmail URL to an API ID. "
                   "Use search('from:… subject:…') to find the thread."
            )
        raise ValueError(f"Not a Gmail URL: {input_value}")

    # If already API format, return as-is
    if GMAIL_API_ID_PATTERN.match(input_value):
        return input_value

    # Try to convert web ID
    if is_gmail_web_id(input_value):
        api_id = convert_gmail_web_id(input_value)
        if api_id:
            return api_id
        raise ValueError(
            f"Could not convert Gmail web ID: {input_value[:25]}...\n\n"
            f"This happens with self-sent emails (thread-a format).\n"
            f"Try searching by subject or sender instead."
        )

    raise ValueError(
        f"Invalid Gmail ID format: {input_value}\n"
        f"API IDs are 16-character hex strings (e.g., 19b0e7fe6f653f69)"
    )


# =============================================================================
# SEARCH QUERY ESCAPING
# =============================================================================

def escape_drive_query(query: str) -> str:
    """
    Escape user input for use in Drive search queries.

    Drive uses single-quoted strings in queries like:
        fullText contains 'search term'

    Without escaping, a query like "test' OR name contains 'secret" becomes:
        fullText contains 'test' OR name contains 'secret'
    which is query injection.

    Args:
        query: Raw user search input

    Returns:
        Escaped string safe for use in single-quoted Drive query clauses

    Example:
        >>> escape_drive_query("test' OR name contains 'secret")
        "test\\' OR name contains \\'secret"
    """
    if not query:
        return query

    # Escape backslashes first (before we add more with quote escaping)
    escaped = query.replace('\\', '\\\\')
    # Escape single quotes
    escaped = escaped.replace("'", "\\'")

    return escaped


# Drive query-language shapes, precise enough not to fire on English. Each needs
# an operator AND its syntax — bare words like "contains" or "and" are ordinary
# in a search phrase ("what the box contains", "budget and forecast") and matching
# them would break the common path to protect the rare one.
_DRIVE_OPERATOR_PATTERNS = [
    re.compile(r"\b(?:fullText|name|mimeType)\s+contains\b", re.I),
    re.compile(r"\bmimeType\s*!?=", re.I),
    re.compile(r"\b(?:modifiedTime|createdTime|viewedByMeTime)\s*[<>=]", re.I),
    re.compile(r"'[^']*'\s+in\s+(?:parents|owners|writers|readers)\b", re.I),
    re.compile(r"\btrashed\s*=", re.I),
    re.compile(r"\bstarred\s*=", re.I),
    re.compile(r"\bsharedWithMe\b", re.I),
]


def looks_like_drive_query(query: str) -> bool:
    """Is this Drive query-language rather than search terms?

    mise wraps `query` in a single `fullText contains '…'` clause, so Drive
    syntax typed there doesn't error — it silently becomes a keyword search for
    the operator names. Probed 2026-07-27: `name contains 'PCA'` returned ten
    plausible files including a 1:1 doc and a probation review, because those
    contain the words *name*, *contains* and *PCA*. A wrong answer with no
    warning is worse than a refusal, so callers get routed to raw_query instead.
    """
    return any(p.search(query) for p in _DRIVE_OPERATOR_PATTERNS)


def sanitize_gmail_query(query: str) -> str:
    """
    Sanitize user input for Gmail search queries.

    Gmail search supports operators (from:, subject:, is:, etc.) which users
    should be able to use. We only strip control characters and null bytes
    that could cause issues.

    Args:
        query: Raw user search input

    Returns:
        Sanitized string safe for Gmail API

    Example:
        >>> sanitize_gmail_query("from:alice subject:meeting")
        "from:alice subject:meeting"
        >>> sanitize_gmail_query("test\\x00with\\x1fnull")
        "testwithnull"
    """
    if not query:
        return query

    # Strip control characters (ASCII 0-31 except tab, newline, carriage return)
    # and DEL (127). Gmail handles these poorly.
    sanitized = ''.join(
        char for char in query
        if ord(char) >= 32 or char in '\t\n\r'
    )
    # Also strip DEL
    sanitized = sanitized.replace('\x7f', '')

    return sanitized.strip()


# =============================================================================
# DRIVE ID VALIDATION
# =============================================================================

_DRIVE_ID_RE = re.compile(r'^[A-Za-z0-9_\-]+$')


def sanitize_title(title: str) -> str:
    """Strip control characters from a title intended for Drive file names.

    Removes ASCII control chars (0x00-0x1F) and DEL (0x7F). Preserves
    all printable characters including unicode.
    """
    return "".join(c for c in title if ord(c) >= 32 and ord(c) != 0x7F)


def validate_drive_id(drive_id: str, param_name: str = "drive_id") -> None:
    """
    Raise ValueError if drive_id contains characters outside the Drive ID alphabet.

    Drive file/folder IDs are base62-ish: [A-Za-z0-9_-]. Anything else
    (spaces, quotes, operators) indicates either a malformed ID or an
    injection attempt against Drive query strings.

    Args:
        drive_id: The Drive file or folder ID to validate
        param_name: Name used in the error message (e.g. 'folder_id')

    Raises:
        ValueError: If drive_id contains disallowed characters
    """
    if not _DRIVE_ID_RE.match(drive_id):
        raise ValueError(
            f"Invalid {param_name}: must contain only alphanumeric characters, "
            f"hyphens, and underscores"
        )


# =============================================================================
# FETCH INPUT SHAPE DIAGNOSIS
# =============================================================================

# Domains whose URLs carry a fetchable Drive file ID in /d/{id} or ?id={id}.
_WORKSPACE_FILE_DOMAINS = (
    "docs.google.com", "sheets.google.com", "slides.google.com", "drive.google.com",
)

# Deposit folders are named {type}--{slug}--{id[:12]}; this is that trailing chunk.
_DEPOSIT_PREFIX_LEN = 12


def detect_fetch_input_problem(file_id: str) -> str | None:
    """
    Pre-flight diagnosis of the two fetch-input shapes agents reliably get wrong.

    Both were mined from calls.jsonl 404s (mise-dizupe): every non-trivial fetch
    failure was either (a) a 12-char deposit-folder prefix re-used as a file ID, or
    (b) a URL that isn't a fetchable Workspace handle. Left alone, both fall through
    ID detection to a bare Google 404 that teaches the agent nothing. This returns a
    message that names the mistake AND the next move — self-disclosing data over
    instructional copy (the friendly-error-wrapper pattern).

    Pure function (no I/O) so it's trivially testable and runs before any API call.

    Args:
        file_id: The raw fetch input.

    Returns:
        A teaching error message if the input matches a known-bad shape, else None
        (input is plausibly fetchable — let normal routing handle it).
    """
    if not file_id:
        return None
    s = file_id.strip()

    # Shape (b): URLs.
    if s.startswith(("http://", "https://")):
        head = s if len(s) <= 60 else s[:60] + "…"

        # Gmail URLs are fine only if a single thread is extractable. Search, label,
        # inbox, and self-sent thread-a URLs can't resolve to an API ID.
        if "mail.google.com" in s:
            if extract_gmail_id_from_url(s) is not None:
                return None  # genuine thread URL — let it through
            reason = diagnose_gmail_url(s)
            if reason:
                return f"'{head}' cannot be fetched. {reason}"
            return (
                f"'{head}' is a Gmail web URL that doesn't point at a single fetchable "
                f"thread. Use search('from:… subject:…') to find the thread — fetch "
                f"retrieves a specific thread, not a Gmail query."
            )

        # Workspace file URLs are fine if an ID is extractable from the path/query.
        if any(domain in s for domain in _WORKSPACE_FILE_DOMAINS):
            if GOOGLE_DRIVE_ID_PATTERN.search(s) or GOOGLE_DRIVE_QUERY_PATTERN.search(s):
                return None  # genuine Drive/Docs/Sheets/Slides URL — let it through

        # Anything else: an arbitrary web page (GitHub, docs sites, …) or a Workspace
        # domain URL with no extractable ID. fetch is not a generic web fetcher.
        return (
            f"'{head}' isn't a Google Workspace handle. fetch retrieves Google Drive "
            f"items (Docs/Sheets/Slides/PDFs/folders) and Gmail threads — by ID, or by "
            f"a Workspace URL (docs.google.com/…, drive.google.com/…, "
            f"mail.google.com/…). For an arbitrary web page use passe or WebFetch; for "
            f"a Gmail query use search()."
        )

    # Shape (a): a bare 12-char deposit-folder prefix re-used as an ID. No genuine
    # Workspace ID is 12 chars (Drive IDs are ~33+, Gmail API IDs 16-hex), so an
    # exactly-12-char Drive-charset string is almost always the truncated prefix.
    if len(s) == _DEPOSIT_PREFIX_LEN and _DRIVE_ID_RE.match(s):
        return (
            f"'{s}' looks like a 12-character deposit-folder prefix, not a full file ID. "
            f"mise names deposit folders {{type}}--{{slug}}--{{id[:12]}}, and that "
            f"trailing chunk is a truncated ID (real Drive file IDs are ~33+ chars). "
            f"Open the deposit's manifest.json and use its 'id' field, or call search() "
            f"to look the file up again."
        )

    return None


def diagnose_fetch_404(file_id: str, *, tried_message_lookup: bool = False) -> str | None:
    """
    Explain a fetch 404 by the SHAPE of the id that produced it — mise-tuveda.

    Everything else in this section runs *before* the call and can only judge inputs
    that are wrong on their face. This runs *after*, on ids that looked plausible and
    still 404'd, which is a different and larger class: the 2026-08-01 usage review
    found raw 404s to be the only failure class carrying no recovery route, 6 of 23
    lifetime failures, and the week's single real detour (7 minutes on a mid-thread
    message id). The same review measured why this pays: every teaching-shaped failure
    that week was obeyed to the letter within ~30 seconds.

    Pure function, no I/O — the caller has already made the failing call and tells us
    what it tried.

    Args:
        file_id: The id that 404'd.
        tried_message_lookup: True when the caller already attempted the Gmail
            thread→message fallback (messages.get) and that 404'd too. Changes the
            16-hex advice from "this is probably a mid-thread message id" to "it is
            neither a live thread nor a live message here", because after the fallback
            the first message would be actively misleading.

    Returns:
        Teaching text naming the likely id type and a concrete next move, or None when
        the shape says nothing useful (let the plain not-found stand).
    """
    if not file_id:
        return None
    s = file_id.strip()

    # Gmail draft ids: 'r' + digits. Two of these were passed to fetch in the review
    # window, each retried once against a permanent 404, then abandoned.
    if GMAIL_DRAFT_ID_PATTERN.match(s):
        return (
            f"'{s}' is a Gmail DRAFT id, and drafts are not fetchable as threads — a "
            f"draft is a separate object with its own id space. Retrying will 404 "
            f"forever. To read the conversation it belongs to, use the thread id that "
            f"appears alongside the draft in the draft listing; to edit the draft "
            f"itself, use do(draft, file_id='{s}')."
        )

    # 16-hex: the Gmail API id band. Thread ids and message ids are indistinguishable
    # by shape, and a message id only resolves as a thread when it HEADS that thread.
    if GMAIL_API_ID_PATTERN.match(s):
        if tried_message_lookup:
            return (
                f"'{s}' is a 16-hex Gmail id, but it is neither a live thread nor a "
                f"live message on this account — mise tried it both ways. It may be "
                f"deleted, may belong to a different account, or may not be a Gmail id "
                f"at all. Find the conversation with search('subject:…') or "
                f"search('rfc822msgid:<message-id>')."
            )
        return (
            f"'{s}' is a 16-hex Gmail id that 404s as a thread. Gmail thread ids and "
            f"MESSAGE ids share this shape, and a message id only resolves as a thread "
            f"when that message heads the thread — so this is most likely a mid-thread "
            f"message id. {_SHOW_ORIGINAL_ROUTE}"
        )

    # Gmail web tokens that reached the API layer un-converted.
    if s.startswith(GMAIL_WEB_ID_PREFIXES):
        return (
            f"'{s[:40]}' is a Gmail WEB id (the token from a mail.google.com URL), not "
            f"an API id, so it cannot be fetched directly. {_SHOW_ORIGINAL_ROUTE}"
        )

    # Drive-shaped: the id is well-formed, so 404 means genuinely absent or unreadable
    # — a real answer, and saying so stops the caller retrying a shape question.
    if _DRIVE_ID_RE.match(s):
        return (
            f"'{s}' is a well-formed Drive id, so this 404 means the file does not "
            f"exist, is in the trash, or is not shared with this account — not that "
            f"the id is malformed. Check sharing, or search() for the file by name. If "
            f"it lives on a Shared Drive you cannot see, ask the owner for access."
        )

    return None

