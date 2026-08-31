"""
Tests for Slides/Sheets comment locators (mise-dukacu).

The falsifier this suite exists to attack is Sameer's, verbatim: *"we miss some
gnarly edge cases and lose comments to the void."* So the load-bearing assertion
throughout is not that a locator is pretty — it is that **every comment the Drive
plane returned still appears in comments.md**, whatever the anchor map says or
fails to say. Each edge below is a way a comment could have disappeared.

Fixtures: `real_*_anchors.json` are verbatim live responses from the 2026-08-31
Developer Preview probe (`docs/research/2026-08-31-anchored-comments-probe/`,
evidence 14 and 41 — the second includes Sameer's hand-made UI comment, which is
the case the whole item is for). `edge_*_anchors.json` are hand-built from those
shapes to hold the combinations one real file rarely carries at once.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from adapters.comment_anchors import (
    AnchorRead,
    fetch_sheets_comment_anchors,
    fetch_slides_comment_anchors,
)
from adapters.drive import fetch_file_comments
from extractors.comment_anchors import (
    AnchorLocator,
    column_label,
    grid_range_to_a1,
    quote_tab,
    sheets_locators,
    slides_locators,
)
from extractors.comments import extract_comments_content
from models import CommentData, FileCommentsData
from tools.fetch.common import _enrich_with_comments

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "comments"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def comments_data(*ids: str, file_name: str = "Test file") -> FileCommentsData:
    """A Drive-plane comment set with the given ids, in the given order."""
    return FileCommentsData(
        file_id="f1",
        file_name=file_name,
        comments=[
            CommentData(
                id=cid,
                content=f"body of {cid}",
                author_name="Alice",
                author_email="alice@example.com",
                created_time="2026-08-31T10:00:00.000Z",
            )
            for cid in ids
        ],
    )


# =============================================================================
# A1 arithmetic — the bound, not today's value
# =============================================================================


class TestA1Rendering:
    @pytest.mark.parametrize(
        "index0,expected",
        [(0, "A"), (1, "B"), (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ"),
         (52, "BA"), (701, "ZZ"), (702, "AAA"), (18277, "ZZZ")],
    )
    def test_column_label(self, index0: int, expected: str) -> None:
        """Columns past Z are where a naive chr() renders nonsense silently."""
        assert column_label(index0) == expected

    def test_column_label_rejects_negative(self) -> None:
        assert column_label(-1) == ""

    @pytest.mark.parametrize(
        "rng,expected",
        [
            ({"startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 1, "endColumnIndex": 2}, "B2"),
            ({"startRowIndex": 11, "endRowIndex": 12, "startColumnIndex": 2, "endColumnIndex": 3}, "C12"),
            ({"startRowIndex": 13, "endRowIndex": 14, "startColumnIndex": 3, "endColumnIndex": 4}, "D14"),
            ({"startRowIndex": 11, "endRowIndex": 14, "startColumnIndex": 2, "endColumnIndex": 4}, "C12:D14"),
            ({"startColumnIndex": 2, "endColumnIndex": 3}, "C:C"),
            ({"startColumnIndex": 2, "endColumnIndex": 4}, "C:D"),
            ({"startRowIndex": 4, "endRowIndex": 5}, "5:5"),
            ({"startRowIndex": 1, "endRowIndex": 5}, "2:5"),
            ({}, ""),
            ({"startRowIndex": 1, "startColumnIndex": 1, "endColumnIndex": 2}, "B2:B"),
        ],
    )
    def test_grid_range_to_a1(self, rng: dict, expected: str) -> None:
        """Every GridRange field is optional; the omissions carry meaning."""
        assert grid_range_to_a1(rng) == expected

    def test_c12_and_d14_match_the_live_measurement(self) -> None:
        """The two live colleague threads on the Melt SoW sheet resolved to C12
        and D14 (probe evidence 32, redacted). Those are the known-positive
        values this arithmetic has to reproduce."""
        assert grid_range_to_a1(
            {"startRowIndex": 11, "endRowIndex": 12, "startColumnIndex": 2, "endColumnIndex": 3}
        ) == "C12"
        assert grid_range_to_a1(
            {"startRowIndex": 13, "endRowIndex": 14, "startColumnIndex": 3, "endColumnIndex": 4}
        ) == "D14"

    @pytest.mark.parametrize(
        "title,expected",
        [("Sheet1", "Sheet1"), ("Q3 Plan", "'Q3 Plan'"), ("O'Brien", "'O''Brien'"),
         ("2026", "2026"), ("", "''")],
    )
    def test_quote_tab(self, title: str, expected: str) -> None:
        assert quote_tab(title) == expected


# =============================================================================
# Slides locators
# =============================================================================


class TestSlidesLocators:
    def test_live_ui_authored_comment_resolves(self) -> None:
        """The load-bearing case: Sameer's hand-made deck comment (evidence 41)
        is a SHAPE anchor with a character range, not a page anchor."""
        payload = load("real_slides_anchors.json")
        locs = slides_locators(payload, deck=[("p", "Probe deck")])

        ui = locs["AAACGW2WCac"]
        assert ui.label == "slide 1 (Probe deck)"
        assert ui.quote == "Hello Claude!"
        assert not ui.orphaned

        api_made = locs["AAACGW2am2w"]
        assert api_made.label == "slide 1 (Probe deck)"
        assert api_made.quote == ""

    def test_untitled_slide_drops_the_parenthetical(self) -> None:
        locs = slides_locators(load("real_slides_anchors.json"), deck=[("p", None)])
        assert locs["AAACGW2WCac"].label == "slide 1"

    def test_deck_order_decides_the_number(self) -> None:
        """Slide numbering comes from mise's own deck order, so comments.md and
        the manifest's slides_index agree even if the preview read reorders."""
        payload = load("edge_slides_anchors.json")
        locs = slides_locators(payload, deck=[("pX", None), ("p2", "Two"), ("p", "One")])
        assert locs["c-slide1-page"].label == "slide 3 (One)"
        assert locs["c-slide2-page"].label == "slide 2 (Two)"

    def test_falls_back_to_payload_order_without_a_deck(self) -> None:
        locs = slides_locators(load("edge_slides_anchors.json"))
        assert locs["c-slide1-page"].label == "slide 1"
        assert locs["c-slide2-page"].label == "slide 2"

    def test_table_cell_anchor_resolves_by_containment(self) -> None:
        """A shape mise has never seen still lands on the right slide, because
        the join is on the slide that HOLDS the anchor, not on the anchored
        object's type."""
        locs = slides_locators(load("edge_slides_anchors.json"))
        assert locs["c-slide2-tablecell"].label == "slide 2"
        assert locs["c-slide2-tablecell"].quote == "Q3 actuals"

    def test_deleted_slide_is_orphaned_not_dropped(self) -> None:
        locs = slides_locators(load("edge_slides_anchors.json"))
        assert locs["c-orphan"].orphaned is True
        assert locs["c-orphan"].label == ""

    def test_unanchored_comment_gets_no_locator(self) -> None:
        locs = slides_locators(load("edge_slides_anchors.json"))
        assert "c-unanchored" not in locs

    def test_thread_without_a_comment_id_is_skipped(self) -> None:
        """Unjoinable, so it cannot be attached to anything — and the Drive
        plane renders it regardless."""
        locs = slides_locators(load("edge_slides_anchors.json"))
        assert "" not in locs

    def test_empty_anchor_map_mints_no_orphan_verdicts(self) -> None:
        """An absent map is a fact about the instrument. Calling every comment
        orphaned because we could not read the deck would be the instrument's
        limits reported as the subject's."""
        payload = {"comments": [{"commentId": "c1", "anchorId": "{...}"}]}
        assert slides_locators(payload) == {}

    def test_survives_junk(self) -> None:
        assert slides_locators({}) == {}
        assert slides_locators({"slides": None, "comments": None}) == {}
        assert slides_locators({"slides": ["not a dict"], "comments": [42]}) == {}


