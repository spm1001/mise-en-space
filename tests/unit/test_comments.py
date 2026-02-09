"""
Tests for comments extractor.

Tests pure extraction functions with no API calls.
"""

import pytest
from inline_snapshot import snapshot

from models import FileCommentsData, CommentData, CommentReply
from extractors.comments import extract_comments_content


class TestCommentsExtraction:
    """Tests for extracting comments content."""

    def test_extracts_basic_comment(self, comments_response):
        """Extract content from comments with replies — full output verification."""
        result = extract_comments_content(comments_response)
        assert result == snapshot("""\
## Comments on "Q4 Planning Document" (3 total)

### [Alice Smith <alice@example.com>] • 2026-01-15
*Mentions: @bob@example.com, @carol@example.com*

> Revenue target: $2.5M

@bob@example.com @carol@example.com - Should we increase the budget for this initiative?

**Replies:**
- **[Bob Jones <bob@example.com>]** (2026-01-15): Yes, I think we need at least 20% more.
- **[Carol White <carol@example.com>]** (2026-01-16): @finance@example.com Let me check with finance first. *[@finance@example.com]*

---

### [David Chen <david@example.com>] • 2026-01-17
*[RESOLVED]*

> Beta launch: March 15

This timeline seems aggressive. Can we push back the launch?

**Replies:**
- **[Alice Smith <alice@example.com>]** (2026-01-17): Agreed. Let's target April 1 instead.

---

### [Eve Wilson <eve@example.com>] • 2026-01-18

Consider adding a fallback strategy here.\
""")

    def test_respects_max_length(self, comments_response):
        """Truncate when max_length exceeded."""
        result = extract_comments_content(comments_response, max_length=300)
        assert len(result) <= 400  # Some slack for truncation message
        assert 'TRUNCATED' in result

    def test_empty_comments(self):
        """Handle file with no comments."""
        data = FileCommentsData(
            file_id="test-id",
            file_name="Empty Doc",
            comments=[],
        )
        result = extract_comments_content(data)
        assert 'Empty Doc' in result
        assert '0 total' in result
        assert 'No comments found' in result

    def test_comment_without_date(self):
        """Handle comment with missing date."""
        data = FileCommentsData(
            file_id="test-id",
            file_name="Test Doc",
            comments=[
                CommentData(
                    id="c1",
                    content="A comment without date",
                    author_name="Test User",
                    # No created_time
                )
            ],
        )
        result = extract_comments_content(data)
        assert 'Test User' in result
        assert 'A comment without date' in result
        # Should still have header format even without date
        assert '###' in result

    def test_reply_without_date(self):
        """Handle reply with missing date."""
        data = FileCommentsData(
            file_id="test-id",
            file_name="Test Doc",
            comments=[
                CommentData(
                    id="c1",
                    content="Parent comment",
                    author_name="Parent User",
                    created_time="2026-01-20T10:00:00.000Z",
                    replies=[
                        CommentReply(
                            id="r1",
                            content="Reply without date",
                            author_name="Reply User",
                            # No created_time
                        )
                    ],
                )
            ],
        )
        result = extract_comments_content(data)
        assert 'Reply User' in result
        assert 'Reply without date' in result

    def test_populates_warnings_on_truncation(self):
        """Warnings should be set when content is truncated."""
        data = FileCommentsData(
            file_id="test-id",
            file_name="Long Doc",
            comments=[
                CommentData(
                    id=f"c{i}",
                    content="A" * 100,
                    author_name=f"User {i}",
                    created_time="2026-01-20T10:00:00.000Z",
                )
                for i in range(10)
            ],
        )
        data.warnings = []  # Clear any post_init warnings

        result = extract_comments_content(data, max_length=500)
        assert 'TRUNCATED' in result
        # Should have both anchor warning and truncation warning
        assert any('truncated' in w.lower() for w in data.warnings)

    def test_warns_when_no_anchor_context(self):
        """Should warn when no comments have quoted_text (DOCX/Sheets behavior)."""
        data = FileCommentsData(
            file_id="test-id",
            file_name="Test DOCX",
            comments=[
                CommentData(
                    id="c1",
                    content="Comment without anchor",
                    author_name="User 1",
                    quoted_text="",  # Empty anchor
                ),
                CommentData(
                    id="c2",
                    content="Another comment",
                    author_name="User 2",
                    quoted_text="",  # Also empty
                ),
            ],
        )
        data.warnings = []

        extract_comments_content(data)

        assert len(data.warnings) == 1
        assert 'anchor context not available' in data.warnings[0].lower()

    def test_no_anchor_warning_when_anchors_present(self):
        """Should NOT warn when at least one comment has anchor text."""
        data = FileCommentsData(
            file_id="test-id",
            file_name="Test Doc",
            comments=[
                CommentData(
                    id="c1",
                    content="Comment with anchor",
                    author_name="User 1",
                    quoted_text="Some highlighted text",
                ),
                CommentData(
                    id="c2",
                    content="Comment without anchor",
                    author_name="User 2",
                    quoted_text="",
                ),
            ],
        )
        data.warnings = []

        extract_comments_content(data)

        assert len(data.warnings) == 0


