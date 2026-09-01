"""
Tests for suggested edits — do(…, suggest=True) and do(suggest, …) (mise-hupago).

Sameer's falsifier, verbatim: *"We mishandle weird comments and markup like
those imported from MS Word."* So the corpus is not native Docs. It is a REAL
Google Doc converted from a `.docx` carrying Word tracked changes and a Word
comment — built as OOXML, uploaded through Google's own converter on
2026-09-01, and captured as `fixtures/docs/word_import_tracked_changes.json`.
What Google's converter does to Word markup is the subject; inventing a fixture
of the shape we expected would have measured our expectations instead.

The invariant every test here defends: **a suggest batch can return HTTP 200
having done nothing.** Three distinct ways, all silent, all fatal to a feature
whose entire promise is "a human will review this before it lands".
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models import DoResult, ErrorKind, MiseError
from tools.edit import do_replace_text
from tools.suggestions import (
    NBSP,
    check_batch_state,
    created_ids,
    do_suggest,
    nbsp_hint,
    suggest_write_control,
)

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "docs"
DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
FID = "1" + "b" * 30


def word_import() -> dict:
    return json.loads((FIXTURES / "word_import_tracked_changes.json").read_text())


def ok(*ids: str, state: str = "ALL_SAVED", occurrences: int = 1) -> dict:
    return {
        "replies": [{"replaceAllText": {"occurrencesChanged": occurrences}}],
        "suggestionResponses": [{"createdSuggestionIds": list(ids)}] if ids else [{}],
        "commentUpdateState": state,
    }


# =============================================================================
# What Google's .docx converter actually produces
# =============================================================================


class TestWordImportedMarkup:
    def test_word_tracked_changes_arrive_as_real_docs_suggestions(self) -> None:
        """Measured, not assumed: `w:del`/`w:ins` in the source became
        suggestedDeletionIds/suggestedInsertionIds on the imported runs. The
        whole fold lane depends on this being true, and nothing in the API docs
        promises it."""
        runs = [
            e["textRun"]
            for tab in word_import()["tabs"]
            for el in tab["documentTab"]["body"]["content"]
            for e in el["paragraph"]["elements"]
        ]
        deletions = [r for r in runs if r.get("suggestedDeletionIds")]
        insertions = [r for r in runs if r.get("suggestedInsertionIds")]
        assert deletions and insertions, "Word tracked changes did not survive the import"
        assert "twelve" in deletions[0]["content"]
        assert "fourteen" in insertions[0]["content"]

    def test_the_paired_edit_shares_one_suggestion_id(self) -> None:
        """Word's delete-then-insert pair converts to ONE Docs suggestion, which
        is what makes `{--twelve--}[s1]{++fourteen++}[s1]` render as a replace
        rather than two unrelated changes — and what makes accepting `s1` fold
        both halves."""
        by_id: dict[str, list[str]] = {}
        for tab in word_import()["tabs"]:
            for el in tab["documentTab"]["body"]["content"]:
                for e in el["paragraph"]["elements"]:
                    run = e["textRun"]
                    for key in ("suggestedDeletionIds", "suggestedInsertionIds"):
                        for sid in run.get(key) or []:
                            by_id.setdefault(sid, []).append(key)
        paired = [k for k, v in by_id.items() if len(set(v)) == 2]
        assert paired, f"expected one id carrying both halves, got {by_id}"

    def test_mise_renders_word_markup_with_stable_tags(self) -> None:
        """The read side already handled this; the fold lane keys on those tags,
        so it is pinned here rather than assumed to keep working."""
        from extractors.docs import annotate_suggestion_markup
        from models import DocTab

        tabs = [
            DocTab(title="t", tab_id=t["tabProperties"]["tabId"], index=i,
                   body=t["documentTab"]["body"])
            for i, t in enumerate(word_import()["tabs"])
        ]
        count = annotate_suggestion_markup(tabs)
        assert count == 2, f"expected 2 distinct suggestions from the Word import, got {count}"
        tags = {
            e["textRun"].get("_mise_suggestion_tag")
            for tab in tabs for el in tab.body["content"]
            for e in el["paragraph"]["elements"]
        }
        assert {"s1", "s2"} <= tags


# =============================================================================
# The NBSP trap — the Word face the estate had already met
# =============================================================================


class TestNbspTrap:
    def test_the_hint_fires_only_when_nbsp_is_really_the_reason(self) -> None:
        """Measured live: the same paragraph took 0 occurrences with ordinary
        spaces and 1 with NBSPs. The hint reads the document to check rather
        than guessing — a confidently wrong cause is worse than none."""
        nbsp_doc = {"tabs": [{"documentTab": {"body": {"content": [
            {"paragraph": {"elements": [{"textRun": {
                "content": f"Quarterly{NBSP}revenue{NBSP}grew{NBSP}by{NBSP}twelve"}}]}}]}}}]}
        client = MagicMock()
        client.get_json.return_value = nbsp_doc
        with patch("tools.suggestions.get_sync_client", return_value=client):
            hint = nbsp_hint("d", "revenue grew by twelve")
        assert "non-breaking spaces" in hint and "Word" in hint

    def test_no_hint_when_the_text_is_simply_absent(self) -> None:
        """Positive control. Without it the hint could be unconditional, and
        every miss would be blamed on Word."""
        plain = {"tabs": [{"documentTab": {"body": {"content": [
            {"paragraph": {"elements": [{"textRun": {"content": "nothing like it here"}}]}}]}}}]}
        client = MagicMock()
        client.get_json.return_value = plain
        with patch("tools.suggestions.get_sync_client", return_value=client):
            assert nbsp_hint("d", "revenue grew by twelve") == ""

    def test_single_word_finds_skip_the_check_entirely(self) -> None:
        client = MagicMock()
        with patch("tools.suggestions.get_sync_client", return_value=client):
            assert nbsp_hint("d", "twelve") == ""
        client.get_json.assert_not_called()

    def test_a_suggested_replace_that_matches_nothing_RAISES(self) -> None:
        """The centre of the falsifier. Under a direct edit, zero occurrences is
        a cue. Under suggest= the caller has been told a change awaits review
        and there is nothing to review — so it must not be a cue."""
        client = MagicMock()
        client.post_json.return_value = ok(occurrences=0, state="NO_UPDATES_REQUESTED")
        client.get_json.return_value = {"tabs": [{"documentTab": {"body": {"content": [
            {"paragraph": {"elements": [{"textRun": {
                "content": f"Quarterly{NBSP}revenue{NBSP}grew{NBSP}by{NBSP}twelve"}}]}}]}}}]}
        with patch("tools.edit.get_sync_client", return_value=client), \
             patch("tools.suggestions.get_sync_client", return_value=client), \
             patch("tools.edit._get_doc_meta", return_value={"title": "T", "end_index": 50}):
            result = do_replace_text(
                file_id=FID, find="revenue grew by twelve", content="revenue grew by fourteen",
                metadata={"mimeType": DOC}, suggest=True)
        assert result["error"] is True
        assert "NO suggestion was created" in result["message"]
        assert "non-breaking spaces" in result["message"]
        assert "Drop suggest=True" in result["message"]

    def test_the_same_miss_without_suggest_stays_a_cue(self) -> None:
        """Positive control for the row above — the direct-edit contract is
        unchanged, and the escalation belongs to suggest= alone."""
        client = MagicMock()
        client.post_json.return_value = {"replies": [{"replaceAllText": {"occurrencesChanged": 0}}]}
        with patch("tools.edit.get_sync_client", return_value=client), \
             patch("tools.edit._get_doc_meta", return_value={"title": "T", "end_index": 50}):
            result = do_replace_text(file_id=FID, find="absent", content="x",
                                     metadata={"mimeType": DOC})
        assert isinstance(result, DoResult)
        assert result.cues["occurrences_changed"] == 0
        assert "warning" in result.cues


# =============================================================================
# A 200 that did nothing
# =============================================================================


class TestTwoHundredMeansNothing:
    def test_the_flag_is_what_it_claims(self) -> None:
        assert suggest_write_control() == {"writeMode": "SUGGEST"}

    def test_created_ids_reads_the_response_not_the_request(self) -> None:
        assert created_ids(ok("suggest.abc")) == ["suggest.abc"]
        assert created_ids(ok()) == []
        assert created_ids({}) == []

    def test_a_batch_that_reports_failure_raises(self) -> None:
        """ALL_FAILED_UNKNOWN_REASON is documented to coexist with committed
        model changes, so a 200 is not evidence the batch saved."""
        with pytest.raises(MiseError) as excinfo:
            check_batch_state({"commentUpdateState": "ALL_FAILED_UNKNOWN_REASON"})
        assert "may not have saved" in excinfo.value.message

    @pytest.mark.parametrize("state", ["ALL_SAVED", "NO_UPDATES_REQUESTED", ""])
    def test_the_saving_states_pass(self, state: str) -> None:
        """Positive control — the check must be able to return the other answer."""
        check_batch_state({"commentUpdateState": state})

    def test_no_created_id_on_a_landed_edit_means_COALESCED_not_failed(self) -> None:
        """Measured live 2026-09-01, and it reversed an earlier guard. An edit
        touching text already inside a pending suggestion is absorbed into that
        thread, so Google returns no NEW id — while the change really is sitting
        in the document awaiting review. Raising here was the inverse error and
        worse than the one it guarded: the caller is told nothing happened, and
        a retry double-applies."""
        client = MagicMock()
        client.post_json.return_value = ok(occurrences=1)  # 200, changed text, no new id
        with patch("tools.edit.get_sync_client", return_value=client), \
             patch("tools.edit._get_doc_meta", return_value={"title": "T", "end_index": 50}):
            result = do_replace_text(file_id=FID, find="a", content="b",
                                     metadata={"mimeType": DOC}, suggest=True)
        assert isinstance(result, DoResult), result
        assert result.cues["suggested"] is True
        assert "absorbed into a suggestion" in result.cues["coalesced"]
        assert "suggestion_ids" not in result.cues  # honest: there is no new id to name

    def test_a_new_thread_reports_its_id_and_no_coalesced_note(self) -> None:
        """Positive control for the row above — the two outcomes must be told
        apart, or 'coalesced' becomes unconditional furniture."""
        client = MagicMock()
        client.post_json.return_value = ok("suggest.new")
        with patch("tools.edit.get_sync_client", return_value=client), \
             patch("tools.edit._get_doc_meta", return_value={"title": "T", "end_index": 50}):
            result = do_replace_text(file_id=FID, find="a", content="b",
                                     metadata={"mimeType": DOC}, suggest=True)
        assert result.cues["suggestion_ids"] == ["suggest.new"]
        assert "coalesced" not in result.cues

    def test_a_real_suggestion_reports_its_id_and_the_coalescing_caveat(self) -> None:
        client = MagicMock()
        client.post_json.return_value = ok("suggest.abc")
        with patch("tools.edit.get_sync_client", return_value=client), \
             patch("tools.edit._get_doc_meta", return_value={"title": "T", "end_index": 50}):
            result = do_replace_text(file_id=FID, find="a", content="b",
                                     metadata={"mimeType": DOC}, suggest=True)
        assert result.cues["suggested"] is True
        assert result.cues["suggestion_ids"] == ["suggest.abc"]
        assert "COALESCE" in result.cues["coalescing"]
        body = client.post_json.call_args.kwargs["json_body"]
        assert body["writeControl"] == {"writeMode": "SUGGEST"}


# =============================================================================
# A refused suggestion must never become a real edit
# =============================================================================


class TestNeverDowngradesToARealEdit:
    @pytest.mark.parametrize("mime", [SHEET, "text/plain", "application/pdf"])
    def test_suggest_off_the_docs_plane_refuses_without_writing(self, mime: str) -> None:
        """Only Docs has tracked changes. Accepting the flag elsewhere and
        dropping it would land a REAL edit while the caller believes a proposal
        is waiting — the inversion this feature exists to prevent."""
        client = MagicMock()
        with patch("tools.edit.get_sync_client", return_value=client):
            result = do_replace_text(file_id=FID, find="a", content="b",
                                     metadata={"mimeType": mime}, suggest=True)
        assert result["error"] and "Google Docs only" in result["message"]
        assert "Nothing was written" in result["message"]
        client.post_json.assert_not_called()

    def test_the_refusal_names_the_direct_alternative(self) -> None:
        result = do_replace_text(file_id=FID, find="a", content="b",
                                 metadata={"mimeType": SHEET}, suggest=True)
        assert "Drop suggest=True" in result["message"]


# =============================================================================
# Folding a suggestion back — the ordinal problem
# =============================================================================


class TestFolding:
    @pytest.mark.parametrize("kwargs,fragment", [
        ({"action": "accept", "find": "s1"}, "requires 'file_id'"),
        ({"file_id": FID, "find": "s1"}, "action='accept'"),
        ({"file_id": FID, "action": "sideways", "find": "s1"}, "action='accept'"),
        ({"file_id": FID, "action": "accept"}, "requires find="),
    ])
    def test_basic_refusals(self, kwargs: dict, fragment: str) -> None:
        r = do_suggest(**kwargs)
        assert r["error"] and fragment in r["message"]

    def test_folding_a_list_is_refused_because_tags_renumber(self) -> None:
        """[sN] are ordinals minted for rendering. Accept s1 and every later tag
        shifts — so a list would fold the wrong suggestions after the first."""
        r = do_suggest(file_id=FID, action="accept", find="s1,s2")
        assert r["error"] and "RENUMBER" in r["message"]

    def test_a_nonsense_reference_is_refused(self) -> None:
        r = do_suggest(file_id=FID, action="accept", find="banana")
        assert r["error"] and "not a suggestion reference" in r["message"]

    def test_a_word_import_id_is_accepted_raw(self) -> None:
        """`suggestIdImport…` is what a converted .docx produces. Refusing it as
        "not a suggestion reference" would fail on exactly the documents this
        card is about."""
        client = MagicMock()
        client.post_json.return_value = {
            "suggestionResponses": [{"acceptedSuggestionIds": ["suggestIdImportabc_1"]}],
            "commentUpdateState": "ALL_SAVED"}
        with patch("tools.suggestions.get_sync_client", return_value=client), \
             patch("tools.suggestions.fetch_document") as fetch:
            r = do_suggest(file_id=FID, action="accept", find="suggestIdImportabc_1")
        fetch.assert_not_called()
        assert r.cues["suggestion_id"] == "suggestIdImportabc_1"

    def test_a_raw_id_bypasses_the_ordinal_lookup(self) -> None:
        """A suggest.… id is stable, so a caller holding one should not be made
        to re-derive an ordinal for it."""
        client = MagicMock()
        client.post_json.return_value = {
            "suggestionResponses": [{"acceptedSuggestionIds": ["suggest.xyz"]}],
            "commentUpdateState": "ALL_SAVED"}
        with patch("tools.suggestions.get_sync_client", return_value=client), \
             patch("tools.suggestions.fetch_document") as fetch:
            r = do_suggest(file_id=FID, action="accept", find="suggest.xyz")
        fetch.assert_not_called()
        assert r.cues["suggestion_id"] == "suggest.xyz"
        body = client.post_json.call_args.kwargs["json_body"]
        assert body["requests"] == [{"acceptSuggestion": {"suggestionId": "suggest.xyz"}}]

    def test_reject_sends_the_reject_request(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {
            "suggestionResponses": [{"rejectedSuggestionIds": ["suggest.xyz"]}],
            "commentUpdateState": "ALL_SAVED"}
        with patch("tools.suggestions.get_sync_client", return_value=client):
            do_suggest(file_id=FID, action="reject", find="suggest.xyz")
        body = client.post_json.call_args.kwargs["json_body"]
        assert body["requests"] == [{"rejectSuggestion": {"suggestionId": "suggest.xyz"}}]

    def test_an_ordinal_resolves_against_a_word_imported_document(self) -> None:
        """The fold lane, exercised on Word markup rather than native Docs."""
        from models import DocData, DocTab

        tabs = [
            DocTab(title="t", tab_id=t["tabProperties"]["tabId"], index=i,
                   body=t["documentTab"]["body"])
            for i, t in enumerate(word_import()["tabs"])
        ]
        doc = DocData(title="Word import", document_id=FID, tabs=tabs)
        client = MagicMock()
        client.post_json.return_value = {
            "suggestionResponses": [{"acceptedSuggestionIds": ["x"]}],
            "commentUpdateState": "ALL_SAVED"}
        with patch("tools.suggestions.fetch_document", return_value=doc), \
             patch("tools.suggestions.get_sync_client", return_value=client):
            r = do_suggest(file_id=FID, action="accept", find="s1")
        sent = client.post_json.call_args.kwargs["json_body"]["requests"][0]
        # A converted .docx mints `suggestIdImport<uuid>_N`, NOT the native
        # `suggest.<hash>` — measured on this very fixture, and the reason the
        # raw-id passthrough tests the family rather than the native spelling.
        resolved = sent["acceptSuggestion"]["suggestionId"]
        assert resolved.startswith("suggestIdImport"), resolved
        assert "RENUMBER" in r.cues["renumbered"], "a second suggestion remains; say the tags moved"

    def test_an_out_of_range_ordinal_names_what_exists(self) -> None:
        from models import DocData, DocTab

        tabs = [
            DocTab(title="t", tab_id=t["tabProperties"]["tabId"], index=i,
                   body=t["documentTab"]["body"])
            for i, t in enumerate(word_import()["tabs"])
        ]
        with patch("tools.suggestions.fetch_document",
                   return_value=DocData(title="W", document_id=FID, tabs=tabs)):
            r = do_suggest(file_id=FID, action="accept", find="s9")
        assert r["error"] and "2 TEXT suggestion(s)" in r["message"]
        assert "s1" in r["message"] and "s2" in r["message"]

    def test_a_fold_that_resolved_nothing_says_so(self) -> None:
        """A 200 with an empty acceptedSuggestionIds means the request was taken
        and nothing happened — the same 200-did-nothing shape as the write side."""
        client = MagicMock()
        client.post_json.return_value = {"suggestionResponses": [{}],
                                         "commentUpdateState": "ALL_SAVED"}
        with patch("tools.suggestions.get_sync_client", return_value=client):
            r = do_suggest(file_id=FID, action="accept", find="suggest.xyz")
        assert r.cues["resolved"] is False
        assert "may not have been" in r.cues["warning"]

    def test_an_ambiguous_transport_failure_is_named(self) -> None:
        import httpx

        client = MagicMock()
        client.post_json.side_effect = httpx.ReadTimeout("gone")
        with patch("tools.suggestions.get_sync_client", return_value=client):
            r = do_suggest(file_id=FID, action="accept", find="suggest.xyz")
        assert r["error"] and "NOT known whether" in r["message"]

    def test_a_failing_batch_state_surfaces_on_the_fold_too(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"commentUpdateState": "ALL_FAILED_UNKNOWN_REASON"}
        with patch("tools.suggestions.get_sync_client", return_value=client):
            r = do_suggest(file_id=FID, action="accept", find="suggest.xyz")
        assert r["error"] and "may not have saved" in r["message"]


# =============================================================================
# The essayeur's three (2026-09-01) — each was structurally invisible to the
# tests above, and one was a live inversion of the feature's whole purpose.
# =============================================================================


class TestEssayeurFindings:
    def test_tab_plus_suggest_refuses_instead_of_smuggling_a_real_edit(self) -> None:
        """The worst finding, confirmed live: the tab branch RETURNED before the
        suggest guard ran, so do(append, tab='T', suggest=True) committed a real
        unreviewed edit and reported success. A guard placed after a return is
        not a guard. The suite could not see it because
        TestNeverDowngradesToARealEdit varies only mimeType."""
        from tools.edit import do_append

        client = MagicMock()
        with patch("tools.edit.get_sync_client", return_value=client), \
             patch("tools.edit._append_as_tab") as as_tab:
            r = do_append(file_id=FID, content="SMUGGLED", tab="New tab",
                          metadata={"mimeType": DOC}, suggest=True)
        assert r["error"] is True
        assert "Nothing was written" in r["message"]
        as_tab.assert_not_called()
        client.post_json.assert_not_called()

    def test_tab_without_suggest_still_works(self) -> None:
        """Positive control — the guard must not break the tab lane itself."""
        from tools.edit import do_append

        with patch("tools.edit._append_as_tab", return_value="ok") as as_tab:
            r = do_append(file_id=FID, content="body", tab="New tab",
                          metadata={"mimeType": DOC})
        assert r == "ok"
        as_tab.assert_called_once()

    def test_formatting_suggestions_are_counted_and_disclosed(self) -> None:
        """A Word `w:rPrChange` converts to suggestedTextStyleChanges, which
        carries no inserted or deleted run — so mise rendered nothing, cued
        nothing, and told the caller the document had 0 unresolved suggestions
        while Google held a real pending one. A style-only document read as
        settled when it was not."""
        from models import DocTab
        from tools.suggestions import count_untaggable_suggestions, untaggable_note

        tabs = [DocTab(title="t", tab_id="t.0", index=0, body={"content": [
            {"paragraph": {"elements": [{"textRun": {
                "content": "bolded by a tracked change",
                "suggestedTextStyleChanges": {"suggestIdImportabc_1": {}}}}]}}]})]
        assert count_untaggable_suggestions(tabs) == 1
        note = untaggable_note(1)
        assert "FORMATTING suggestion" in note and "Docs UI" in note

    def test_no_formatting_suggestions_means_no_note(self) -> None:
        """Positive control — the disclosure must not be unconditional."""
        from models import DocTab
        from tools.suggestions import count_untaggable_suggestions, untaggable_note

        tabs = [DocTab(title="t", tab_id="t.0", index=0, body={"content": [
            {"paragraph": {"elements": [{"textRun": {"content": "plain"}}]}}]})]
        assert count_untaggable_suggestions(tabs) == 0
        assert untaggable_note(0) == ""

    def test_a_style_only_doc_still_raises_has_suggestions_on_fetch(self) -> None:
        from models import DocData, DocTab
        from tools.fetch.common import suggestion_cues

        tabs = [DocTab(title="t", tab_id="t.0", index=0, body={"content": [
            {"paragraph": {"elements": [{"textRun": {
                "content": "bolded", "suggestedTextStyleChanges": {"x": {}}}}]}}]})]
        doc = DocData(title="d", document_id=FID, tabs=tabs)
        cues = suggestion_cues(doc)
        assert cues["has_suggestions"] is True
        assert cues["formatting_suggestions"] == 1
        assert "NOT settled" in cues["formatting_suggestions_note"]

    def test_the_fold_lane_scopes_its_count_to_text_suggestions(self) -> None:
        """"This document has 0 unresolved suggestions" was a false statement
        about a document with a pending formatting change. The count is now
        labelled TEXT, and the rest are disclosed beside it."""
        from models import DocData, DocTab

        tabs = [DocTab(title="t", tab_id="t.0", index=0, body={"content": [
            {"paragraph": {"elements": [{"textRun": {
                "content": "bolded", "suggestedTextStyleChanges": {"x": {}}}}]}}]})]
        with patch("tools.suggestions.fetch_document",
                   return_value=DocData(title="d", document_id=FID, tabs=tabs)):
            r = do_suggest(file_id=FID, action="accept", find="s1")
        assert r["error"]
        assert "0 TEXT suggestion(s)" in r["message"]
        assert "1 FORMATTING suggestion(s)" in r["message"]

    def test_the_nbsp_diagnosis_sees_inside_tables(self) -> None:
        """The hint walked top-level paragraphs only, so it stayed silent on a
        Word-imported TABLE — the shape most likely to carry the trap."""
        in_table = {"tabs": [{"documentTab": {"body": {"content": [
            {"table": {"tableRows": [{"tableCells": [{"content": [
                {"paragraph": {"elements": [{"textRun": {
                    "content": f"unit{NBSP}economics{NBSP}improved"}}]}}]}]}]}}]}}}]}
        client = MagicMock()
        client.get_json.return_value = in_table
        with patch("tools.suggestions.get_sync_client", return_value=client):
            hint = nbsp_hint("d", "unit economics improved")
        assert "non-breaking spaces" in hint
