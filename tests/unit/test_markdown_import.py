"""
Tests for markdown_import.convert_fenced_blocks — the pre-import rewrite that
saves fenced code blocks from Google's per-word-pill mangling (mise-sejule).

The target output form was established by live probes (2026-07-23): per-line
inline-code spans, backslash hard breaks within a block, blank-line padding
around it, double-backtick wrapping for lines that contain a backtick.
"""

from markdown_import import convert_fenced_blocks


class TestPassthrough:
    def test_no_fences_returned_verbatim(self):
        md = "# Title\n\nSome text with `inline code` and **bold**.\n"
        assert convert_fenced_blocks(md) == md

    def test_inline_triple_backtick_mention_untouched(self):
        # A fence must be alone on its line — prose mentioning ``` is not one.
        md = "Use ``` fences in markdown files.\n"
        assert convert_fenced_blocks(md) == md

    def test_idempotent_on_own_output(self):
        md = "# T\n\n```\ncmd --flag\n```\n"
        once = convert_fenced_blocks(md)
        assert convert_fenced_blocks(once) == once


class TestSingleLineBlocks:
    def test_bare_fence_single_line(self):
        md = "```\ngcloud projects list --filter=x\n```"
        out = convert_fenced_blocks(md)
        assert "`gcloud projects list --filter=x`" in out
        assert "```" not in out

    def test_language_tag_stripped(self):
        out = convert_fenced_blocks("```bash\ncmd --flag\n```")
        assert "`cmd --flag`" in out
        assert "bash" not in out

    def test_tilde_fence(self):
        out = convert_fenced_blocks("~~~\ncmd --flag\n~~~")
        assert "`cmd --flag`" in out
        assert "~~~" not in out


class TestMultiLineBlocks:
    def test_lines_joined_with_hard_breaks(self):
        out = convert_fenced_blocks("```\nfirst --a\nsecond --b\n```")
        assert "`first --a`\\\n`second --b`" in out

    def test_blank_line_splits_paragraphs(self):
        out = convert_fenced_blocks("```\nfirst --a\n\nsecond --b\n```")
        # Two groups — no hard break bridging the blank line.
        assert "`first --a`\n\n`second --b`" in out

    def test_blank_padding_prevents_soft_wrap_merge(self):
        out = convert_fenced_blocks("Run this:\n```\ncmd --flag\n```\nDone.")
        assert "Run this:\n\n`cmd --flag`\n\nDone." in out


class TestEdgeCases:
    def test_line_containing_backtick_double_wrapped(self):
        out = convert_fenced_blocks("```\necho `hi` there\n```")
        assert "`` echo `hi` there ``" in out

    def test_unclosed_fence_runs_to_end(self):
        out = convert_fenced_blocks("text\n```\ncmd --flag")
        assert "`cmd --flag`" in out
        assert "```" not in out

    def test_empty_block_leaves_no_spans(self):
        out = convert_fenced_blocks("a\n```\n```\nb")
        assert "`" not in out
        assert "a" in out and "b" in out

    def test_indented_fence_recognised(self):
        out = convert_fenced_blocks("  ```\n  cmd --flag\n  ```")
        assert "`  cmd --flag`" in out

    def test_longer_close_fence_accepted(self):
        out = convert_fenced_blocks("```\ncmd --flag\n`````")
        assert "`cmd --flag`" in out
        assert "```" not in out

    def test_shorter_close_ignored_until_real_close(self):
        # Opening with 4 backticks — a 3-backtick line is content, not close.
        out = convert_fenced_blocks("````\n```\ncmd\n````")
        assert "`` ``` ``" in out  # the inner ``` is code content
        assert "`cmd`" in out

    def test_indentation_preserved_inside_span(self):
        out = convert_fenced_blocks("```\ndef f():\n    return 1\n```")
        assert "`def f():`\\\n`    return 1`" in out

    def test_four_space_indented_block_left_alone(self):
        md = "para\n\n    indented code\n"
        assert convert_fenced_blocks(md) == md
