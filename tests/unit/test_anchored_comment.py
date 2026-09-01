"""
Tests for anchored do(comment) (mise-jupuja).

Sameer's falsifier on the card, verbatim: *"is that we make assumptions about
the interactions around comments without thinking through all the different
possibilities."* So this file is organised as an ENUMERATION of the interaction
space rather than by function, and each class names one axis. A possibility that
was considered and deliberately refused gets a test proving it refuses — a
refusal is a design decision, and an undocumented one is indistinguishable from
an oversight.

The load-bearing invariant across all of it: **a comment either lands exactly
where it was aimed, or it does not land at all.** Anchoring is not best-effort.
A comment placed on the wrong sentence reads as authored intent, and the human
has no way to tell it was a machine's near-miss.

Live shapes and every measured claim: docs/research/2026-09-01-jupuja-anchored-write/
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from adapters.anchored_comments import thread_from_reply
from extractors.comment_anchors import (
    column_index,
    locate_quote,
    parse_a1_cell,
    parse_slide_spec,
)
from models import CommentData, ErrorKind, MiseError
from tools.comment import do_comment

DOC = "application/vnd.google-apps.document"
DECK = "application/vnd.google-apps.presentation"
SHEET = "application/vnd.google-apps.spreadsheet"
FID = "1" + "a" * 30


def doc_payload(*paragraphs: str, tabs: int = 1) -> dict:
    """A documents.get payload with real index arithmetic (1-based, \n per para)."""
    content, index = [], 1
    for text in paragraphs:
        body = text + "\n"
        content.append({
            "startIndex": index, "endIndex": index + len(body),
            "paragraph": {"elements": [{
                "startIndex": index, "endIndex": index + len(body),
                "textRun": {"content": body}}]},
        })
        index += len(body)
    if tabs == 1:
        return {"revisionId": "rev1", "tabs": [
            {"tabProperties": {"tabId": "t.0"}, "documentTab": {"body": {"content": content}}}]}
    return {"revisionId": "rev1", "tabs": [
        {"tabProperties": {"tabId": f"t.{i}"}, "documentTab": {"body": {"content": content}}}
        for i in range(tabs)]}


def deck_payload(n: int = 3) -> dict:
    return {"revisionId": "r1", "slides": [{"objectId": f"g{i}"} for i in range(n)]}


def book_payload() -> dict:
    return {"sheets": [
        {"properties": {"sheetId": 0, "title": "Sheet1",
                        "gridProperties": {"rowCount": 100, "columnCount": 26}}},
        {"properties": {"sheetId": 77, "title": "Q3 Plan",
                        "gridProperties": {"rowCount": 10, "columnCount": 5}}}]}


def ok_reply(comment_id: str = "AAAC1", quote: str = "some text") -> dict:
    return {"replies": [{"insertComment": {"commentThread": {
        "commentId": comment_id, "plainTextQuote": quote,
        "headPost": {"content": "x"}}}}]}


def run(mime: str, **kwargs):
    """do_comment with the metadata read stubbed to one mime type."""
    with patch("tools.comment.get_file_metadata", return_value={"mimeType": mime}):
        return do_comment(**kwargs)


# =============================================================================
# Axis 1 — the anchor grammar itself
# =============================================================================


class TestGrammar:
    @pytest.mark.parametrize("letters,expected", [
        ("A", 0), ("B", 1), ("Z", 25), ("AA", 26), ("AB", 27), ("ZZ", 701), ("AAA", 702)])
    def test_column_index(self, letters: str, expected: int) -> None:
        assert column_index(letters) == expected

    def test_column_index_round_trips_its_own_label(self) -> None:
        """The write side inverts the read side's arithmetic; if they disagree,
        a locator read out of comments.md aims somewhere else when pasted back."""
        from extractors.comment_anchors import column_label
        for i in (0, 1, 25, 26, 27, 701, 702, 18277):
            assert column_index(column_label(i)) == i

    @pytest.mark.parametrize("spec,expected", [
        ("B12", (None, 11, 1)), ("b12", (None, 11, 1)), ("$B$12", (None, 11, 1)),
        ("Sheet1!B12", ("Sheet1", 11, 1)), ("'Q3 Plan'!C4", ("Q3 Plan", 3, 2)),
        ("'O''Brien'!A1", ("O'Brien", 0, 0)), ("AB1", (None, 0, 27))])
    def test_parse_a1_cell(self, spec: str, expected: tuple) -> None:
        assert parse_a1_cell(spec) == expected

    @pytest.mark.parametrize("spec", ["B2:D5", "Sheet1!A1:B2", "", "12", "B", "!B2", "'unterminated!B2"])
    def test_parse_a1_cell_refuses(self, spec: str) -> None:
        """A range is refused rather than reduced to its corner: a caller who
        wrote B2:D5 asked for something this API cannot do, and commenting on B2
        would be a different act from the one they requested."""
        with pytest.raises(ValueError):
            parse_a1_cell(spec)

    @pytest.mark.parametrize("spec,expected", [
        ("slide 3", ("index", 2)), ("Slide 1", ("index", 0)), ("3", ("index", 2)),
        ("g4f9", ("object_id", "g4f9")), ("p", ("object_id", "p"))])
    def test_parse_slide_spec(self, spec: str, expected: tuple) -> None:
        assert parse_slide_spec(spec) == expected

    def test_slide_zero_is_refused(self) -> None:
        """comments.md prints 1-based numbers, so 'slide 0' is a category error,
        not an off-by-one to absorb silently."""
        with pytest.raises(ValueError):
            parse_slide_spec("slide 0")


class TestQuoteLocation:
    def test_finds_a_unique_quote_with_real_indices(self) -> None:
        payload = doc_payload("Hello world.", "The second paragraph is here.")
        assert locate_quote(payload, "second paragraph") == [(18, 34)]

    def test_whitespace_is_forgiven(self) -> None:
        """Docs splits sentences across runs and ends paragraphs with \\n, so a
        caller's quote never matches raw text."""
        payload = doc_payload("A sentence   with  odd spacing")
        assert len(locate_quote(payload, "sentence with odd spacing")) == 1

    def test_every_occurrence_is_returned(self) -> None:
        """The COUNT is the caller's business — one anchors, several must refuse."""
        payload = doc_payload("repeat me", "and repeat me again")
        assert len(locate_quote(payload, "repeat me")) == 2

    def test_absent_quote_returns_nothing(self) -> None:
        assert locate_quote(doc_payload("something else"), "not here") == []

    def test_empty_quote_matches_nothing(self) -> None:
        """A positive control against a matcher that would anchor anywhere."""
        assert locate_quote(doc_payload("anything"), "   ") == []


