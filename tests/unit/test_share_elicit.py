"""The elicit-or-confirm seam on do(share) — mise-jonoha.

Two paths, one preview text. Where the connected client declared form
elicitation, the share confirmation rides mcp's resolver injection (the
`share_confirm` resolver on do()'s `share_answer`) and executes only on an
accepted proceed=true; everywhere else the existing preview-then-confirm=True
round-trip runs unchanged. Pinned at three depths: the body's reading of an
outcome, the resolver against declared capabilities, and the real envelope —
an in-memory mcp Client against server.mcp in BOTH protocol eras (legacy
back-channel, which is Claude Code today, and 2026-07-28 InputRequiredResult).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.elicitation import AcceptedElicitation, CancelledElicitation, DeclinedElicitation
from mcp.server.mcpserver import Elicit
from mcp.types import (
    ClientCapabilities,
    ElicitationCapability,
    FormElicitationCapability,
    UrlElicitationCapability,
)

from models import DoResult
from tools.elicit import ConfirmAnswer, client_supports_elicitation, dialog_verdict
from tools.share import do_share, share_confirm

_META = {"id": "f1", "name": "Report", "webViewLink": "https://docs.google.com/d/f1"}
_PREVIEW_TEXT = "Would share 'Report' with alice@example.com as reader"
YES = AcceptedElicitation(data=ConfirmAnswer(proceed=True))
NO_VIA_FORM = AcceptedElicitation(data=ConfirmAnswer(proceed=False))
NOT_ASKED = AcceptedElicitation[object].model_construct(data=None)  # resolver returned None


def _client() -> MagicMock:
    client = MagicMock()
    client.get_json.return_value = dict(_META)
    return client


def _ctx(elicitation: ElicitationCapability | None) -> SimpleNamespace:
    caps = ClientCapabilities(elicitation=elicitation) if elicitation is not None else ClientCapabilities()
    return SimpleNamespace(client_capabilities=caps)


class TestVerdictReading:
    def test_not_asked_is_none_not_a_yes(self) -> None:
        assert dialog_verdict(None) is None
        assert dialog_verdict(NOT_ASKED) is None

    @pytest.mark.parametrize("outcome,action", [
        (YES, "accept"), (NO_VIA_FORM, "decline"),
        (DeclinedElicitation(), "decline"), (CancelledElicitation(), "cancel"),
    ])
    def test_each_outcome_maps_to_its_action(self, outcome, action) -> None:
        got = dialog_verdict(outcome)
        assert got is not None and got[0] == action
        assert "human" not in got[1].lower()  # never "the human approved"


class TestBodyOnAVerdict:
    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_accept_executes_with_a_mechanism_naming_cue(self, get_client, _sleep) -> None:
        client = _client(); get_client.return_value = client
        result = do_share("f1", "alice@example.com", answer=YES)
        assert isinstance(result, DoResult)
        assert client.post_json.call_count == 1
        assert result.cues["confirm_gate"].startswith("elicitation:")
        assert "human" not in result.cues["confirm_gate"].lower()

    @pytest.mark.parametrize("outcome,action", [
        (NO_VIA_FORM, "decline"), (DeclinedElicitation(), "decline"), (CancelledElicitation(), "cancel"),
    ])
    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_anything_but_accept_shares_nothing(self, get_client, _sleep, outcome, action) -> None:
        client = _client(); get_client.return_value = client
        result = do_share("f1", "alice@example.com", answer=outcome)
        assert not isinstance(result, DoResult) and result["preview"] is True
        assert result["message"] == _PREVIEW_TEXT
        assert client.post_json.call_count == 0
        assert action in result["cues"]["confirm_gate"]
        # cancel = no dialog decided this, confirm= stays open; decline = an answer, it closes.
        assert ("confirm_required" in result["cues"]) == (action == "cancel")
        if action == "decline":
            assert "confirm=True" not in result["cues"]["confirm_gate"]

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_no_dialog_returns_todays_preview_byte_for_byte(self, get_client, _sleep) -> None:
        get_client.return_value = _client()
        assert do_share("f1", "alice@example.com", answer=NOT_ASKED) == do_share("f1", "alice@example.com")
        assert "confirm_gate" not in do_share("f1", "alice@example.com", answer=None)["cues"]

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_confirm_true_executes_regardless_of_the_dialog(self, get_client, _sleep) -> None:
        client = _client(); get_client.return_value = client
        result = do_share("f1", "alice@example.com", confirm=True, answer=CancelledElicitation())
        assert isinstance(result, DoResult) and client.post_json.call_count == 1
        assert "confirm_gate" not in result.cues

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_validation_errors_win_over_any_answer(self, get_client, _sleep) -> None:
        client = _client(); get_client.return_value = client
        assert do_share("f1", "alice@example.com", role="owner", answer=YES)["error"] is True
        assert client.post_json.call_count == 0


class TestResolverAtTheClientSeam:
    @pytest.mark.parametrize("cap,expected", [
        (None, False),
        (ElicitationCapability(), True),                                    # bare {} = form (pre-modes shape)
        (ElicitationCapability(form=FormElicitationCapability()), True),
        (ElicitationCapability(url=UrlElicitationCapability()), False),    # url-only cannot render a form
    ])
    def test_capability_rule_mirrors_the_framework(self, cap, expected) -> None:
        assert client_supports_elicitation(_ctx(cap)) is expected

    def test_no_session_means_no(self) -> None:
        class NoRequest:
            @property
            def client_capabilities(self):
                raise ValueError("Context is not available outside of a request")
        assert client_supports_elicitation(NoRequest()) is False
        assert client_supports_elicitation(object()) is False

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_asks_only_for_an_unconfirmed_share_on_a_capable_client(self, get_client, _sleep) -> None:
        get_client.return_value = _client()
        capable = _ctx(ElicitationCapability())
        marker = share_confirm("share", capable, file_id="f1", to="alice@example.com")
        assert isinstance(marker, Elicit)
        assert marker.message == _PREVIEW_TEXT  # the dialog text IS the preview text
        assert marker.schema is ConfirmAnswer
        assert share_confirm("share", capable, file_id="f1", to="alice@example.com", confirm=True) is None
        assert share_confirm("rename", capable, file_id="f1", to="alice@example.com") is None
        assert share_confirm("share", _ctx(None), file_id="f1", to="alice@example.com") is None

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_bad_inputs_and_drive_errors_answer_none_so_the_body_reports_them(self, get_client, _sleep) -> None:
        from models import ErrorKind, MiseError
        client = _client(); get_client.return_value = client
        capable = _ctx(ElicitationCapability())
        assert share_confirm("share", capable, file_id="f1", to="alice@example.com", role="owner") is None
        assert share_confirm("share", capable, file_id=["f1", "f2"], to="alice@example.com") is None
        assert share_confirm("share", capable, file_id="f1", to=None) is None
        client.get_json.side_effect = MiseError(ErrorKind.NOT_FOUND, "gone")
        assert share_confirm("share", capable, file_id="f1", to="alice@example.com") is None
        assert client.post_json.call_count == 0


# ---------------------------------------------------------------------------
# The real envelope: an in-memory mcp Client against server.mcp. This crosses
# the MCPServer registration, resolver injection into a SYNC tool body, the
# capability declaration at initialize, and the era switch the framework makes
# on the negotiated protocol: mode="legacy" is the initialize handshake with a
# back-channel (Claude Code today — elicitation/create mid-call); the default
# mode is >= 2026-07-28, where the question rides an InputRequiredResult and
# the client retries with the answer. Neither is reachable from the unit seam.
# ---------------------------------------------------------------------------

import json

from mcp import Client
from mcp.types import INVALID_REQUEST, ElicitResult, ErrorData

import server  # registers search/fetch/do on server.mcp

_ARGS = {"operation": "share", "file_id": "f1", "to": "alice@example.com"}
ERAS = ["legacy", "auto"]


@pytest.fixture
def drive(monkeypatch, tmp_path) -> MagicMock:
    """Hermetic: fake Drive, no token on disk, no lifespan Drive sweep."""
    fake = _client()
    monkeypatch.setenv("MISE_TOKEN_PATH", str(tmp_path / "deliberately-absent.json"))
    monkeypatch.setattr("server.cleanup_orphaned_temp_files", lambda: 0)
    monkeypatch.setattr("tools.share.get_sync_client", lambda: fake)
    monkeypatch.setattr("retry.time.sleep", lambda *_a, **_k: None)
    return fake


def _payload(result) -> dict:
    if result.structured_content:
        return result.structured_content
    return json.loads("".join(getattr(b, "text", "") for b in result.content))


class TestGateThroughTheEnvelope:
    @pytest.mark.parametrize("mode", ERAS)
    async def test_client_without_the_capability_gets_the_confirm_round_trip(self, drive, mode) -> None:
        async with Client(server.mcp, mode=mode) as c:  # no elicitation_callback => capability not declared
            schema = {t.name: t.input_schema for t in (await c.list_tools()).tools}["do"]
            assert "share_answer" not in schema["properties"]  # never a wire param
            preview = _payload(await c.call_tool("do", _ARGS))
            assert preview["preview"] is True and "confirm_gate" not in preview["cues"]
            assert preview["cues"]["confirm_required"].startswith("This is a preview.")
            assert drive.post_json.call_count == 0
            done = _payload(await c.call_tool("do", {**_ARGS, "confirm": True}))
        assert done["operation"] == "share" and not done.get("preview")
        assert drive.post_json.call_count == 1

    @pytest.mark.parametrize("mode", ERAS)
    async def test_accept_executes_and_the_dialog_shows_the_preview_text(self, drive, mode) -> None:
        shown: list[str] = []

        async def accept(_context, params):
            shown.append(params.message)
            return ElicitResult(action="accept", content={"proceed": True})

        async with Client(server.mcp, mode=mode, elicitation_callback=accept) as c:
            done = _payload(await c.call_tool("do", _ARGS))
        assert drive.post_json.call_count == 1
        assert done["cues"]["confirm_gate"].startswith("elicitation:")
        assert "human" not in done["cues"]["confirm_gate"].lower()
        async with Client(server.mcp, mode=mode) as c:  # same facts either way
            fallback = _payload(await c.call_tool("do", _ARGS))
        assert shown == [fallback["message"]] == [_PREVIEW_TEXT]

    @pytest.mark.parametrize("mode", ERAS)
    @pytest.mark.parametrize("action", ["cancel", "decline"])
    async def test_cancel_or_decline_shares_nothing(self, drive, mode, action) -> None:
        async def answer(_context, _params):
            return ElicitResult(action=action)

        async with Client(server.mcp, mode=mode, elicitation_callback=answer) as c:
            out = _payload(await c.call_tool("do", _ARGS))
        assert drive.post_json.call_count == 0
        assert out["preview"] is True and action in out["cues"]["confirm_gate"]
        assert ("confirm_required" in out["cues"]) == (action == "cancel")

    async def test_a_capable_client_that_errors_the_request_shares_nothing_and_fails_loud(self, drive) -> None:
        async def refuse(_context, _params):
            return ErrorData(code=INVALID_REQUEST, message="no dialogs on this surface")

        # Undesigned failure (a client that declared the capability and then errors
        # the request): loud, not a silent fallback. The MCPError escapes the call,
        # wrapped in anyio's task-group exception group by the in-memory transport.
        from mcp.shared.exceptions import MCPError

        def leaves(exc: BaseException) -> list[BaseException]:
            return [x for e in exc.exceptions for x in leaves(e)] if isinstance(exc, BaseExceptionGroup) else [exc]

        with pytest.raises((MCPError, BaseExceptionGroup)) as info:
            async with Client(server.mcp, mode="legacy", elicitation_callback=refuse) as c:
                await c.call_tool("do", _ARGS)
        assert any(isinstance(e, MCPError) and "no dialogs on this surface" in str(e) for e in leaves(info.value))
        assert drive.post_json.call_count == 0
