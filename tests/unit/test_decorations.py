"""
URL decorations: parse, manifest structure, resolution (mise-dogape).

The contract under test: a URL's tail (?gid, ?tab, #heading, #slide, ?disco)
is parsed at the detect_id_type seam, resolved post-fetch against the deposit
alone, and POINTS the cue at the deposited artefact — deposits themselves are
identical for decorated and bare fetches, and a dangling pointer is reported
as stale rather than ignored.
"""

from pathlib import Path
from unittest.mock import patch

import orjson

from models import DocData, DocTab, FetchResult, PresentationData, SlideData
from extractors.docs import extract_doc_content
from tools.fetch.decorations import (
    UrlDecorations,
    apply_url_decorations,
    build_doc_structure,
    build_slides_index,
    parse_drive_url_decorations,
)
from tools.fetch.router import detect_id_type


# =============================================================================
# PARSING
# =============================================================================


class TestParseDriveUrlDecorations:
    def test_gid_in_query(self) -> None:
        d = parse_drive_url_decorations(
            "https://docs.google.com/spreadsheets/d/ID/edit?gid=1466289902"
        )
        assert d.values == {"gid": "1466289902"}
        assert d.warnings == []

    def test_gid_in_fragment(self) -> None:
        d = parse_drive_url_decorations(
            "https://docs.google.com/spreadsheets/d/ID/edit#gid=1466289902"
        )
        assert d.values == {"gid": "1466289902"}

    def test_gid_in_both_agreeing(self) -> None:
        d = parse_drive_url_decorations(
            "https://docs.google.com/spreadsheets/d/ID/edit?gid=5#gid=5"
        )
        assert d.values == {"gid": "5"}
        assert d.warnings == []

    def test_gid_conflict_says_so_rather_than_picking(self) -> None:
        d = parse_drive_url_decorations(
            "https://docs.google.com/spreadsheets/d/ID/edit?gid=1#gid=2"
        )
        assert "gid" not in d.values
        assert len(d.warnings) == 1
        assert "gid=1" in d.warnings[0] and "gid=2" in d.warnings[0]

    def test_range_rides_alongside_gid(self) -> None:
        d = parse_drive_url_decorations(
            "https://docs.google.com/spreadsheets/d/ID/edit#gid=0&range=F9:F15"
        )
        assert d.values == {"gid": "0", "range": "F9:F15"}

    def test_docs_tab_and_heading_compound(self) -> None:
        d = parse_drive_url_decorations(
            "https://docs.google.com/document/d/ID/edit?tab=t.ems17pqdjs5b#heading=h.isfuiczb8xkm"
        )
        assert d.values == {"tab": "t.ems17pqdjs5b", "heading": "h.isfuiczb8xkm"}

    def test_slide_strips_id_prefix_only(self) -> None:
        d = parse_drive_url_decorations(
            "https://docs.google.com/presentation/d/ID/edit#slide=id.g3f5d00ed841_0_0"
        )
        assert d.values == {"slide": "g3f5d00ed841_0_0"}

    def test_slide_first_slide_p(self) -> None:
        d = parse_drive_url_decorations(
            "https://docs.google.com/presentation/d/ID/edit#slide=id.p"
        )
        assert d.values == {"slide": "p"}

    def test_slide_without_id_prefix_kept_verbatim(self) -> None:
        d = parse_drive_url_decorations(
            "https://docs.google.com/presentation/d/ID/edit?slide=mig_slide_003"
        )
        assert d.values == {"slide": "mig_slide_003"}

    def test_disco_verbatim(self) -> None:
        d = parse_drive_url_decorations(
            "https://docs.google.com/document/d/ID/edit?disco=AAACCH2tTN0"
        )
        assert d.values == {"disco": "AAACCH2tTN0"}

    def test_bare_url_is_falsy(self) -> None:
        d = parse_drive_url_decorations("https://docs.google.com/document/d/ID/edit")
        assert not d

    def test_incidental_params_ignored(self) -> None:
        d = parse_drive_url_decorations(
            "https://drive.google.com/file/d/ID/view?usp=drive_link"
        )
        assert not d


