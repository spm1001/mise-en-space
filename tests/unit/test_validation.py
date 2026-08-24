"""
Tests for validation and ID conversion utilities.
"""

import pytest

from validation import (
    GMAIL_WEB_ID_PREFIXES,
    escape_drive_query,
    encode_gmail_web_token,
    extract_drive_file_id,
    extract_gmail_draft_id,
    extract_gmail_id,
    extract_gmail_id_from_url,
    extract_gmail_url_context,
    extract_rfc822_message_id,
    convert_gmail_web_id,
    detect_fetch_input_problem,
    diagnose_gmail_url,
    gmail_fragment_segments,
    gmail_thread_web_url,
    has_tokenising_separator,
    is_gmail_web_id,
    is_gmail_api_id,
    is_self_sent_gmail_url,
    looks_like_drive_query,
    name_contains_terms,
    sanitize_gmail_query,
    sanitize_title,
    validate_drive_id,
    validate_gmail_id,
)


class TestDriveIdExtraction:
    """Tests for Drive file ID extraction."""

    def test_extracts_from_docs_url(self):
        """Extract ID from Google Docs URL."""
        url = "https://docs.google.com/document/d/1ABC123_test/edit"
        assert extract_drive_file_id(url) == "1ABC123_test"

    def test_extracts_from_sheets_url(self):
        """Extract ID from Google Sheets URL."""
        url = "https://docs.google.com/spreadsheets/d/1XYZ789/edit#gid=0"
        assert extract_drive_file_id(url) == "1XYZ789"

    def test_extracts_from_drive_file_url(self):
        """Extract ID from Drive file URL."""
        url = "https://drive.google.com/file/d/0BwGZ5_abc123/view"
        assert extract_drive_file_id(url) == "0BwGZ5_abc123"

    def test_extracts_from_open_url(self):
        """Extract ID from drive.google.com/open?id= URL."""
        url = "https://drive.google.com/open?id=1abc123"
        assert extract_drive_file_id(url) == "1abc123"

    def test_extracts_from_folder_url(self):
        """Extract ID from Drive folder URL."""
        url = "https://drive.google.com/drive/folders/1_UMRzD4KScPks"
        assert extract_drive_file_id(url) == "1_UMRzD4KScPks"

    def test_returns_bare_id(self):
        """Return bare ID unchanged."""
        assert extract_drive_file_id("1ABC123_test") == "1ABC123_test"

    def test_rejects_invalid_id(self):
        """Reject invalid file ID format."""
        with pytest.raises(ValueError, match="Invalid file ID"):
            extract_drive_file_id("not a valid id!")

    def test_rejects_empty(self):
        """Reject empty input."""
        with pytest.raises(ValueError, match="required"):
            extract_drive_file_id("")


class TestGmailIdConversion:
    """Tests for Gmail ID conversion."""

    def test_is_gmail_api_id(self):
        """Detect valid API IDs."""
        assert is_gmail_api_id("19b0e7fe6f653f69")
        assert is_gmail_api_id("0000000000000000")
        assert not is_gmail_api_id("FMfcgzQdzmSk")  # Too short, wrong chars
        assert not is_gmail_api_id("19b0e7fe6f653f6")  # Too short

    def test_is_gmail_web_id(self):
        """Detect web UI IDs."""
        assert is_gmail_web_id("FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph")
        assert is_gmail_web_id("KtbxLwGXnfZWVpRNLkCVXBbfkLGPdh")
        assert not is_gmail_web_id("19b0e7fe6f653f69")  # API format

    def test_convert_gmail_web_id(self):
        """Convert web ID to API ID."""
        # This is a real conversion - the web ID decodes to a thread-f format
        web_id = "FMfcgzQfBZdVqDtDZnXwMRWvRZjGhdWN"
        api_id = convert_gmail_web_id(web_id)
        # Should be 16 hex chars
        assert api_id is not None
        assert len(api_id) == 16
        assert all(c in '0123456789abcdef' for c in api_id)

    def test_extract_gmail_id_from_url(self):
        """Extract and convert from Gmail URL."""
        url = "https://mail.google.com/mail/u/0/#sent/FMfcgzQfBZdVqDtDZnXwMRWvRZjGhdWN"
        api_id = extract_gmail_id_from_url(url)
        assert api_id is not None
        assert len(api_id) == 16