# =============================================================================
# Sheets locators
# =============================================================================


class TestSheetsLocators:
    def test_live_b2_anchor(self) -> None:
        locs = sheets_locators(load("real_sheets_anchors.json"))
        assert locs["AAACGWrtgyk"].label == "Sheet1!B2"

    def test_every_range_shape(self) -> None:
        locs = sheets_locators(load("edge_sheets_anchors.json"))
        assert locs["c-cell"].label == "Sheet1!B2"
        assert locs["c-block"].label == "Sheet1!C12:D14"
        assert locs["c-farcol"].label == "Sheet1!AB1"
        assert locs["c-wholecol"].label == "'Q3 Plan'!C:C"
        assert locs["c-wholerow"].label == "'Q3 Plan'!5:5"
        assert locs["c-wholesheet"].label == "'Q3 Plan'"
        assert locs["c-untitled-tab"].label == "'sheetId 9001'!A1"

    def test_cleared_range_is_orphaned_not_dropped(self) -> None:
        locs = sheets_locators(load("edge_sheets_anchors.json"))
        assert locs["c-orphan"].orphaned is True

    def test_unanchored_comment_gets_no_locator(self) -> None:
        assert "c-unanchored" not in sheets_locators(load("edge_sheets_anchors.json"))

    def test_empty_anchor_map_mints_no_orphan_verdicts(self) -> None:
        payload = {"comments": [{"commentId": "c1", "anchorId": "{...}"}]}
        assert sheets_locators(payload) == {}

    def test_survives_junk(self) -> None:
        assert sheets_locators({}) == {}
        assert sheets_locators({"sheets": [{"properties": None}], "comments": []}) == {}