class TestDetectIdTypeCarriesDecorations:
    def test_drive_url_decorations_survive_the_seam(self) -> None:
        source, file_id, d = detect_id_type(
            "https://docs.google.com/spreadsheets/d/1abcDEF/edit#gid=42"
        )
        assert (source, file_id) == ("drive", "1abcDEF")
        assert d.values == {"gid": "42"}

    def test_bare_id_has_empty_decorations(self) -> None:
        _, _, d = detect_id_type("1abcDEF")
        assert not d

    def test_gmail_url_has_empty_decorations(self) -> None:
        _, _, d = detect_id_type(
            "https://mail.google.com/mail/u/0/#inbox/FMfcgzQfBZkJgxJdSRBsRcqhpDcdBRxH"
        )
        assert not d


# =============================================================================
# DOC STRUCTURE (offsets computed against the REAL extractor's render)
# =============================================================================


def _paragraph(text: str, style: str | None = None, heading_id: str | None = None) -> dict:
    para_style: dict = {}
    if style:
        para_style["namedStyleType"] = style
    if heading_id:
        para_style["headingId"] = heading_id
    return {
        "paragraph": {
            "paragraphStyle": para_style,
            "elements": [{"textRun": {"content": text + "\n"}}],
        }
    }


def _tab(title: str, tab_id: str, index: int, content: list[dict]) -> DocTab:
    return DocTab(
        title=title, tab_id=tab_id, index=index, body={"content": content}
    )


class TestBuildDocStructure:
    def test_single_tab_headings_get_real_lines(self) -> None:
        doc = DocData(
            title="Doc",
            document_id="d1",
            tabs=[
                _tab("Only", "t.0", 0, [
                    _paragraph("Title", "HEADING_1", "h.one"),
                    _paragraph("Some prose."),
                    _paragraph("Section", "HEADING_2", "h.two"),
                    _paragraph("More prose."),
                ]),
            ],
        )
        content = extract_doc_content(doc)
        structure = build_doc_structure(doc, content)

        assert structure["tabs"] == [{"id": "t.0", "title": "Only", "start_line": 1}]
        lines = content.split("\n")
        h1, h2 = structure["headings"]
        assert h1["id"] == "h.one" and lines[h1["line"] - 1] == "# Title"
        assert h2["id"] == "h.two" and lines[h2["line"] - 1] == "## Section"
        assert h2["tab_id"] == "t.0"

    def test_empty_heading_still_gets_a_line(self) -> None:
        doc = DocData(
            title="Doc",
            document_id="d1",
            tabs=[
                _tab("Only", "t.0", 0, [
                    _paragraph("Head", "HEADING_1", "h.named"),
                    _paragraph("", "HEADING_2", "h.empty"),
                    _paragraph("tail"),
                ]),
            ],
        )
        content = extract_doc_content(doc)
        structure = build_doc_structure(doc, content)
        empty = structure["headings"][1]
        assert empty["id"] == "h.empty"
        assert empty["text"] == ""
        assert "line" in empty

    def test_multi_tab_start_lines_and_injected_title(self) -> None:
        # Neither tab's body starts with an H1, so the extractor injects
        # "# {tab title}" — the alignment must not count those as headings.
        doc = DocData(
            title="Doc",
            document_id="d1",
            tabs=[
                _tab("First", "t.0", 0, [
                    _paragraph("prose"),
                    _paragraph("Inside first", "HEADING_2", "h.a"),
                ]),
                _tab("Second", "t.9", 1, [
                    _paragraph("more prose"),
                    _paragraph("Inside second", "HEADING_3", "h.b"),
                ]),
            ],
        )
        content = extract_doc_content(doc)
        structure = build_doc_structure(doc, content)
        lines = content.split("\n")

        t0, t1 = structure["tabs"]
        assert t0["start_line"] == 1
        assert lines[t1["start_line"] - 1] == "# Second"

        ha, hb = structure["headings"]
        assert lines[ha["line"] - 1] == "## Inside first"
        assert lines[hb["line"] - 1] == "### Inside second"
        assert hb["tab_id"] == "t.9"

    def test_alignment_mismatch_omits_lines_never_wrong(self) -> None:
        # A NORMAL paragraph whose literal text renders as "## fake" produces
        # a heading-shaped line the API's heading list doesn't have. The
        # count+level guard must give up on line numbers rather than misassign.
        doc = DocData(
            title="Doc",
            document_id="d1",
            tabs=[
                _tab("Only", "t.0", 0, [
                    _paragraph("Real Title", "HEADING_1", "h.real"),
                    _paragraph("## fake"),
                    _paragraph("Real Section", "HEADING_2", "h.sec"),
                ]),
            ],
        )
        content = extract_doc_content(doc)
        structure = build_doc_structure(doc, content)
        assert all("line" not in h for h in structure["headings"])
        # ...but ids and text survive, so the cue can still name the heading.
        assert [h["id"] for h in structure["headings"]] == ["h.real", "h.sec"]