class TestEncodeGmailWebToken:
    """The decoder's inverse — clickable web links for Gmail results (mise-hetaba)."""

    # The decoder docstring's documented real pair.
    GOLDEN_API_ID = format(1851234526825889641, "x")  # 19b0e7fe6f653f69
    GOLDEN_TOKEN = "FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph"

    def test_golden_pair(self):
        """Encoding the documented API id reproduces the documented token exactly."""
        assert encode_gmail_web_token(self.GOLDEN_API_ID) == self.GOLDEN_TOKEN

    def test_round_trip_through_real_decoder(self):
        """encode → convert_gmail_web_id lands back on the same API id."""
        for api_id in (self.GOLDEN_API_ID, "19fb9faca1565748", "18a0000000000001"):
            token = encode_gmail_web_token(api_id)
            assert token is not None
            assert convert_gmail_web_id(token) == api_id

    def test_token_passes_the_shape_gate(self):
        """Encoded tokens must clear GMAIL_WEB_ID_PREFIXES or every consumer refuses them."""
        token = encode_gmail_web_token(self.GOLDEN_API_ID)
        assert token.startswith(GMAIL_WEB_ID_PREFIXES)

    def test_prefixed_input_is_rejected_not_encoded(self):
        """The probed pitfall: 'thread-f:...' input must not silently mint a wrong-shape token."""
        assert encode_gmail_web_token("thread-f:1851234526825889641") is None

    def test_non_hex_returns_none(self):
        assert encode_gmail_web_token("not-hex-at-all!") is None
        assert encode_gmail_web_token("") is None

    def test_web_url_shape(self):
        """Fragment-only URL, same shape family as the draft links do() emits."""
        url = gmail_thread_web_url(self.GOLDEN_API_ID)
        assert url == f"https://mail.google.com/mail/#all/{self.GOLDEN_TOKEN}"

    def test_web_url_none_on_bad_input(self):
        assert gmail_thread_web_url("nope!") is None


class TestGmailFragmentSegments:
    """
    The thread token is the LAST fragment segment, not the second.

    Fixtures are the five real URLs recorded on mise-jujoti — measured, not
    invented, because a probe you built yourself fails most convincingly on the
    case you invented.
    """

    # 3 segments: the old regex captured 'from' here and the fetch was refused.
    SEARCH_URL = (
        "https://mail.google.com/mail/u/0/#search/"
        "from%3AStefano.Figoni%40itv.com+lantern/FMfcgzQhVNfMCxqltVrdVFJgqxZhgmhM"
    )
    # 2 segments: worked before and must keep working.
    ALL_URL = "https://mail.google.com/mail/u/0/#all/FMfcgzQgMgKRTRzJtcVbpdRDPZKZGgrW"
    # Self-sent: decodes cleanly to thread-a:, which has no known transform.
    THREAD_A_URL = "https://mail.google.com/mail/u/0/#all/KtbxLwghjwWScTGNNHctnzRVJkLPKbVvSB"
    # Google Chat — different product, different id space, same hostname.
    CHAT_DM_URL = "https://mail.google.com/mail/u/0/#chat/dm/2GLKWSAAAAE"
    CHAT_SPACE_URL = (
        "https://mail.google.com/mail/u/0/#chat/space/AAAAfTECEtQ/kmGYPtDEvdw/GPCeWeXV2XI"
    )

    def test_segments_split_on_slash(self):
        assert gmail_fragment_segments(self.ALL_URL) == [
            "all", "FMfcgzQgMgKRTRzJtcVbpdRDPZKZGgrW"
        ]
        assert len(gmail_fragment_segments(self.CHAT_SPACE_URL)) == 5

    def test_search_url_resolves_to_a_thread(self):
        """The headline defect: a 3-segment fragment used to capture 'from'."""
        api_id = extract_gmail_id_from_url(self.SEARCH_URL)
        assert api_id is not None, "search URL must resolve — the id is the last segment"
        assert len(api_id) == 16
        assert diagnose_gmail_url(self.SEARCH_URL) is None

    def test_two_segment_url_still_resolves(self):
        """Regression: the shape that already worked must not break."""
        assert extract_gmail_id_from_url(self.ALL_URL) == "19f1ff83e58c0567"

    def test_chat_links_refused_by_name(self):
        for url in (self.CHAT_DM_URL, self.CHAT_SPACE_URL):
            assert extract_gmail_id_from_url(url) is None
            reason = diagnose_gmail_url(url)
            assert reason is not None
            assert "Chat" in reason, "a Chat link must be refused AS a Chat link"

    def test_thread_a_names_the_show_original_route(self):
        assert extract_gmail_id_from_url(self.THREAD_A_URL) is None
        reason = diagnose_gmail_url(self.THREAD_A_URL)
        assert reason is not None
        assert "self-sent" in reason.lower()
        assert "rfc822msgid" in reason, "the refusal must name the deterministic route"

    def test_draft_link_named_as_a_draft(self):
        """mise emits #drafts/<id> itself — it should not be a mystery to mise."""
        reason = diagnose_gmail_url("https://mail.google.com/mail/#drafts/r8287431168042343092")
        assert reason is not None
        assert "draft" in reason.lower()
        assert "r8287431168042343092" in reason

    def test_label_view_is_not_decoded_as_a_thread(self):
        """The prefix allowlist gates the captured token."""
        url = "https://mail.google.com/mail/u/0/#label/Finance"
        assert extract_gmail_id_from_url(url) is None
        assert "search(" in (diagnose_gmail_url(url) or "")

    def test_resolvable_url_gets_no_diagnosis(self):
        assert diagnose_gmail_url(self.ALL_URL) is None