# =============================================================================
# The render — and the void
# =============================================================================


class TestRenderWithLocators:
    def test_slides_render_in_deck_order_with_locators(self) -> None:
        data = comments_data(
            "c-orphan", "c-unanchored", "c-slide2-page", "c-slide1-shape",
            file_name="Edge deck",
        )
        locs = slides_locators(load("edge_slides_anchors.json"),
                               deck=[("p", "First"), ("p2", "Second")])
        out = extract_comments_content(data, locators=locs)

        # Deck order, then orphans, then unanchored.
        order = [out.index(f"body of {cid}") for cid in
                 ("c-slide1-shape", "c-slide2-page", "c-orphan", "c-unanchored")]
        assert order == sorted(order)
        assert "*↳ slide 1 (First)*" in out
        assert "*↳ slide 2 (Second)*" in out
        assert "the anchored content no longer exists" in out
        assert "> Hello Claude!" in out  # plainTextQuote used as anchor text

    def test_sheets_render_in_workbook_order(self) -> None:
        data = comments_data("c-wholecol", "c-block", "c-cell")
        out = extract_comments_content(
            data, locators=sheets_locators(load("edge_sheets_anchors.json"))
        )
        order = [out.index(f"body of {cid}") for cid in ("c-cell", "c-block", "c-wholecol")]
        assert order == sorted(order)
        assert "*↳ Sheet1!B2*" in out
        assert "*↳ 'Q3 Plan'!C:C*" in out

    def test_no_comment_is_lost_whatever_the_map_says(self) -> None:
        """The falsifier, asserted directly. Four comments the map cannot place —
        an orphan, an unanchored thread, a comment the preview read never
        mentioned, and one with an empty id — all still render."""
        ids = ("c-slide1-page", "c-orphan", "c-unanchored", "c-never-seen", "")
        data = comments_data(*ids)
        locs = slides_locators(load("edge_slides_anchors.json"))
        out = extract_comments_content(data, locators=locs)
        for cid in ids:
            assert f"body of {cid}" in out, f"{cid!r} vanished from comments.md"
        assert out.startswith('## Comments on "Test file" (5 total)')

    def test_locator_absent_from_the_flat_render(self) -> None:
        """The known-bad control for every assertion above: with no locators,
        none of the locator strings can appear. If this ever passes while the
        tests above also pass, the locator line is being emitted unconditionally."""
        data = comments_data("c-slide1-page", "c-orphan")
        out = extract_comments_content(data)
        assert "↳" not in out
        assert "no longer exists" not in out

    def test_warnings_name_the_unplaceable(self) -> None:
        data = comments_data("c-slide1-page", "c-orphan", "c-unanchored")
        extract_comments_content(data, locators=slides_locators(load("edge_slides_anchors.json")))
        joined = " ".join(data.warnings)
        assert "1 comment(s) anchor to content that no longer exists" in joined
        assert "1 comment(s) are not anchored to a location" in joined

    def test_quotes_suppress_the_no_anchor_context_warning(self) -> None:
        """Slides/Sheets Drive-plane comments carry no quotedFileContent, which
        is why that warning exists. plainTextQuote IS anchor context, so the
        warning must stop firing once we have it."""
        data = comments_data("c-slide1-shape")
        extract_comments_content(data, locators=slides_locators(load("edge_slides_anchors.json")))
        assert not any("Anchor context not available" in w for w in data.warnings)

        bare = comments_data("c-slide1-shape")
        extract_comments_content(bare)
        assert any("Anchor context not available" in w for w in bare.warnings)

    def test_no_quotes_with_a_map_narrows_the_claim(self) -> None:
        """"Not available for this file type" is a claim about Sheets and Slides
        as surfaces, and it is false — a UI-authored comment on either quotes its
        anchor. With a locator map in hand the honest claim is narrower: THESE
        threads quote nothing."""
        data = comments_data("c-slide1-page")  # a page anchor, quote ""
        extract_comments_content(data, locators=slides_locators(load("edge_slides_anchors.json")))
        joined = " ".join(data.warnings)
        assert "None of these comments quote anchored text" in joined
        assert "not available for this file type" not in joined


