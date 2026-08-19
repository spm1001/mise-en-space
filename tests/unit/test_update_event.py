"""Unit tests for do(update_event) — edit a calendar event (mise-rijeco)."""

from unittest.mock import patch

from models import DoResult, ErrorKind, MiseError
from tools.update_event import do_update_event


def _event(**overrides) -> dict:
    ev = {
        "id": "evt123",
        "summary": "Next Action AI Follow ups",
        "status": "confirmed",
        "htmlLink": "https://calendar.google.com/event?eid=abc",
        "description": "old agenda",
        "start": {"dateTime": "2026-08-27T14:00:00+01:00"},
        "end": {"dateTime": "2026-08-27T15:00:00+01:00"},
        "organizer": {"email": "me@itv.com", "self": True},
        "attendees": [
            {"email": "me@itv.com", "self": True, "responseStatus": "accepted"},
            {"email": "colleague@itv.com", "responseStatus": "needsAction"},
        ],
    }
    ev.update(overrides)
    return ev


_TZ = "tools.events_util.resolve_calendar_timezone"


class TestGateGrain:
    """Structural changes gate; cosmetic changes run direct and quiet."""

    @patch("tools.update_event.patch_event")
    @patch("tools.update_event.get_event")
    def test_attendee_add_without_confirm_previews(self, mock_get, mock_patch) -> None:
        mock_get.return_value = _event()
        result = do_update_event(file_id="evt123", attendees=["new@itv.com"])
        assert result["preview"] is True
        assert result["changes"]["attendees_to_add"] == ["new@itv.com"]
        mock_patch.assert_not_called()

    @patch("tools.update_event.patch_event", return_value=_event(description="new agenda"))
    @patch("tools.update_event.get_event")
    def test_description_edit_runs_direct_and_quiet(self, mock_get, mock_patch) -> None:
        mock_get.return_value = _event()
        result = do_update_event(file_id="evt123", content="new agenda")
        assert isinstance(result, DoResult)
        assert mock_patch.call_args.kwargs["send_updates"] == "none"
        body = mock_patch.call_args[0][1]
        assert body == {"description": "new agenda"}
        # Events have no version history — previous IS the restore point
        assert result.cues["previous"]["description"] == "old agenda"

    @patch("tools.update_event.patch_event", return_value=_event())
    @patch("tools.update_event.get_event")
    def test_confirmed_attendee_add_merges_wholesale(self, mock_get, mock_patch) -> None:
        mock_get.return_value = _event()
        result = do_update_event(
            file_id="evt123", attendees=["new@itv.com"], confirm=True,
        )
        assert isinstance(result, DoResult)
        body = mock_patch.call_args[0][1]
        # Patch replaces arrays WHOLESALE — existing attendees must ride along
        emails = [a["email"] for a in body["attendees"]]
        assert emails == ["me@itv.com", "colleague@itv.com", "new@itv.com"]
        assert mock_patch.call_args.kwargs["send_updates"] == "all"
        assert result.cues["attendees_added"] == ["new@itv.com"]

    @patch("tools.update_event.patch_event", return_value=_event())
    @patch("tools.update_event.get_event")
    def test_already_present_attendee_warns_not_patches(self, mock_get, mock_patch) -> None:
        mock_get.return_value = _event()
        result = do_update_event(
            file_id="evt123", attendees=["colleague@itv.com"], confirm=True,
        )
        assert result["error"] is True
        assert "already on the event" in result["message"]
        mock_patch.assert_not_called()

    @patch(_TZ, return_value="Europe/London")
    @patch("tools.update_event.patch_event", return_value=_event())
    @patch("tools.update_event.get_event")
    def test_time_move_gates_then_records_previous(
        self, mock_get, mock_patch, _tz,
    ) -> None:
        mock_get.return_value = _event()
        preview = do_update_event(
            file_id="evt123",
            time_min="2026-08-28T14:00", time_max="2026-08-28T15:00",
        )
        assert preview["preview"] is True
        result = do_update_event(
            file_id="evt123",
            time_min="2026-08-28T14:00", time_max="2026-08-28T15:00",
            confirm=True,
        )
        assert isinstance(result, DoResult)
        assert result.cues["previous"]["start"] == {"dateTime": "2026-08-27T14:00:00+01:00"}

    @patch("tools.update_event.get_event")
    def test_single_time_bound_refused(self, mock_get) -> None:
        result = do_update_event(file_id="evt123", time_min="2026-08-28T14:00")
        assert result["error"] is True
        assert "BOTH" in result["message"]

    @patch(_TZ, return_value="Europe/London")
    @patch("tools.update_event.patch_event",
           return_value=_event(recurrence=["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TH"]))
    @patch("tools.update_event.get_event")
    def test_recurrence_converts_single_to_series(
        self, mock_get, mock_patch, _tz,
    ) -> None:
        # Probed live 2026-08-19: patching recurrence onto a single event
        # genuinely converts it (instances listed).
        mock_get.return_value = _event()
        result = do_update_event(
            file_id="evt123",
            recurrence="RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TH",
            confirm=True,
        )
        assert isinstance(result, DoResult)
        body = mock_patch.call_args[0][1]
        assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TH"]
        assert result.cues["previous"]["recurrence"] is None

    @patch("tools.update_event.get_event")
    def test_nothing_to_change_teaches_the_params(self, mock_get) -> None:
        mock_get.return_value = _event()
        result = do_update_event(file_id="evt123")
        assert result["error"] is True
        assert "content" in result["message"] and "attendees" in result["message"]