class TestGmailIdExtraction:
    def test_extract_gmail_id_returns_api_id(self):
        """Return API ID unchanged."""
        api_id = "19b0e7fe6f653f69"
        assert extract_gmail_id(api_id) == api_id

    def test_extract_gmail_id_converts_web_id(self):
        """Convert web ID automatically."""
        web_id = "FMfcgzQfBZdVqDtDZnXwMRWvRZjGhdWN"
        result = extract_gmail_id(web_id)
        assert len(result) == 16
        assert result != web_id  # Should be converted

    def test_extract_gmail_id_from_full_url(self):
        """Extract from full Gmail URL."""
        url = "https://mail.google.com/mail/u/0/#inbox/FMfcgzQfBZdVqDtDZnXwMRWvRZjGhdWN"
        result = extract_gmail_id(url)
        assert len(result) == 16

    def test_rejects_non_gmail_url(self):
        """Reject non-Gmail URLs."""
        with pytest.raises(ValueError, match="Not a Gmail URL"):
            extract_gmail_id("https://example.com/something")


class TestQueryEscaping:
    """Tests for search query escaping and sanitization."""

    # -------------------------------------------------------------------------
    # escape_drive_query tests
    # -------------------------------------------------------------------------

    def test_escape_drive_query_normal_text(self):
        """Normal text passes through unchanged."""
        assert escape_drive_query("meeting notes") == "meeting notes"
        assert escape_drive_query("budget 2026") == "budget 2026"

    def test_escape_drive_query_single_quotes(self):
        """Single quotes are escaped."""
        assert escape_drive_query("it's") == "it\\'s"
        assert escape_drive_query("'quoted'") == "\\'quoted\\'"

    def test_escape_drive_query_backslashes(self):
        """Backslashes are escaped."""
        assert escape_drive_query("path\\to\\file") == "path\\\\to\\\\file"

    def test_escape_drive_query_injection_attempt(self):
        """Query injection attempts are neutralized."""
        # This would break out of the quoted string without escaping
        malicious = "test' OR name contains 'secret"
        escaped = escape_drive_query(malicious)
        assert escaped == "test\\' OR name contains \\'secret"
        # When used in fullText contains '{escaped}', this stays as a single value

    def test_escape_drive_query_mixed_special_chars(self):
        """Mixed quotes and backslashes handled correctly."""
        # Order matters: backslash before quote
        assert escape_drive_query("it's a\\path") == "it\\'s a\\\\path"

    def test_escape_drive_query_empty(self):
        """Empty string returns empty."""
        assert escape_drive_query("") == ""
        assert escape_drive_query(None) is None

    def test_escape_drive_query_operators_preserved(self):
        """Drive operators in user input are just text (escaped as needed)."""
        # Users can't inject operators because the query is inside quotes
        query = "mimeType:application/pdf"
        assert escape_drive_query(query) == "mimeType:application/pdf"

    # -------------------------------------------------------------------------
    # sanitize_gmail_query tests
    # -------------------------------------------------------------------------

    def test_sanitize_gmail_query_normal_text(self):
        """Normal text passes through unchanged."""
        assert sanitize_gmail_query("meeting notes") == "meeting notes"

    def test_sanitize_gmail_query_preserves_operators(self):
        """Gmail operators are preserved (intentional feature)."""
        assert sanitize_gmail_query("from:alice@example.com") == "from:alice@example.com"
        assert sanitize_gmail_query("subject:meeting is:unread") == "subject:meeting is:unread"
        assert sanitize_gmail_query("has:attachment larger:5M") == "has:attachment larger:5M"

    def test_sanitize_gmail_query_strips_control_chars(self):
        """Control characters are removed."""
        assert sanitize_gmail_query("test\x00with\x1fnull") == "testwithnull"
        assert sanitize_gmail_query("bell\x07char") == "bellchar"

    def test_sanitize_gmail_query_preserves_whitespace(self):
        """Tab, newline, CR are preserved."""
        assert sanitize_gmail_query("line1\nline2") == "line1\nline2"
        assert sanitize_gmail_query("col1\tcol2") == "col1\tcol2"

    def test_sanitize_gmail_query_strips_del(self):
        """DEL character (0x7F) is removed."""
        assert sanitize_gmail_query("test\x7ftext") == "testtext"

    def test_sanitize_gmail_query_strips_surrounding_whitespace(self):
        """Leading/trailing whitespace stripped."""
        assert sanitize_gmail_query("  query  ") == "query"

    def test_sanitize_gmail_query_empty(self):
        """Empty string returns empty."""
        assert sanitize_gmail_query("") == ""
        assert sanitize_gmail_query(None) is None

    def test_sanitize_gmail_query_unicode(self):
        """Unicode characters pass through."""
        assert sanitize_gmail_query("日本語 email") == "日本語 email"
        assert sanitize_gmail_query("émoji 🎉") == "émoji 🎉"