# =============================================================================
# Axis 2 — anchor grammar against the wrong surface
# =============================================================================


class TestSurfaceMismatch:
    def test_slide_spec_on_a_workbook_refuses(self) -> None:
        with patch("tools.comment.read_spreadsheet_tabs", return_value=book_payload()):
            r = run(SHEET, file_id=FID, content="c", anchor="slide 3")
        assert r["error"] and "cell reference" in r["message"]

    def test_cell_spec_on_a_deck_refuses(self) -> None:
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload()):
            r = run(DECK, file_id=FID, content="c", anchor="Sheet1!B12")
        assert r["error"] and "object id" in r["message"]

    def test_anchoring_a_pdf_refuses(self) -> None:
        """Anchored comments exist on three surfaces. Everything else — PDFs,
        uploads, folders — has no anchor plane at all."""
        r = run("application/pdf", file_id=FID, content="c", anchor="slide 1")
        assert r["error"] and "Docs, Sheets and Slides" in r["message"]
        assert "Drop anchor=" in r["message"]


# =============================================================================
# Axis 3 — the anchor resolves to nothing, or to too much
# =============================================================================


class TestResolutionRefusals:
    def test_slide_past_the_end_names_the_deck_size(self) -> None:
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload(3)):
            r = run(DECK, file_id=FID, content="c", anchor="slide 9")
        assert r["error"] and "3 slide(s)" in r["message"]

    def test_unknown_slide_object_id_refuses(self) -> None:
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload()):
            r = run(DECK, file_id=FID, content="c", anchor="gNOPE")
        assert r["error"] and "no slide with object id" in r["message"]

    def test_unknown_tab_names_the_tabs_that_exist(self) -> None:
        with patch("tools.comment.read_spreadsheet_tabs", return_value=book_payload()):
            r = run(SHEET, file_id=FID, content="c", anchor="Nonexistent!B2")
        assert r["error"] and "'Sheet1'" in r["message"] and "'Q3 Plan'" in r["message"]

    def test_cell_off_the_grid_refuses(self) -> None:
        with patch("tools.comment.read_spreadsheet_tabs", return_value=book_payload()):
            r = run(SHEET, file_id=FID, content="c", anchor="'Q3 Plan'!A99")
        assert r["error"] and "10 rows" in r["message"]

    def test_missing_quote_refuses(self) -> None:
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("nothing relevant here")):
            r = run(DOC, file_id=FID, content="c", anchor="a passage that is absent")
        assert r["error"] and "was not found" in r["message"]

    def test_ambiguous_quote_refuses_and_counts(self) -> None:
        """The dangerous one. Taking the first occurrence would place the comment
        on text the caller did not mean, and nothing downstream could notice."""
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("see the note", "again, see the note")):
            r = run(DOC, file_id=FID, content="c", anchor="see the note")
        assert r["error"] and "appears 2 times" in r["message"]

    def test_multi_tab_doc_refuses_rather_than_guessing(self) -> None:
        """Declined deliberately: the per-tab index space is unmeasured, and an
        anchor resolved in the wrong tab's coordinates is exactly the failure
        this whole design refuses to risk."""
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("some text", tabs=3)):
            r = run(DOC, file_id=FID, content="c", anchor="some text")
        assert r["error"] and "3 tabs" in r["message"]

    def test_every_resolution_refusal_names_the_fallback(self) -> None:
        """A refusal that doesn't say what to do instead is a dead end."""
        cases = [
            (DECK, "read_presentation_slides", deck_payload(), "slide 9"),
            (SHEET, "read_spreadsheet_tabs", book_payload(), "Nope!B2"),
            (DOC, "read_document_for_anchoring", doc_payload("x"), "absent text"),
        ]
        for mime, fn, payload, anchor in cases:
            with patch(f"tools.comment.{fn}", return_value=payload):
                r = run(mime, file_id=FID, content="c", anchor=anchor)
            assert "Drop anchor=" in r["message"], f"{mime} refusal has no exit"


