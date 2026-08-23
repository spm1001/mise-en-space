"""The colleague-diary detail lane (mise-wavotu): validation, detail fields,
room-hold tell, and the honest ACL note."""
from unittest.mock import patch

import pytest

from models import CalendarEvent, CalendarSearchResult
from tools.search import do_search
from tools.search_calendar import (
    calendar_acl_note,
    format_calendar_result,
    validate_calendar_id,
)


def _event(**kw):
    base = dict(
        event_id="e1", summary="Clear inboxes",
        start_time="2026-08-27T08:00:00+01:00", end_time="2026-08-27T09:00:00+01:00",
    )
    base.update(kw)
    return CalendarEvent(**base)


class TestValidation:
    def test_non_email_refused(self):
        with pytest.raises(ValueError, match="doesn't look like a calendar"):
            validate_calendar_id("stef", None, None)

    def test_drive_scoping_params_refused(self):
        with pytest.raises(ValueError, match="cannot combine"):
            validate_calendar_id("stef@itv.com", "1abcFolder", None)
        with pytest.raises(ValueError, match="cannot combine"):
            validate_calendar_id("stef@itv.com", None, "name contains 'x'")

    def test_email_and_primary_pass(self):
        validate_calendar_id("stef@itv.com", None, None)
        validate_calendar_id("primary", None, None)


class TestDetailFields:
    def test_transparent_event_carries_transparency(self):
        r = format_calendar_result(_event(transparency="transparent"))
        assert r["transparency"] == "transparent"

    def test_opaque_default_emits_nothing(self):
        r = format_calendar_result(_event())
        assert "transparency" not in r
        assert "event_type" not in r
        assert "room_hold" not in r

    def test_non_default_event_type_carried(self):
        r = format_calendar_result(_event(event_type="outOfOffice"))
        assert r["event_type"] == "outOfOffice"

    def test_room_hold_tell(self):
        r = format_calendar_result(
            _event(organizer_email="c_188.._x@resource.calendar.google.com")
        )
        assert r["room_hold"] is True

    def test_human_organizer_is_not_a_room(self):
        r = format_calendar_result(_event(organizer_email="stef@itv.com"))
        assert "room_hold" not in r


class TestSearchWiring:
    @patch("tools.search.list_events")
    def test_calendar_id_forces_calendar_source_and_passes_through(self, mock_list, tmp_path):
        mock_list.return_value = CalendarSearchResult(events=[_event()])
        result = do_search(
            query="", sources=None, base_path=tmp_path,
            calendar_id="stef@itv.com",
        )
        assert mock_list.call_count == 1
        assert mock_list.call_args.kwargs["calendar_id"] == "stef@itv.com"
        assert result.calendar_results
        assert "calendar_id" in result.cues
        # drive/gmail were not searched: forcing worked
        assert result.drive_results == [] and result.gmail_results == []

    @patch("tools.search.list_events")
    def test_acl_refusal_gets_honest_note(self, mock_list, tmp_path):
        from models import ErrorKind, MiseError
        mock_list.side_effect = MiseError(
            kind=ErrorKind.NOT_FOUND, message="Not Found", retryable=False,
        )
        result = do_search(
            query="", sources=None, base_path=tmp_path,
            calendar_id="stef@itv.com",
        )
        assert any("free/busy-only" in e for e in result.errors)

    def test_calendar_id_with_folder_id_refused(self, tmp_path):
        with pytest.raises(ValueError, match="cannot combine"):
            do_search(query="x", base_path=tmp_path,
                      calendar_id="stef@itv.com", folder_id="1abcFolder")


def test_acl_note_names_freebusy():
    note = calendar_acl_note("stef@itv.com")
    assert "freebusy" in note and "stef@itv.com" in note
