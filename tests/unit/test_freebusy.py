"""Unit tests for do(freebusy) — availability + slot mining (mise-rijeco)."""

from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from models import ErrorKind, MiseError
from tools.freebusy import _common_free_slots, _merge_blocks, do_freebusy

_LON = ZoneInfo("Europe/London")


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=_LON)


class TestSlotMining:
    def test_merge_overlapping_blocks(self) -> None:
        merged = _merge_blocks([
            (_dt(8, 10), _dt(8, 11)),
            (_dt(8, 10, 30), _dt(8, 12)),
            (_dt(8, 14), _dt(8, 15)),
        ])
        assert merged == [(_dt(8, 10), _dt(8, 12)), (_dt(8, 14), _dt(8, 15))]

    def test_busy_block_splits_the_day(self) -> None:
        # Tue 8 Sep, one meeting 11:00–14:00 → free 09:00–11:00 and 14:00–17:30
        slots = _common_free_slots(
            [(_dt(8, 11), _dt(8, 14))],
            _dt(8, 0), _dt(8, 23), duration_minutes=30, tz=_LON,
        )
        assert slots == [(_dt(8, 9), _dt(8, 11)), (_dt(8, 14), _dt(8, 17, 30))]

    def test_short_gap_dropped_by_duration(self) -> None:
        # 09:00–09:20 gap is under a 30-min ask
        slots = _common_free_slots(
            [(_dt(8, 9, 20), _dt(8, 17, 30))],
            _dt(8, 0), _dt(8, 23), duration_minutes=30, tz=_LON,
        )
        assert slots == []

    def test_weekend_excluded(self) -> None:
        # 12–13 Sep 2026 is a weekend
        slots = _common_free_slots(
            [], _dt(12, 0), _dt(13, 23), duration_minutes=30, tz=_LON,
        )
        assert slots == []

    def test_free_day_is_one_office_hours_slot(self) -> None:
        slots = _common_free_slots(
            [], _dt(8, 0), _dt(8, 23), duration_minutes=30, tz=_LON,
        )
        assert slots == [(_dt(8, 9), _dt(8, 17, 30))]


def _fb(email_blocks: dict) -> dict:
    """Build a freebusy_query return: email → busy list or errors."""
    out = {}
    for email, blocks in email_blocks.items():
        if blocks is None:
            out[email] = {"errors": [{"reason": "notFound"}]}
        else:
            out[email] = {"busy": [
                {"start": s.isoformat(), "end": e.isoformat()} for s, e in blocks
            ]}
    return out


class TestDoFreebusy:
    @patch("tools.freebusy.resolve_calendar_timezone", return_value="Europe/London")
    @patch("tools.freebusy.list_status_events", return_value=[])
    @patch("tools.freebusy.current_user_email", return_value="me@itv.com")
    @patch("tools.freebusy.freebusy_query")
    def test_self_always_joins_the_arithmetic(
        self, mock_fb, _me, _wl, _tz,
    ) -> None:
        mock_fb.return_value = _fb({"me@itv.com": [], "a@itv.com": []})
        do_freebusy(
            attendees=["a@itv.com"],
            time_min="2026-09-08", time_max="2026-09-09",
        )
        asked = mock_fb.call_args[0][0]
        assert asked == ["me@itv.com", "a@itv.com"]

    @patch("tools.freebusy.resolve_calendar_timezone", return_value="Europe/London")
    @patch("tools.freebusy.list_status_events", return_value=[])
    @patch("tools.freebusy.current_user_email", return_value=None)
    @patch("tools.freebusy.freebusy_query")
    def test_invisible_calendar_is_named_and_excluded(
        self, mock_fb, _me, _wl, _tz,
    ) -> None:
        mock_fb.return_value = _fb({"a@itv.com": [], "hidden@itv.com": None})
        result = do_freebusy(
            attendees=["a@itv.com", "hidden@itv.com"],
            time_min="2026-09-08", time_max="2026-09-09", duration=30,
        )
        assert result["not_visible"] == ["hidden@itv.com"]
        # The honesty invariant: exclusion is said out loud, next to the slots
        assert any("EXCLUDED" in w for w in result["cues"]["warnings"])
        assert "hidden@itv.com" not in result["slot_note"]

    @patch("tools.freebusy.resolve_calendar_timezone", return_value="Europe/London")
    @patch("tools.freebusy.current_user_email", return_value=None)
    @patch("tools.freebusy.freebusy_query")
    def test_office_days_woven_and_acl_failure_honest(
        self, mock_fb, _me, _tz,
    ) -> None:
        mock_fb.return_value = _fb({"a@itv.com": [], "b@itv.com": []})
        wl_event = {
            "start": {"date": "2026-09-08"},
            "workingLocationProperties": {
                "type": "officeLocation",
                "officeLocation": {"label": "UK - London White City"},
            },
        }

        def status_side_effect(email, *args, **kwargs):
            if email == "a@itv.com":
                return [wl_event]
            raise MiseError(ErrorKind.NOT_FOUND, "not shared")

        with patch("tools.freebusy.list_status_events", side_effect=status_side_effect):
            result = do_freebusy(
                attendees=["a@itv.com", "b@itv.com"],
                time_min="2026-09-08", time_max="2026-09-09",
            )
        assert result["people"]["a@itv.com"]["office_days"] == {
            "2026-09-08": "UK - London White City",
        }
        # free/busy-only sharing must never read as "not in the office"
        assert "not visible" in result["people"]["b@itv.com"]["office_days"]

    @patch("tools.freebusy.resolve_calendar_timezone", return_value="Europe/London")
    @patch("tools.freebusy.list_status_events", return_value=[])
    @patch("tools.freebusy.current_user_email", return_value=None)
    @patch("tools.freebusy.freebusy_query")
    def test_duration_mines_common_slots(self, mock_fb, _me, _wl, _tz) -> None:
        busy = [(_dt(8, 9), _dt(8, 12))]
        mock_fb.return_value = _fb({"a@itv.com": busy})
        result = do_freebusy(
            attendees=["a@itv.com"],
            time_min="2026-09-08", time_max="2026-09-08", duration=30,
        )
        starts = [s["start"] for s in result["common_free"]]
        assert starts == [_dt(8, 12).isoformat()]

    @patch("tools.freebusy.current_user_email", return_value=None)
    @patch("tools.freebusy.freebusy_query",
           side_effect=MiseError(ErrorKind.PERMISSION_DENIED, "insufficient scope"))
    def test_scope_403_teaches_the_new_scope(self, mock_fb, _me) -> None:
        result = do_freebusy(
            attendees=["a@itv.com"],
            time_min="2026-09-08", time_max="2026-09-09",
        )
        assert result["error"] is True
        assert "calendar.freebusy" in result["message"]
        assert "setup_oauth" in result["message"]

    @patch("tools.freebusy.current_user_email", return_value=None)
    def test_bad_duration_refused(self, _me) -> None:
        result = do_freebusy(
            attendees=["a@itv.com"],
            time_min="2026-09-08", time_max="2026-09-09", duration=0,
        )
        assert result["error"] is True

    def test_freebusy_not_in_remote_allowed_ops(self) -> None:
        """Read-only, but remote's whitelist is an audited security decision —
        excluded until deliberately admitted."""
        from tools.remote import REMOTE_ALLOWED_OPS
        assert "freebusy" not in REMOTE_ALLOWED_OPS