# =============================================================================
# Axis 4 — a refused anchor must NEVER become an unanchored comment
# =============================================================================


class TestNeverSilentlyDowngrades:
    @pytest.mark.parametrize("mime,fn,payload,anchor", [
        (DECK, "read_presentation_slides", deck_payload(), "slide 99"),
        (SHEET, "read_spreadsheet_tabs", book_payload(), "ZZZ!A1"),
        (DOC, "read_document_for_anchoring", doc_payload("hello"), "goodbye"),
    ])
    def test_no_unanchored_comment_is_created_on_refusal(
        self, mime: str, fn: str, payload: dict, anchor: str
    ) -> None:
        with patch(f"tools.comment.{fn}", return_value=payload), \
             patch("tools.comment.create_comment") as fallback:
            r = run(mime, file_id=FID, content="c", anchor=anchor)
        assert r["error"] is True
        fallback.assert_not_called()

    def test_preview_refusal_is_loud_not_a_downgrade(self) -> None:
        """An unenrolled caller gets a refusal here, where the READ side (dukacu)
        degrades quietly. Opposite treatments, and deliberately: a missing
        locator costs information, a misplaced comment costs trust."""
        denied = MiseError(ErrorKind.PERMISSION_DENIED,
                           "creating the anchored comment: HTTP 403",
                           details={"http_status": 403, "google_message": "denied"})
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload()), \
             patch("tools.comment.insert_slide_comment", side_effect=denied), \
             patch("tools.comment.create_comment") as fallback:
            r = run(DECK, file_id=FID, content="c", anchor="slide 1")
        assert r["error"] and r["kind"] == "permission_denied"
        assert "Drop anchor=" in r["message"]
        fallback.assert_not_called()


# =============================================================================
# Axis 5 — the file changes while we are aiming (the dukacu race, write side)
# =============================================================================


