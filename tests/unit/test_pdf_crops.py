"""Exhibit crops + anchors for vision-only graphics (mise-jopohi).

The census measured ~3% of PDF values as vision-only (inside embedded
chart images). These tests pin the corpus-calibrated filter, the anchor
contract cigene consumes, and the deposit wiring.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from adapters.pdf import PdfConversionResult
from adapters.pdf_info import PdfCrop, _select_crop_objects, extract_pdf_crops
from extractors.pdf_anchors import ANCHOR_PREFIX, anchor_line, insert_crop_anchors
from tools.fetch.common import deposit_pdf_crops

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "pdf"


class TestSelectCropObjects:
    """The pure filter — thresholds calibrated on the census corpus."""

    def _obj(self, **kw):
        base = dict(num=0, pages={1}, w=400, h=300, xppi=96.0, yppi=96.0)
        base.update(kw)
        return base

    def test_chart_sized_object_kept(self) -> None:
        assert _select_crop_objects([self._obj()], 93.5)

    def test_small_logo_dropped(self) -> None:
        # the 455x87 header banner class from the corpus
        assert not _select_crop_objects([self._obj(w=455, h=87)], 93.5)

    def test_furniture_spread_dropped(self) -> None:
        # same object placed on >3 pages = repeated furniture
        assert not _select_crop_objects([self._obj(pages={1, 2, 3, 4})], 93.5)

    def test_full_page_background_dropped(self) -> None:
        # image physical area == page area -> background photo, excluded
        obj = self._obj(w=2666, h=1499, xppi=200.0, yppi=200.0)
        page_area = (2666 / 200.0) * (1499 / 200.0)
        assert not _select_crop_objects([obj], page_area)

    def test_no_page_area_skips_coverage_test(self) -> None:
        # pdfinfo unavailable: size + spread still filter, coverage can't
        obj = self._obj(w=2666, h=1499, xppi=200.0, yppi=200.0)
        assert _select_crop_objects([obj], None)


class TestAnchorInsertion:
    """Pure content transformation — the eye-level half of the contract."""

    CROP = {"file": "crop_p002_i003.png", "pages": [2], "width": 500, "height": 400}

    def test_lands_at_its_page(self) -> None:
        content = "page one text\fpage two text\fpage three"
        new, placed = insert_crop_anchors(content, [self.CROP])
        assert placed
        pages = new.split("\f")
        assert "crop_p002_i003.png" in pages[1]
        assert "crop" not in pages[0] and "crop" not in pages[2]

    def test_grep_contract(self) -> None:
        """The stable prefix and single-line shape ARE the two-repo
        contract (agsp-cigene greps for this) — change them only with a
        deposit-structure.md update and a note on cigene's board."""
        line = anchor_line("crop_p008_i012.png", 8, 751, 452)
        assert line.startswith(ANCHOR_PREFIX)
        assert "\n" not in line  # one grep hit carries the whole story
        assert "crop_p008_i012.png" in line
        assert "page 8" in line

    def test_markerless_content_gets_end_block(self) -> None:
        new, placed = insert_crop_anchors("all one blob, no form feeds", [self.CROP])
        assert not placed
        assert new.startswith("all one blob")
        assert "crop_p002_i003.png" in new

    def test_no_crops_no_change(self) -> None:
        assert insert_crop_anchors("text\fmore", []) == ("text\fmore", True)

    def test_multi_page_object_anchors_each_page(self) -> None:
        crop = {"file": "crop_p001_i000.png", "pages": [1, 3], "width": 500, "height": 400}
        new, _ = insert_crop_anchors("a\fb\fc", [crop])
        pages = new.split("\f")
        assert "crop_p001_i000" in pages[0]
        assert "crop_p001_i000" in pages[2]
        assert "crop_p001_i000" not in pages[1]


class TestExtractPdfCropsLive:
    """Live poppler (CI installs poppler-utils; ci.yml)."""

    def test_chart_fixture_yields_one_crop(self) -> None:
        crops = extract_pdf_crops(file_path=FIXTURES / "chart_page.pdf")
        assert len(crops) == 1
        c = crops[0]
        assert c.name == "crop_p001_i000.png"
        assert c.pages == [1]
        assert (c.width, c.height) == (400, 300)
        assert c.png_bytes[:4] == b"\x89PNG"

    def test_imageless_pdf_yields_none(self) -> None:
        # negative control: a probe that reports absence, run on a known negative
        assert extract_pdf_crops(file_path=FIXTURES / "two_pages.pdf") == []

    def test_bytes_variant_matches_path(self) -> None:
        fx = FIXTURES / "chart_page.pdf"
        a = extract_pdf_crops(file_path=fx)
        b = extract_pdf_crops(file_bytes=fx.read_bytes())
        assert [c.name for c in a] == [c.name for c in b]


class TestDepositPdfCrops:
    def _result(self, content: str = "p1 text", crops=()) -> PdfConversionResult:
        return PdfConversionResult(
            content=content, method="pdftotext",
            char_count=len(content), crops=list(crops),
        )

    def _crop(self) -> PdfCrop:
        return PdfCrop(
            name="crop_p001_i000.png", pages=[1],
            width=400, height=300, png_bytes=b"\x89PNGfake",
        )

    def test_writes_file_anchors_content_returns_extras(self, tmp_path: Path) -> None:
        r = self._result(crops=[self._crop()])
        extras = deposit_pdf_crops(tmp_path, r)
        assert (tmp_path / "crop_p001_i000.png").read_bytes() == b"\x89PNGfake"
        assert "exhibit: crop_p001_i000.png" in r.content
        assert extras["crop_count"] == 1
        assert extras["crops"][0]["pages"] == [1]
        assert r.warnings == []

    def test_no_crops_is_empty_noop(self, tmp_path: Path) -> None:
        r = self._result()
        assert deposit_pdf_crops(tmp_path, r) == {}
        assert r.content == "p1 text"

    def test_markerless_placement_warns(self, tmp_path: Path) -> None:
        crop = PdfCrop(name="crop_p005_i002.png", pages=[5],
                       width=400, height=300, png_bytes=b"x")
        r = self._result(content="blob with no markers", crops=[crop])
        deposit_pdf_crops(tmp_path, r)
        assert any("grouped at" in w for w in r.warnings)


class TestCropsReachManifest:
    @patch("tools.fetch.drive.fetch_and_convert_pdf")
    @patch("tools.fetch.drive.get_deposit_folder")
    @patch("tools.fetch.drive.write_content")
    @patch("tools.fetch.drive.write_manifest")
    def test_fetch_pdf_carries_crops(
        self, mock_manifest, mock_write, mock_folder, mock_extract, tmp_path: Path
    ) -> None:
        from tools.fetch import fetch_pdf
        mock_extract.return_value = PdfConversionResult(
            content="page text", method="pdftotext", char_count=9,
            crops=[PdfCrop(name="crop_p001_i000.png", pages=[1],
                           width=400, height=300, png_bytes=b"png")],
        )
        mock_folder.return_value = tmp_path
        mock_write.return_value = tmp_path / "content.md"

        fetch_pdf("abc", "T", {"mimeType": "application/pdf"})

        extra = mock_manifest.call_args.kwargs.get("extra") or mock_manifest.call_args[1].get("extra")
        assert extra["crop_count"] == 1
        # assert on the artefact: the content actually written carries the anchor
        written = mock_write.call_args[0][1]
        assert "exhibit: crop_p001_i000.png" in written
        assert (tmp_path / "crop_p001_i000.png").exists()