class TestGuestBoundary:
    @patch("tools.update_event.patch_event")
    @patch("tools.update_event.get_event")
    def test_guest_cannot_reshape(self, mock_get, mock_patch) -> None:
        mock_get.return_value = _event(
            organizer={"email": "boss@itv.com", "self": False},
        )
        result = do_update_event(file_id="evt123", content="my agenda")
        assert result["error"] is True
        assert "guest" in result["message"]
        assert "respond" in result["message"]
        mock_patch.assert_not_called()

    @patch("tools.update_event.patch_event", return_value=_event())
    @patch("tools.update_event.get_event")
    def test_guest_may_add_attendees_by_default(self, mock_get, mock_patch) -> None:
        # guestsCanInviteOthers defaults true when absent
        mock_get.return_value = _event(
            organizer={"email": "boss@itv.com", "self": False},
        )
        result = do_update_event(
            file_id="evt123", attendees=["new@itv.com"], confirm=True,
        )
        assert isinstance(result, DoResult)

    @patch("tools.update_event.patch_event")
    @patch("tools.update_event.get_event")
    def test_guest_attendee_add_blocked_when_disallowed(self, mock_get, mock_patch) -> None:
        mock_get.return_value = _event(
            organizer={"email": "boss@itv.com", "self": False},
            guestsCanInviteOthers=False,
        )
        result = do_update_event(
            file_id="evt123", attendees=["new@itv.com"], confirm=True,
        )
        assert result["error"] is True
        mock_patch.assert_not_called()

    @patch("tools.update_event.patch_event", return_value=_event())
    @patch("tools.update_event.get_event")
    def test_guests_can_modify_opens_the_door(self, mock_get, mock_patch) -> None:
        mock_get.return_value = _event(
            organizer={"email": "boss@itv.com", "self": False},
            guestsCanModify=True,
        )
        result = do_update_event(file_id="evt123", content="shared agenda")
        assert isinstance(result, DoResult)


class TestErrorPaths:
    @patch("tools.update_event.get_event")
    def test_cancelled_event_refuses(self, mock_get) -> None:
        mock_get.return_value = _event(status="cancelled")
        result = do_update_event(file_id="evt123", content="x")
        assert result["error"] is True
        assert "CANCELLED" in result["message"]

    @patch("tools.update_event.get_event",
           side_effect=MiseError(ErrorKind.NOT_FOUND, "404"))
    def test_unknown_id_teaches_both_id_kinds(self, mock_get) -> None:
        result = do_update_event(file_id="evt123", content="x")
        assert result["error"] is True
        assert "thread id" in result["message"] or "event id" in result["message"]

    @patch("tools.update_event.get_event",
           side_effect=MiseError(ErrorKind.PERMISSION_DENIED, "insufficient scope"))
    def test_permission_denied_teaches_reauth(self, mock_get) -> None:
        result = do_update_event(file_id="evt123", content="x")
        assert result["error"] is True
        assert "setup_oauth" in result["message"]

    def test_update_event_not_in_remote_allowed_ops(self) -> None:
        from tools.remote import REMOTE_ALLOWED_OPS
        assert "update_event" not in REMOTE_ALLOWED_OPS


class TestProperties:
    """properties= is a COSMETIC change: quiet, no gate, merge-not-replace."""

    @patch("tools.update_event.patch_event", return_value=_event(
        extendedProperties={"private": {
            "mise:minted_by": "mise", "mise:programme": "1to1-2026",
        }},
    ))
    @patch("tools.update_event.get_event")
    def test_properties_run_direct_sending_only_new_keys(self, mock_get, mock_patch) -> None:
        """Patch MERGES the private map per-key (probed 2026-08-19) — the
        backfill route for events booked before stamping shipped."""
        mock_get.return_value = _event(
            extendedProperties={"private": {"mise:minted_by": "mise"}},
        )
        result = do_update_event(
            file_id="evt123", properties={"mise:programme": "1to1-2026"},
        )
        assert isinstance(result, DoResult)
        assert mock_patch.call_args.kwargs["send_updates"] == "none"
        body = mock_patch.call_args[0][1]
        assert body == {"extendedProperties": {"private": {"mise:programme": "1to1-2026"}}}
        assert "properties" in result.cues["changed"]
        # Read-back shows the merged map — stamps survived beside the new key
        assert result.cues["properties"]["mise:minted_by"] == "mise"

    @patch("tools.update_event.patch_event", return_value=_event())
    @patch("tools.update_event.get_event")
    def test_overwritten_keys_recorded_as_previous(self, mock_get, mock_patch) -> None:
        mock_get.return_value = _event(
            extendedProperties={"private": {"mise:programme": "old-prog"}},
        )
        result = do_update_event(
            file_id="evt123", properties={"mise:programme": "new-prog"},
        )
        assert isinstance(result, DoResult)
        assert result.cues["previous"]["properties"] == {"mise:programme": "old-prog"}

    @patch("tools.update_event.get_event")
    def test_equals_in_key_refused(self, mock_get) -> None:
        mock_get.return_value = _event()
        result = do_update_event(file_id="evt123", properties={"a=b": "x"})
        assert result["error"] is True
        assert "privateExtendedProperty" in result["message"]