class TestBuildSlidesIndex:
    def test_order_ids_titles_thumbnails(self) -> None:
        pres = PresentationData(
            title="Deck",
            presentation_id="p1",
            slides=[
                SlideData(slide_id="p", index=0, title="Cover", thumbnail_bytes=b"x"),
                SlideData(slide_id="g3f5d00ed841_0_0", index=1, title=None),
            ],
        )
        assert build_slides_index(pres) == [
            {"id": "p", "title": "Cover", "has_thumbnail": True},
            {"id": "g3f5d00ed841_0_0", "title": None, "has_thumbnail": False},
        ]


# =============================================================================
# RESOLUTION — against a synthetic deposit
# =============================================================================


def _result(tmp_path: Path, type_: str) -> FetchResult:
    return FetchResult(
        path=str(tmp_path),
        content_file=str(tmp_path / "content.md"),
        format="markdown",
        type=type_,
        metadata={},
        cues={},
    )


def _write_manifest(tmp_path: Path, extra: dict) -> None:
    (tmp_path / "manifest.json").write_bytes(orjson.dumps(extra))


def _apply(result: FetchResult, values: dict, warnings: list | None = None) -> None:
    apply_url_decorations(
        result, UrlDecorations(values=values, warnings=warnings or [])
    )


class TestResolveGid:
    TABS = [
        {"name": "Summary", "sheet_id": 0, "filename": "content_summary.csv"},
        {"name": "Costs", "sheet_id": 1466289902, "filename": "content_costs.csv"},
    ]

    def test_hit_points_at_per_tab_csv(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"tabs": self.TABS})
        r = _result(tmp_path, "sheet")
        _apply(r, {"gid": "1466289902"})
        assert r.cues["pointer"] == (
            "URL points at tab 'Costs' — content_costs.csv."
        )
        assert "warnings" not in r.cues

    def test_range_appended(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"tabs": self.TABS})
        r = _result(tmp_path, "sheet")
        _apply(r, {"gid": "1466289902", "range": "F9:F15"})
        assert "cells F9:F15" in r.cues["pointer"]

    def test_single_tab_points_at_content_csv(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            {"tabs": [{"name": "Sheet1", "sheet_id": 0, "filename": "content.csv"}]},
        )
        r = _result(tmp_path, "sheet")
        _apply(r, {"gid": "0"})
        assert "content.csv" in r.cues["pointer"]

    def test_miss_is_stale_and_warned(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"tabs": self.TABS})
        r = _result(tmp_path, "sheet")
        _apply(r, {"gid": "999"})
        assert "stale" in r.cues["pointer"]
        assert "'Summary'" in r.cues["pointer"]  # names what IS there
        assert any("stale" in w for w in r.cues["warnings"])

    def test_xlsx_has_no_id_map(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            {"tabs": [{"name": "S1", "sheet_id": None, "filename": "content.csv"}]},
        )
        r = _result(tmp_path, "xlsx")
        _apply(r, {"gid": "5"})
        assert "pointer" not in r.cues
        assert any("no tab-id map" in w for w in r.cues["warnings"])


