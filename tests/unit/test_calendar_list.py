"""The calendar list and the fan-out over it (mise-cegeva): which calendars an
account can see, a merged read across them, and the two degraded paths — a
token without calendar.readonly, and one calendar refusing while the rest
answer. Mocked at the HTTP client, like test_calendar.py."""
from unittest.mock import MagicMock, patch

from adapters.calendar_list import list_all_events, list_calendars
from models import CalendarSearchResult, ErrorKind, MiseError
from oauth_config import SCOPES


def _cal(cal_id, summary=None, primary=False, override=None):
    item = {"id": cal_id, "summary": summary or cal_id}
    if primary:
        item["primary"] = True
    if override:
        item["summaryOverride"] = override
    return item


def _api_event(event_id, summary="Evt", start="2026-09-15T10:00:00Z", end="2026-09-15T11:00:00Z"):
    return {"id": event_id, "summary": summary,
            "start": {"dateTime": start}, "end": {"dateTime": end}}


def test_scope_list_carries_calendar_readonly():
    """calendar.events reads a calendar you can NAME; the list needs this one."""
    assert "https://www.googleapis.com/auth/calendar.readonly" in SCOPES


class TestListCalendars:
    @patch("retry.time.sleep")
    @patch("adapters.calendar_list.get_sync_client")
    def test_primary_first_and_user_rename_wins(self, mock_get_client, _sleep):
        client = MagicMock(); mock_get_client.return_value = client
        client.get_json.return_value = {"items": [
            _cal("family@planetmodha.com", "Family", override="Home"),
            _cal("me@itv.com", "me@itv.com", primary=True),
        ]}
        cals = list_calendars()
        assert [c["id"] for c in cals] == ["me@itv.com", "family@planetmodha.com"]
        assert cals[0]["primary"] is True
        assert cals[1]["summary"] == "Home"
        params = client.get_json.call_args.kwargs["params"]
        assert params["minAccessRole"] == "reader"  # free/busy-only shares 404 on events

    @patch("retry.time.sleep")
    @patch("adapters.calendar_list.get_sync_client")
    def test_pagination_follows_token(self, mock_get_client, _sleep):
        client = MagicMock(); mock_get_client.return_value = client
        client.get_json.side_effect = [
            {"items": [_cal("a@x.com")], "nextPageToken": "p2"},
            {"items": [_cal("b@x.com")]},
        ]
        assert [c["id"] for c in list_calendars()] == ["a@x.com", "b@x.com"]
        assert client.get_json.call_args_list[1].kwargs["params"]["pageToken"] == "p2"


class TestListAllEvents:
    """list_calendars and list_events are patched at the module's own seam."""

    @patch("adapters.calendar_list.list_events")
    @patch("adapters.calendar_list.list_calendars")
    def test_fans_out_merges_and_names_every_calendar(self, mock_cals, mock_events):
        mock_cals.return_value = [
            {"id": "primary", "summary": "Me", "primary": True},
            {"id": "family@planetmodha.com", "summary": "Family", "primary": False},
        ]
        from adapters.calendar import _parse_event
        def per_calendar(**kw):
            if kw["calendar_id"] == "primary":
                return CalendarSearchResult(events=[])
            return CalendarSearchResult(events=[_parse_event(_api_event("run1", "Go for a run"))])
        mock_events.side_effect = per_calendar

        result = list_all_events(query="run")

        assert [e.summary for e in result.events] == ["Go for a run"]
        assert [c["id"] for c in result.calendars] == ["primary", "family@planetmodha.com"]
        assert result.calendar_list_error is None and result.warnings == []
        assert {c.kwargs["calendar_id"] for c in mock_events.call_args_list} == {
            "primary", "family@planetmodha.com"}
        assert mock_events.call_args.kwargs["query"] == "run"

    @patch("adapters.calendar_list.list_events")
    @patch("adapters.calendar_list.list_calendars")
    def test_duplicate_event_ids_collapse_primary_copy_first(self, mock_cals, mock_events):
        from adapters.calendar import _parse_event
        mock_cals.return_value = [
            {"id": "primary", "summary": "Me", "primary": True},
            {"id": "team@group.calendar.google.com", "summary": "Team", "primary": False},
        ]
        mock_events.side_effect = lambda **kw: CalendarSearchResult(
            events=[_parse_event(_api_event("shared1", f"seen via {kw['calendar_id']}"))])
        result = list_all_events()
        assert [e.summary for e in result.events] == ["seen via primary"]

    @patch("adapters.calendar_list.list_events")
    @patch("adapters.calendar_list.list_calendars",
           side_effect=MiseError(ErrorKind.PERMISSION_DENIED, "insufficient authentication scopes"))
    def test_scope_403_falls_back_to_primary_and_says_why(self, _cals, mock_events):
        from adapters.calendar import _parse_event
        mock_events.return_value = CalendarSearchResult(
            events=[_parse_event(_api_event("e1"))])
        result = list_all_events()
        assert len(result.events) == 1
        assert [c["id"] for c in result.calendars] == ["primary"]
        assert "insufficient authentication scopes" in result.calendar_list_error
        assert mock_events.call_args.kwargs["calendar_id"] == "primary"

    @patch("adapters.calendar_list.list_events")
    @patch("adapters.calendar_list.list_calendars",
           side_effect=MiseError(ErrorKind.NETWORK_ERROR, "boom"))
    def test_non_scope_failure_is_raised_not_swallowed(self, _cals, mock_events):
        import pytest
        with pytest.raises(MiseError, match="boom"):
            list_all_events()
        mock_events.assert_not_called()

    @patch("adapters.calendar_list.list_events")
    @patch("adapters.calendar_list.list_calendars")
    def test_one_calendar_refusing_warns_and_the_rest_answer(self, mock_cals, mock_events):
        from adapters.calendar import _parse_event
        mock_cals.return_value = [
            {"id": "primary", "summary": "Me", "primary": True},
            {"id": "gone@x.com", "summary": "Gone", "primary": False},
        ]
        def per_calendar(**kw):
            if kw["calendar_id"] == "gone@x.com":
                raise MiseError(ErrorKind.NOT_FOUND, "Not Found")
            return CalendarSearchResult(events=[_parse_event(_api_event("e1"))])
        mock_events.side_effect = per_calendar
        result = list_all_events()
        assert len(result.events) == 1
        assert [c["id"] for c in result.calendars] == ["primary"]  # read, not listed
        assert result.warnings and "gone@x.com" in result.warnings[0]
        # mise-gudeci: the miss travels as a record, not only as prose — the tools
        # layer counts coverage as calendars + calendars_failed
        assert result.calendars_failed == [
            {"id": "gone@x.com", "summary": "Gone", "kind": "not_found", "error": "Not Found"}
        ]

    @patch("adapters.calendar_list.list_events")
    @patch("adapters.calendar_list.list_calendars")
    def test_merged_overflow_is_capped_and_flagged(self, mock_cals, mock_events):
        from adapters.calendar import _parse_event
        mock_cals.return_value = [
            {"id": "primary", "summary": "Me", "primary": True},
            {"id": "b@x.com", "summary": "B", "primary": False},
        ]
        mock_events.side_effect = lambda **kw: CalendarSearchResult(events=[
            _parse_event(_api_event(f"{kw['calendar_id']}-{i}")) for i in range(3)])
        result = list_all_events(max_results=4)
        assert len(result.events) == 4 and result.truncated is True
