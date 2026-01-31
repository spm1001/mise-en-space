"""
Tests for comments extractor.

Tests pure extraction functions with no API calls.
"""

import pytest

from models import FileCommentsData, CommentData, CommentReply
from extractors.comments import extract_comments_content


class TestCommentsExtraction:
    """Tests for extracting comments content."""

    def test_extracts_basic_comment(self, comments_response):
        """Extract content from comments with replies."""
        result = extract_comments_content(comments_response)

        # Should have file name in header
        assert 'Q4 Planning Document' in result
        assert '3 total' in result

        # Should have all commenters with emails
        assert 'Alice Smith <alice@example.com>' in result
        assert 'David Chen <david@example.com>' in result
        assert 'Eve Wilson <eve@example.com>' in result

        # Should have comment content
        assert 'increase the budget' in result
        assert 'timeline seems aggressive' in result
        assert 'fallback strategy' in result

        # Should have quoted text
        assert 'Revenue target: $2.5M' in result
        assert 'Beta launch: March 15' in result

        # Should have replies with emails
        assert 'Bob Jones <bob@example.com>' in result
        assert 'Carol White <carol@example.com>' in result
        assert '20% more' in result

        # Should have dates
        assert '2026-01-15' in result
        assert '2026-01-17' in result

    def test_extracts_mentions(self, comments_response):
        """Extract @mentions from comments."""
        result = extract_comments_content(comments_response)

        # Alice's comment mentions Bob and Carol
        assert '@bob@example.com' in result
        assert '@carol@example.com' in result

        # Carol's reply mentions finance
        assert '@finance@example.com' in result

    def test_formats_mentions_section(self, comments_response):
        """Mentions should be formatted in a *Mentions:* line."""
        result = extract_comments_content(comments_response)

        # Check the format
        assert '*Mentions: @bob@example.com, @carol@example.com*' in result

    def test_shows_resolved_indicator(self, comments_response):
        """Resolved comments should be marked."""
        result = extract_comments_content(comments_response)
        assert '[RESOLVED]' in result

    def test_handles_no_quoted_text(self, comments_response):
        """Comment without anchor text should still work."""
        result = extract_comments_content(comments_response)
        # Eve's comment has no quoted_text
        assert 'Eve Wilson' in result
        assert 'fallback strategy' in result

    def test_handles_no_replies(self, comments_response):
        """Comment without replies should still work."""
        result = extract_comments_content(comments_response)
        # Eve's comment has no replies
        assert 'Eve Wilson' in result

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
