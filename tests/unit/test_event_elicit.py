"""The elicit-or-confirm seam on the calendar writes — mise-pukiri.

create_event with attendees and a structural update_event carry the same
two paths share does (tests/unit/test_share_elicit.py): where the client
declared form elicitation, do()'s one `confirm_gate` resolver asks with the
op's preview message and the write runs only on an accepted proceed=true;
everywhere else the preview-then-confirm=True round-trip runs unchanged.

Pinned at three depths, like share: the body's reading of an outcome, the
resolver against declared capabilities, and the real envelope — an in-memory
mcp Client against server.mcp in both protocol eras. The load-bearing
property throughout is that the dialog text IS the preview's message, and
that message carries every attendee, the clash check and its caveat, and
the warnings: the human must never approve less than the preview shows.
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
from tools.confirm_gate import confirm_gate
from tools.create_event import do_create_event
from tools.elicit import ConfirmAnswer
from tools.update_event import do_update_event

YES = AcceptedElicitation(data=ConfirmAnswer(proceed=True))
NO_VIA_FORM = AcceptedElicitation(data=ConfirmAnswer(proceed=False))
NOT_ASKED = AcceptedElicitation[object].model_construct(data=None)  # resolver returned None
UNACCEPTED = [(NO_VIA_FORM, "decline"), (DeclinedElicitation(), "decline"), (CancelledElicitation(), "cancel")]

_TZ = "tools.events_util.resolve_calendar_timezone"
_CLASHES = ["Standup (2026-09-08T14:00:00+01:00 – 2026-09-08T14:15:00+01:00)"]
_BOOKING = {
    "title": "LSM catch-up", "time_min": "2026-09-08T14:00", "time_max": "2026-09-08T14:30",
    "attendees": ["a@itv.com", "b@itv.com"],
}
# 2026-09-08 is a Tuesday, so BYDAY=MO draws the stray-instance warning.
_RECURRING = {**_BOOKING, "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=MO", "location": "Room 4", "meet": True}


def _ctx(capable: bool) -> SimpleNamespace:
    caps = ClientCapabilities(elicitation=ElicitationCapability()) if capable else ClientCapabilities()
    return SimpleNamespace(client_capabilities=caps)


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
         patch("tools.create_event.clash_summaries", return_value=list(_CLASHES)), \
         patch("tools.create_event.insert_event", return_value=_created()) as insert, \
         patch("tools.update_event.get_event", return_value=_event()), \
         patch("tools.update_event.patch_event", return_value=_event()) as patch_:
        yield SimpleNamespace(insert=insert, patch=patch_)


# ---------------------------------------------------------------------------
# The preview's message: what the human would be approving.
# ---------------------------------------------------------------------------

class TestPreviewMessage:
    def test_create_event_message_carries_every_fact_the_preview_shows(self, calendar) -> None:
        preview = do_create_event(**_RECURRING)
        message = preview["message"]
        for attendee in preview["attendees"]:
            assert attendee in message
        for clash in preview["clashes"]:
            assert clash in message
        assert preview["clash_note"] in message
        assert preview["cues"]["warnings"] and all(w in message for w in preview["cues"]["warnings"])
        assert "Room 4" in message and "Meet link" in message
        assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in message
        assert "2026-09-08T14:00:00" in message and "Europe/London" in message
        assert "invite emails go to every attendee" in message

    def test_create_event_message_says_when_no_invite_is_emailed(self, calendar) -> None:
        message = do_create_event(**_BOOKING, send_updates="none")["message"]
        assert "NO invite emails" in message

    def test_create_event_message_says_no_clashes_rather_than_nothing(self, calendar) -> None:
        with patch("tools.create_event.clash_summaries", return_value=[]):
            assert "No clashes in your diary." in do_create_event(**_BOOKING)["message"]

    def test_update_event_message_carries_every_fact_the_preview_shows(self, calendar) -> None:
        preview = do_update_event(**_MOVE)
        message = preview["message"]
        assert "Weekly sync" in message and "2026-08-27T14:00:00+01:00" in message  # which event, as it stands
        assert "2026-08-28T10:00" in message and "2026-08-28T11:00" in message   # where it moves
        for attendee in preview["changes"]["attendees_to_add"]:
            assert attendee in message
        assert f"{preview['attendee_count']} attendee(s)" in message
        assert "update emails go to every attendee" in message

    def test_update_event_message_names_the_invite_thread_it_resolved(self, calendar) -> None:
        with patch("tools.update_event._resolve_event_from_thread",
                   return_value=(_event(), {"resolved_from_thread": "19fb9faca1565748", "ical_uid": "u@google.com"})):
            preview = do_update_event(file_id="19fb9faca1565748", meet=True)
        assert "19fb9faca1565748" in preview["message"]
        assert "add a Meet link" in preview["message"]


# ---------------------------------------------------------------------------
# The body's reading of an outcome.
# ---------------------------------------------------------------------------

class TestCreateEventOnAVerdict:
    def test_accept_books_with_a_mechanism_naming_cue(self, calendar) -> None:
        result = do_create_event(**_BOOKING, answer=YES)
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

    def test_a_solo_event_is_not_gated_so_no_cue_claims_the_dialog(self, calendar) -> None:
        solo = {k: v for k, v in _BOOKING.items() if k != "attendees"}
        result = do_create_event(**solo, answer=YES)
        assert isinstance(result, DoResult) and "confirm_gate" not in result.cues

    def test_validation_errors_win_over_any_answer(self, calendar) -> None:
        result = do_create_event(**{**_BOOKING, "time_max": "2026-09-08T13:00"}, answer=YES)
        assert result["error"] is True
        calendar.insert.assert_not_called()


class TestUpdateEventOnAVerdict:
    def test_accept_patches_with_a_mechanism_naming_cue(self, calendar) -> None:
        result = do_update_event(**_MOVE, answer=YES)
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

    def test_a_cosmetic_edit_is_not_gated_so_no_cue_claims_the_dialog(self, calendar) -> None:
        result = do_update_event(file_id="evt123", content="new agenda", answer=YES)
        assert isinstance(result, DoResult) and "confirm_gate" not in result.cues

    def test_confirm_true_patches_regardless_of_the_dialog(self, calendar) -> None:
        result = do_update_event(**_MOVE, confirm=True, answer=DeclinedElicitation())
        assert isinstance(result, DoResult) and "confirm_gate" not in result.cues


# ---------------------------------------------------------------------------
# The resolver at the client seam.
# ---------------------------------------------------------------------------

class TestResolverAtTheClientSeam:
    def test_create_event_dialog_text_is_the_preview_message(self, calendar) -> None:
        marker = confirm_gate("create_event", _ctx(True), **_RECURRING)
        assert isinstance(marker, Elicit) and marker.schema is ConfirmAnswer
        assert marker.message == do_create_event(**_RECURRING)["message"]

    def test_update_event_dialog_text_is_the_preview_message(self, calendar) -> None:
        marker = confirm_gate("update_event", _ctx(True), **_MOVE)
        assert isinstance(marker, Elicit)
        assert marker.message == do_update_event(**_MOVE)["message"]

    @pytest.mark.parametrize("operation,args", [
        ("create_event", {**_BOOKING, "confirm": True}),                                   # policy A
        ("create_event", {k: v for k, v in _BOOKING.items() if k != "attendees"}),        # nothing to gate
        ("create_event", {**_BOOKING, "time_max": "2026-09-08T13:00"}),                   # body refuses
        ("create_event", {**_BOOKING, "attendees": " , "}),                                 # no address survives
        ("update_event", {**_MOVE, "confirm": True}),
        ("update_event", {"file_id": "evt123", "content": "new agenda"}),                 # cosmetic only
        ("update_event", {"file_id": "evt123", "time_min": "2026-08-28T10:00"}),          # half a move
        ("update_event", {"file_id": ["evt123"], "attendees": ["x@itv.com"]}),            # not an id
    ])
    def test_answers_none_wherever_the_body_would_not_preview(self, calendar, operation, args) -> None:
        assert confirm_gate(operation, _ctx(True), **args) is None

    def test_a_client_without_the_capability_is_never_asked_and_costs_no_api_read(self, calendar) -> None:
        with patch("tools.create_event.clash_summaries") as clash, \
             patch("tools.update_event.get_event") as get:
            assert confirm_gate("create_event", _ctx(False), **_BOOKING) is None
            assert confirm_gate("update_event", _ctx(False), **_MOVE) is None
        clash.assert_not_called(); get.assert_not_called()

    def test_a_guest_reshape_answers_none_so_the_body_reports_the_refusal(self, calendar) -> None:
        guest = _event(organizer={"email": "boss@itv.com"})
        with patch("tools.update_event.get_event", return_value=guest):
            assert confirm_gate("update_event", _ctx(True), **_MOVE) is None


# ---------------------------------------------------------------------------
# The real envelope, both protocol eras (see test_share_elicit.py's note).
# ---------------------------------------------------------------------------

ERAS = ["legacy", "auto"]
_CREATE = {"operation": "create_event", **_RECURRING}
_UPDATE = {"operation": "update_event", **_MOVE}


@pytest.fixture
def envelope(monkeypatch, tmp_path, calendar):
    monkeypatch.setenv("MISE_TOKEN_PATH", str(tmp_path / "deliberately-absent.json"))
    monkeypatch.setattr("server.cleanup_orphaned_temp_files", lambda: 0)
    return calendar


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
        # The facts a human must see are in the words they were shown.
        if args is _CREATE:
            assert all(a in shown[0] for a in fallback["attendees"])
            assert all(c in shown[0] for c in fallback["clashes"])
            assert fallback["clash_note"] in shown[0]
            assert all(w in shown[0] for w in fallback["cues"]["warnings"])
        else:
            assert all(a in shown[0] for a in fallback["changes"]["attendees_to_add"])

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
