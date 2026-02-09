"""
Tests for Activity API adapter and models.
"""

import pytest
from unittest.mock import patch, MagicMock

from models import (
    ActivityActor,
    ActivityTarget,
    CommentActivity,
    ActivitySearchResult,
)
from adapters.activity import (
    _parse_actor,
    _parse_target,
    _parse_comment_action,
    search_comment_activities,
    get_file_activities,
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


# ============================================================================
# PURE PARSERS
# ============================================================================


class TestParseActor:
    """Test _parse_actor with various actor structures."""

    def test_known_user(self) -> None:
        actor = _parse_actor({"user": {"knownUser": {"personName": "Alice Smith"}}})
        assert actor.name == "Alice Smith"
        assert actor.email is None  # Activity API doesn't expose email

    def test_administrator(self) -> None:
        actor = _parse_actor({"administrator": {}})
        assert actor.name == "Administrator"

    def test_impersonation(self) -> None:
        actor = _parse_actor({
            "impersonation": {
                "impersonatedUser": {
                    "knownUser": {"personName": "Bob Jones"}
                }
            }
        })
        assert actor.name == "Bob Jones"

    def test_impersonation_missing_name(self) -> None:
        actor = _parse_actor({"impersonation": {"impersonatedUser": {}}})
        assert actor.name == "Unknown"

    def test_system(self) -> None:
        actor = _parse_actor({"system": {}})
        assert actor.name == "System"

    def test_empty_data(self) -> None:
        actor = _parse_actor({})
        assert actor.name == "Unknown"

    def test_user_without_known_user(self) -> None:
        """User present but no knownUser data."""
        actor = _parse_actor({"user": {}})
        assert actor.name == "Unknown"

    def test_known_user_empty_name(self) -> None:
        """knownUser present but personName empty → falls through."""
        actor = _parse_actor({"user": {"knownUser": {"personName": ""}}})
        assert actor.name == "Unknown"


class TestParseTarget:
    """Test _parse_target with various target structures."""

    def test_google_doc(self) -> None:
        target = _parse_target({
            "driveItem": {
                "name": "items/abc123",
                "title": "Q4 Plan",
                "mimeType": "application/vnd.google-apps.document",
            }
        })
        assert target is not None
        assert target.file_id == "abc123"
        assert target.file_name == "Q4 Plan"
        assert target.web_link == "https://docs.google.com/document/d/abc123/edit"

    def test_google_sheet(self) -> None:
        target = _parse_target({
            "driveItem": {
                "name": "items/sheet456",
                "title": "Budget",
                "mimeType": "application/vnd.google-apps.spreadsheet",
            }
        })
        assert target.web_link == "https://docs.google.com/spreadsheets/d/sheet456/edit"

    def test_google_slides(self) -> None:
        target = _parse_target({
            "driveItem": {
                "name": "items/pres789",
                "title": "Deck",
                "mimeType": "application/vnd.google-apps.presentation",
            }
        })
        assert target.web_link == "https://docs.google.com/presentation/d/pres789/edit"

    def test_other_file_type(self) -> None:
        target = _parse_target({
            "driveItem": {
                "name": "items/pdf001",
                "title": "Report.pdf",
                "mimeType": "application/pdf",
            }
        })
        assert target.web_link == "https://drive.google.com/file/d/pdf001/view"

    def test_no_drive_item(self) -> None:
        assert _parse_target({}) is None

    def test_empty_drive_item(self) -> None:
        assert _parse_target({"driveItem": {}}) is None

    def test_name_without_items_prefix(self) -> None:
        """Name not in items/ format kept as-is."""
        target = _parse_target({
            "driveItem": {
                "name": "raw_id",
                "title": "Test",
                "mimeType": "application/pdf",
            }
        })
        assert target.file_id == "raw_id"

    def test_missing_title_and_mime(self) -> None:
        target = _parse_target({
            "driveItem": {"name": "items/id123"}
        })
        assert target.file_id == "id123"
        assert target.file_name == ""
        assert target.mime_type == ""
        assert target.web_link == "https://drive.google.com/file/d/id123/view"


class TestParseCommentAction:
    """Test _parse_comment_action with all comment subtypes."""

    # -- Post subtypes --

    def test_post_added(self) -> None:
        action_type, mentions, content = _parse_comment_action({
            "comment": {"post": {"subtype": "ADDED"}}
        })
        assert action_type == "comment"

    def test_post_reply_added(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"post": {"subtype": "REPLY_ADDED"}}
        })
        assert action_type == "reply"

    def test_post_resolved(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"post": {"subtype": "RESOLVED"}}
        })
        assert action_type == "resolve"

    def test_post_reopened(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"post": {"subtype": "REOPENED"}}
        })
        assert action_type == "reopen"

    def test_post_deleted(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"post": {"subtype": "DELETED"}}
        })
        assert action_type == "delete"

    def test_post_unknown_subtype(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"post": {"subtype": "WEIRD_NEW_TYPE"}}
        })
        assert action_type == "post_weird_new_type"

    def test_post_empty_subtype(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"post": {"subtype": ""}}
        })
        assert action_type == "post"

    def test_post_empty_dict_falls_through(self) -> None:
        """Empty post dict is falsy → falls through to comment_action."""
        action_type, _, _ = _parse_comment_action({
            "comment": {"post": {}}
        })
        assert action_type == "comment_action"

    # -- Assignment subtypes --

    def test_assignment_added(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"assignment": {"subtype": "ADDED"}}
        })
        assert action_type == "assign"

    def test_assignment_removed(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"assignment": {"subtype": "REMOVED"}}
        })
        assert action_type == "unassign"

    def test_assignment_unknown_subtype(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"assignment": {"subtype": "CHANGED"}}
        })
        assert action_type == "assignment_changed"

    def test_assignment_empty_dict_falls_through(self) -> None:
        """Empty assignment dict is falsy → falls through to comment_action."""
        action_type, _, _ = _parse_comment_action({
            "comment": {"assignment": {}}
        })
        assert action_type == "comment_action"

    # -- Suggestion subtypes --

    def test_suggestion_added(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"suggestion": {"subtype": "ADDED"}}
        })
        assert action_type == "suggest"

    def test_suggestion_accepted(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"suggestion": {"subtype": "ACCEPTED"}}
        })
        assert action_type == "accept_suggestion"

    def test_suggestion_rejected(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"suggestion": {"subtype": "REJECTED"}}
        })
        assert action_type == "reject_suggestion"

    def test_suggestion_unknown_subtype(self) -> None:
        action_type, _, _ = _parse_comment_action({
            "comment": {"suggestion": {"subtype": "MODIFIED"}}
        })
        assert action_type == "suggestion_modified"

    def test_suggestion_empty_dict_falls_through(self) -> None:
        """Empty suggestion dict is falsy → falls through to comment_action."""
        action_type, _, _ = _parse_comment_action({
            "comment": {"suggestion": {}}
        })
        assert action_type == "comment_action"

    # -- Edge cases --

    def test_no_comment_key(self) -> None:
        action_type, mentions, content = _parse_comment_action({})
        assert action_type == "unknown"
        assert mentions == []
        assert content is None

    def test_empty_comment_dict_is_falsy(self) -> None:
        """Empty comment dict {} is falsy → returns 'unknown' early."""
        action_type, _, _ = _parse_comment_action({"comment": {}})
        assert action_type == "unknown"

    def test_mentioned_users_parsed(self) -> None:
        _, mentions, _ = _parse_comment_action({
            "comment": {
                "post": {"subtype": "ADDED"},
                "mentionedUsers": [
                    {"knownUser": {"personName": "Alice Smith"}},
                    {"knownUser": {"personName": "Bob Jones"}},
                    {"knownUser": {}},  # No personName — skipped
                ],
            }
        })
        assert mentions == ["Alice Smith", "Bob Jones"]

    def test_content_always_none(self) -> None:
        """Activity API doesn't expose comment content."""
        _, _, content = _parse_comment_action({
            "comment": {"post": {"subtype": "ADDED"}}
        })
        assert content is None