class TestResolveDocTabHeading:
    STRUCTURE = {
        "tabs": [
            {"id": "t.0", "title": "Main", "start_line": 1},
            {"id": "t.ems", "title": "Template", "start_line": 213},
        ],
        "headings": [
            {"id": "h.top", "level": 1, "text": "Overview", "tab_id": "t.0", "line": 1},
            {"id": "h.team", "level": 2, "text": "Team Inbox:", "tab_id": "t.ems", "line": 340},
            {"id": "h.anon", "level": 3, "text": "", "tab_id": "t.ems", "line": 355},
        ],
    }

    def test_tab_points_at_start_line(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"structure": self.STRUCTURE})
        r = _result(tmp_path, "doc")
        _apply(r, {"tab": "t.ems"})
        assert r.cues["pointer"] == (
            "URL points at tab 'Template' — content.md from line 213."
        )

    def test_heading_names_tab_and_line(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"structure": self.STRUCTURE})
        r = _result(tmp_path, "doc")
        _apply(r, {"tab": "t.ems", "heading": "h.team"})
        assert r.cues["pointer"] == (
            "URL points at heading 'Team Inbox:' in tab 'Template' — "
            "content.md from line 340."
        )

    def test_single_tab_doc_omits_tab_clause(self, tmp_path: Path) -> None:
        structure = {
            "tabs": [self.STRUCTURE["tabs"][0]],
            "headings": [self.STRUCTURE["headings"][0]],
        }
        _write_manifest(tmp_path, {"structure": structure})
        r = _result(tmp_path, "doc")
        _apply(r, {"heading": "h.top"})
        assert r.cues["pointer"] == (
            "URL points at heading 'Overview' — content.md from line 1."
        )

    def test_empty_heading_falls_back_to_nearest_named(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"structure": self.STRUCTURE})
        r = _result(tmp_path, "doc")
        _apply(r, {"heading": "h.anon"})
        assert "an unnamed heading (below 'Team Inbox:')" in r.cues["pointer"]
        assert "line 355" in r.cues["pointer"]

    def test_stale_tab_is_firm(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"structure": self.STRUCTURE})
        r = _result(tmp_path, "doc")
        _apply(r, {"tab": "t.gone"})
        assert "immutable" in r.cues["pointer"]
        assert any("stale" in w for w in r.cues["warnings"])

    def test_stale_heading_is_gentle(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"structure": self.STRUCTURE})
        r = _result(tmp_path, "doc")
        _apply(r, {"heading": "h.gone"})
        assert "may have been deleted" in r.cues["pointer"]
        assert "stale" not in r.cues["pointer"]  # softer than the tab wording

    def test_tab_heading_disagreement_is_named(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"structure": self.STRUCTURE})
        r = _result(tmp_path, "doc")
        _apply(r, {"tab": "t.0", "heading": "h.team"})
        assert any("disagree" in w for w in r.cues["warnings"])

    def test_heading_without_line_says_search(self, tmp_path: Path) -> None:
        structure = {
            "tabs": [self.STRUCTURE["tabs"][0]],
            "headings": [
                {"id": "h.x", "level": 2, "text": "Findings", "tab_id": "t.0"}
            ],
        }
        _write_manifest(tmp_path, {"structure": structure})
        r = _result(tmp_path, "doc")
        _apply(r, {"heading": "h.x"})
        assert "line unresolved" in r.cues["pointer"]


class TestResolveSlide:
    INDEX = [
        {"id": "p", "title": "Cover", "has_thumbnail": True},
        {"id": "g3f5d_0", "title": "Results", "has_thumbnail": True},
        {"id": "g3f5d_1", "title": None, "has_thumbnail": False},
    ]

    def test_hit_names_index_title_thumbnail(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"slides_index": self.INDEX})
        r = _result(tmp_path, "slides")
        _apply(r, {"slide": "g3f5d_0"})
        assert r.cues["pointer"] == (
            "URL points at slide 2 of 3 ('Results') — slide_02.png."
        )

    def test_hit_without_thumbnail_is_honest(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"slides_index": self.INDEX})
        r = _result(tmp_path, "slides")
        _apply(r, {"slide": "g3f5d_1"})
        assert "no thumbnail was deposited" in r.cues["pointer"]

    def test_miss_is_stale(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"slides_index": self.INDEX})
        r = _result(tmp_path, "slides")
        _apply(r, {"slide": "mig_slide_003"})
        assert "stale" in r.cues["pointer"]
        assert any("stale" in w for w in r.cues["warnings"])


