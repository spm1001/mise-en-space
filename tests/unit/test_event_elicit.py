"""The elicit-or-confirm seam on the calendar writes — mise-pukiri.

create_event with attendees and a structural update_event carry the same
two paths share does (tests/unit/test_share_elicit.py): where the client
declared form elicitation, do()'s one `confirm_gate` resolver asks with the
op's preview message and the write runs only on an accepted proceed=true;
everywhere else the preview-then-confirm=True round-trip runs unchanged.

Pinned at three depths, like share: the body's reading of an outcome, the
resolver against declared capabilities, and the real envelope — an in-memory
mcp Client against server.mcp in both protocol eras. The load-bearing
property throughout is that the dialog text IS the preview's message, that
the message carries every attendee, the clash check and its caveat, and the
warnings — and that a dialog is raised only when Claude Code would show that
message WHOLE (4 lines of 74 columns; tools/elicit.py). A message that would
be clipped takes the confirm= path instead: the human must never approve
less than the preview shows.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from mcp import Client
from mcp.server.elicitation import AcceptedElicitation, CancelledElicitation, DeclinedElicitation
from mcp.server.mcpserver import Elicit
from mcp.types import ClientCapabilities, ElicitationCapability, ElicitResult

import server  # registers search/fetch/do on server.mcp
from models import DoResult
from tools.confirm_gate import confirm_ask, gate_question
from tools.create_event import do_create_event
from tools.elicit import (
    DIALOG_MAX_COLS, ORPHANED, TOO_LONG, TOO_LONG_CUE, UNAVAILABLE, ConfirmAnswer, GateQuestion, fits_dialog,
)
from models import ErrorKind, MiseError
from tools.update_event import do_update_event

YES = AcceptedElicitation(data=ConfirmAnswer(proceed=True))
NO_VIA_FORM = AcceptedElicitation(data=ConfirmAnswer(proceed=False))
NOT_ASKED = AcceptedElicitation[object].model_construct(data=None)  # resolver returned None
UNACCEPTED = [(NO_VIA_FORM, "decline"), (DeclinedElicitation(), "decline"), (CancelledElicitation(), "cancel")]

_TZ = "tools.events_util.resolve_calendar_timezone"
_CLASHES = ["Standup (09:00 – 09:15)"]
_BOOKING = {
    "title": "LSM catch-up", "time_min": "2026-09-08T14:00", "time_max": "2026-09-08T14:30",
    "attendees": ["a@itv.com", "b@itv.com"],
}
# Fits the dialog when the diary is clear: the clash caveat rides line 4.
_RECURRING = {**_BOOKING, "recurrence": "RRULE:FREQ=DAILY;COUNT=2", "location": "Room 4", "meet": True}
# 2026-09-08 is a Tuesday, so BYDAY=MO draws the stray-instance warning — never fits.
_WARNED = {**_BOOKING, "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=MO"}
_CROWD = {**_BOOKING, "attendees": [f"person{i}@itv.com" for i in range(8)]}


def confirm_gate(operation, ctx, **kwargs):
    """The dialog a call would raise: the two gate resolvers, composed as mcp runs them."""
    return confirm_ask(gate_question(operation, ctx, **kwargs))


def _ctx(capable: bool) -> SimpleNamespace:
    caps = ClientCapabilities(elicitation=ElicitationCapability()) if capable else ClientCapabilities()
    return SimpleNamespace(client_capabilities=caps, input_responses=None)  # a first round


def _created() -> dict:
    return {"id": "new_evt_1", "summary": "LSM catch-up", "htmlLink": "https://calendar.google.com/e"}


def _event(**overrides) -> dict:
    ev = {
        "id": "evt123", "summary": "Weekly sync", "status": "confirmed",
        "htmlLink": "https://calendar.google.com/event?eid=abc",
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


_MOVE = {"file_id": "evt123", "time_min": "2026-08-28T10:00", "time_max": "2026-08-28T11:00",
         "attendees": ["new@itv.com"]}


@pytest.fixture
def calendar():
    """Hermetic calendar: fixed zone and clashes, recorded writes."""
    with patch(_TZ, return_value="Europe/London"), \
         patch("tools.create_event.clash_summaries", return_value=list(_CLASHES)) as clash, \
         patch("tools.create_event.insert_event", return_value=_created()) as insert, \
         patch("tools.update_event.get_event", return_value=_event()), \
         patch("tools.update_event.patch_event", return_value=_event()) as patch_:
        yield SimpleNamespace(insert=insert, patch=patch_, clash=clash)


# ---------------------------------------------------------------------------
# The preview's message: what the human would be approving.
# ---------------------------------------------------------------------------

class TestPreviewMessage:
    def test_create_event_message_carries_every_fact_the_preview_shows(self, calendar) -> None:
        preview = do_create_event(**_BOOKING)
        message = preview["message"]
        assert message.splitlines()[0] == "Book 'LSM catch-up'"
        assert "When: Tue 8 Sep 2026 14:00–14:30 Europe/London" in message
        for attendee in preview["attendees"]:
            assert attendee in message
        for clash in preview["clashes"]:
            assert clash in message
        assert "(invites emailed)" in message
        assert fits_dialog(message)

    def test_a_recurring_booking_carries_the_clash_caveat_and_can_still_fit(self, calendar) -> None:
        calendar.clash.return_value = []
        preview = do_create_event(**_RECURRING)
        message = preview["message"]
        assert preview["clash_note"] in message and "Clashes: none." in message
        assert "RRULE:FREQ=DAILY;COUNT=2" in message
        assert "Room 4" in message and "Meet link" in message
        assert fits_dialog(message)

    def test_every_warning_is_in_the_message_and_a_warned_booking_never_fits(self, calendar) -> None:
        preview = do_create_event(**_WARNED)
        assert preview["cues"]["warnings"]
        assert all(f"Warning: {w}" in preview["message"] for w in preview["cues"]["warnings"])
        assert not fits_dialog(preview["message"])

    def test_every_attendee_is_listed_even_when_that_cannot_fit(self, calendar) -> None:
        message = do_create_event(**_CROWD)["message"]
        assert all(a in message for a in _CROWD["attendees"])
        assert not fits_dialog(message)

    def test_create_event_message_says_when_no_invite_is_emailed(self, calendar) -> None:
        assert "(no emails sent)" in do_create_event(**_BOOKING, send_updates="none")["message"]

    def test_create_event_message_says_no_clashes_rather_than_nothing(self, calendar) -> None:
        calendar.clash.return_value = []
        assert "Clashes: none." in do_create_event(**_BOOKING)["message"]

    def test_update_event_message_carries_every_fact_the_preview_shows(self, calendar) -> None:
        preview = do_update_event(**_MOVE)
        message = preview["message"]
        assert message.splitlines()[0] == "Update 'Weekly sync', now Thu 27 Aug 2026 14:00–15:00 UTC+01:00"
        assert "Move to: Fri 28 Aug 2026 10:00–11:00" in message
        for attendee in preview["changes"]["attendees_to_add"]:
            assert attendee in message
        assert f"({preview['attendee_count']} attendee(s) on it now)" in message
        assert "Emails: sent to every attendee" in message
        assert fits_dialog(message)

    def test_update_event_message_names_each_structural_change(self, calendar) -> None:
        preview = do_update_event(file_id="evt123", meet=True, recurrence="RRULE:FREQ=WEEKLY", title="Renamed")
        message = preview["message"]
        assert "Meet: add a Meet link" in message
        assert "Repeat: none → RRULE:FREQ=WEEKLY" in message
        assert "Also: title" in message

    def test_every_warning_reaches_the_update_message(self, calendar) -> None:
        with_meet = _event(conferenceData={"entryPoints": [{"uri": "https://meet.google.com/abc"}]})
        with patch("tools.update_event.get_event", return_value=with_meet):
            preview = do_update_event(**_MOVE, meet=True)
        assert preview["changes"]["time"]  # still structural, so still previewed
        assert "Warning: The event already has a Meet link — left as is." in preview["message"]

    def test_an_offset_time_is_shown_in_the_events_own_zone(self, calendar) -> None:
        args = {**_BOOKING, "time_min": "2026-10-05T08:00:00Z", "time_max": "2026-10-05T08:15:00Z",
                "recurrence": "RRULE:FREQ=DAILY;COUNT=5"}
        preview = do_create_event(**args)
        assert preview["start"]["timeZone"] == "Europe/London"
        assert "When: Mon 5 Oct 2026 09:00–09:15 Europe/London" in preview["message"]  # 08:00Z is 09:00 BST

    def test_the_clash_check_asks_about_the_booked_hour(self, calendar) -> None:
        do_create_event(**{**_BOOKING, "time_min": "2026-10-05T09:00", "time_max": "2026-10-05T09:30"})
        start, end = calendar.clash.call_args[0]
        assert start.isoformat() == "2026-10-05T09:00:00+01:00" and end.isoformat() == "2026-10-05T09:30:00+01:00"

    def test_update_warnings_computed_for_the_write_reach_the_message(self, calendar) -> None:
        args = {"file_id": "evt123", "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=TU"}  # the event starts on a Thursday
        preview = do_update_event(**args)
        assert "Warning: The start falls on a Thursday but BYDAY=TU" in preview["message"]
        assert not fits_dialog(preview["message"])
        with patch(_TZ, return_value=None):
            unzoned = do_update_event(**_MOVE)["message"]
        assert "Warning: time_min has no timezone" in unzoned

    def test_the_move_line_carries_the_zone_the_write_will_use(self, calendar) -> None:
        assert "Move to: Fri 28 Aug 2026 10:00–11:00" in do_update_event(**{**_MOVE, "recurrence": "RRULE:FREQ=DAILY"})["message"]
        message = do_update_event(**{**_MOVE, "recurrence": "RRULE:FREQ=DAILY"})["message"]
        assert "Europe/London" in message.splitlines()[1]

    def test_the_invite_thread_rides_the_cues_and_the_message_names_the_event(self, calendar) -> None:
        with patch("tools.update_event._resolve_event_from_thread",
                   return_value=(_event(), {"resolved_from_thread": "19fb9faca1565748", "ical_uid": "u@google.com"})):
            preview = do_update_event(file_id="19fb9faca1565748", meet=True)
        assert preview["cues"]["resolved_from_thread"] == "19fb9faca1565748"
        assert "Weekly sync" in preview["message"]


class TestDialogBudget:
    """Claude Code 2.1.283 clips lines at (width − 6) and shows 4 lines (tools/elicit.py)."""

    def test_four_lines_of_74_columns_fit_and_one_more_of_either_does_not(self) -> None:
        full = "\n".join(["x" * DIALOG_MAX_COLS] * 4)
        assert fits_dialog(full)
        assert not fits_dialog(full + "\ny")
        assert not fits_dialog("x" * (DIALOG_MAX_COLS + 1))

    def test_the_budget_is_an_80_column_terminal_less_claude_codes_six(self) -> None:
        # Measured, not chosen: CC clips at max(20, width - 6); 193 columns in a
        # 199-column pane, seen live. Moving this number is a claim about CC.
        assert fits_dialog("x" * 74) and not fits_dialog("x" * 75)

    def test_a_too_long_question_is_kept_but_not_asked(self, calendar) -> None:
        question = gate_question("create_event", _ctx(True), **_CROWD)
        assert question.skipped == TOO_LONG and not question.ask
        assert question.message == do_create_event(**_CROWD)["message"]
        assert confirm_ask(question) is None

    def test_wide_characters_take_two_columns(self) -> None:
        assert fits_dialog("日" * (DIALOG_MAX_COLS // 2))
        assert not fits_dialog("日" * (DIALOG_MAX_COLS // 2 + 1))


# ---------------------------------------------------------------------------
# The body's reading of an outcome.
# ---------------------------------------------------------------------------

def _asked(message: str) -> GateQuestion:
    return GateQuestion(message=message)


class TestCreateEventOnAVerdict:
    def test_accept_books_with_a_mechanism_naming_cue(self, calendar) -> None:
        asked = _asked(do_create_event(**_BOOKING)["message"])
        result = do_create_event(**_BOOKING, answer=YES, question=asked)
        assert isinstance(result, DoResult)
        calendar.insert.assert_called_once()
        assert result.cues["confirm_gate"] == "elicitation: the client answered proceed=true; booked on that answer"
        assert "human" not in result.cues["confirm_gate"].lower()

    @pytest.mark.parametrize("outcome,action", UNACCEPTED)
    def test_anything_but_accept_books_nothing(self, calendar, outcome, action) -> None:
        result = do_create_event(**_BOOKING, answer=outcome)
        calendar.insert.assert_not_called()
        assert result["preview"] is True
        assert result["message"] == do_create_event(**_BOOKING)["message"]
        gate = result["cues"]["confirm_gate"]
        assert gate.startswith(f"elicitation: {'declined' if action == 'decline' else action} — ")
        assert "Nothing was booked and nobody was emailed" in gate
        assert "human" not in gate.lower()
        # cancel = no dialog decided this, confirm= stays open; decline = an answer, it closes.
        assert ("confirm_required" in result["cues"]) == (action == "cancel")
        if action == "decline":
            assert "confirm=True" not in gate

    def test_no_dialog_returns_todays_preview_byte_for_byte(self, calendar) -> None:
        assert do_create_event(**_BOOKING, answer=NOT_ASKED) == do_create_event(**_BOOKING)
        assert "confirm_gate" not in do_create_event(**_BOOKING, answer=None)["cues"]

    def test_confirm_true_books_regardless_of_the_dialog(self, calendar) -> None:
        result = do_create_event(**_BOOKING, confirm=True, answer=CancelledElicitation())
        assert isinstance(result, DoResult) and "confirm_gate" not in result.cues
        calendar.insert.assert_called_once()

    def test_a_solo_event_books_with_no_cue_claiming_a_dialog(self, calendar) -> None:
        solo = {k: v for k, v in _BOOKING.items() if k != "attendees"}
        result = do_create_event(**solo)
        assert isinstance(result, DoResult) and "confirm_gate" not in result.cues

    def test_an_accept_of_different_words_books_nothing(self, calendar) -> None:
        asked = _asked(do_create_event(**_BOOKING)["message"])
        calendar.clash.return_value = ["Board meeting (14:00 – 15:00)"]  # the diary moved while the dialog sat
        result = do_create_event(**_BOOKING, answer=YES, question=asked)
        calendar.insert.assert_not_called()
        assert result["preview"] is True and "Board meeting" in result["message"]
        assert "no longer matches" in result["cues"]["confirm_gate"]
        assert "confirm_required" in result["cues"]  # the round-trip stays open

    def test_an_answer_without_its_question_books_nothing(self, calendar) -> None:
        result = do_create_event(**_BOOKING, answer=YES)
        calendar.insert.assert_not_called()
        assert "no longer matches" in result["cues"]["confirm_gate"]

    def test_validation_errors_win_over_any_answer(self, calendar) -> None:
        result = do_create_event(**{**_BOOKING, "time_max": "2026-09-08T13:00"}, answer=YES)
        assert result["error"] is True
        calendar.insert.assert_not_called()


class TestUpdateEventOnAVerdict:
    def test_accept_patches_with_a_mechanism_naming_cue(self, calendar) -> None:
        asked = _asked(do_update_event(**_MOVE)["message"])
        result = do_update_event(**_MOVE, answer=YES, question=asked)
        assert isinstance(result, DoResult)
        calendar.patch.assert_called_once()
        assert result.cues["confirm_gate"] == "elicitation: the client answered proceed=true; updated on that answer"

    @pytest.mark.parametrize("outcome,action", UNACCEPTED)
    def test_anything_but_accept_changes_nothing(self, calendar, outcome, action) -> None:
        result = do_update_event(**_MOVE, answer=outcome)
        calendar.patch.assert_not_called()
        assert result["preview"] is True
        assert result["message"] == do_update_event(**_MOVE)["message"]
        gate = result["cues"]["confirm_gate"]
        assert "Nothing has changed and nobody was emailed" in gate and "human" not in gate.lower()
        assert ("confirm_required" in result["cues"]) == (action == "cancel")

    def test_no_dialog_returns_todays_preview_byte_for_byte(self, calendar) -> None:
        assert do_update_event(**_MOVE, answer=NOT_ASKED) == do_update_event(**_MOVE)

    def test_a_cosmetic_edit_runs_with_no_cue_claiming_a_dialog(self, calendar) -> None:
        result = do_update_event(file_id="evt123", content="new agenda")
        assert isinstance(result, DoResult) and "confirm_gate" not in result.cues

    _WITH_MEET = {"conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}]}}

    @pytest.mark.parametrize("outcome", [DeclinedElicitation(), NO_VIA_FORM, CancelledElicitation()])
    def test_a_decline_never_writes_even_when_the_re_read_turns_cosmetic(self, calendar, outcome) -> None:
        # The dialog asked about removing a Meet link; by the time the body re-reads,
        # the link is gone, so the rest looks cosmetic. The person still said no.
        args = {"file_id": "evt123", "meet": False, "title": "Renamed"}
        with patch("tools.update_event.get_event", return_value=_event(**self._WITH_MEET)):
            asked = _asked(do_update_event(**args)["message"])
        assert "Meet: remove the Meet link" in asked.message
        result = do_update_event(**args, answer=outcome, question=asked)  # re-read: no Meet link
        calendar.patch.assert_not_called()
        assert result["preview"] is True

    def test_an_accept_never_writes_what_the_dialog_ruled_out(self, calendar) -> None:
        # The dialog said "no Meet link — nothing to remove"; the re-read has one.
        args = {**_MOVE, "meet": False}
        asked = _asked(do_update_event(**args)["message"])
        assert "nothing to remove" in asked.message
        with patch("tools.update_event.get_event", return_value=_event(**self._WITH_MEET)):
            result = do_update_event(**args, answer=YES, question=asked)
        calendar.patch.assert_not_called()
        assert "no longer matches" in result["cues"]["confirm_gate"]
        assert "remove the Meet link" in result["message"]  # the preview shows what would happen now

    def test_confirm_true_patches_regardless_of_the_dialog(self, calendar) -> None:
        result = do_update_event(**_MOVE, confirm=True, answer=DeclinedElicitation())
        assert isinstance(result, DoResult) and "confirm_gate" not in result.cues


# ---------------------------------------------------------------------------
# The resolver at the client seam.
# ---------------------------------------------------------------------------

class TestResolverAtTheClientSeam:
    @pytest.mark.parametrize("args,clear_diary", [(_BOOKING, False), (_RECURRING, True)])
    def test_create_event_dialog_text_is_the_preview_message(self, calendar, args, clear_diary) -> None:
        if clear_diary:
            calendar.clash.return_value = []
        marker = confirm_gate("create_event", _ctx(True), **args)
        assert isinstance(marker, Elicit) and marker.schema is ConfirmAnswer
        assert marker.message == do_create_event(**args)["message"]

    def test_update_event_dialog_text_is_the_preview_message(self, calendar) -> None:
        marker = confirm_gate("update_event", _ctx(True), **_MOVE)
        assert isinstance(marker, Elicit)
        assert marker.message == do_update_event(**_MOVE)["message"]

    def test_every_argument_reaches_the_resolver_as_it_reaches_the_body(self, calendar) -> None:
        # One fixture setting every update argument, so a swapped pair in the
        # resolver's call (content/location, recurrence/include) cannot pass.
        full = {"file_id": "evt123", "title": "T2", "content": "agenda", "location": "Rm 9",
                "time_min": "2026-08-28T10:00", "time_max": "2026-08-28T10:30",
                "attendees": ["n@itv.com"], "recurrence": "RRULE:FREQ=DAILY;COUNT=2",
                "include": ["1AbCdEfGhIjKlMnOpQrStUvWxYz"], "meet": True, "send_updates": "none",
                "properties": {"k": "v"}, "color": "sage", "visibility": "private", "transparency": "free"}
        question = gate_question("update_event", _ctx(True), **full)
        assert question.message == do_update_event(**full)["message"]

    @pytest.mark.parametrize("operation,args", [
        ("create_event", {**_BOOKING, "confirm": True}),                                   # policy A
        ("create_event", {k: v for k, v in _BOOKING.items() if k != "attendees"}),        # nothing to gate
        ("create_event", {**_BOOKING, "time_max": "2026-09-08T13:00"}),                   # body refuses
        ("create_event", {**_BOOKING, "attendees": " , "}),                                 # no address survives
        ("create_event", _WARNED),                                                        # a warning never fits
        ("create_event", _CROWD),                                                         # attendees would clip
        ("create_event", {**_BOOKING, "title": "T" * 80}),                                # the title would clip
        ("update_event", {**_MOVE, "confirm": True}),
        ("update_event", {"file_id": "evt123", "content": "new agenda"}),                 # cosmetic only
        ("update_event", {"file_id": "evt123", "time_min": "2026-08-28T10:00"}),          # half a move
        ("update_event", {"file_id": ["evt123"], "attendees": ["x@itv.com"]}),            # not an id
        ("update_event", {**_MOVE, "time_min": "2026-08-28T11:00", "time_max": "2026-08-28T10:00"}),  # end before start
    ])
    def test_answers_none_wherever_the_body_would_not_preview(self, calendar, operation, args) -> None:
        assert confirm_gate(operation, _ctx(True), **args) is None

    def test_a_client_without_the_capability_is_never_asked_and_costs_no_api_read(self, calendar) -> None:
        with patch("tools.create_event.clash_summaries") as clash, \
             patch("tools.update_event.get_event") as get:
            assert confirm_gate("create_event", _ctx(False), **_BOOKING) is None
            assert confirm_gate("update_event", _ctx(False), **_MOVE) is None
        clash.assert_not_called(); get.assert_not_called()

    def test_an_answer_this_round_no_longer_asks_about_is_orphaned(self, calendar) -> None:
        retry = SimpleNamespace(client_capabilities=_ctx(True).client_capabilities,
                                input_responses={"tools.confirm_gate:confirm_ask": object()})
        # Nothing to ask now (cosmetic only), but the client is answering a prior round.
        question = gate_question("update_event", retry, file_id="evt123", content="agenda")
        assert question.skipped == ORPHANED and not question.ask
        # A round that still asks is not orphaned: the framework reads the answer.
        assert gate_question("update_event", retry, **_MOVE).ask

    def test_a_failed_read_is_unavailable_not_silent(self, calendar) -> None:
        with patch("tools.update_event.get_event", side_effect=MiseError(ErrorKind.NETWORK_ERROR, "boom")):
            assert gate_question("update_event", _ctx(True), **_MOVE).skipped == UNAVAILABLE
        preview = do_update_event(**_MOVE, question=GateQuestion(skipped=UNAVAILABLE))
        assert preview["cues"]["confirm_gate"].startswith("no dialog: the confirmation question could not be prepared")
        calendar.patch.assert_not_called()

    def test_an_orphaned_answer_never_writes_even_when_nothing_is_gated(self, calendar) -> None:
        result = do_update_event(file_id="evt123", title="Renamed", answer=None,
                                 question=GateQuestion(message=None, skipped=ORPHANED))
        calendar.patch.assert_not_called()
        assert "no longer asks" in result["cues"]["confirm_gate"]

    def test_mixed_naive_and_offset_bounds_are_refused_before_any_dialog(self, calendar) -> None:
        mixed = {**_BOOKING, "time_max": "2026-09-08T14:30:00Z"}
        assert gate_question("create_event", _ctx(True), **mixed).message is None
        result = do_create_event(**mixed)
        assert result["error"] is True and "both carry an offset or both omit one" in result["message"]

    def test_nobody_is_asked_to_approve_adding_people_already_there(self, calendar) -> None:
        already = {"file_id": "evt123", "attendees": ["colleague@itv.com"]}
        assert gate_question("update_event", _ctx(True), **already).message is None
        assert "already on the event" in do_update_event(**already)["message"]
        moved = do_update_event(**{**_MOVE, "attendees": ["colleague@itv.com"]})
        assert "Add:" not in moved["message"] and "Move to:" in moved["message"]

    def test_a_guest_reshape_answers_none_so_the_body_reports_the_refusal(self, calendar) -> None:
        guest = _event(organizer={"email": "boss@itv.com"})
        with patch("tools.update_event.get_event", return_value=guest):
            assert confirm_gate("update_event", _ctx(True), **_MOVE) is None


# ---------------------------------------------------------------------------
# The real envelope, both protocol eras (see test_share_elicit.py's note).
# ---------------------------------------------------------------------------

ERAS = ["legacy", "auto"]
_CREATE = {"operation": "create_event", **_BOOKING}
_UPDATE = {"operation": "update_event", **_MOVE}


@pytest.fixture
def envelope(monkeypatch, tmp_path, calendar):
    monkeypatch.setenv("MISE_TOKEN_PATH", str(tmp_path / "deliberately-absent.json"))
    monkeypatch.setattr("server.cleanup_orphaned_temp_files", lambda: 0)
    return calendar


def _raise_no_token():
    raise FileNotFoundError("No OAuth token found at /nowhere/token.json")


def _payload(result) -> dict:
    if result.structured_content:
        return result.structured_content
    return json.loads("".join(getattr(b, "text", "") for b in result.content))


class TestGateThroughTheEnvelope:
    @pytest.mark.parametrize("mode", ERAS)
    @pytest.mark.parametrize("args,write", [(_CREATE, "insert"), (_UPDATE, "patch")])
    async def test_client_without_the_capability_gets_the_confirm_round_trip(self, envelope, mode, args, write) -> None:
        async with Client(server.mcp, mode=mode) as c:  # no elicitation_callback => capability not declared
            preview = _payload(await c.call_tool("do", args))
            assert preview["preview"] is True and "confirm_gate" not in preview["cues"]
            assert "confirm=True" in preview["cues"]["confirm_required"]
            assert getattr(envelope, write).call_count == 0
            done = _payload(await c.call_tool("do", {**args, "confirm": True}))
        assert done["operation"] == args["operation"] and not done.get("preview")
        assert "confirm_gate" not in done["cues"]
        assert getattr(envelope, write).call_count == 1

    @pytest.mark.parametrize("mode", ERAS)
    @pytest.mark.parametrize("args,write,done_word", [(_CREATE, "insert", "booked"), (_UPDATE, "patch", "updated")])
    async def test_accept_writes_and_the_dialog_shows_the_preview_message(self, envelope, mode, args, write, done_word) -> None:
        shown: list[str] = []

        async def accept(_context, params):
            shown.append(params.message)
            return ElicitResult(action="accept", content={"proceed": True})

        async with Client(server.mcp, mode=mode, elicitation_callback=accept) as c:
            done = _payload(await c.call_tool("do", args))
        assert getattr(envelope, write).call_count == 1
        assert done["cues"]["confirm_gate"] == f"elicitation: the client answered proceed=true; {done_word} on that answer"
        async with Client(server.mcp, mode=mode) as c:  # same facts either way
            fallback = _payload(await c.call_tool("do", args))
        assert shown == [fallback["message"]]
        # The facts a human must see are in the words they were shown, and all of them fit.
        assert fits_dialog(shown[0])
        if args is _CREATE:
            assert all(a in shown[0] for a in fallback["attendees"])
            assert all(c in shown[0] for c in fallback["clashes"])
        else:
            assert all(a in shown[0] for a in fallback["changes"]["attendees_to_add"])

    @pytest.mark.parametrize("mode", ERAS)
    async def test_a_recurring_booking_shows_the_clash_caveat_in_the_dialog(self, envelope, mode) -> None:
        envelope.clash.return_value = []
        shown: list[str] = []

        async def accept(_context, params):
            shown.append(params.message)
            return ElicitResult(action="accept", content={"proceed": True})

        async with Client(server.mcp, mode=mode, elicitation_callback=accept) as c:
            await c.call_tool("do", {"operation": "create_event", **_RECURRING})
        assert len(shown) == 1 and "Clash check covers the FIRST instance only." in shown[0]
        assert envelope.insert.call_count == 1

    @pytest.mark.parametrize("mode", ERAS)
    @pytest.mark.parametrize("args", [_WARNED, _CROWD], ids=["warned", "crowd"])
    async def test_a_message_the_dialog_would_clip_takes_the_confirm_path(self, envelope, mode, args) -> None:
        asked: list[str] = []

        async def record(_context, params):
            asked.append(params.message)
            return ElicitResult(action="accept", content={"proceed": True})

        async with Client(server.mcp, mode=mode, elicitation_callback=record) as c:
            out = _payload(await c.call_tool("do", {"operation": "create_event", **args}))
        assert asked == []  # a capable client, never asked
        assert out["preview"] is True and "confirm_required" in out["cues"]
        assert out["cues"]["confirm_gate"] == TOO_LONG_CUE  # the fallback says why it fired
        assert envelope.insert.call_count == 0

    @pytest.mark.parametrize("mode", ERAS)
    @pytest.mark.parametrize("answer", [
        ElicitResult(action="decline"), ElicitResult(action="cancel"),
        ElicitResult(action="accept", content={"proceed": False}),
        ElicitResult(action="accept", content={"proceed": True}),
    ], ids=["decline", "cancel", "proceed-false", "accept"])
    async def test_nothing_is_written_when_the_event_changes_while_the_dialog_waits(self, envelope, mode, answer) -> None:
        # The dialog asks about removing a Meet link. Every later read finds the link
        # gone, so the rest is cosmetic. On 2026-07-28 the retry round re-runs the
        # resolvers against the new state and asks nothing; the answer must not be
        # dropped and turned into a write (round-2 essayeur).
        meet = {"conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc"}]}}
        reads = iter([_event(**meet)] + [_event()] * 5)
        shown: list[str] = []

        async def respond(_context, params):
            shown.append(params.message)
            return answer

        with patch("tools.update_event.get_event", side_effect=lambda _id: next(reads)):
            async with Client(server.mcp, mode=mode, elicitation_callback=respond) as c:
                out = _payload(await c.call_tool("do", {"operation": "update_event", "file_id": "evt123",
                                                        "meet": False, "title": "Renamed"}))
        assert len(shown) == 1 and "Meet: remove the Meet link" in shown[0]
        assert envelope.patch.call_count == 0
        assert out["preview"] is True and "confirm_gate" in out["cues"]

    @pytest.mark.parametrize("mode", ERAS)
    async def test_no_token_on_a_capable_client_still_teaches(self, monkeypatch, tmp_path, mode) -> None:
        # No calendar mocks: the resolver meets the missing token first. It must
        # step aside so the body reports the teaching error, not an opaque failure.
        monkeypatch.setenv("MISE_TOKEN_PATH", str(tmp_path / "deliberately-absent.json"))
        monkeypatch.setattr("server.cleanup_orphaned_temp_files", lambda: 0)
        monkeypatch.setattr("tools.events_util.resolve_calendar_timezone", _raise_no_token)
        asked: list[str] = []

        async def record(_context, params):
            asked.append(params.message)
            return ElicitResult(action="accept", content={"proceed": True})

        async with Client(server.mcp, mode=mode, elicitation_callback=record) as c:
            out = _payload(await c.call_tool("do", _CREATE))
        assert asked == [] and out["error"] is True and "No OAuth token" in out["message"]

    @pytest.mark.parametrize("mode", ERAS)
    @pytest.mark.parametrize("action", ["cancel", "decline"])
    @pytest.mark.parametrize("args,write", [(_CREATE, "insert"), (_UPDATE, "patch")])
    async def test_cancel_or_decline_writes_nothing(self, envelope, mode, action, args, write) -> None:
        async def answer(_context, _params):
            return ElicitResult(action=action)

        async with Client(server.mcp, mode=mode, elicitation_callback=answer) as c:
            out = _payload(await c.call_tool("do", args))
        assert getattr(envelope, write).call_count == 0
        assert out["preview"] is True and action in out["cues"]["confirm_gate"]
        assert ("confirm_required" in out["cues"]) == (action == "cancel")

    @pytest.mark.parametrize("mode", ERAS)
    async def test_a_pre_supplied_confirm_never_raises_a_dialog(self, envelope, mode) -> None:
        asked: list[str] = []

        async def record(_context, params):
            asked.append(params.message)
            return ElicitResult(action="accept", content={"proceed": True})

        async with Client(server.mcp, mode=mode, elicitation_callback=record) as c:
            done = _payload(await c.call_tool("do", {**_CREATE, "confirm": True}))
        assert asked == [] and "confirm_gate" not in done["cues"]
        assert envelope.insert.call_count == 1