class TestNameContainsTerms:
    """mise-jefaki — pulling the terms out of raw Drive queries so the search
    tool can recognise the zero-hits-on-a-punctuated-name shape."""

    def test_single_clause(self):
        assert name_contains_terms("name contains 'cudoba-probe'") == ["cudoba-probe"]

    def test_multiple_clauses(self):
        q = "name contains 'a-b' and name contains 'c_d'"
        assert name_contains_terms(q) == ["a-b", "c_d"]

    def test_case_insensitive_keywords(self):
        assert name_contains_terms("NAME CONTAINS 'x-y'") == ["x-y"]

    def test_escaped_quote_in_term(self):
        # Drive escapes a literal single quote as \' inside the quoted term.
        assert name_contains_terms(r"name contains 'it\'s-here'") == ["it's-here"]

    def test_other_clauses_ignored(self):
        q = "fullText contains 'a-b' and mimeType = 'application/pdf'"
        assert name_contains_terms(q) == []

    def test_no_clause(self):
        assert name_contains_terms("trashed = false") == []

    def test_composed_query_extracts_only_name_terms(self):
        q = ("trashed = false and (name contains 'report-2026' "
             "or fullText contains 'other-term')")
        assert name_contains_terms(q) == ["report-2026"]


class TestHasTokenisingSeparator:
    """The separators are the measured set Drive splits filenames on
    (docs/research/2026-08-24-jefaki-name-probe/): hyphen, underscore,
    dot, space. Letter-digit boundaries split too but have no character."""

    def test_hyphen(self):
        assert has_tokenising_separator("cudoba-probe") is True

    def test_underscore(self):
        assert has_tokenising_separator("a_b") is True

    def test_dot(self):
        assert has_tokenising_separator("report.pdf") is True

    def test_space(self):
        assert has_tokenising_separator("annual report") is True

    def test_single_token(self):
        assert has_tokenising_separator("cudoba") is False

    def test_digit_boundary_alone_is_not_flagged(self):
        # 'arm2' does split at the letter-digit boundary, but the term is
        # still findable as typed (both its tokens are whole), so no cue.
        assert has_tokenising_separator("arm2") is False


class TestValidateDriveId:
    """Test the Drive ID validation helper — injection guard for query strings."""

    def test_valid_ids_pass(self) -> None:
        for valid_id in ["abc123", "1UclqiqLBfe3BfLRNFTWb0eDbnssxA3Tp", "folder-id_ABC"]:
            validate_drive_id(valid_id)  # should not raise

    def test_single_quote_rejected(self) -> None:
        """Single quote would enable query injection into Drive search strings."""
        with pytest.raises(ValueError):
            validate_drive_id("abc' OR '1'='1")

    def test_space_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_drive_id("abc 123")

    def test_param_name_in_error(self) -> None:
        """Error message includes the parameter name for debuggability."""
        with pytest.raises(ValueError, match="folder_id"):
            validate_drive_id("bad id!", param_name="folder_id")


class TestValidateGmailId:
    """Test the Gmail ID validation helper."""

    def test_valid_hex_id_passes(self) -> None:
        validate_gmail_id("abc123def456abc1")  # should not raise

    def test_valid_short_id_passes(self) -> None:
        validate_gmail_id("abc1")  # should not raise — length not enforced

    def test_rejects_url(self) -> None:
        with pytest.raises(ValueError):
            validate_gmail_id("https://mail.google.com/mail/#inbox/abc123")

    def test_rejects_spaces(self) -> None:
        with pytest.raises(ValueError):
            validate_gmail_id("abc 123")

    def test_rejects_underscores(self) -> None:
        with pytest.raises(ValueError):
            validate_gmail_id("bad_thread")

    def test_rejects_slashes(self) -> None:
        with pytest.raises(ValueError):
            validate_gmail_id("../../etc")

    def test_param_name_in_error(self) -> None:
        with pytest.raises(ValueError, match="file_id"):
            validate_gmail_id("bad!", param_name="file_id")


