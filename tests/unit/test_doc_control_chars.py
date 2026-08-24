"""Control characters Google Docs destroys on the way in (mise-melaso).

Two layers, and the second is the one that matters. The pure transform is
easy to get right and easy to leave unwired — the failure this whole item
came from was a silent thinning nobody was told about, so the wiring tests
assert on the bytes/JSON that would actually reach Google and on the cues
the caller actually reads, per write path.

Live measurements behind the expectations (2026-08-24, five scratch docs,
``docs/research/2026-08-24-melaso-control-chars/``):

- markdown import: ``\\f`` deleted, ``\\x00`` TRUNCATES the document there
- insertText / replaceAllText: ``\\f`` and ``\\x00`` both deleted
- a ``---`` line imports as a real ``horizontalRule`` element (probe F1),
  which is why converting is "survives visibly" and not a second guess
- a plain byte upload (``doc_type='file'``) preserves both, which is why
  the plain-file paths must NOT be sanitised
"""

from unittest.mock import MagicMock, patch

import pytest

from models import DoResult
from tools.create import do_create
from tools.doc_control_chars import (
    PAGE_BREAK_MARKER,
    apply_sanitise_cues,
    find_string_warning,
    sanitise_doc_content,
    sanitise_for_import,
    sanitise_for_insert,
)
from tools.edit import do_append, do_prepend, do_replace_text
from tools.overwrite import do_overwrite


@pytest.fixture(autouse=True)
def _stub_restore_points(monkeypatch):
    """No revisions.list calls from unit tests (same reason as test_edit.py)."""
    monkeypatch.setattr(
        "tools.edit.capture_restore_point", lambda file_id, comment=False: {}
    )
    monkeypatch.setattr(
        "tools.overwrite.capture_restore_point", lambda file_id, comment=False: {}
    )


GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
# A two-page PDF deposit's shape: pdftotext ends each page with a form feed.
PDF_SHAPED = "Page one.\n\fPage two.\n"


def _doc_metadata(name: str = "Test Doc") -> dict:
    return {"mimeType": GOOGLE_DOC_MIME, "name": name}


def _plain_metadata(name: str = "notes.md") -> dict:
    return {"mimeType": "text/markdown", "name": name, "size": "12"}


def _mock_sync_client(end_index: int = 50):
    client = MagicMock()
    client.get_json.return_value = {
        "title": "Test Doc",
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0", "title": "Tab 1"},
                "documentTab": {
                    "body": {"content": [{"endIndex": 1}, {"endIndex": end_index}]}
                },
            }
        ],
    }
    client.post_json.return_value = {
        "replies": [{"replaceAllText": {"occurrencesChanged": 1}}]
    }
    return client


def _inserted_text(mock_client) -> str:
    """The text that would cross the wire on an insertText batchUpdate."""
    requests = mock_client.post_json.call_args_list[0][1]["json_body"]["requests"]
    return requests[0]["insertText"]["text"]


# =============================================================================
# THE TRANSFORM
# =============================================================================