# ============================================================================
# search_comment_activities (mocked service)
# ============================================================================


def _api_activity(
    *,
    name: str = "activities/123",
    timestamp: str = "2026-01-20T10:00:00Z",
    action_detail: dict | None = None,
    actor: dict | None = None,
    target: dict | None = None,
) -> dict:
    """Build an API-shaped activity dict."""
    if action_detail is None:
        action_detail = {"comment": {"post": {"subtype": "ADDED"}}}
    if actor is None:
        actor = {"user": {"knownUser": {"personName": "Alice"}}}
    if target is None:
        target = {
            "driveItem": {
                "name": "items/doc123",
                "title": "Test Doc",
                "mimeType": "application/vnd.google-apps.document",
            }
        }
    return {
        "name": name,
        "timestamp": timestamp,
        "primaryActionDetail": action_detail,
        "actors": [actor],
        "targets": [target],
    }


class TestSearchCommentActivities:
    """Test search_comment_activities with mocked Activity API."""

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_basic_search(self, mock_svc, _sleep) -> None:
        """Parses activity response into CommentActivity list."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.activity().query().execute.return_value = {
            "activities": [
                _api_activity(
                    name="act/1",
                    timestamp="2026-01-20T10:00:00Z",
                ),
            ],
        }

        result = search_comment_activities()

        assert len(result.activities) == 1
        act = result.activities[0]
        assert act.activity_id == "act/1"
        assert act.timestamp == "2026-01-20T10:00:00Z"
        assert act.actor.name == "Alice"
        assert act.target.file_id == "doc123"
        assert act.target.file_name == "Test Doc"
        assert act.action_type == "comment"
        assert result.warnings == []

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_empty_response(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {}

        result = search_comment_activities()

        assert result.activities == []
        assert result.next_page_token is None

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_pagination_token_returned(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {
            "activities": [_api_activity()],
            "nextPageToken": "page2",
        }

        result = search_comment_activities()

        assert result.next_page_token == "page2"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_pagination_token_sent(self, mock_svc, _sleep) -> None:
        """page_token forwarded to API request body."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {"activities": []}

        search_comment_activities(page_token="tok123")

        call_args = mock_service.activity().query.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
        assert body.get("pageToken") == "tok123"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_page_size_capped_at_100(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {"activities": []}

        search_comment_activities(page_size=200)

        call_args = mock_service.activity().query.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
        assert body["pageSize"] == 100

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_missing_target_warns_and_skips(self, mock_svc, _sleep) -> None:
        """Activity without target generates warning and is skipped."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.activity().query().execute.return_value = {
            "activities": [
                {
                    "name": "act/orphan",
                    "timestamp": "2026-01-20T10:00:00Z",
                    "primaryActionDetail": {"comment": {"post": {"subtype": "ADDED"}}},
                    "actors": [{"user": {"knownUser": {"personName": "Alice"}}}],
                    "targets": [{}],  # Empty target → _parse_target returns None
                },
            ],
        }

        result = search_comment_activities()

        assert result.activities == []
        assert any("missing target" in w for w in result.warnings)

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_no_actors_uses_unknown(self, mock_svc, _sleep) -> None:
        """Activity with empty actors list → Unknown actor."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        activity = _api_activity()
        activity["actors"] = []
        mock_service.activity().query().execute.return_value = {
            "activities": [activity],
        }

        result = search_comment_activities()

        assert result.activities[0].actor.name == "Unknown"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_comment_filter_in_request(self, mock_svc, _sleep) -> None:
        """Request includes COMMENT filter."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {"activities": []}

        search_comment_activities()

        call_args = mock_service.activity().query.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
        assert "COMMENT" in body["filter"]


# ============================================================================
# get_file_activities (mocked service)
# ============================================================================


class TestGetFileActivities:
    """Test get_file_activities with mocked Activity API."""

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_comment_activity(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.activity().query().execute.return_value = {
            "activities": [
                _api_activity(
                    action_detail={"comment": {"post": {"subtype": "ADDED"}}},
                ),
            ],
        }

        result = get_file_activities("doc123")

        assert len(result.activities) == 1
        assert result.activities[0].action_type == "comment"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_edit_activity(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.activity().query().execute.return_value = {
            "activities": [
                _api_activity(action_detail={"edit": {}}),
            ],
        }

        result = get_file_activities("doc123")

        assert result.activities[0].action_type == "edit"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_create_activity(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {
            "activities": [_api_activity(action_detail={"create": {}})],
        }

        result = get_file_activities("doc123")
        assert result.activities[0].action_type == "create"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_move_activity(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {
            "activities": [_api_activity(action_detail={"move": {}})],
        }

        result = get_file_activities("doc123")
        assert result.activities[0].action_type == "move"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_rename_activity(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {
            "activities": [_api_activity(action_detail={"rename": {}})],
        }

        result = get_file_activities("doc123")
        assert result.activities[0].action_type == "rename"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_delete_activity(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {
            "activities": [_api_activity(action_detail={"delete": {}})],
        }

        result = get_file_activities("doc123")
        assert result.activities[0].action_type == "delete"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_restore_activity(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {
            "activities": [_api_activity(action_detail={"restore": {}})],
        }

        result = get_file_activities("doc123")
        assert result.activities[0].action_type == "restore"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_other_activity(self, mock_svc, _sleep) -> None:
        """Unknown action type → 'other'."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {
            "activities": [_api_activity(action_detail={"permissionChange": {}})],
        }

        result = get_file_activities("doc123")
        assert result.activities[0].action_type == "other"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_missing_target_uses_file_id_fallback(self, mock_svc, _sleep) -> None:
        """Missing target in file activities → fallback target with file_id."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        activity = _api_activity()
        activity["targets"] = [{}]  # Empty → _parse_target returns None
        mock_service.activity().query().execute.return_value = {
            "activities": [activity],
        }

        result = get_file_activities("myfile")

        assert len(result.activities) == 1
        assert result.activities[0].target.file_id == "myfile"
        assert result.activities[0].target.file_name == ""

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_filter_type_comments(self, mock_svc, _sleep) -> None:
        """filter_type='comments' adds COMMENT filter."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {"activities": []}

        get_file_activities("doc123", filter_type="comments")

        call_args = mock_service.activity().query.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
        assert "COMMENT" in body["filter"]

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_filter_type_edits(self, mock_svc, _sleep) -> None:
        """filter_type='edits' adds EDIT filter."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {"activities": []}

        get_file_activities("doc123", filter_type="edits")

        call_args = mock_service.activity().query.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
        assert "EDIT" in body["filter"]

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_filter_type_none_no_filter(self, mock_svc, _sleep) -> None:
        """filter_type=None → no filter in request body."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {"activities": []}

        get_file_activities("doc123", filter_type=None)

        call_args = mock_service.activity().query.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
        assert "filter" not in body

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_item_name_in_request(self, mock_svc, _sleep) -> None:
        """File ID is formatted as items/{file_id} in request."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {"activities": []}

        get_file_activities("abc123")

        call_args = mock_service.activity().query.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
        assert body["itemName"] == "items/abc123"

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_page_size_capped(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.activity().query().execute.return_value = {"activities": []}

        get_file_activities("doc123", page_size=500)

        call_args = mock_service.activity().query.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
        assert body["pageSize"] == 100

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_multiple_activities_mixed_types(self, mock_svc, _sleep) -> None:
        """Multiple activities with different action types parsed correctly."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        mock_service.activity().query().execute.return_value = {
            "activities": [
                _api_activity(name="a1", action_detail={"comment": {"post": {"subtype": "ADDED"}}}),
                _api_activity(name="a2", action_detail={"edit": {}}),
                _api_activity(name="a3", action_detail={"comment": {"post": {"subtype": "RESOLVED"}}}),
            ],
        }

        result = get_file_activities("doc123", filter_type=None)

        assert len(result.activities) == 3
        types = [a.action_type for a in result.activities]
        assert types == ["comment", "edit", "resolve"]

    @patch("retry.time.sleep")
    @patch("adapters.activity.get_activity_service")
    def test_no_actors_uses_unknown(self, mock_svc, _sleep) -> None:
        mock_service = MagicMock()
        mock_svc.return_value = mock_service

        activity = _api_activity()
        activity["actors"] = []
        mock_service.activity().query().execute.return_value = {
            "activities": [activity],
        }

        result = get_file_activities("doc123")
        assert result.activities[0].actor.name == "Unknown"