class TestRevisionRace:
    @staticmethod
    def _stale() -> MiseError:
        return MiseError(ErrorKind.INVALID_INPUT, "creating the anchored comment: HTTP 400",
                         details={"http_status": 400,
                                  "google_message": "The required revision ID 'x' "
                                                    "does not match the latest revision."})

    def test_a_stale_pin_re_resolves_and_retries(self) -> None:
        """The caller's intent is the TEXT they quoted, not the indices we
        derived from it — so a document that moved under us is re-read and the
        quote located again, rather than the write being abandoned."""
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("anchor me")) as read, \
             patch("tools.comment.insert_doc_comment",
                   side_effect=[self._stale(), ok_reply(quote="anchor me")]) as insert:
            r = run(DOC, file_id=FID, content="c", anchor="anchor me")
        assert not isinstance(r, dict), r
        assert read.call_count == 2 and insert.call_count == 2
        assert r.cues["retries"] == 1

    def test_a_permanently_moving_file_refuses_without_writing(self) -> None:
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("anchor me")), \
             patch("tools.comment.insert_doc_comment", side_effect=self._stale()) as insert:
            r = run(DOC, file_id=FID, content="c", anchor="anchor me")
        assert r["error"] and r["kind"] == "conflict"
        assert "nothing was" in r["message"]
        assert insert.call_count == 3  # the first try plus two re-resolves

    def test_a_non_stale_error_does_not_retry(self) -> None:
        """Positive control for the retry: only the revision-mismatch message
        loops. Retrying a genuine refusal would triple every failed call."""
        other = MiseError(ErrorKind.INVALID_INPUT, "creating the anchored comment: HTTP 400",
                          details={"http_status": 400, "google_message": "Index 9 must be less"})
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("anchor me")), \
             patch("tools.comment.insert_doc_comment", side_effect=other) as insert:
            r = run(DOC, file_id=FID, content="c", anchor="anchor me")
        assert r["error"] and insert.call_count == 1

    def test_sheets_says_it_cannot_guard_the_race(self) -> None:
        """Sheets accepts writeControl and ignores it (measured), so the guard
        the other two surfaces have does not exist here. Disclosed, not faked."""
        with patch("tools.comment.read_spreadsheet_tabs", return_value=book_payload()), \
             patch("tools.comment.insert_cell_comment", return_value=ok_reply(quote="B12")):
            r = run(SHEET, file_id=FID, content="c", anchor="Sheet1!B12")
        assert "no revision guard" in r.cues["race_note"]


# =============================================================================
# Axis 6 — assignment
# =============================================================================


class TestAssignment:
    def test_assigning_needs_an_anchor(self) -> None:
        """The Drive plane that serves unanchored comments has no assignee
        field at all, so accepting to= there would drop it in silence."""
        r = do_comment(file_id=FID, content="c", to="a@b.com")
        assert r["error"] and "anchored comment" in r["message"]

    def test_one_assignee_only(self) -> None:
        r = do_comment(file_id=FID, content="c", anchor="slide 1", to="a@b.com, c@d.com")
        assert r["error"] and "ONE person" in r["message"]

    def test_assignment_always_carries_the_access_warning(self) -> None:
        """Measured 2026-09-01: Google accepted and stored an assignee who could
        not open the file, with no error and no notification. A 200 therefore
        cannot be read as 'they will see it'."""
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload()), \
             patch("tools.comment.insert_slide_comment", return_value=ok_reply()) as insert:
            r = run(DECK, file_id=FID, content="c", anchor="slide 2", to="a@b.com")
        assert r.cues["assignee"] == "a@b.com"
        assert "does NOT check" in r.cues["assignee_warning"]
        assert insert.call_args.kwargs["assignee"] == "a@b.com"

    def test_no_assignee_no_warning(self) -> None:
        """Positive control — the warning must not be unconditional furniture."""
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload()), \
             patch("tools.comment.insert_slide_comment", return_value=ok_reply()):
            r = run(DECK, file_id=FID, content="c", anchor="slide 2")
        assert "assignee_warning" not in r.cues


# =============================================================================
# Axis 7 — the unanchored default, and what it now admits (mise-mikawi)
# =============================================================================


class TestUnanchoredDefault:
    def test_unanchored_still_works_and_takes_no_extra_call(self) -> None:
        with patch("tools.comment.create_comment",
                   return_value=CommentData(id="c1", content="x", author_name="A")), \
             patch("tools.comment.get_file_metadata") as meta:
            r = do_comment(file_id=FID, content="hello")
        assert r.cues["comment_id"] == "c1"
        meta.assert_not_called()  # no anchor, no mime lookup, no preview dependency

    def test_unanchored_discloses_that_it_renders_nowhere(self) -> None:
        """mise-mikawi: five comments posted this way during real work were
        reported as missing. They were not missing — they were panel-only, and
        nothing said so."""
        with patch("tools.comment.create_comment",
                   return_value=CommentData(id="c1", content="x", author_name="A")):
            r = do_comment(file_id=FID, content="hello")
        assert r.cues["anchored"] is False
        assert "Original content deleted" in r.cues["visibility"]
        assert "anchor=" in r.cues["visibility"]

    def test_anchored_result_does_not_carry_the_panel_only_warning(self) -> None:
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload()), \
             patch("tools.comment.insert_slide_comment", return_value=ok_reply()):
            r = run(DECK, file_id=FID, content="c", anchor="slide 1")
        assert "visibility" not in r.cues