class TestResolveDisco:
    def test_hit_points_at_comments_md(self, tmp_path: Path) -> None:
        (tmp_path / "comments.md").write_text(
            "### [Alice <a@x.com>] • 2026-01-15 · `AAACCH2tTN0`\n\nLooks wrong?\n"
        )
        _write_manifest(tmp_path, {})
        r = _result(tmp_path, "doc")
        _apply(r, {"disco": "AAACCH2tTN0"})
        assert "comments.md" in r.cues["pointer"]
        assert "comment_reply" in r.cues["pointer"]

    def test_absent_comment_reads_as_resolved(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {})
        r = _result(tmp_path, "doc")
        _apply(r, {"disco": "AAACCH2tTN0"})
        assert "likely been resolved" in r.cues["pointer"]
        assert any("resolved" in w for w in r.cues["warnings"])

    def test_disco_works_on_sheets_too(self, tmp_path: Path) -> None:
        (tmp_path / "comments.md").write_text("· `AAAAsheet1`\n")
        _write_manifest(tmp_path, {"tabs": []})
        r = _result(tmp_path, "sheet")
        _apply(r, {"disco": "AAAAsheet1"})
        assert "comments.md" in r.cues["pointer"]


class TestApplyGuards:
    def test_inapplicable_decoration_is_named_not_dropped(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"structure": {"tabs": [], "headings": []}})
        r = _result(tmp_path, "doc")
        _apply(r, {"gid": "5"})
        assert any("gid=" in w and "doc" in w for w in r.cues["warnings"])

    def test_parse_warnings_reach_cues(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {})
        r = _result(tmp_path, "doc")
        _apply(r, {}, warnings=["URL carries gid= twice with different values"])
        assert r.cues["warnings"] == ["URL carries gid= twice with different values"]

    def test_resolution_failure_is_disclosed_never_raised(self, tmp_path: Path) -> None:
        r = _result(tmp_path, "sheet")
        with patch(
            "tools.fetch.decorations._apply", side_effect=RuntimeError("boom")
        ):
            apply_url_decorations(r, UrlDecorations(values={"gid": "1"}))
        assert any("resolving it against the deposit failed" in w
                   for w in r.cues["warnings"])

    def test_apply_touches_no_files(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, {"tabs": self_tabs()})
        r = _result(tmp_path, "sheet")
        before = sorted(p.name for p in tmp_path.iterdir())
        _apply(r, {"gid": "1466289902"})
        assert sorted(p.name for p in tmp_path.iterdir()) == before


def self_tabs() -> list:
    return [
        {"name": "Costs", "sheet_id": 1466289902, "filename": "content_costs.csv"}
    ]


class TestBareUrlRegression:
    """Step 10: a URL with no decorations gains no cue and changes nothing."""

    def test_router_skips_apply_for_bare_input(self) -> None:
        fetched = FetchResult(
            path="/tmp/x", content_file="/tmp/x/content.md",
            format="markdown", type="doc", metadata={}, cues={},
        )
        with patch("tools.fetch.router.fetch_drive", return_value=fetched), \
             patch("tools.fetch.router.apply_url_decorations") as mock_apply:
            from tools.fetch.router import do_fetch
            result = do_fetch("1OepZjuwi2emuHPAP-LWxWZnw9g0Sbkjh")
        mock_apply.assert_not_called()
        assert "pointer" not in result.cues

    def test_router_applies_for_decorated_url(self) -> None:
        fetched = FetchResult(
            path="/tmp/x", content_file="/tmp/x/content.md",
            format="markdown", type="doc", metadata={}, cues={},
        )
        with patch("tools.fetch.router.fetch_drive", return_value=fetched), \
             patch("tools.fetch.router.apply_url_decorations") as mock_apply:
            from tools.fetch.router import do_fetch
            do_fetch("https://docs.google.com/document/d/1abcDEF/edit?tab=t.0")
        mock_apply.assert_called_once()
        decorations = mock_apply.call_args.args[1]
        assert decorations.values == {"tab": "t.0"}