class TestSanitizeTitle:
    """Test the title sanitization helper."""

    def test_normal_title_unchanged(self) -> None:
        assert sanitize_title("My Document (v2)") == "My Document (v2)"

    def test_strips_null_bytes(self) -> None:
        assert sanitize_title("hello\x00world") == "helloworld"

    def test_strips_control_chars(self) -> None:
        assert sanitize_title("hello\x01\x1fworld") == "helloworld"

    def test_strips_del(self) -> None:
        assert sanitize_title("hello\x7fworld") == "helloworld"

    def test_preserves_unicode(self) -> None:
        assert sanitize_title("日本語タイトル 🎉") == "日本語タイトル 🎉"

    def test_preserves_tabs_not_stripped(self) -> None:
        # Tab is control char (0x09), should be stripped
        assert sanitize_title("col1\tcol2") == "col1col2"

    def test_empty_after_strip(self) -> None:
        assert sanitize_title("\x00\x01\x02") == ""


class TestDetectFetchInputProblem:
    """The two fetch-input shapes agents reliably get wrong (mise-dizupe)."""

    # --- Shape (a): 12-char deposit-folder prefix ---

    def test_twelve_char_prefix_flagged(self) -> None:
        # '1OepZjuwi2em' is exactly the deposit-folder prefix from CLAUDE.md's example.
        msg = detect_fetch_input_problem("1OepZjuwi2em")
        assert msg is not None
        assert "manifest.json" in msg
        assert "12-character" in msg or "12-char" in msg

    def test_full_drive_id_not_flagged(self) -> None:
        # A real ~33-char Drive ID must pass through untouched.
        assert detect_fetch_input_problem("1OepZjuwi2emAbCdEfGhIjKlMnOpQrStUv") is None

    def test_gmail_api_id_not_flagged(self) -> None:
        # 16-char hex Gmail API ID is not 12 chars — must not trip the prefix trap.
        assert detect_fetch_input_problem("19b0e7fe6f653f69") is None

    def test_eleven_and_thirteen_char_not_flagged(self) -> None:
        # The trap is exactly 12; neighbours must not fire.
        assert detect_fetch_input_problem("1OepZjuwi2e") is None      # 11
        assert detect_fetch_input_problem("1OepZjuwi2emX") is None    # 13

    # --- Shape (b): non-fetchable / wrong URLs ---

    def test_github_url_flagged(self) -> None:
        msg = detect_fetch_input_problem("https://github.com/spm1001/mise-en-space")
        assert msg is not None
        assert "Workspace" in msg
        assert "search()" in msg or "WebFetch" in msg or "passe" in msg

    def test_arbitrary_web_url_flagged(self) -> None:
        msg = detect_fetch_input_problem("https://example.com/some/article")
        assert msg is not None
        assert "isn't a Google Workspace handle" in msg

    def test_gmail_search_url_flagged(self) -> None:
        msg = detect_fetch_input_problem("https://mail.google.com/mail/u/0/#search/quarterly")
        assert msg is not None
        assert "search(" in msg

    def test_gmail_inbox_url_flagged(self) -> None:
        # A bare inbox view with no thread is not fetchable.
        msg = detect_fetch_input_problem("https://mail.google.com/mail/u/0/#inbox")
        assert msg is not None

    # --- Pass-through: genuine Workspace handles must not be flagged ---

    def test_genuine_docs_url_not_flagged(self) -> None:
        url = "https://docs.google.com/document/d/1ABC123_realdocidthatislong/edit"
        assert detect_fetch_input_problem(url) is None

    def test_genuine_drive_open_url_not_flagged(self) -> None:
        url = "https://drive.google.com/open?id=0BwGZ5_realdriveid_longenough"
        assert detect_fetch_input_problem(url) is None

    def test_genuine_gmail_thread_url_not_flagged(self) -> None:
        # Convertible thread URL (FM… web token) must pass through to normal routing.
        url = "https://mail.google.com/mail/u/0/#inbox/FMfcgzQdzmSkKHmvSJPBLDSZTbfWQwph"
        assert detect_fetch_input_problem(url) is None

    def test_bare_full_id_not_flagged(self) -> None:
        assert detect_fetch_input_problem("1ABC123_realdocidthatislongenough") is None

    def test_empty_input_not_flagged(self) -> None:
        assert detect_fetch_input_problem("") is None
        assert detect_fetch_input_problem("   ") is None