# =============================================================================
# Degradation — the compliance leg (DPP program term iv)
# =============================================================================


class TestPreviewUnavailable:
    @pytest.mark.parametrize("status", [400, 403, 404])
    def test_refusal_names_enrollment(self, status: int) -> None:
        """An unenrolled caller's refusal reaches the user as a reason, not a
        stack trace and not silence."""
        response = httpx.Response(status, request=httpx.Request("GET", "https://x"))
        client = MagicMock()
        client.get_json.side_effect = httpx.HTTPStatusError(
            "boom", request=response.request, response=response
        )
        with patch("adapters.comment_anchors.get_sync_client", return_value=client):
            read = fetch_slides_comment_anchors("deck1")
        assert read.payload is None
        assert f"HTTP {status}" in read.reason
        assert "Developer Preview" in read.reason
        assert "Slides" in read.reason

    def test_server_error_omits_the_enrollment_guess(self) -> None:
        response = httpx.Response(500, request=httpx.Request("GET", "https://x"))
        client = MagicMock()
        client.get_json.side_effect = httpx.HTTPStatusError(
            "boom", request=response.request, response=response
        )
        with patch("adapters.comment_anchors.get_sync_client", return_value=client):
            read = fetch_sheets_comment_anchors("book1")
        assert "HTTP 500" in read.reason
        assert "Developer Preview" not in read.reason
        assert "Sheets" in read.reason

    def test_transport_error_is_named_not_swallowed(self) -> None:
        client = MagicMock()
        client.get_json.side_effect = httpx.ConnectError("no route")
        with patch("adapters.comment_anchors.get_sync_client", return_value=client):
            read = fetch_slides_comment_anchors("deck1")
        assert "ConnectError" in read.reason
        assert "no route" in read.reason

    def test_non_dict_payload_is_refused(self) -> None:
        client = MagicMock()
        client.get_json.return_value = ["not", "an", "object"]
        with patch("adapters.comment_anchors.get_sync_client", return_value=client):
            read = fetch_sheets_comment_anchors("book1")
        assert read.payload is None
        assert "not an object" in read.reason

    def test_success_path_sends_the_preview_param(self) -> None:
        """Known-positive control for the three refusals above: the same code
        returns a payload and no reason when the API answers."""
        client = MagicMock()
        client.get_json.return_value = load("real_sheets_anchors.json")
        with patch("adapters.comment_anchors.get_sync_client", return_value=client):
            read = fetch_sheets_comment_anchors("book1")
        assert read.reason is None
        assert read.payload["spreadsheetId"]
        params = client.get_json.call_args.kwargs["params"]
        assert params["commentsViewMode"] == "COMMENTS_VIEW_MODE_INCLUDED"
        assert "commentAnchors" in params["fields"]


