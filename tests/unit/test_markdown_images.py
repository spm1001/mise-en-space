"""Tests for extractors/markdown_images.py — lifting base64 images out of
Drive's docx markdown export (mise-gerefe)."""

from __future__ import annotations

import base64

from extractors.markdown_images import extract_markdown_images

PNG_BYTES = b"\x89PNG-fake-payload-for-tests"
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
JPG_BYTES = b"\xff\xd8-fake-jpeg"
JPG_B64 = base64.b64encode(JPG_BYTES).decode()


class TestReferenceDefinitions:
    def test_ref_def_lifted_to_figure(self) -> None:
        md = f"Intro\n\n![][image1]\n\n[image1]: <data:image/png;base64,{PNG_B64}>\n"
        result = extract_markdown_images(md)

        assert len(result.figures) == 1
        fig = result.figures[0]
        assert fig.filename == "figure-1.png"
        assert fig.data == PNG_BYTES
        assert fig.source_ref == "image1"
        assert "[image1]: figure-1.png" in result.markdown
        assert "data:image" not in result.markdown
        assert result.dangling_refs == []

    def test_def_without_angle_brackets(self) -> None:
        md = f"![][image1]\n\n[image1]: data:image/jpeg;base64,{JPG_B64}\n"
        result = extract_markdown_images(md)

        assert result.figures[0].filename == "figure-1.jpg"
        assert result.figures[0].data == JPG_BYTES

    def test_multiple_defs_numbered_in_order(self) -> None:
        md = (
            f"![][image1] ![][image2]\n\n"
            f"[image1]: <data:image/png;base64,{PNG_B64}>\n"
            f"[image2]: <data:image/jpeg;base64,{JPG_B64}>\n"
        )
        result = extract_markdown_images(md)

        assert [f.filename for f in result.figures] == ["figure-1.png", "figure-2.jpg"]

    def test_undecodable_payload_left_in_place_with_note(self) -> None:
        # Valid base64 charset, invalid length — decodes to binascii.Error.
        md = "![][image1]\n\n[image1]: <data:image/png;base64,AAAAA>\n"
        result = extract_markdown_images(md)

        assert result.figures == []
        assert "AAAAA" in result.markdown
        assert any("undecodable" in n for n in result.notes)
        # The ref still resolves to its (broken) definition — not dangling.
        assert result.dangling_refs == []

    def test_non_base64_definition_is_not_a_data_uri(self) -> None:
        # Charset outside base64 means the pattern must not fire at all —
        # the line is an ordinary reference definition, untouched, no note.
        md = "![][image1]\n\n[image1]: <data:image/png;base64,@@not-base64@@>\n"
        result = extract_markdown_images(md)

        assert result.figures == []
        assert "@@not-base64@@" in result.markdown
        assert result.notes == []
        assert result.dangling_refs == []


class TestInlineImages:
    def test_inline_data_uri_lifted(self) -> None:
        md = f"Before ![Chart 1](data:image/png;base64,{PNG_B64}) after."
        result = extract_markdown_images(md)

        assert result.figures[0].filename == "figure-1.png"
        assert result.figures[0].source_ref is None
        assert "![Chart 1](figure-1.png)" in result.markdown
        assert "base64" not in result.markdown


class TestDanglingRefs:
    def test_ref_without_definition_reported(self) -> None:
        md = "See ![][image2] here.\n"
        result = extract_markdown_images(md)

        assert result.figures == []
        assert result.dangling_refs == ["image2"]

    def test_mixed_retained_and_dangling(self) -> None:
        md = (
            f"![][image1] and ![][image2]\n\n"
            f"[image1]: <data:image/png;base64,{PNG_B64}>\n"
        )
        result = extract_markdown_images(md)

        assert [f.filename for f in result.figures] == ["figure-1.png"]
        assert result.dangling_refs == ["image2"]


class TestPassthrough:
    def test_no_images_is_identity(self) -> None:
        md = "# Title\n\nPlain prose with [a link](https://example.com).\n"
        result = extract_markdown_images(md)

        assert result.markdown == md
        assert result.figures == []
        assert result.dangling_refs == []
        assert result.notes == []

    def test_normal_file_image_untouched(self) -> None:
        md = "![alt](diagram.png)\n"
        result = extract_markdown_images(md)

        assert result.markdown == md
        assert result.figures == []