class TestSanitiseDocContent:
    def test_clean_content_is_returned_untouched(self) -> None:
        content, warnings, counts = sanitise_doc_content("# Title\n\nBody.", rich=True)
        assert content == "# Title\n\nBody."
        assert warnings == []
        assert counts == {}

    def test_empty_and_none_pass_through(self) -> None:
        assert sanitise_doc_content("", rich=True) == ("", [], {})
        assert sanitise_doc_content(None, rich=False) == (None, [], {})

    def test_form_feed_becomes_a_visible_marker(self) -> None:
        content, warnings, counts = sanitise_doc_content(PDF_SHAPED, rich=True)
        assert "\f" not in content
        assert PAGE_BREAK_MARKER.strip() in content
        assert counts == {"page_breaks_marked": 1}
        assert "form feed" in warnings[0]
        assert "page" in warnings[0].lower()

    def test_every_form_feed_is_counted_and_replaced(self) -> None:
        content, _warnings, counts = sanitise_doc_content(
            "a\fb\fc\fd", rich=False
        )
        assert "\f" not in content
        assert counts["page_breaks_marked"] == 3

    def test_nul_is_stripped_and_counted(self) -> None:
        content, warnings, counts = sanitise_doc_content("BEFORE\x00AFTER", rich=True)
        assert content == "BEFOREAFTER"
        assert counts == {"nuls_removed": 1}
        assert "NUL" in warnings[0]

    def test_import_path_nul_warning_names_the_truncation(self) -> None:
        """The consequence differs by engine and the caller needs the sharp one."""
        _c, rich_warnings, _ = sanitise_doc_content("a\x00b", rich=True)
        _c, plain_warnings, _ = sanitise_doc_content("a\x00b", rich=False)
        assert "TRUNCATES" in rich_warnings[0]
        assert "TRUNCATES" not in plain_warnings[0]
        assert "deletes NULs" in plain_warnings[0]

    def test_marker_wording_differs_by_engine(self) -> None:
        _c, rich_warnings, _ = sanitise_doc_content("a\fb", rich=True)
        _c, plain_warnings, _ = sanitise_doc_content("a\fb", rich=False)
        assert "horizontal rule" in rich_warnings[0]
        assert "plain text" in plain_warnings[0]

    def test_both_characters_together(self) -> None:
        content, warnings, counts = sanitise_doc_content("a\fb\x00c", rich=True)
        assert "\f" not in content and "\x00" not in content
        assert counts == {"page_breaks_marked": 1, "nuls_removed": 1}
        assert len(warnings) == 2

    def test_characters_docs_keeps_are_left_alone(self) -> None:
        """\\r\\n, \\t and \\v measured as surviving — transforming them would be
        noise, and \\v specifically MUST survive: convert_fenced_blocks relies
        on Docs importing backslash hard breaks as \\v."""
        original = "a\r\nb\tc\vd"
        content, warnings, counts = sanitise_doc_content(original, rich=True)
        assert content == original
        assert warnings == [] and counts == {}


class TestInteractionWithFencedBlocks:
    """The marker is inserted BEFORE convert_fenced_blocks runs, so the two
    passes meet. A form feed inside a code fence is vanishingly rare —
    pdftotext emits one only at a page boundary, after a whole line — but
    the meeting should degrade rather than corrupt: the block stays a block,
    the marker becomes one more code line, and nothing is lost."""

    def test_form_feed_inside_a_fence_keeps_the_block_intact(self) -> None:
        from markdown_import import convert_fenced_blocks

        content, _warnings, counts = sanitise_doc_content(
            "```\nline one\fline two\n```\n", rich=True
        )
        assert counts["page_breaks_marked"] == 1
        rendered = convert_fenced_blocks(content)
        assert "```" not in rendered  # the fence was consumed as a fence
        assert "`line one`" in rendered and "`line two`" in rendered
        assert "`---`" in rendered  # marker survives as a code line


class TestCallSiteWrappers:
    def test_import_wrapper_gates_on_doc_type(self) -> None:
        """Only doc_type='doc' rides the markdown import engine."""
        for doc_type in ("file", "sheet", None):
            content, state = sanitise_for_import(doc_type, PDF_SHAPED)
            assert content == PDF_SHAPED, doc_type
            assert state == ([], {}), doc_type

    def test_import_wrapper_transforms_docs(self) -> None:
        content, (warnings, counts) = sanitise_for_import("doc", PDF_SHAPED)
        assert "\f" not in content
        assert counts["page_breaks_marked"] == 1 and warnings

    def test_insert_wrapper_transforms_unconditionally(self) -> None:
        content, (warnings, counts) = sanitise_for_insert(PDF_SHAPED)
        assert "\f" not in content
        assert counts["page_breaks_marked"] == 1 and warnings

    def test_apply_cues_appends_rather_than_replacing(self) -> None:
        """A second warning must not silence the first — the tab path folds a
        duplicate-title warning into the same list."""
        cues: dict[str, object] = {"warnings": ["pre-existing"]}
        apply_sanitise_cues(cues, (["new one"], {"nuls_removed": 2}))
        assert cues["warnings"] == ["pre-existing", "new one"]
        assert cues["nuls_removed"] == 2

    def test_apply_cues_is_a_no_op_when_nothing_changed(self) -> None:
        cues: dict[str, object] = {}
        apply_sanitise_cues(cues, ([], {}))
        assert cues == {}


class TestFindStringWarning:
    def test_clean_find_gets_no_warning(self) -> None:
        assert find_string_warning("ordinary text") is None
        assert find_string_warning("") is None
        assert find_string_warning(None) is None

    def test_control_chars_in_find_are_diagnosed(self) -> None:
        warning = find_string_warning("page one\fpage two")
        assert warning is not None and "can never match" in warning
        assert "form feed" in find_string_warning("a\fb")
        assert "NUL" in find_string_warning("a\x00b")


