"""Tests for do_share operation."""

from unittest.mock import patch, MagicMock, Mock

import httpx

from models import DoResult, MiseError, ErrorKind
from tools.share import do_share, VALID_ROLES


class TestDoShareValidation:
    """Parameter validation for share (runs before confirm check)."""

    def test_missing_file_id(self) -> None:
        result = do_share(to="alice@example.com")
        assert result["error"] is True
        assert "file_id" in result["message"]

    def test_missing_to(self) -> None:
        result = do_share(file_id="f1")
        assert result["error"] is True
        assert "to" in result["message"]

    def test_missing_both(self) -> None:
        result = do_share()
        assert result["error"] is True
        assert "file_id" in result["message"]
        assert "to" in result["message"]

    def test_empty_string_file_id(self) -> None:
        result = do_share(file_id="", to="alice@example.com")
        assert result["error"] is True

    def test_empty_string_to(self) -> None:
        result = do_share(file_id="f1", to="")
        assert result["error"] is True

    def test_invalid_role(self) -> None:
        result = do_share(file_id="f1", to="alice@example.com", role="owner")
        assert result["error"] is True
        assert "Invalid role" in result["message"]
        assert "owner" in result["message"]

    def test_whitespace_only_to(self) -> None:
        result = do_share(file_id="f1", to="  ,  , ")
        assert result["error"] is True
        assert "No valid email" in result["message"]


class TestDoSharePreview:
    """Preview mode (confirm=False, the default)."""

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_returns_preview_by_default(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Report", "webViewLink": "https://docs.google.com/d/f1",
        }

        result = do_share("f1", "alice@example.com")

        assert not isinstance(result, DoResult)
        assert result["preview"] is True
        assert result["operation"] == "share"
        assert result["file_id"] == "f1"
        assert result["title"] == "Report"
        assert "alice@example.com" in result["message"]
        assert "reader" in result["message"]
        assert result["shared_with"] == ["alice@example.com"]
        assert result["role"] == "reader"

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_preview_does_not_create_permissions(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        do_share("f1", "alice@example.com")

        # post_json should NOT have been called (no permissions created)
        # Only get_json for file metadata
        mock_client.post_json.assert_not_called()

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_preview_shows_explicit_role(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        result = do_share("f1", "alice@example.com", role="writer")

        assert result["preview"] is True
        assert result["role"] == "writer"
        assert "writer" in result["message"]

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_preview_with_multiple_emails(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        result = do_share("f1", "alice@example.com, bob@example.com")

        assert result["preview"] is True
        assert result["shared_with"] == ["alice@example.com", "bob@example.com"]

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_preview_includes_confirm_cue(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        result = do_share("f1", "alice@example.com")

        assert "confirm_required" in result["cues"]
        assert "confirm=True" in result["cues"]["confirm_required"]


class TestDoShareConfirmed:
    """Confirmed execution (confirm=True)."""

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_shares_file_with_confirm(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1",
            "name": "Report",
            "webViewLink": "https://docs.google.com/document/d/f1/edit",
        }

        result = do_share("f1", "alice@example.com", confirm=True)

        assert isinstance(result, DoResult)
        assert result.operation == "share"
        assert result.file_id == "f1"
        assert result.title == "Report"
        assert result.cues["role"] == "reader"
        assert result.cues["shared_with"] == ["alice@example.com"]

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_confirmed_with_explicit_role(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        result = do_share("f1", "alice@example.com", role="writer", confirm=True)

        assert isinstance(result, DoResult)
        assert result.cues["role"] == "writer"

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_confirmed_with_multiple_emails(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        result = do_share("f1", "alice@example.com, bob@example.com", confirm=True)

        assert isinstance(result, DoResult)
        assert result.cues["shared_with"] == ["alice@example.com", "bob@example.com"]
        # Two post_json calls for permissions
        assert mock_client.post_json.call_count >= 2

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_confirmed_creates_permission_with_correct_params(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        do_share("f1", "alice@example.com", role="commenter", confirm=True)

        # Verify post_json was called with correct permission body
        mock_client.post_json.assert_called_once()
        call_args = mock_client.post_json.call_args
        assert "/permissions" in call_args[0][0]
        assert call_args[1]["json_body"] == {
            "type": "user",
            "role": "commenter",
            "emailAddress": "alice@example.com",
        }
        assert call_args[1]["params"]["sendNotificationEmail"] == "false"
        assert call_args[1]["params"]["supportsAllDrives"] == "true"

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_none_role_defaults_to_reader(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        result = do_share("f1", "alice@example.com", role=None, confirm=True)
        assert result.cues["role"] == "reader"


class TestDoShareErrors:
    """Error handling for share."""

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_mise_error_returns_error_dict(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.side_effect = MiseError(
            ErrorKind.NOT_FOUND, "File not found"
        )

        result = do_share("f1", "alice@example.com", confirm=True)

        assert result["error"] is True
        assert result["kind"] == "not_found"
        assert "File not found" in result["message"]

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_mise_error_on_preview_also_returns_error(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.side_effect = MiseError(
            ErrorKind.NOT_FOUND, "File not found"
        )

        result = do_share("f1", "alice@example.com")

        assert result["error"] is True
        assert result["kind"] == "not_found"


class TestDoShareNotificationFallback:
    """Non-Google accounts require notification email fallback."""

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_falls_back_to_notification(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        # First post_json raises HTTPStatusError (invalidSharingRequest),
        # second (with notification) succeeds
        mock_response = Mock()
        mock_response.status_code = 400
        error = httpx.HTTPStatusError(
            "invalidSharingRequest", request=Mock(), response=mock_response
        )
        call_count = [0]

        def post_json_side_effect(*args, **kwargs):
            call_count[0] += 1
            params = kwargs.get("params", {})
            if params.get("sendNotificationEmail") == "false":
                raise error
            return {"id": "perm1"}

        mock_client.post_json.side_effect = post_json_side_effect

        result = do_share("f1", "alice@icloud.com", confirm=True)

        assert isinstance(result, DoResult)
        assert result.cues["shared_with"] == ["alice@icloud.com"]
        assert result.cues["notified"] == ["alice@icloud.com"]
        assert "notification_note" in result.cues

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_non_sharing_error_still_raises(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        # 403 error (not invalidSharingRequest) should propagate
        mock_response = Mock()
        mock_response.status_code = 403
        error = httpx.HTTPStatusError(
            "forbidden", request=Mock(), response=mock_response
        )
        mock_client.post_json.side_effect = error

        # Should propagate — caught by retry then raised as error
        result = do_share("f1", "alice@example.com", confirm=True)
        assert result["error"] is True

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_no_notification_cue_for_google_accounts(self, mock_get_client, _sleep) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        result = do_share("f1", "alice@example.com", confirm=True)

        assert isinstance(result, DoResult)
        assert "notified" not in result.cues
        assert "notification_note" not in result.cues


class TestValidRoles:
    """Role validation constants."""

    def test_valid_roles_contains_expected(self) -> None:
        assert VALID_ROLES == {"reader", "writer", "commenter"}

    def test_valid_roles_is_frozenset(self) -> None:
        assert isinstance(VALID_ROLES, frozenset)