class TestRealCommentsEdgeCases:
    """Tests for edge cases found in purpose-built test doc."""

    def test_long_anchor_truncated(self, real_comments_response):
        """Long anchor text should be truncated at 200 chars."""
        result = extract_comments_content(real_comments_response)
        # The fixture has an 800+ char anchor — should be truncated
        assert "…" in result
        # No single blockquote line should exceed ~210 chars (200 + ellipsis + formatting)
        for line in result.split("\n"):
            if line.startswith("> "):
                assert len(line) <= 210, f"Anchor too long: {len(line)} chars"

    def test_empty_reply_skipped(self, real_comments_response):
        """Empty replies (resolved markers) should not appear in output."""
        result = extract_comments_content(real_comments_response)
        # The resolved comment has an empty reply — should be skipped
        # Find the RESOLVED section
        resolved_idx = result.index("[RESOLVED]")
        # Get the section between RESOLVED and next ---
        next_sep = result.index("---", resolved_idx)
        resolved_section = result[resolved_idx:next_sep]
        assert "Replies:" not in resolved_section

    def test_multiline_reply_stays_in_list(self, real_comments_response):
        """Reply with newlines should stay within the list item."""
        result = extract_comments_content(real_comments_response)
        # Find the reply with rich text
        assert "this is a reply to the comment" in result
        assert "And **rich text** for fun" in result
        # The continuation should be indented (part of list item)
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if "And **rich text** for fun" in line:
                assert line.startswith("  "), f"Continuation not indented: '{line}'"
                break
        else:
            pytest.fail("Rich text continuation line not found")

    def test_multiple_mentions(self, real_comments_response):
        """Comment with multiple @mentions should list all."""
        result = extract_comments_content(real_comments_response)
        # The multi-mention comment has two emails
        assert "@alice@example.com" in result
        assert "@bob@example.com" in result

    def test_resolved_comment_marked(self, real_comments_response):
        """Resolved comment should show [RESOLVED] indicator."""
        result = extract_comments_content(real_comments_response)
        assert "[RESOLVED]" in result

    def test_total_count_includes_all(self, real_comments_response):
        """Header should show total count of all comments."""
        result = extract_comments_content(real_comments_response)
        assert "7 total" in result

    def test_rich_text_in_content_preserved(self, real_comments_response):
        """Markdown-like formatting in comment content should pass through."""
        result = extract_comments_content(real_comments_response)
        assert "*rich*" in result


class TestCommentDataModel:
    """Tests for the comment data models."""

    def test_file_comments_data_counts(self):
        """FileCommentsData should count comments in post_init."""
        data = FileCommentsData(
            file_id="test-id",
            file_name="Test Doc",
            comments=[
                CommentData(id="c1", content="One", author_name="A"),
                CommentData(id="c2", content="Two", author_name="B"),
                CommentData(id="c3", content="Three", author_name="C"),
            ],
        )
        assert data.comment_count == 3

    def test_comment_reply_default_values(self):
        """CommentReply should have sensible defaults."""
        reply = CommentReply(
            id="r1",
            content="Test reply",
            author_name="Replier",
        )
        assert reply.author_email is None
        assert reply.created_time is None
        assert reply.modified_time is None
        assert reply.mentioned_emails == []

    def test_comment_data_default_values(self):
        """CommentData should have sensible defaults."""
        comment = CommentData(
            id="c1",
            content="Test comment",
            author_name="Commenter",
        )
        assert comment.author_email is None
        assert comment.resolved is False
        assert comment.quoted_text == ""
        assert comment.mentioned_emails == []
        assert comment.replies == []

    def test_comment_with_mentions(self):
        """CommentData should store mentioned emails."""
        comment = CommentData(
            id="c1",
            content="@alice@test.com check this",
            author_name="Commenter",
            mentioned_emails=["alice@test.com"],
        )
        assert comment.mentioned_emails == ["alice@test.com"]

    def test_reply_with_mentions(self):
        """CommentReply should store mentioned emails."""
        reply = CommentReply(
            id="r1",
            content="@bob@test.com done",
            author_name="Replier",
            mentioned_emails=["bob@test.com"],
        )
        assert reply.mentioned_emails == ["bob@test.com"]