# =============================================================================
# THE WIRING — per write path, asserted on what crosses the wire
# =============================================================================

class TestCreateWiring:
    @patch("retry.time.sleep")
    @patch("tools.create.get_sync_client")
    def test_create_doc_uploads_sanitised_bytes(self, mock_get_client, _sleep) -> None:
        client = MagicMock()
        client.upload_multipart.return_value = {
            "id": "doc1", "webViewLink": "http://x", "name": "T",
        }
        mock_get_client.return_value = client

        result = do_create(f"Page one.\n\fPage two.\x00\n", "T")

        assert isinstance(result, DoResult)
        uploaded = client.upload_multipart.call_args[0][2]
        assert b"\x0c" not in uploaded and b"\x00" not in uploaded
        assert b"---" in uploaded
        assert result.cues["page_breaks_marked"] == 1
        assert result.cues["nuls_removed"] == 1
        assert len(result.cues["warnings"]) == 2

    @patch("retry.time.sleep")
    @patch("tools.create.get_sync_client")
    def test_create_file_preserves_control_chars(self, mock_get_client, _sleep) -> None:
        """A plain byte upload keeps both (measured) — it must not be touched."""
        client = MagicMock()
        client.upload_multipart.return_value = {
            "id": "f1", "webViewLink": "http://x", "name": "notes.md",
        }
        mock_get_client.return_value = client

        result = do_create("Page one.\n\fPage two.", "notes.md", doc_type="file")

        assert isinstance(result, DoResult)
        assert b"\x0c" in client.upload_multipart.call_args[0][2]
        assert "page_breaks_marked" not in result.cues


class TestOverwriteWiring:
    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content")
    def test_overwrite_doc_uploads_sanitised_bytes(self, mock_upload, _sleep) -> None:
        mock_upload.return_value = {"name": "My Doc"}

        result = do_overwrite(file_id="doc123", content="Head\x00Tail\fMore")

        assert isinstance(result, DoResult)
        uploaded = mock_upload.call_args[0][1]
        assert b"\x00" not in uploaded and b"\x0c" not in uploaded
        # The tail the live import would have discarded is still there.
        assert b"Tail" in uploaded and b"More" in uploaded
        assert result.cues["nuls_removed"] == 1
        assert result.cues["page_breaks_marked"] == 1

    @patch("retry.time.sleep")
    @patch("tools.plain_file.upload_file_content")
    def test_overwrite_plain_file_preserves_control_chars(
        self, mock_upload, _sleep
    ) -> None:
        """The transform sits BELOW the plain-file routing, deliberately."""
        mock_upload.return_value = {"name": "notes.md"}

        result = do_overwrite(
            file_id="file123", content="Page one.\n\fPage two.",
            metadata=_plain_metadata(),
        )

        assert isinstance(result, DoResult)
        assert b"\x0c" in mock_upload.call_args[0][1]
        assert "page_breaks_marked" not in result.cues


class TestInsertWiring:
    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_prepend_sanitises(self, mock_get_client, _sleep) -> None:
        client = _mock_sync_client()
        mock_get_client.return_value = client

        result = do_prepend("doc123", "Head\fTail\x00")

        assert isinstance(result, DoResult)
        text = _inserted_text(client)
        assert "\f" not in text and "\x00" not in text and "---" in text
        assert result.cues["page_breaks_marked"] == 1
        assert result.cues["nuls_removed"] == 1

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_append_sanitises(self, mock_get_client, _sleep) -> None:
        client = _mock_sync_client(end_index=100)
        mock_get_client.return_value = client

        result = do_append("doc123", PDF_SHAPED)

        assert isinstance(result, DoResult)
        assert "\f" not in _inserted_text(client)
        assert result.cues["page_breaks_marked"] == 1
        assert any("form feed" in w for w in result.cues["warnings"])

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_inserted_chars_counts_what_was_actually_written(
        self, mock_get_client, _sleep
    ) -> None:
        """The cue must describe the sanitised text, not the caller's input —
        an inserted_chars taken before the transform is a small lie about the
        document."""
        client = _mock_sync_client(end_index=100)
        mock_get_client.return_value = client

        result = do_append("doc123", PDF_SHAPED)

        assert result.cues["inserted_chars"] == len(_inserted_text(client))

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_multi_tab_warning_and_disclosure_coexist(
        self, mock_get_client, _sleep
    ) -> None:
        """_append assigns its own cues['warnings'] list; the disclosure is
        folded in afterwards and must extend it, not replace it."""
        client = _mock_sync_client(end_index=100)
        client.get_json.return_value["tabs"].append(
            {
                "tabProperties": {"tabId": "t.1", "title": "Redraft"},
                "documentTab": {"body": {"content": [{"endIndex": 20}]}},
            }
        )
        mock_get_client.return_value = client

        result = do_append("doc123", PDF_SHAPED)

        warnings = result.cues["warnings"]
        assert any("FIRST tab" in w for w in warnings)
        assert any("form feed" in w for w in warnings)

    @patch("retry.time.sleep")
    @patch("tools.edit.plain_append")
    def test_append_plain_file_is_not_sanitised(self, mock_plain, _sleep) -> None:
        mock_plain.return_value = DoResult(
            file_id="f1", title="notes.md", web_link="http://x",
            operation="append", cues={},
        )

        do_append("file123", PDF_SHAPED, metadata=_plain_metadata())

        assert mock_plain.call_args[0][1] == PDF_SHAPED