class TestEnrichDegradesWithACue:
    """End-to-end through `_enrich_with_comments`, forcing the failure at the
    HTTP client rather than stubbing the adapter — the point is that the real
    degradation path runs, not that a stub returns what we told it to."""

    @staticmethod
    def _drive_comments() -> FileCommentsData:
        return comments_data("AAACGW2WCac", "AAACGW2am2w", file_name="Probe deck")

    def _run(self, tmp_path: Path, client: MagicMock, warnings: list[str]):
        with patch("tools.fetch.common.fetch_file_comments", return_value=self._drive_comments()), \
             patch("adapters.comment_anchors.get_sync_client", return_value=client):
            return _enrich_with_comments(
                "deck1", tmp_path, surface="slides",
                slides=[MagicMock(slide_id="p", title="Probe deck")],
                warnings=warnings,
            )

    def test_preview_available_writes_locators(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.get_json.return_value = load("real_slides_anchors.json")
        warnings: list[str] = []
        count, md = self._run(tmp_path, client, warnings)

        assert count == 2
        deposited = (tmp_path / "comments.md").read_text()
        assert "*↳ slide 1 (Probe deck)*" in deposited
        assert deposited == md
        assert not any("locators unavailable" in w for w in warnings)

    def test_no_preview_access_still_deposits_and_says_why(self, tmp_path: Path) -> None:
        """The compliance leg: a caller Google refuses gets today's flat render
        plus a cue naming the reason. Nothing raises, nothing is withheld."""
        response = httpx.Response(403, request=httpx.Request("GET", "https://x"))
        client = MagicMock()
        client.get_json.side_effect = httpx.HTTPStatusError(
            "denied", request=response.request, response=response
        )
        warnings: list[str] = []
        count, md = self._run(tmp_path, client, warnings)

        assert count == 2
        deposited = (tmp_path / "comments.md").read_text()
        assert "body of AAACGW2WCac" in deposited
        assert "body of AAACGW2am2w" in deposited
        assert "↳" not in deposited  # today's flat render, unchanged

        reason = [w for w in warnings if "Comment locators unavailable" in w]
        assert len(reason) == 1
        assert "HTTP 403" in reason[0]
        assert "Developer Preview" in reason[0]
        assert "API order" in reason[0]

    def test_flat_and_located_renders_hold_the_same_comments(self, tmp_path: Path) -> None:
        """Degrading may cost locators. It may not cost comments."""
        ok = MagicMock()
        ok.get_json.return_value = load("real_slides_anchors.json")
        _, located = self._run(tmp_path, ok, [])

        response = httpx.Response(403, request=httpx.Request("GET", "https://x"))
        bad = MagicMock()
        bad.get_json.side_effect = httpx.HTTPStatusError(
            "denied", request=response.request, response=response
        )
        _, flat = self._run(tmp_path, bad, [])

        for cid in ("AAACGW2WCac", "AAACGW2am2w"):
            assert f"body of {cid}" in located
            assert f"body of {cid}" in flat

    def test_comment_read_failure_is_disclosed(self, tmp_path: Path) -> None:
        """A broken comments read used to render identically to a file with no
        comments. Now it says which one happened."""
        warnings: list[str] = []
        with patch("tools.fetch.common.fetch_file_comments", side_effect=RuntimeError("boom")):
            count, md = _enrich_with_comments(
                "deck1", tmp_path, surface="slides", warnings=warnings
            )
        assert (count, md) == (0, None)
        assert any("Comments could not be read" in w and "boom" in w for w in warnings)

    def test_docs_path_is_untouched(self, tmp_path: Path) -> None:
        """No surface, no preview read — the Docs path must not gain an API
        call or a locator line."""
        client = MagicMock()
        with patch("tools.fetch.common.fetch_file_comments", return_value=self._drive_comments()), \
             patch("adapters.comment_anchors.get_sync_client", return_value=client):
            _enrich_with_comments("doc1", tmp_path, document_markdown="# Heading\n")
        client.get_json.assert_not_called()
        assert "↳" not in (tmp_path / "comments.md").read_text()

    def test_no_comments_means_no_preview_call(self, tmp_path: Path) -> None:
        """The checkbox-oracle cost pattern: pay for the second read only on
        files that have something to locate."""
        empty = FileCommentsData(file_id="f", file_name="n", comments=[])
        client = MagicMock()
        with patch("tools.fetch.common.fetch_file_comments", return_value=empty), \
             patch("adapters.comment_anchors.get_sync_client", return_value=client):
            count, md = _enrich_with_comments("deck1", tmp_path, surface="slides", warnings=[])
        assert (count, md) == (0, None)
        client.get_json.assert_not_called()


class TestEssayeurFindings:
    """The three the cold-eyes pass got through (2026-09-01), each with the
    control that proves the guard can go red."""

    def test_capped_read_never_states_a_total(self) -> None:
        """A file with more threads than the cap used to print a confident
        "(100 total)". The token is the evidence; the header has to carry it."""
        data = comments_data("a", "b")
        data.truncated = True
        out = extract_comments_content(data)
        assert out.startswith('## Comments on "Test file" (2+ — capped, this file has more)')

    def test_uncapped_read_still_says_total(self) -> None:
        """Positive control — without it, `truncated` could be hardwired True."""
        out = extract_comments_content(comments_data("a", "b"))
        assert out.startswith('## Comments on "Test file" (2 total)')

    def test_resolved_threads_cannot_starve_the_open_ones(self) -> None:
        """The worst rendition the essayeur found: 100 resolved threads ahead of
        20 open ones exhausted max_results BEFORE the resolved filter ran, so a
        file with 20 live comments deposited no comments.md at all."""
        page1 = {
            "comments": [
                {"id": f"r{i}", "content": "resolved", "resolved": True,
                 "author": {"displayName": "A"}}
                for i in range(100)
            ],
            "nextPageToken": "p2",
        }
        page2 = {
            "comments": [
                {"id": f"o{i}", "content": "open", "resolved": False,
                 "author": {"displayName": "A"}}
                for i in range(20)
            ]
        }
        client = MagicMock()
        client.get_json.side_effect = [page1, page2]
        with patch("adapters.drive.get_sync_client", return_value=client), \
             patch("adapters.drive.get_file_metadata",
                   return_value={"name": "Busy deck", "mimeType": "application/vnd.google-apps.presentation"}):
            data = fetch_file_comments("f", include_resolved=False, max_results=100)

        assert len(data.comments) == 20, "open comments starved by resolved ones"
        assert data.truncated is False
        assert all(c.id.startswith("o") for c in data.comments)

    def test_surviving_token_at_the_cap_sets_truncated(self) -> None:
        page = {
            "comments": [
                {"id": f"c{i}", "content": "open", "resolved": False,
                 "author": {"displayName": "A"}}
                for i in range(100)
            ],
            "nextPageToken": "more",
        }
        client = MagicMock()
        client.get_json.return_value = page
        with patch("adapters.drive.get_sync_client", return_value=client), \
             patch("adapters.drive.get_file_metadata",
                   return_value={"name": "Busy", "mimeType": "application/vnd.google-apps.document"}):
            data = fetch_file_comments("f", include_resolved=False, max_results=100)

        assert len(data.comments) == 100
        assert data.truncated is True
        assert any("more" in w and "partial view" in w for w in data.warnings)

    def test_exact_fit_is_not_truncated(self) -> None:
        """Positive control for the row above."""
        page = {
            "comments": [
                {"id": f"c{i}", "content": "open", "resolved": False,
                 "author": {"displayName": "A"}}
                for i in range(100)
            ]
        }
        client = MagicMock()
        client.get_json.return_value = page
        with patch("adapters.drive.get_sync_client", return_value=client), \
             patch("adapters.drive.get_file_metadata",
                   return_value={"name": "Busy", "mimeType": "application/vnd.google-apps.document"}):
            data = fetch_file_comments("f", include_resolved=False, max_results=100)
        assert data.truncated is False
        assert not data.warnings

    def test_a_slide_added_mid_fetch_is_not_given_a_wrong_number(self) -> None:
        """mise reads the deck, then (up to ~20s of thumbnails later) reads the
        anchors. A slide inserted in that window is in the second read only, and
        labelling it by payload position names a DIFFERENT slide from the one
        the deposit numbers that way."""
        payload = {
            "slides": [
                {"objectId": "sA", "commentAnchors": [{"anchorId": "a1"}]},
                {"objectId": "sX", "commentAnchors": [{"anchorId": "a2"}]},
                {"objectId": "sB", "commentAnchors": [{"anchorId": "a3"}]},
            ],
            "comments": [
                {"commentId": "on-A", "anchorId": "a1"},
                {"commentId": "on-new", "anchorId": "a2"},
                {"commentId": "on-B", "anchorId": "a3"},
            ],
        }
        locs = slides_locators(payload, deck=[("sA", "Alpha"), ("sB", "Bravo")])
        assert locs["on-A"].label == "slide 1 (Alpha)"
        assert locs["on-B"].label == "slide 2 (Bravo)"
        assert "not in this deposit" in locs["on-new"].label
        assert not locs["on-new"].orphaned  # it resolves; it just isn't here
        assert locs["on-new"].order > locs["on-B"].order

    def test_absent_slide_and_orphan_are_told_apart(self) -> None:
        """Two different facts: 'the anchor points at a slide you don't have'
        and 'the anchor points at nothing'. Rendering them alike would let the
        first read as content deletion."""
        payload = {
            "slides": [{"objectId": "sX", "commentAnchors": [{"anchorId": "a2"}]}],
            "comments": [
                {"commentId": "absent", "anchorId": "a2"},
                {"commentId": "orphan", "anchorId": "gone"},
            ],
        }
        locs = slides_locators(payload, deck=[("sA", "Alpha")])
        out = extract_comments_content(comments_data("absent", "orphan"), locators=locs)
        assert "not in this deposit" in out
        assert "no longer exists in this file" in out

    def test_separator_truncation_leaves_a_marker(self) -> None:
        """The break at the separator boundary used to drop the rest with no
        marker and no warning — a partial render that read as a complete one."""
        data = comments_data(*[f"c{i}" for i in range(6)])
        out = extract_comments_content(data, max_length=134)
        assert "TRUNCATED" in out
        assert any("truncated" in w.lower() for w in data.warnings)

    def test_a_200_with_no_anchor_map_is_a_reason_not_a_silence(self) -> None:
        """A successful read carrying no `slides` would leave every comment
        unlocated with nothing to explain it."""
        client = MagicMock()
        client.get_json.return_value = {"presentationId": "d", "comments": []}
        with patch("adapters.comment_anchors.get_sync_client", return_value=client):
            read = fetch_slides_comment_anchors("d")
        assert read.payload is None
        assert "no `slides`" in read.reason


def test_anchor_read_is_one_or_the_other() -> None:
    """A read carrying both a payload and a reason would let a caller act on
    stale locators while cueing that it degraded."""
    assert AnchorRead(payload={"a": 1}).reason is None
    assert AnchorRead(reason="nope").payload is None
    assert AnchorLocator(order=(0,), label="slide 1").quote == ""