class TestLooksLikeDriveQuery:
    """mise-decaza. Drive syntax in `query` doesn't error — it becomes a keyword
    search for the operator names and returns plausible wrong files. The guard
    routes those to raw_query. Its precision matters in both directions: a false
    negative is the old silent-wrong-answer, a false positive breaks ordinary
    searching to protect a rare case.
    """

    @pytest.mark.parametrize("q", [
        "name contains 'PCA'",
        "fullText contains 'budget'",
        "mimeType = 'application/pdf'",
        "mimeType contains 'image/'",
        "modifiedTime > '2025-01-01T00:00:00'",
        "createdTime <= '2024-06-01'",
        "'sameer.modha@itv.com' in owners",
        "'1abc' in parents",
        "trashed = true",
        "starred = true",
        "sharedWithMe",
        "NAME CONTAINS 'pca'",           # case-insensitive
    ])
    def test_detects_drive_syntax(self, q: str) -> None:
        assert looks_like_drive_query(q) is True

    @pytest.mark.parametrize("q", [
        "PCA",
        "budget 2026",
        "what the box contains",          # bare 'contains' is ordinary English
        "revenue and costs",              # bare 'and'
        "profit or loss",                 # bare 'or'
        "O'Brien quarterly review",       # apostrophe
        "Region:Lift GeoX",               # colon
        "ViewersLogic post campaign analysis",
        "shared drive migration notes",   # 'shared' without sharedWithMe
        "name of the new product",        # 'name' without 'contains'
    ])
    def test_leaves_ordinary_search_terms_alone(self, q: str) -> None:
        """The false-positive controls. Every one of these is a query someone
        would really type, and rejecting any of them would be worse than the bug
        the guard exists to prevent."""
        assert looks_like_drive_query(q) is False


class TestRfc822MessageIdExtraction:
    """extract_rfc822_message_id — fetch() accepting Message-IDs (mise-lerulo).

    The Message-ID comes from Gmail's Show original view and is the one
    deterministic route into threads whose web tokens decode to thread-a
    (self-sent, no known transform).
    """

    def test_angle_bracketed_exchange_id(self):
        mid = ("<VI0PR01MB11914227C6036AFC7DC888B59E8D12"
               "@VI0PR01MB11914.eurprd01.prod.exchangelabs.com>")
        assert extract_rfc822_message_id(mid) == mid[1:-1]

    def test_bare_gmail_id_with_specials(self):
        # Gmail-originated Message-IDs carry '=', '+', '-' in the local part
        mid = "CALWvZAA=weBi2-S=c3Zbs-96zgjjkvgz6_y4f8oqJRvT+d6HSA@mail.gmail.com"
        assert extract_rfc822_message_id(mid) == mid

    def test_surrounding_whitespace_stripped(self):
        assert extract_rfc822_message_id("  <a.b@c.example.com> ") == "a.b@c.example.com"

    @pytest.mark.parametrize("not_a_message_id", [
        "19fdaeed11138ef2",                                    # Gmail API id
        "1OepZjuwi2emuHPAP-LWxWZnw9g0SbkjhkBJh9ta1rqU",        # Drive id
        "FMfcgzQhVhjbJVgnGqBkMTktjRNBlvCQ",                    # Gmail web token
        "https://mail.google.com/mail/u/0/#inbox/FMfcgzQhV",   # URL
        "two words@example.com",                               # space
        "<half@open",                                          # unbalanced bracket
        "no-at-sign.example.com",                              # no @
        "user@localhost",                                      # dotless domain
        "a@b@c.example.com",                                   # two @
        "",
    ])
    def test_rejects_non_message_ids(self, not_a_message_id):
        assert extract_rfc822_message_id(not_a_message_id) is None


class TestShowOriginalUrl:
    """Show-original URLs (?view=om&permmsgid=…) as fetch inputs (mise-lerulo).

    msg-f decimals convert to hex API message ids by the same transform as
    thread-f (confirmed live: 1872845353272970994 → 19fdaeed11138ef2, the very
    thread its FMfcgz token names). msg-a (self-sent) has no transform — but the
    page the URL names displays the Message-ID, so the teaching text says so.
    """

    URL_F = ("https://mail.google.com/mail/u/0/"
             "?ik=2bb48b24a5&view=om&permmsgid=msg-f:1872845353272970994")
    URL_A = ("https://mail.google.com/mail/u/0/"
             "?ik=2bb48b24a5&view=om&permmsgid=msg-a:r-8125895545114462359")

    def test_msg_f_converts_to_hex_message_id(self):
        assert extract_gmail_id_from_url(self.URL_F) == "19fdaeed11138ef2"

    def test_msg_f_with_urlencoded_colon(self):
        url = self.URL_F.replace("msg-f:", "msg-f%3A")
        assert extract_gmail_id_from_url(url) == "19fdaeed11138ef2"

    def test_msg_f_passes_preflight(self):
        assert detect_fetch_input_problem(self.URL_F) is None

    def test_msg_f_resolves_via_extract_gmail_id(self):
        assert extract_gmail_id(self.URL_F) == "19fdaeed11138ef2"

    def test_msg_a_returns_none(self):
        assert extract_gmail_id_from_url(self.URL_A) is None

    def test_msg_a_teaching_text_names_the_message_id_on_the_page(self):
        diag = diagnose_gmail_url(self.URL_A)
        assert diag is not None
        assert "msg-a" in diag
        assert "Message-ID" in diag

    def test_msg_a_preflight_carries_the_diagnosis(self):
        problem = detect_fetch_input_problem(self.URL_A)
        assert problem is not None
        assert "Message-ID" in problem

    def test_plain_mailbox_view_diagnosis_unchanged(self):
        # A no-fragment URL without permmsgid still gets the mailbox-view text
        diag = diagnose_gmail_url("https://mail.google.com/mail/u/0/")
        assert diag is not None
        assert "mailbox view" in diag