class TestTabWiring:
    @patch("retry.time.sleep")
    @patch("tools.edit.add_tab_with_content")
    @patch("tools.edit.get_doc_tabs_meta")
    def test_append_tab_sanitises(self, mock_meta, mock_add, _sleep) -> None:
        mock_meta.return_value = {
            "title": "Doc", "tabs": [{"tab_id": "t.0", "title": "Tab 1",
                                      "index": 0, "depth": 0}],
        }
        mock_add.return_value = {"tabId": "t.9", "title": "Draft", "index": 1}

        result = do_append("doc123", PDF_SHAPED, metadata=_doc_metadata(),
                           tab="Draft")

        assert isinstance(result, DoResult)
        written = mock_add.call_args[0][2]
        assert "\f" not in written and "---" in written
        assert result.cues["page_breaks_marked"] == 1
        assert result.cues["inserted_chars"] == len(written)

    @patch("retry.time.sleep")
    @patch("tools.edit.add_tab_with_content")
    @patch("tools.edit.get_doc_tabs_meta")
    def test_duplicate_title_warning_does_not_silence_the_disclosure(
        self, mock_meta, mock_add, _sleep
    ) -> None:
        """Both warnings must survive — the duplicate-title cue used to
        ASSIGN cues['warnings'], which would have dropped the other."""
        mock_meta.return_value = {
            "title": "Doc", "tabs": [{"tab_id": "t.0", "title": "Draft",
                                      "index": 0, "depth": 0}],
        }
        mock_add.return_value = {"tabId": "t.9", "title": "Draft", "index": 1}

        result = do_append("doc123", PDF_SHAPED, metadata=_doc_metadata(),
                           tab="Draft")

        warnings = result.cues["warnings"]
        assert any("form feed" in w for w in warnings)
        assert any("already existed" in w for w in warnings)


class TestReplaceTextWiring:
    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_replacement_text_is_sanitised(self, mock_get_client, _sleep) -> None:
        client = _mock_sync_client()
        mock_get_client.return_value = client

        result = do_replace_text("doc123", find="OLD", content="new\fnew\x00")

        assert isinstance(result, DoResult)
        requests = client.post_json.call_args_list[0][1]["json_body"]["requests"]
        replacement = requests[0]["replaceAllText"]["replaceText"]
        assert "\f" not in replacement and "\x00" not in replacement
        assert result.cues["page_breaks_marked"] == 1
        assert result.cues["nuls_removed"] == 1

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_find_carrying_a_form_feed_is_diagnosed(
        self, mock_get_client, _sleep
    ) -> None:
        """A find copied out of a PDF deposit can never match — say so rather
        than let the caller read 0 occurrences as 'not in the document'."""
        client = _mock_sync_client()
        mock_get_client.return_value = client

        result = do_replace_text("doc123", find="page one\fpage two",
                                 content="replacement")

        assert isinstance(result, DoResult)
        assert any("can never match" in w for w in result.cues["warnings"])

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_clean_replace_text_gains_no_control_char_cues(
        self, mock_get_client, _sleep
    ) -> None:
        client = _mock_sync_client()
        mock_get_client.return_value = client

        result = do_replace_text("doc123", find="OLD", content="NEW")

        assert isinstance(result, DoResult)
        assert "page_breaks_marked" not in result.cues
        assert "nuls_removed" not in result.cues
        assert "warnings" not in result.cues
