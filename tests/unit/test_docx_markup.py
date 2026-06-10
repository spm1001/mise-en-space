"""Tests for the DOCX markup counter (flattened-content warnings)."""

from extractors.docx_markup import (
    DocxMarkupCounts,
    count_docx_markup,
    format_markup_warnings,
)


def _doc(body: str) -> bytes:
    return f'<?xml version="1.0"?><w:document xmlns:w="x"><w:body>{body}</w:body></w:document>'.encode()


class TestCountDocxMarkup:
    """Counting tracked changes, comments, and images from raw XML bytes."""

    def test_counts_insertions_and_deletions(self) -> None:
        xml = _doc(
            '<w:ins w:id="1" w:author="Jaclyn Wilkins" w:date="2026-05-27T18:04:00Z"><w:r/></w:ins>'
            '<w:del w:id="2" w:author="Jaclyn Wilkins"><w:r/></w:del>'
            '<w:del w:id="3" w:author="Todd Brown"><w:r/></w:del>'
        )
        counts = count_docx_markup(xml)

        assert counts.insertions == 1
        assert counts.deletions == 2
        assert counts.tracked_changes == 3
        assert counts.authors == ["Jaclyn Wilkins", "Todd Brown"]

    def test_instrText_not_counted_as_insertion(self) -> None:
        # <w:instrText> (field codes, e.g. TOC) prefix-collides with <w:ins
        xml = _doc("<w:r><w:instrText>TOC \\o</w:instrText></w:r>")
        counts = count_docx_markup(xml)

        assert counts.insertions == 0
        assert not counts.has_flattened_content

    def test_delText_not_counted_as_deletion(self) -> None:
        # <w:delText> appears inside <w:del> runs — count the wrapper only
        xml = _doc('<w:del w:id="1"><w:r><w:delText>gone</w:delText></w:r></w:del>')
        counts = count_docx_markup(xml)

        assert counts.deletions == 1

    def test_moves_counted(self) -> None:
        xml = _doc(
            '<w:moveFrom w:id="1" w:author="A"><w:r/></w:moveFrom>'
            '<w:moveTo w:id="2" w:author="A"><w:r/></w:moveTo>'
        )
        counts = count_docx_markup(xml)

        assert counts.moves == 2
        assert counts.tracked_changes == 2

    def test_authors_deduped_sorted_empty_filtered(self) -> None:
        xml = _doc(
            '<w:ins w:author="Zoe"/><w:ins w:author="Al"/>'
            '<w:ins w:author="Zoe"/><w:ins w:author=""/>'
        )
        counts = count_docx_markup(xml)

        assert counts.authors == ["Al", "Zoe"]

    def test_comments_counted_from_comments_xml(self) -> None:
        comments = (
            b'<w:comments xmlns:w="x">'
            b'<w:comment w:id="0" w:author="A"/><w:comment w:id="1" w:author="B"/>'
            b"</w:comments>"
        )
        counts = count_docx_markup(_doc(""), comments)

        assert counts.comments == 2

    def test_no_comments_xml_means_zero(self) -> None:
        assert count_docx_markup(_doc("")).comments == 0

    def test_inline_images_counted(self) -> None:
        xml = _doc("<w:r><w:drawing><pic/></w:drawing></w:r><w:r><w:drawing/></w:r>")
        counts = count_docx_markup(xml)

        assert counts.inline_images == 2

    def test_clean_document(self) -> None:
        counts = count_docx_markup(_doc("<w:r><w:t>plain text</w:t></w:r>"))

        assert not counts.has_flattened_content
        assert counts.tracked_changes == 0


class TestFormatMarkupWarnings:
    """Warning text names the trap and the remedy."""

    def test_clean_counts_yield_no_warnings(self) -> None:
        assert format_markup_warnings(DocxMarkupCounts()) == []

    def test_tracked_changes_warning_names_counts_and_authors(self) -> None:
        counts = DocxMarkupCounts(
            insertions=3, deletions=2, authors=["Jaclyn Wilkins"]
        )
        warnings = format_markup_warnings(counts)

        assert len(warnings) == 1
        text = warnings[0]
        assert "5 tracked change(s)" in text
        assert "3 insertion(s)" in text
        assert "2 deletion(s)" in text
        assert "Jaclyn Wilkins" in text
        assert "FLATTENED" in text
        assert "deleted text reads as present" in text

    def test_comments_and_images_each_warn(self) -> None:
        counts = DocxMarkupCounts(comments=4, inline_images=2)
        warnings = format_markup_warnings(counts)

        assert len(warnings) == 2
        assert any("4 Word comment(s)" in w for w in warnings)
        assert any("2 inline image(s)" in w for w in warnings)