# =============================================================================
# Axis 8 — what the caller is told about where it landed
# =============================================================================


class TestLandingDisclosure:
    def test_the_api_s_own_quote_is_echoed_back(self) -> None:
        """plainTextQuote is the API reporting the text it anchored to — the
        only confirmation that the comment landed where it was aimed rather
        than merely landing."""
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("the target sentence")), \
             patch("tools.comment.insert_doc_comment",
                   return_value=ok_reply(quote="the target sentence")):
            r = run(DOC, file_id=FID, content="c", anchor="the target sentence")
        assert r.cues["anchor_text"] == "the target sentence"

    def test_slide_label_uses_the_read_side_s_numbering(self) -> None:
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload()), \
             patch("tools.comment.insert_slide_comment", return_value=ok_reply()):
            r = run(DECK, file_id=FID, content="c", anchor="g2")
        assert r.cues["anchored_to"] == "slide 3"  # objectId resolved to its position

    def test_a_reply_with_no_thread_refuses_rather_than_inventing_an_id(self) -> None:
        """A 200 carrying no thread means the write reported success without
        producing a comment. Minting a cue from it would be a fabricated id."""
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload()), \
             patch("tools.comment.insert_slide_comment", return_value={"replies": [{}]}):
            r = run(DECK, file_id=FID, content="c", anchor="slide 1")
        assert r["error"] and "cannot confirm" in r["message"]

    @pytest.mark.parametrize("response", [
        {}, {"replies": []}, {"replies": [None]}, {"replies": [{"insertComment": {}}]}])
    def test_thread_from_reply_survives_every_empty_shape(self, response: dict) -> None:
        assert thread_from_reply(response) is None


# =============================================================================
# Axis 9 — the write must not be able to touch content
# =============================================================================


class TestWriteIsCommentOnly:
    def test_the_batch_carries_exactly_one_insert_comment(self) -> None:
        """A comment write must never be able to alter the document it comments
        on, and a batch mixing comment and content requests can report failure
        while its content changes commit (picihi). One request, one kind."""
        from adapters.anchored_comments import insert_doc_comment

        client = MagicMock()
        client.post_json.return_value = ok_reply()
        with patch("adapters.anchored_comments.get_sync_client", return_value=client):
            insert_doc_comment("d", "text", 1, 5, revision="rev1")
        body = client.post_json.call_args.kwargs["json_body"]
        assert len(body["requests"]) == 1
        assert set(body["requests"][0]) == {"insertComment"}
        assert body["writeControl"] == {"requiredRevisionId": "rev1"}

    def test_sheets_is_never_sent_a_write_control(self) -> None:
        """Offering one would be a guard that guards nothing — measured: Sheets
        accepts a bogus requiredRevisionId and returns 200."""
        from adapters.anchored_comments import insert_cell_comment

        client = MagicMock()
        client.post_json.return_value = ok_reply()
        with patch("adapters.anchored_comments.get_sync_client", return_value=client):
            insert_cell_comment("s", "text", 0, 1, 1)
        assert "writeControl" not in client.post_json.call_args.kwargs["json_body"]

    def test_the_doc_read_pins_the_suggestions_inline_view(self) -> None:
        """The measurement that cost the most to find: insertComment.range is
        interpreted in the SUGGESTIONS_INLINE index space. Resolve against the
        clean view and the comment anchors to different text under a 200."""
        from adapters.anchored_comments import read_document_for_anchoring

        client = MagicMock()
        client.get_json.return_value = {}
        with patch("adapters.anchored_comments.get_sync_client", return_value=client):
            read_document_for_anchoring("d")
        params = client.get_json.call_args.kwargs["params"]
        assert params["suggestionsViewMode"] == "SUGGESTIONS_INLINE"

    def test_google_s_message_survives_into_the_error(self) -> None:
        """Google's own text is the diagnostic — a paraphrase loses the index or
        the object id that says what was actually wrong."""
        from adapters.anchored_comments import insert_slide_comment

        response = httpx.Response(
            400, request=httpx.Request("POST", "https://x"),
            json={"error": {"message": "The object (NO_SUCH) could not be found."}})
        client = MagicMock()
        client.post_json.side_effect = httpx.HTTPStatusError(
            "x", request=response.request, response=response)
        with patch("adapters.anchored_comments.get_sync_client", return_value=client):
            with pytest.raises(MiseError) as excinfo:
                insert_slide_comment("p", "c", "NO_SUCH")
        assert "NO_SUCH" in excinfo.value.message
        assert excinfo.value.kind is ErrorKind.INVALID_INPUT


