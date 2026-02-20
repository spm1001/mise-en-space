"""
Input validation and ID conversion utilities.

Handles:
- Gmail web URL/ID → API ID conversion
- Google Drive URL → file ID extraction

Patterns adopted from mcp-google-workspace.
"""

import re
from base64 import b64decode

# =============================================================================
# PATTERNS
# =============================================================================

# Google Drive URL patterns
GOOGLE_DRIVE_ID_PATTERN = re.compile(r'/(?:d|folders)/([a-zA-Z0-9_-]+)')
GOOGLE_DRIVE_QUERY_PATTERN = re.compile(r'[?&]id=([a-zA-Z0-9_-]+)')
GOOGLE_FILE_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

# Gmail patterns
GMAIL_API_ID_PATTERN = re.compile(r'^[0-9a-f]{16}$')
GMAIL_WEB_URL_PATTERN = re.compile(r'https?://mail\.google\.com/mail/.*#[^/]+/([a-zA-Z0-9_-]+)')
GMAIL_WEB_ID_PREFIXES = ('FM', 'KtbxL', 'QgrcJHs', 'CLL', 'Gtj')



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


def extract_gmail_id_from_url(url: str) -> str | None:
    """
    Extract and convert Gmail thread/message ID from a Gmail web URL.

    Combines URL parsing with web ID conversion to get an API-usable ID.

    Args:
        url: Gmail web URL (e.g., "https://mail.google.com/mail/u/0/#inbox/FMfcgz...")

    Returns:
        API thread/message ID (16-char hex) or None if extraction/conversion fails

    Example:
        >>> extract_gmail_id_from_url("https://mail.google.com/mail/u/0/#inbox/FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph")
        '19b0e7fe6f653f69'
    """
    if not url or 'mail.google.com' not in url:
        return None

    match = GMAIL_WEB_URL_PATTERN.search(url)
    if not match:
        return None

    web_id = match.group(1)
    return convert_gmail_web_id(web_id)


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
                f"Could not convert Gmail URL to API ID.\n\n"
                f"This can happen with:\n"
                f"- Self-sent emails (thread-a format, ~2018+)\n"
                f"- Malformed URLs\n\n"
                f"Try searching by subject or sender instead:\n"
                f"  search('from:... subject:...')"
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