class TestIsSelfSentGmailUrl:
    """is_self_sent_gmail_url — the one refusal class that earns candidates."""

    @pytest.mark.parametrize("url", [
        # Ktbx token in a mailbox view (the 2026-07-31 original defect URL shape)
        "https://mail.google.com/mail/u/0/#all/KtbxLwghjwWScTGNNHctnzRVJkLPKbVvSB",
        # Qgrc token behind a search (decodes to thread-a — probed 2026-08-07)
        "https://mail.google.com/mail/u/0/#search/hasan.patel%40itv.com/"
        "QgrcJHsbdJTGBvvQznvJDWRjKHcsnKvmpKQ",
        # msg-a Show-original URL
        "https://mail.google.com/mail/u/0/"
        "?ik=2bb48b24a5&view=om&permmsgid=msg-a:r-8125895545114462359",
    ])
    def test_self_sent_shapes(self, url):
        assert is_self_sent_gmail_url(url) is True

    @pytest.mark.parametrize("url", [
        # Convertible thread-f URL — resolves, never refused
        "https://mail.google.com/mail/u/0/#inbox/FMfcgzQhVhjbJVgnGqBkMTktjRNBlvCQ",
        # msg-f Show-original URL — converts
        "https://mail.google.com/mail/u/0/"
        "?ik=2bb48b24a5&view=om&permmsgid=msg-f:1872845353272970994",
        # Chat link — different product, no mail thread to find
        "https://mail.google.com/mail/u/0/#chat/dm/2GLKWSAAAAE",
        # Bare mailbox view — a view, not a thread
        "https://mail.google.com/mail/u/0/#inbox",
        # Not Gmail at all
        "https://docs.google.com/document/d/1ABC123/edit",
    ])
    def test_other_shapes_stay_out(self, url):
        assert is_self_sent_gmail_url(url) is False


class TestExtractGmailDraftId:
    """The #drafts URL mise itself writes yields its draft id (mise-jujoti step 7)."""

    MISE_OWN_LINK = "https://mail.google.com/mail/#drafts/r8287431168042343092"

    def test_mise_own_draft_link_yields_the_draft_id(self):
        assert extract_gmail_draft_id(self.MISE_OWN_LINK) == "r8287431168042343092"

    def test_dashed_draft_id_matches_too(self):
        url = "https://mail.google.com/mail/u/0/#drafts/r-8125895545114462359"
        assert extract_gmail_draft_id(url) == "r-8125895545114462359"

    def test_ui_shaped_drafts_url_takes_the_thread_route(self):
        """#drafts/FMfcgz… carries a web THREAD token, not a draft id — it must
        fall through to normal thread routing rather than hit drafts.get."""
        url = "https://mail.google.com/mail/u/0/#drafts/FMfcgzQgMgKRTRzJtcVbpdRDPZKZGgrW"
        assert extract_gmail_draft_id(url) is None

    def test_non_drafts_views_yield_nothing(self):
        url = "https://mail.google.com/mail/u/0/#inbox/FMfcgzQgMgKRTRzJtcVbpdRDPZKZGgrW"
        assert extract_gmail_draft_id(url) is None

    def test_non_gmail_and_bare_ids_yield_nothing(self):
        assert extract_gmail_draft_id("https://example.com/#drafts/r123") is None
        assert extract_gmail_draft_id("r8287431168042343092") is None
        assert extract_gmail_draft_id("") is None