# =============================================================================
# Axis 10 — the plain refusals that predate anchoring, still intact
# =============================================================================


class TestUnchangedContract:
    @pytest.mark.parametrize("kwargs,fragment", [
        ({"content": "c"}, "requires 'file_id'"),
        ({"file_id": FID}, "requires 'content'"),
        ({"file_id": FID, "content": "   "}, "requires 'content'"),
        ({"file_id": "not a drive id!", "content": "c"}, "file_id"),
    ])
    def test_basic_refusals(self, kwargs: dict, fragment: str) -> None:
        r = do_comment(**kwargs)
        assert r["error"] and fragment in r["message"]

    def test_agent_prefix_rides_the_anchored_plane_too(self) -> None:
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload()), \
             patch("tools.comment.insert_slide_comment", return_value=ok_reply()) as insert:
            run(DECK, file_id=FID, content="flag this", anchor="slide 1")
        assert insert.call_args.args[1].startswith("[agent] ")

    def test_an_already_prefixed_body_is_not_double_prefixed(self) -> None:
        with patch("tools.comment.read_presentation_slides", return_value=deck_payload()), \
             patch("tools.comment.insert_slide_comment", return_value=ok_reply()) as insert:
            run(DECK, file_id=FID, content="[agent] already", anchor="slide 1")
        assert insert.call_args.args[1] == "[agent] already"


# =============================================================================
# Axis 11 — what the ten axes above had NOT thought through
#
# Every test here is an essayeur finding (2026-09-01). The class the axes missed
# is the same one each time: the document was modelled as a flat list of sibling
# tabs holding paragraphs of plain BMP text, and a real Doc is none of those.
# =============================================================================


