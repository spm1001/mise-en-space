"""The colleague-diary detail lane (mise-wavotu): validation, detail fields,
room-hold tell, and the honest ACL note."""
from unittest.mock import patch

import pytest

from models import CalendarEvent, CalendarSearchResult, ErrorKind, MiseError
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


class TestFanOutWiring:
    """The default calendar search reads every calendar in the list
    (mise-cegeva) and says which. Adapter seams: adapters.calendar_list's
    own list_calendars/list_events, so do_search → list_all_events runs real."""

    _TWO = [
        {"id": "primary", "summary": "Sameer Modha", "primary": True},
        {"id": "family@planetmodha.com", "summary": "Family", "primary": False},
    ]

    @patch("adapters.calendar_list.list_events")
    @patch("adapters.calendar_list.list_calendars")
    def test_event_only_on_a_shared_calendar_is_found(self, mock_cals, mock_events, tmp_path):
        """The card's regression: 'Go for a run' lived on the family calendar
        and a primary-only search read it as no events."""
        mock_cals.return_value = list(self._TWO)
        mock_events.side_effect = lambda **kw: CalendarSearchResult(
            events=[_event(event_id="run1", summary="Go for a run Sameer")]
            if kw["calendar_id"] == "family@planetmodha.com" else [])

        result = do_search(query="run", sources=["calendar"], base_path=tmp_path)

        assert [r["summary"] for r in result.calendar_results] == ["Go for a run Sameer"]
        cue = result.cues["calendars_read"]
        assert "Sameer Modha" in cue and "Family (family@planetmodha.com)" in cue
        assert "2 calendars" in cue
        assert "calendar_scope" not in result.cues

    @patch("adapters.calendar_list.list_events")
    @patch("adapters.calendar_list.list_calendars",
           side_effect=MiseError(ErrorKind.PERMISSION_DENIED, "insufficient authentication scopes"))
    def test_pre_scope_token_gets_primary_and_the_reconsent_cue(self, _cals, mock_events, tmp_path):
        mock_events.return_value = CalendarSearchResult(events=[_event()])

        result = do_search(query="", sources=["calendar"], base_path=tmp_path)

        assert len(result.calendar_results) == 1  # primary still answered
        assert result.errors == []                 # degraded, not failed
        scope = result.cues["calendar_scope"]
        assert "calendar.readonly" in scope and "setup_oauth" in scope and "force=True" in scope
        assert "insufficient authentication scopes" in scope
        assert "primary" in result.cues["calendars_read"]

    @patch("tools.search.list_all_events")
    @patch("tools.search.list_events")
    def test_named_primary_reads_one_calendar_not_the_list(self, mock_one, mock_all, tmp_path):
        mock_one.return_value = CalendarSearchResult(events=[_event()])
        result = do_search(query="", sources=None, base_path=tmp_path, calendar_id="primary")
        mock_all.assert_not_called()
        assert mock_one.call_args.kwargs["calendar_id"] == "primary"
        assert "calendars_read" not in result.cues and "calendar_id" in result.cues

    @patch("adapters.calendar_list.list_events")
    @patch("adapters.calendar_list.list_calendars")
    def test_a_calendar_that_refuses_is_a_warning_not_a_failure(self, mock_cals, mock_events, tmp_path):
        mock_cals.return_value = list(self._TWO)
        def per_calendar(**kw):
            if kw["calendar_id"] == "family@planetmodha.com":
                raise MiseError(ErrorKind.NOT_FOUND, "Not Found")
            return CalendarSearchResult(events=[_event()])
        mock_events.side_effect = per_calendar
        result = do_search(query="", sources=["calendar"], base_path=tmp_path)
        assert len(result.calendar_results) == 1 and result.errors == []
        assert any("family@planetmodha.com" in w for w in result.cues["calendar_warnings"])
        assert "Family" not in result.cues["calendars_read"]
