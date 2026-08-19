"""Unit tests for do(create_event) — book a calendar event (mise-rijeco)."""

from unittest.mock import patch

from models import DoResult, ErrorKind, MiseError
from tools.create_event import do_create_event


def _created(**overrides) -> dict:
    ev = {
        "id": "new_evt_1",
        "summary": "LSM catch-up",
        "htmlLink": "https://calendar.google.com/event?eid=new",
        "status": "confirmed",
    }
    ev.update(overrides)
    return ev


_TZ = "tools.events_util.resolve_calendar_timezone"


class TestConfirmGate:
    """Attendees mean other people's diaries — gate fires; solo books direct."""

    @patch("tools.create_event.clash_summaries", return_value=["Standup (09:00 – 09:15)"])
    @patch("tools.create_event.insert_event")
    @patch(_TZ, return_value="Europe/London")
    def test_attendees_without_confirm_previews(self, _tz, mock_insert, _clash) -> None:
        result = do_create_event(
            title="LSM catch-up",
            time_min="2026-09-08T14:00", time_max="2026-09-08T14:30",
            attendees=["a@itv.com", "b@itv.com"],
        )
        assert result["preview"] is True
        assert result["clashes"] == ["Standup (09:00 – 09:15)"]
        assert "confirm=True" in result["cues"]["confirm_required"]
        mock_insert.assert_not_called()

    @patch("tools.create_event.insert_event", return_value=_created())
    @patch(_TZ, return_value="Europe/London")
    def test_solo_event_books_directly(self, _tz, mock_insert) -> None:
        result = do_create_event(
            title="Focus block",
            time_min="2026-09-08T14:00", time_max="2026-09-08T15:00",
        )
        assert isinstance(result, DoResult)
        mock_insert.assert_called_once()

    @patch("tools.create_event.insert_event", return_value=_created())
    @patch(_TZ, return_value="Europe/London")
    def test_confirm_books_and_invites_all(self, _tz, mock_insert) -> None:
        result = do_create_event(
            title="LSM catch-up",
            time_min="2026-09-08T14:00", time_max="2026-09-08T14:30",
            attendees=["a@itv.com"], confirm=True,
        )
        assert isinstance(result, DoResult)
        body = mock_insert.call_args[0][0]
        assert body["attendees"] == [{"email": "a@itv.com"}]
        # Invite-first: send_updates defaults to 'all'
        assert mock_insert.call_args.kwargs["send_updates"] == "all"
        assert result.cues["attendees_invited"] == ["a@itv.com"]
        assert "emailed" in result.cues["attendees_notified"]

    @patch("tools.create_event.insert_event", return_value=_created())
    @patch(_TZ, return_value="Europe/London")
    def test_send_updates_none_is_cued_loudly(self, _tz, mock_insert) -> None:
        result = do_create_event(
            title="Quiet hold",
            time_min="2026-09-08T14:00", time_max="2026-09-08T14:30",
            attendees=["a@itv.com"], confirm=True, send_updates="none",
        )
        assert "NO invite emails" in result.cues["attendees_notified"]


class TestTimeHandling:
    @patch("tools.create_event.insert_event", return_value=_created())
    @patch(_TZ, return_value="Europe/London")
    def test_naive_datetime_gets_user_timezone(self, _tz, mock_insert) -> None:
        do_create_event(
            title="X", time_min="2026-09-08T14:00", time_max="2026-09-08T14:30",
        )
        body = mock_insert.call_args[0][0]
        assert body["start"] == {"dateTime": "2026-09-08T14:00", "timeZone": "Europe/London"}

    @patch("tools.create_event.insert_event", return_value=_created())
    @patch(_TZ, return_value=None)
    def test_no_resolvable_timezone_warns_utc(self, _tz, mock_insert) -> None:
        result = do_create_event(
            title="X", time_min="2026-09-08T14:00", time_max="2026-09-08T14:30",
        )
        assert any("UTC" in w for w in result.cues["warnings"])

    @patch("tools.create_event.insert_event", return_value=_created())
    @patch(_TZ, return_value="Europe/London")
    def test_two_bare_dates_make_all_day(self, _tz, mock_insert) -> None:
        do_create_event(title="Offsite", time_min="2026-09-08", time_max="2026-09-09")
        body = mock_insert.call_args[0][0]
        assert body["start"] == {"date": "2026-09-08"}
        assert body["end"] == {"date": "2026-09-09"}

    @patch(_TZ, return_value="Europe/London")
    def test_mixed_kinds_refused(self, _tz) -> None:
        result = do_create_event(
            title="X", time_min="2026-09-08", time_max="2026-09-08T15:00",
        )
        assert result["error"] is True
        assert "same kind" in result["message"]

    @patch(_TZ, return_value="Europe/London")
    def test_end_before_start_refused(self, _tz) -> None:
        result = do_create_event(
            title="X", time_min="2026-09-08T15:00", time_max="2026-09-08T14:00",
        )
        assert result["error"] is True
        assert "after" in result["message"]


