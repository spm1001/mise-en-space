"""
Tests for Activity API adapter and models.

Tests pure model functionality without API calls.
"""

import pytest

from models import (
    ActivityActor,
    ActivityTarget,
    CommentActivity,
    ActivitySearchResult,
)


class TestActivityModels:
    """Tests for Activity data models."""

    def test_activity_actor_defaults(self):
        """ActivityActor should have sensible defaults."""
        actor = ActivityActor(name="Alice")
        assert actor.name == "Alice"
        assert actor.email is None

    def test_activity_actor_with_email(self):
        """ActivityActor can store email."""
        actor = ActivityActor(name="Alice", email="alice@example.com")
        assert actor.name == "Alice"
        assert actor.email == "alice@example.com"

    def test_activity_target_defaults(self):
        """ActivityTarget should have sensible defaults."""
        target = ActivityTarget(file_id="1abc", file_name="Test Doc")
        assert target.file_id == "1abc"
        assert target.file_name == "Test Doc"
        assert target.mime_type is None
        assert target.web_link is None

    def test_activity_target_full(self):
        """ActivityTarget with all fields."""
        target = ActivityTarget(
            file_id="1abc",
            file_name="Test Doc",
            mime_type="application/vnd.google-apps.document",
            web_link="https://docs.google.com/document/d/1abc/edit",
        )
        assert target.file_id == "1abc"
        assert target.mime_type == "application/vnd.google-apps.document"
        assert "docs.google.com" in target.web_link

    def test_comment_activity_defaults(self):
        """CommentActivity should have sensible defaults."""
        activity = CommentActivity(
            activity_id="act-123",
            timestamp="2026-01-20T10:00:00Z",
            actor=ActivityActor(name="Alice"),
            target=ActivityTarget(file_id="1abc", file_name="Doc"),
            action_type="comment",
        )
        assert activity.activity_id == "act-123"
        assert activity.action_type == "comment"
        assert activity.mentioned_users == []
        assert activity.comment_content is None

    def test_comment_activity_with_mentions(self):
        """CommentActivity should store mentioned users."""
        activity = CommentActivity(
            activity_id="act-123",
            timestamp="2026-01-20T10:00:00Z",
            actor=ActivityActor(name="Alice"),
            target=ActivityTarget(file_id="1abc", file_name="Doc"),
            action_type="comment",
            mentioned_users=["Bob Jones", "Carol White"],
        )
        assert activity.mentioned_users == ["Bob Jones", "Carol White"]

    def test_activity_search_result_defaults(self):
        """ActivitySearchResult should have sensible defaults."""
        result = ActivitySearchResult(activities=[])
        assert result.activities == []
        assert result.next_page_token is None
        assert result.warnings == []

    def test_activity_search_result_with_data(self):
        """ActivitySearchResult with activities."""
        activity = CommentActivity(
            activity_id="act-123",
            timestamp="2026-01-20T10:00:00Z",
            actor=ActivityActor(name="Alice"),
            target=ActivityTarget(file_id="1abc", file_name="Doc"),
            action_type="comment",
        )
        result = ActivitySearchResult(
            activities=[activity],
            next_page_token="token-abc",
            warnings=["Some warning"],
        )
        assert len(result.activities) == 1
        assert result.activities[0].actor.name == "Alice"
        assert result.next_page_token == "token-abc"
        assert result.warnings == ["Some warning"]


class TestActivityActionTypes:
    """Tests for various activity action types."""

    def test_comment_action(self):
        """Test comment action type."""
        activity = CommentActivity(
            activity_id="act-1",
            timestamp="2026-01-20T10:00:00Z",
            actor=ActivityActor(name="Alice"),
            target=ActivityTarget(file_id="1abc", file_name="Doc"),
            action_type="comment",
        )
        assert activity.action_type == "comment"

    def test_reply_action(self):
        """Test reply action type."""
        activity = CommentActivity(
            activity_id="act-2",
            timestamp="2026-01-20T10:00:00Z",
            actor=ActivityActor(name="Bob"),
            target=ActivityTarget(file_id="1abc", file_name="Doc"),
            action_type="reply",
        )
        assert activity.action_type == "reply"

    def test_resolve_action(self):
        """Test resolve action type."""
        activity = CommentActivity(
            activity_id="act-3",
            timestamp="2026-01-20T10:00:00Z",
            actor=ActivityActor(name="Carol"),
            target=ActivityTarget(file_id="1abc", file_name="Doc"),
            action_type="resolve",
        )
        assert activity.action_type == "resolve"

    def test_edit_action(self):
        """Test edit action type (for file activity)."""
        activity = CommentActivity(
            activity_id="act-4",
            timestamp="2026-01-20T10:00:00Z",
            actor=ActivityActor(name="Dave"),
            target=ActivityTarget(file_id="1abc", file_name="Doc"),
            action_type="edit",
        )
        assert activity.action_type == "edit"