class TestExtractGmailUrlContext:
    """Provenance beside the thread token — previously accepted-and-dropped
    (mise-jujoti steps 5-6)."""

    # The real production refusal from calls.jsonl that opened the bon.
    CAPTIFY_URL = (
        "https://mail.google.com/mail/u/0/#search/"
        "from%3Aniharika.verma%40captify.co.uk/FMfcgzQXKhLsKgFZmMwJgntMLhRLltMN"
    )
    STEFANO_URL = (
        "https://mail.google.com/mail/u/0/#search/"
        "from%3AStefano.Figoni%40itv.com+lantern/FMfcgzQhVNfMCxqltVrdVFJgqxZhgmhM"
    )

    def test_search_query_is_carried_and_decoded(self):
        ctx = extract_gmail_url_context(self.CAPTIFY_URL)
        assert ctx == {"search_query": "from:niharika.verma@captify.co.uk"}

    def test_plus_decodes_to_space_not_literal_plus(self):
        """unquote alone leaves '+' intact — the brief names unquote_plus."""
        ctx = extract_gmail_url_context(self.STEFANO_URL)
        assert ctx["search_query"] == "from:Stefano.Figoni@itv.com lantern"

    def test_label_is_carried_and_decoded(self):
        url = "https://mail.google.com/mail/u/0/#label/Weekly+Digests/FMfcgzABC"
        ctx = extract_gmail_url_context(url)
        assert ctx == {"label": "Weekly Digests"}

    def test_nonzero_account_index_is_carried(self):
        url = "https://mail.google.com/mail/u/1/#all/FMfcgzABC"
        ctx = extract_gmail_url_context(url)
        assert ctx == {"account_index": 1}

    def test_default_account_index_is_no_signal(self):
        url = "https://mail.google.com/mail/u/0/#all/FMfcgzABC"
        assert extract_gmail_url_context(url) is None

    def test_account_index_composes_with_search(self):
        url = "https://mail.google.com/mail/u/2/#search/lantern/FMfcgzABC"
        ctx = extract_gmail_url_context(url)
        assert ctx == {"account_index": 2, "search_query": "lantern"}

    def test_label_listing_without_thread_carries_no_context(self):
        """Two segments = a mailbox view, not a thread's provenance."""
        url = "https://mail.google.com/mail/u/0/#label/Finance"
        assert extract_gmail_url_context(url) is None

    def test_non_gmail_input_yields_nothing(self):
        assert extract_gmail_url_context("https://example.com/mail/u/3/#search/x/y") is None
        assert extract_gmail_url_context("") is None


class TestParseTimeWindow:
    """Calendar window bound parsing (mise-riduka)."""

    def test_bare_dates_widen_to_whole_days(self):
        from datetime import datetime, timezone
        from validation import parse_time_window

        lo, hi = parse_time_window("2026-08-03", "2026-08-05")
        assert lo == datetime(2026, 8, 3, tzinfo=timezone.utc)
        # 'between 3 and 5 Aug' includes the 5th: exclusive next-midnight bound
        assert hi == datetime(2026, 8, 6, tzinfo=timezone.utc)

    def test_same_date_both_bounds_is_one_full_day(self):
        from datetime import timedelta
        from validation import parse_time_window

        lo, hi = parse_time_window("2026-08-04", "2026-08-04")
        assert hi - lo == timedelta(days=1)

    def test_datetimes_pass_through_with_offset(self):
        from datetime import datetime, timezone, timedelta
        from validation import parse_time_window

        lo, hi = parse_time_window("2026-08-03T09:00:00+01:00", "2026-08-03T17:30:00Z")
        assert lo == datetime(2026, 8, 3, 9, tzinfo=timezone(timedelta(hours=1)))
        assert hi == datetime(2026, 8, 3, 17, 30, tzinfo=timezone.utc)

    def test_naive_datetime_becomes_utc(self):
        from datetime import timezone
        from validation import parse_time_window

        lo, _ = parse_time_window("2026-08-03T09:00:00", None)
        assert lo is not None and lo.tzinfo == timezone.utc

    def test_either_bound_may_be_absent(self):
        from validation import parse_time_window

        assert parse_time_window(None, None) == (None, None)
        lo, hi = parse_time_window("2026-08-03", None)
        assert lo is not None and hi is None
        lo, hi = parse_time_window(None, "2026-08-05")
        assert lo is None and hi is not None

    def test_garbage_names_the_expected_format(self):
        from validation import parse_time_window

        with pytest.raises(ValueError, match="ISO date"):
            parse_time_window("next tuesday", None)
        with pytest.raises(ValueError, match="time_max"):
            parse_time_window(None, "05/08/2026")

    def test_empty_window_refused(self):
        from validation import parse_time_window

        with pytest.raises(ValueError, match="empty calendar window"):
            parse_time_window("2026-08-05", "2026-08-03")
        # Datetime equality is empty too
        with pytest.raises(ValueError, match="empty calendar window"):
            parse_time_window("2026-08-03T09:00:00Z", "2026-08-03T09:00:00Z")