class TestRecurrence:
    @patch("tools.create_event.insert_event",
           return_value=_created(recurrence=["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"]))
    @patch(_TZ, return_value="Europe/London")
    def test_rrule_rides_with_timezone(self, _tz, mock_insert) -> None:
        # 2026-09-08 is a Tuesday — no mismatch warning expected
        result = do_create_event(
            title="1:1", time_min="2026-09-08T10:00", time_max="2026-09-08T10:30",
            recurrence="RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU",
        )
        body = mock_insert.call_args[0][0]
        assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"]
        # Recurring events carry an IANA zone — the DST invariant
        assert body["start"]["timeZone"] == "Europe/London"
        assert result.cues["recurrence"]
        assert not any("BYDAY" in w for w in result.cues["warnings"])

    @patch(_TZ, return_value="Europe/London")
    def test_junk_recurrence_teaches(self, _tz) -> None:
        result = do_create_event(
            title="X", time_min="2026-09-08T10:00", time_max="2026-09-08T10:30",
            recurrence="every other tuesday",
        )
        assert result["error"] is True
        assert "RRULE" in result["message"]

    @patch("tools.create_event.insert_event", return_value=_created())
    @patch(_TZ, return_value="Europe/London")
    def test_byday_mismatch_warns_stray_instance(self, _tz, mock_insert) -> None:
        # 2026-09-10 is a Thursday; BYDAY=WE — probed 2026-08-19: DTSTART
        # survives as an extra instance outside the pattern.
        result = do_create_event(
            title="X", time_min="2026-09-10T10:00", time_max="2026-09-10T10:30",
            recurrence="RRULE:FREQ=WEEKLY;BYDAY=WE",
        )
        assert any("EXTRA instance" in w for w in result.cues["warnings"])


class TestExtras:
    @patch("tools.events_util.get_file_metadata",
           return_value={"name": "1:1 doc", "mimeType": "application/vnd.google-apps.document"})
    @patch("tools.create_event.insert_event",
           return_value=_created(attachments=[{"title": "1:1 doc", "fileId": "f1"}]))
    @patch(_TZ, return_value="Europe/London")
    def test_include_becomes_attachments(self, _tz, mock_insert, mock_meta) -> None:
        result = do_create_event(
            title="X", time_min="2026-09-08T10:00", time_max="2026-09-08T10:30",
            include=["f1"],
        )
        body = mock_insert.call_args[0][0]
        assert body["attachments"][0]["fileUrl"].endswith("id=f1")
        assert body["attachments"][0]["title"] == "1:1 doc"
        assert result.cues["attachments"] == ["1:1 doc"]

    @patch("tools.create_event.insert_event", return_value=_created(
        conferenceData={"entryPoints": [
            {"entryPointType": "video", "uri": "https://meet.google.com/abc"},
        ]},
    ))
    @patch(_TZ, return_value="Europe/London")
    def test_meet_mints_fresh_request(self, _tz, mock_insert) -> None:
        result = do_create_event(
            title="X", time_min="2026-09-08T10:00", time_max="2026-09-08T10:30",
            meet=True,
        )
        body = mock_insert.call_args[0][0]
        assert "createRequest" in body["conferenceData"]
        assert result.cues["meet_link"] == "https://meet.google.com/abc"

    @patch(_TZ, return_value="Europe/London")
    def test_non_email_attendee_refused(self, _tz) -> None:
        result = do_create_event(
            title="X", time_min="2026-09-08T10:00", time_max="2026-09-08T10:30",
            attendees=["not-an-email"],
        )
        assert result["error"] is True


class TestErrorPaths:
    @patch("tools.create_event.insert_event",
           side_effect=MiseError(ErrorKind.PERMISSION_DENIED, "insufficient scope"))
    @patch(_TZ, return_value="Europe/London")
    def test_permission_denied_teaches_reauth(self, _tz, mock_insert) -> None:
        result = do_create_event(
            title="X", time_min="2026-09-08T10:00", time_max="2026-09-08T10:30",
        )
        assert result["error"] is True
        assert "setup_oauth" in result["message"]


class TestDeliberateBoundaries:
    def test_create_event_not_in_remote_allowed_ops(self) -> None:
        """Booking meetings is organiser-visible mutation — excluded from remote."""
        from tools.remote import REMOTE_ALLOWED_OPS
        assert "create_event" not in REMOTE_ALLOWED_OPS

    def test_calendar_ops_refuse_in_ambient_mode(self) -> None:
        """A service account has no personal calendar or diary peers."""
        from tools.dispatch import AMBIENT_UNAVAILABLE_OPS
        assert {"create_event", "update_event", "freebusy"} <= AMBIENT_UNAVAILABLE_OPS