class TestDocumentsAreNotFlatParagraphsOfAscii:
    def test_astral_characters_do_not_slide_the_anchor(self) -> None:
        """Docs indices count UTF-16 code units; Python counts code points. Two
        emoji ahead of the target used to shift every index by two, and the
        comment landed two characters left under a 200. This repo already fixed
        the identical bug in tools/doc_chips.py (mise-rubucu) — the lesson was
        sitting in a sibling module and was not borrowed."""
        payload = doc_payload("Hi 👋👋 there, anchor me please")
        (start, end), = locate_quote(payload, "anchor me")
        # "Hi " = 3, two emoji = 4 UTF-16 units, " there, " = 8 → 1 + 15 = 16
        assert (start, end) == (16, 25)

    def test_an_astral_character_inside_the_quote_widens_the_end(self) -> None:
        payload = doc_payload("prefix 🎯 target")
        (start, end), = locate_quote(payload, "🎯 target")
        assert end - start == 9  # 2 + 1 + 6, not 8

    def test_text_inside_a_table_is_found(self) -> None:
        """A quote living only in a table used to report 'not found in this
        document' — false, and the caller had no way to know the search never
        looked there."""
        payload = {"revisionId": "r", "tabs": [{"tabProperties": {"tabId": "t.0"},
            "documentTab": {"body": {"content": [
                {"startIndex": 1, "endIndex": 60, "table": {"tableRows": [{"tableCells": [
                    {"content": [{"startIndex": 5, "endIndex": 20, "paragraph": {"elements": [
                        {"startIndex": 5, "endIndex": 20,
                         "textRun": {"content": "inside a cell\n"}}]}}]}]}]}}]}}}]}
        assert locate_quote(payload, "inside a cell") == [(5, 18)]

    def test_a_quote_in_prose_and_in_a_table_is_ambiguous(self) -> None:
        """The dangerous half of the same bug: the table copy was invisible, so
        two occurrences counted as one, no ambiguity refusal fired, and the
        comment anchored to the prose copy — with plainTextQuote echoing
        identical text, so even the landing check could not catch it."""
        payload = {"revisionId": "r", "tabs": [{"tabProperties": {"tabId": "t.0"},
            "documentTab": {"body": {"content": [
                {"startIndex": 1, "endIndex": 14, "paragraph": {"elements": [
                    {"startIndex": 1, "endIndex": 14, "textRun": {"content": "see the note\n"}}]}},
                {"startIndex": 14, "endIndex": 40, "table": {"tableRows": [{"tableCells": [
                    {"content": [{"startIndex": 20, "endIndex": 33, "paragraph": {"elements": [
                        {"startIndex": 20, "endIndex": 33,
                         "textRun": {"content": "see the note\n"}}]}}]}]}]}}]}}}]}
        assert len(locate_quote(payload, "see the note")) == 2
        with patch("tools.comment.read_document_for_anchoring", return_value=payload):
            r = run(DOC, file_id=FID, content="c", anchor="see the note")
        assert r["error"] and "appears 2 times" in r["message"]

    def test_nested_tabs_count_as_multi_tab(self) -> None:
        """Tabs nest. A document with one root tab and children arrived looking
        single-tab, so the deliberate multi-tab decline was vacuous against the
        shape real tabbed documents actually take."""
        para = [{"startIndex": 1, "endIndex": 8, "paragraph": {"elements": [
            {"startIndex": 1, "endIndex": 8, "textRun": {"content": "hello\n"}}]}}]
        payload = {"revisionId": "r", "tabs": [{
            "tabProperties": {"tabId": "t.0"},
            "documentTab": {"body": {"content": para}},
            "childTabs": [{"tabProperties": {"tabId": "t.1"},
                           "documentTab": {"body": {"content": para}}}]}]}
        with patch("tools.comment.read_document_for_anchoring", return_value=payload):
            r = run(DOC, file_id=FID, content="c", anchor="hello")
        assert r["error"] and "2 tabs" in r["message"]

    def test_the_not_found_refusal_scopes_its_own_claim(self) -> None:
        """"Not found in this document" was a claim the search could not support
        — it had only looked at part of it."""
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("nothing relevant")):
            r = run(DOC, file_id=FID, content="c", anchor="absent passage")
        assert "headers, footers, footnotes" in r["message"]


