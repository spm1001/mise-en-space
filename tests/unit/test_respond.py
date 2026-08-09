"""Unit tests for do(respond) — RSVP a calendar invite (mise-gepiwe)."""

from unittest.mock import MagicMock, patch

from models import DoResult, ErrorKind, MiseError
from tools.respond import do_respond


def _event(**overrides) -> dict:
    ev = {
        "id": "evt123",
        "summary": "Sameer / Sameer",
        "status": "confirmed",
        "htmlLink": "https://calendar.google.com/event?eid=abc",
        "start": {"dateTime": "2026-08-09T10:00:00+01:00"},
        "attendees": [
            {"email": "organiser@planetmodha.com", "organizer": True, "responseStatus": "accepted"},
            {"email": "me@itv.com", "self": True, "responseStatus": "needsAction"},
        ],
    }
    ev.update(overrides)
    return ev


class TestActionValidation:
    def test_unknown_action_teaches_the_three(self) -> None:
        result = do_respond(file_id="evt123", action="yes")
        assert result["error"] is True
        assert "accept, decline, tentative" in result["message"]

    def test_none_action_same_error(self) -> None:
        result = do_respond(file_id="evt123", action=None)
        assert result["error"] is True


class TestEventIdRoute:
    @patch("tools.respond.respond_to_event")
    @patch("tools.respond.get_event")
    def test_accept_flips_status(self, mock_get, mock_respond) -> None:
        mock_get.return_value = _event()
        result = do_respond(file_id="evt123", action="accept")
        assert isinstance(result, DoResult)
        assert result.cues["my_response"] == "accepted"
        assert result.cues["event_summary"] == "Sameer / Sameer"
        mock_respond.assert_called_once()
        assert mock_respond.call_args[0][1] == "accepted"

    @patch("tools.respond.respond_to_event")
    @patch("tools.respond.get_event")
    def test_decline_and_tentative_map(self, mock_get, mock_respond) -> None:
        mock_get.return_value = _event()
        do_respond(file_id="evt123", action="decline")
        assert mock_respond.call_args[0][1] == "declined"
        do_respond(file_id="evt123", action="tentative")
        assert mock_respond.call_args[0][1] == "tentative"

    @patch("tools.respond.get_event")
    def test_cancelled_event_refuses(self, mock_get) -> None:
        mock_get.return_value = _event(status="cancelled")
        result = do_respond(file_id="evt123", action="accept")
        assert result["error"] is True
        assert "CANCELLED" in result["message"]

    @patch("tools.respond.respond_to_event", side_effect=ValueError("No self attendee on this event"))
    @patch("tools.respond.get_event")
    def test_no_self_attendee_is_invalid_input(self, mock_get, mock_respond) -> None:
        mock_get.return_value = _event(attendees=[])
        result = do_respond(file_id="evt123", action="accept")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"

    @patch("tools.respond.get_event",
           side_effect=MiseError(ErrorKind.NOT_FOUND, "404"))
    def test_unknown_event_id_teaches_both_id_kinds(self, mock_get) -> None:
        result = do_respond(file_id="evt123", action="accept")
        assert result["error"] is True
        assert "calendar event" in result["message"]
        assert "thread id" in result["message"] or "event id" in result["message"]

    @patch("tools.respond.get_event",
           side_effect=MiseError(ErrorKind.PERMISSION_DENIED, "insufficient scope"))
    def test_permission_denied_teaches_reauth(self, mock_get) -> None:
        result = do_respond(file_id="evt123", action="accept")
        assert result["error"] is True
        assert "setup_oauth" in result["message"]


class TestThreadRoute:
    """16-hex file_id resolves through the invite's ICS to the live event."""

    _THREAD_ID = "19fe5407daef309a"
    _ICS = "BEGIN:VCALENDAR\nUID:probe-uid-123\nEND:VCALENDAR"

    def _thread_with_invite(self) -> MagicMock:
        msg = MagicMock()
        msg.calendar_attachments = [MagicMock(mime_type="text/calendar")]
        thread = MagicMock()
        thread.messages = [msg]
        return thread

    @patch("tools.respond.respond_to_event")
    @patch("tools.respond.find_event_by_ical_uid")
    @patch("tools.fetch.gmail_attachments._download_attachment_bytes")
    @patch("tools.respond.fetch_thread")
    def test_thread_resolves_and_discloses(
        self, mock_thread, mock_download, mock_find, mock_respond,
    ) -> None:
        mock_thread.return_value = self._thread_with_invite()
        mock_download.return_value = self._ICS.encode()
        mock_find.return_value = _event()

        result = do_respond(file_id=self._THREAD_ID, action="accept")

        assert isinstance(result, DoResult)
        mock_find.assert_called_once_with("probe-uid-123")
        assert result.cues["resolved_from_thread"] == self._THREAD_ID
        assert result.cues["ical_uid"] == "probe-uid-123"

    @patch("tools.respond.fetch_thread")
    def test_thread_without_invite_teaches(self, mock_thread) -> None:
        msg = MagicMock()
        msg.calendar_attachments = []
        thread = MagicMock()
        thread.messages = [msg]
        mock_thread.return_value = thread

        result = do_respond(file_id=self._THREAD_ID, action="accept")
        assert result["error"] is True
        assert "no calendar invite" in result["message"]

    @patch("tools.respond.find_event_by_ical_uid", return_value=None)
    @patch("tools.fetch.gmail_attachments._download_attachment_bytes")
    @patch("tools.respond.fetch_thread")
    def test_invite_not_on_calendar_teaches(
        self, mock_thread, mock_download, mock_find,
    ) -> None:
        mock_thread.return_value = self._thread_with_invite()
        mock_download.return_value = self._ICS.encode()

        result = do_respond(file_id=self._THREAD_ID, action="accept")
        assert result["error"] is True
        assert "never added" in result["message"]

    @patch("tools.respond.respond_to_event")
    @patch("tools.respond.find_event_by_ical_uid")
    @patch("tools.fetch.gmail_attachments._download_attachment_bytes")
    @patch("tools.respond.fetch_thread")
    def test_newest_invite_wins(
        self, mock_thread, mock_download, mock_find, mock_respond,
    ) -> None:
        """Cancel-and-recreate threads: the LATEST invite names the live meeting.

        threads.get returns messages oldest-first; resolving the first ICS
        found would pick the dead event's UID and refuse on a cancellation
        while a live invite sits later in the thread.
        """
        old_msg = MagicMock()
        old_msg.calendar_attachments = [MagicMock(mime_type="text/calendar")]
        new_msg = MagicMock()
        new_msg.calendar_attachments = [MagicMock(mime_type="text/calendar")]
        thread = MagicMock()
        thread.messages = [old_msg, new_msg]  # oldest first, as the API returns
        mock_thread.return_value = thread
        mock_download.return_value = b"BEGIN:VCALENDAR\nUID:uid-newest\nEND:VCALENDAR"
        mock_find.return_value = _event()

        result = do_respond(file_id=self._THREAD_ID, action="accept")

        assert isinstance(result, DoResult)
        # The newest message's ICS was the one downloaded and resolved.
        assert mock_download.call_args[0][0] is new_msg
        mock_find.assert_called_once_with("uid-newest")


class TestRemoteExclusion:
    def test_respond_not_in_remote_allowed_ops(self) -> None:
        """An RSVP is organiser-visible — deliberately excluded from remote."""
        from tools.remote import REMOTE_ALLOWED_OPS
        assert "respond" not in REMOTE_ALLOWED_OPS