class TestTheLandingIsChecked:
    def test_a_mislanding_is_disclosed_loudly(self) -> None:
        """plainTextQuote was identified as the ORACLE in the research and then
        used as decoration. Comparing it turns every mislanding — including ones
        caused by bugs nobody has thought of — into a visible one."""
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("anchor me")), \
             patch("tools.comment.insert_doc_comment",
                   return_value=ok_reply(quote="ENTIRELY DIFFERENT TEXT")):
            r = run(DOC, file_id=FID, content="c", anchor="anchor me")
        assert "MISLANDED" in r.cues["landing_mismatch"]
        assert "AAAC1" in r.cues["landing_mismatch"]  # the id to delete

    def test_a_correct_landing_is_silent(self) -> None:
        """Positive control — the check must not cry wolf on every write."""
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("anchor me")), \
             patch("tools.comment.insert_doc_comment", return_value=ok_reply(quote="anchor  me")):
            r = run(DOC, file_id=FID, content="c", anchor="anchor me")
        assert "landing_mismatch" not in r.cues

    def test_a_missing_quote_on_a_doc_is_flagged_as_unverified(self) -> None:
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("anchor me")), \
             patch("tools.comment.insert_doc_comment", return_value=ok_reply(quote="")):
            r = run(DOC, file_id=FID, content="c", anchor="anchor me")
        assert "cannot confirm" in r.cues["landing_unverified"]

    def test_anchoring_to_a_suggestion_is_cued_as_provisional(self) -> None:
        """Anchoring to suggested text is legal and lands. It is also fragile:
        reject the suggestion and the thread orphans to 'Original content
        deleted' — the exact failure this feature exists to prevent."""
        payload = doc_payload("plain text here")
        run_obj = payload["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0]
        run_obj["textRun"]["suggestedInsertionIds"] = ["suggest.abc"]
        with patch("tools.comment.read_document_for_anchoring", return_value=payload), \
             patch("tools.comment.insert_doc_comment", return_value=ok_reply(quote="plain text here")):
            r = run(DOC, file_id=FID, content="c", anchor="plain text here")
        assert "unaccepted SUGGESTION" in r.cues["anchor_is_provisional"]

    def test_settled_text_is_not_flagged_provisional(self) -> None:
        with patch("tools.comment.read_document_for_anchoring",
                   return_value=doc_payload("plain text here")), \
             patch("tools.comment.insert_doc_comment", return_value=ok_reply(quote="plain text here")):
            r = run(DOC, file_id=FID, content="c", anchor="plain text here")
        assert "anchor_is_provisional" not in r.cues


class TestSlideRetriesAimAtTheSlideNotThePosition:
    @staticmethod
    def _stale() -> MiseError:
        return MiseError(ErrorKind.INVALID_INPUT, "creating the anchored comment: HTTP 400",
                         details={"http_status": 400,
                                  "google_message": "The required revision ID 'x' "
                                                    "does not match the latest revision."})

    def test_a_slide_inserted_mid_retry_does_not_steal_the_comment(self) -> None:
        """A Docs anchor is content-addressed, so re-resolving finds the caller's
        intent again. A slide anchor is POSITION-addressed, and the retry only
        happens when the deck changed — so re-resolving 'slide 2' aimed at a
        slide the caller had never seen."""
        before = {"revisionId": "r1", "slides": [{"objectId": "g0"}, {"objectId": "g1"}]}
        after = {"revisionId": "r2", "slides": [
            {"objectId": "g0"}, {"objectId": "gNEW"}, {"objectId": "g1"}]}
        with patch("tools.comment.read_presentation_slides", side_effect=[before, after]), \
             patch("tools.comment.insert_slide_comment",
                   side_effect=[self._stale(), ok_reply()]) as insert:
            r = run(DECK, file_id=FID, content="c", anchor="slide 2")
        assert insert.call_args.args[2] == "g1", "the retry re-aimed at the new slide"
        assert r.cues["anchored_to"] == "slide 2"

    def test_a_deleted_target_slide_refuses_rather_than_re_aiming(self) -> None:
        before = {"revisionId": "r1", "slides": [{"objectId": "g0"}, {"objectId": "g1"}]}
        after = {"revisionId": "r2", "slides": [{"objectId": "g0"}]}
        with patch("tools.comment.read_presentation_slides", side_effect=[before, after]), \
             patch("tools.comment.insert_slide_comment", side_effect=[self._stale(), ok_reply()]), \
             patch("tools.comment.create_comment") as fallback:
            r = run(DECK, file_id=FID, content="c", anchor="slide 2")
        assert r["error"] and r["kind"] == "conflict" and "was deleted" in r["message"]
        fallback.assert_not_called()


class TestAmbiguousAndDeadEndFailures:
    def test_a_transport_failure_says_the_write_may_have_landed(self) -> None:
        """A timeout says nothing about whether Google committed. A caller who
        reads 'failed' and retries posts the comment twice — worse than the
        200-with-no-thread case, which already had a warning."""
        from adapters.anchored_comments import insert_doc_comment as raw

        client = MagicMock()
        client.post_json.side_effect = httpx.ReadTimeout("timed out")
        with patch("adapters.anchored_comments.get_sync_client", return_value=client):
            with pytest.raises(MiseError) as excinfo:
                raw("d", "c", 1, 5)
        assert "NOT known whether the comment was created" in excinfo.value.message
        assert excinfo.value.details.get("ambiguous_write") is True

    def test_an_uncommentable_type_is_not_coached_into_a_second_refusal(self) -> None:
        """'Drop anchor=' is a dead end on a folder: the Drive comments plane
        refuses those too."""
        r = run("application/vnd.google-apps.folder", file_id=FID, content="c", anchor="slide 1")
        assert r["error"] and "no comments at all" in r["message"]
        assert "Drop anchor=" not in r["message"]

    def test_a_commentable_type_still_gets_the_exit(self) -> None:
        """Positive control for the row above."""
        r = run("application/pdf", file_id=FID, content="c", anchor="slide 1")
        assert "Drop anchor=" in r["message"]

    def test_a_blank_anchor_is_a_mistake_not_a_request(self) -> None:
        """The one path where typing anchor= got you a panel-only comment."""
        r = do_comment(file_id=FID, content="c", anchor="   ")
        assert r["error"] and "empty" in r["message"]
