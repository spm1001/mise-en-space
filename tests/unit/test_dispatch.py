"""Tests for do() dispatch infrastructure."""

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import orjson

from models import DoResult, FetchResult, FetchError, SearchResult
import server
from server import do, fetch, search
from tools import OPERATIONS
from tools.dispatch import DISPATCH as _DISPATCH, REQUIRED_PARAMS as _REQUIRED_PARAMS
from tools.remote import REMOTE_ALLOWED_OPS as _REMOTE_ALLOWED_OPS


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _stub_restore_point(monkeypatch):
    """Neutralise the pre-edit restore-point capture (mise-cizuzi) — it makes
    a live revisions.list call. Wiring is asserted in test_restore_point.py;
    here it must never reach the network. Returns {} = merge no-op."""
    monkeypatch.setattr(
        "tools.overwrite.capture_restore_point", lambda file_id, comment=False: {}
    )



class TestDispatchConstant:
    """OPERATIONS constant and _DISPATCH dict stay in sync."""

    def test_operations_matches_dispatch_keys(self) -> None:
        """Every operation in OPERATIONS has a dispatch handler, and vice versa."""
        assert set(OPERATIONS) == set(_DISPATCH.keys())

    def test_operations_is_frozenset(self) -> None:
        """OPERATIONS is immutable."""
        assert isinstance(OPERATIONS, frozenset)

    def test_required_params_matches_dispatch_keys(self) -> None:
        """Every operation in _DISPATCH has a _REQUIRED_PARAMS entry."""
        assert set(_REQUIRED_PARAMS.keys()) == set(_DISPATCH.keys())

    def test_unknown_operation_returns_error(self) -> None:
        result = do(operation="explode")
        assert result["error"] is True
        assert result["kind"] == "invalid_input"
        assert "explode" in result["message"]
        # Error message lists supported operations
        for op in OPERATIONS:
            assert op in result["message"]

    def test_missing_required_params_returns_error(self) -> None:
        """do() with missing required params returns clear error naming them."""
        result = do(operation="move")
        assert result["error"] is True
        assert result["kind"] == "INVALID_INPUT"
        assert "file_id" in result["message"]
        # move's only unconditional required param is now file_id; the folder
        # target (folder_id, or its destination_folder_id alias) is validated
        # in the handler once file_id is supplied — mirrors create's content-OR-source.

    def test_missing_single_required_param(self) -> None:
        result = do(operation="rename", file_id="f1")
        assert result["error"] is True
        assert "title" in result["message"]


class TestSignatureCarriesEveryDispatchParam:
    """Every param a dispatch handler reads must exist in do()'s signature.

    FastMCP derives the tool schema from the signature — a param consumed by
    dispatch but absent from the signature is silently discarded by pydantic
    before dispatch ever sees it (force was dropped this way for two months
    while the tool description advertised it; caught by live smoke, 2026-07-07).
    This is the mechanical guard: adding a param to a handler without adding
    it to do()'s signature fails here, not in production.
    """

    def test_every_handler_param_is_in_do_signature(self) -> None:
        import inspect
        import re
        from pathlib import Path

        dispatch_src = (
            Path(__file__).parents[2] / "tools" / "dispatch.py"
        ).read_text()
        handler_keys = set(re.findall(r'p\.get\("([a-z_]+)"', dispatch_src))
        handler_keys |= set(re.findall(r'p\["([a-z_]+)"\]', dispatch_src))

        injected_by_run_operation = {"_metadata"}
        sig_params = set(inspect.signature(do).parameters)

        missing = handler_keys - sig_params - injected_by_run_operation
        assert not missing, (
            f"dispatch handlers read {sorted(missing)} from params, but do()'s "
            "signature doesn't declare them — FastMCP's schema won't carry them "
            "and pydantic will silently drop callers' values. Add them to the "
            "do() signature in server.py (and the params dict + call_params list)."
        )


# =============================================================================
# The wrong-op param gate (mise-fumuda)
# =============================================================================
#
# do() is one tool with one flat param list, so its schema accepts every param
# name for every operation and the dispatch lambdas decide what actually
# reaches a handler. Anything a lambda doesn't pass is dropped without a word.
# OP_PARAMS records what each lambda passes; run_operation refuses on the
# mismatch. Two things need pinning: that the record is TRUE of the lambdas,
# and that the gate fires across the whole matrix rather than the one param
# (tab=) the wisuzu rail covered.


def _lambda_reads() -> dict[str, set[str]]:
    """What each DISPATCH lambda actually reads out of the params dict.

    Parsed from the source with ast rather than restated: the lambdas are the
    only place that decides what reaches a handler, so a hand-written map is a
    claim ABOUT them and has to be checked against them. ast (not regex)
    because a param read inside a conditional must still count — the map has
    to stay a superset of real consumption or the gate starts refusing calls
    that would have worked.
    """
    import ast

    source = (Path(__file__).parents[2] / "tools" / "dispatch.py").read_text()
    table = None
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "DISPATCH":
            table = node.value
    assert isinstance(table, ast.Dict), "DISPATCH is no longer a literal dict — retune this parse"

    reads: dict[str, set[str]] = {}
    for key, handler in zip(table.keys, table.values):
        keys: set[str] = set()
        for node in ast.walk(handler):
            index = None
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "p":
                index = node.slice
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "p"
                and node.args
            ):
                index = node.args[0]
            if index is None:
                continue
            assert isinstance(index, ast.Constant), (
                f"the {key.value!r} handler reads params by a computed key — this parse "
                "can only see literals, so OP_PARAMS would silently under-claim and the "
                "gate would refuse a param the op really does consume"
            )
            keys.add(index.value)
        reads[key.value] = keys
    return reads


class TestOpParamsMatchDispatch:
    """OP_PARAMS records exactly what the DISPATCH lambdas pass to handlers."""

    def test_op_params_matches_the_lambdas(self) -> None:
        from tools.dispatch import OP_PARAMS

        injected_by_run_operation = {"_metadata"}
        actual = {op: keys - injected_by_run_operation for op, keys in _lambda_reads().items()}
        declared = {op: set(params) for op, params in OP_PARAMS.items()}

        assert declared == actual, (
            "OP_PARAMS has drifted from the DISPATCH lambdas. Whatever a lambda passes "
            "is what the handler can see; everything else is dropped in silence, and "
            "the gate in run_operation reads this map to say so. Update OP_PARAMS."
        )

    def test_every_consumed_param_has_a_default(self) -> None:
        """A param the gate can fire on needs a default to compare against."""
        from tools.dispatch import DO_PARAM_DEFAULTS, OP_PARAMS

        consumed = set().union(*OP_PARAMS.values())
        assert consumed <= set(DO_PARAM_DEFAULTS)

    def test_param_owners_covers_the_whole_signature(self) -> None:
        """Every do() param is in the matrix — including any consumed by no op."""
        import inspect

        from tools.dispatch import PARAM_OWNERS

        signature = {n for n in inspect.signature(do).parameters if n != "operation"}
        assert set(PARAM_OWNERS) == signature


# A value distinguishable from each param's default, for probing the gate.
# Types are plausible rather than load-bearing: the gate refuses before any
# handler runs, so nothing coerces these.
_PROBE_OVERRIDES: dict[str, Any] = {
    "doc_type": "sheet",
    "include": ["1AbC"],
    "attendees": ["someone@example.com"],
    "properties": {"k": "v"},
    "duration": 30,
    "recurrence": "RRULE:FREQ=WEEKLY",
    "file_id": "probe-file-id",
}


def _probe_value(param: str, default: Any) -> Any:
    if param in _PROBE_OVERRIDES:
        return _PROBE_OVERRIDES[param]
    if isinstance(default, bool):
        return not default
    return "probe"


class TestWrongOpParamsRefuse:
    """The whole (param x operation) matrix, not just tab= (mise-fumuda).

    The positive direction runs through server.do — the real caller path —
    because the gate refuses before any handler, so nothing reaches the wire.
    The negative direction asserts on the pure predicate instead: calling a
    legitimate (param, op) pair through do() would run the handler.
    """

    def test_every_wrong_pairing_refuses_and_names_an_owner(self) -> None:
        from tools.dispatch import (
            DO_PARAM_DEFAULTS,
            PARAM_OWNERS,
            UNGATED_PARAMS,
        )

        checked = 0
        for param, owners in sorted(PARAM_OWNERS.items()):
            if param in UNGATED_PARAMS or not owners:
                continue
            value = _probe_value(param, DO_PARAM_DEFAULTS[param])
            for op in sorted(OPERATIONS - owners):
                result = do(operation=op, **{param: value})
                assert result.get("error") is True, (
                    f"do({op}, {param}=...) was accepted — the param cannot reach the "
                    f"handler, so it is being dropped in silence"
                )
                assert result["kind"] == "invalid_input"
                assert f"{param}=" in result["message"]
                for owner in owners:
                    assert owner in result["message"], (
                        f"the refusal for {param}= on {op} must name {owner}, "
                        f"which does take it: {result['message']}"
                    )
                checked += 1
        # The matrix is the point — a collapsed one would pass vacuously.
        # 741 of the 858 cells (22 ops x 39 params) are wrong pairings; the
        # other 117 are the legitimate ones plus base_path's exemption, both
        # covered below. Measured 2026-08-24.
        assert checked > 700, f"only {checked} wrong pairings exercised"

    def test_every_legitimate_pairing_passes_the_gate(self) -> None:
        from tools.dispatch import DO_PARAM_DEFAULTS, PARAM_OWNERS, wrong_op_params

        checked = 0
        for param, owners in PARAM_OWNERS.items():
            value = _probe_value(param, DO_PARAM_DEFAULTS[param])
            for op in owners:
                assert wrong_op_params(op, {param: value}) == [], (
                    f"{param}= is consumed by {op} — the gate must not refuse it"
                )
                checked += 1
        assert checked > 90, f"only {checked} legitimate pairings exercised"

    def test_defaults_alone_never_trip_the_gate(self) -> None:
        """server.py sends every key on every call, most of them None.

        This is the guard that matters: if the gate read presence rather than
        difference-from-default, every do() call in the product would refuse.
        """
        from tools.dispatch import DO_PARAM_DEFAULTS, wrong_op_params

        for op in OPERATIONS:
            assert wrong_op_params(op, dict(DO_PARAM_DEFAULTS)) == []

    def test_partial_params_dict_is_tolerated(self) -> None:
        """Callers inside the repo pass sparse dicts to run_operation."""
        from tools.dispatch import wrong_op_params

        assert wrong_op_params("star", {"file_id": "f1"}) == []
        assert wrong_op_params("star", {}) == []

    def test_base_path_is_exempt_on_every_op(self) -> None:
        """mise_en_space stamps base_path onto every facade call — gating it
        would refuse them all (the exemption is pinned end-to-end in
        tests/unit/test_facade.py)."""
        from tools.dispatch import wrong_op_params

        for op in OPERATIONS:
            assert wrong_op_params(op, {"base_path": "/tmp/anywhere"}) == []

    def test_two_wrong_params_are_both_named(self) -> None:
        result = do(operation="append", file_id="doc1", content="c",
                    source="deposit", range="Sheet1!A1")
        assert result["error"] is True
        assert "source=" in result["message"]
        assert "range=" in result["message"]


class TestWrongOpParamsTeach:
    """The refusal has to say what to do instead — the brief's motivating
    cases, each one a caller mirroring another op's grammar."""

    def test_file_path_on_append_names_its_owners_not_just_content(self) -> None:
        """The reported bug: do(append, file_path=...) answered 'requires
        content', which is true, unhelpful, and silent about the real mistake."""
        result = do(operation="append", file_id="doc1", file_path="/tmp/x.md")
        assert result["error"] is True
        assert "file_path=" in result["message"]
        assert "create" in result["message"] and "overwrite" in result["message"]

    def test_source_on_append_teaches_content(self) -> None:
        result = do(operation="append", file_id="doc1", content="c", source="dep")
        assert result["error"] is True
        assert "content=" in result["message"]

    def test_range_on_append_names_overwrite(self) -> None:
        result = do(operation="append", file_id="s1", content="a,b", range="Tab!A1:B2")
        assert result["error"] is True
        assert "overwrite" in result["message"]

    def test_title_on_overwrite_names_rename(self) -> None:
        result = do(operation="overwrite", file_id="doc1", content="c", title="New")
        assert result["error"] is True
        assert "rename" in result["message"]

    def test_gate_precedes_the_required_params_check(self) -> None:
        """Ordering is the fix, not a detail: with the required-params check
        first, the wrong param stays hidden behind 'append requires: content'
        until the caller has already retried."""
        result = do(operation="append", file_path="/tmp/x.md")
        assert result["error"] is True
        assert "file_path=" in result["message"]

    def test_tab_teaching_survives_the_generalisation(self) -> None:
        """The wisuzu one-param rail folded into the matrix — its text stays."""
        result = do(operation="create", content="body", title="T", tab="Redraft")
        assert result["error"] is True
        assert "append" in result["message"]
        assert "NEW tab" in result["message"]
        assert "replace_text applies across ALL tabs" in result["message"]


class TestRunOperationNeverRaises:
    """run_operation is a do()-funnel of the two-tier error contract
    (CLAUDE.md → Error Handling): whatever a handler throws, the caller
    gets a structured error dict, never an exception."""

    def test_handler_exception_wrapped_as_internal(self) -> None:
        from tools.dispatch import run_operation

        with patch.dict(_DISPATCH, {"star": MagicMock(side_effect=RuntimeError("boom"))}):
            result = run_operation("star", {"file_id": "f1"})

        assert result["error"] is True
        assert result["kind"] == "INTERNAL"
        assert "boom" in result["message"]
        assert result["retryable"] is False

    def test_escaped_miseerror_also_caught(self) -> None:
        """Handlers format their own MiseErrors; one escaping is a handler
        bug — still never raises, wrapped as INTERNAL."""
        from models import ErrorKind, MiseError
        from tools.dispatch import run_operation

        with patch.dict(
            _DISPATCH,
            {"star": MagicMock(side_effect=MiseError(ErrorKind.NOT_FOUND, "gone"))},
        ):
            result = run_operation("star", {"file_id": "f1"})

        assert result["error"] is True
        assert result["kind"] == "INTERNAL"


class TestAllOperationsReturnDoResult:
    """Every operation returns DoResult on success (not raw dict)."""

    @patch("retry.time.sleep")
    @patch("tools.move.get_sync_client")
    def test_move_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.move import do_move

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.side_effect = [
            {"mimeType": "application/vnd.google-apps.folder", "name": "Dest"},
            {"id": "f1", "name": "Test", "parents": ["old"], "webViewLink": ""},
        ]
        mock_client.patch_json.return_value = {
            "id": "f1", "name": "Test", "parents": ["new"], "webViewLink": "",
        }

        result = do_move("f1", "new")
        assert isinstance(result, DoResult)
        assert result.operation == "move"

    @patch("retry.time.sleep")
    @patch("tools.rename.get_sync_client")
    def test_rename_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.rename import do_rename

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.patch_json.return_value = {
            "id": "f1", "name": "New Name", "webViewLink": "",
        }

        result = do_rename("f1", "New Name")
        assert isinstance(result, DoResult)
        assert result.operation == "rename"

    @patch("retry.time.sleep")
    @patch("tools.share.get_sync_client")
    def test_share_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.share import do_share

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "id": "f1", "name": "Doc", "webViewLink": "",
        }

        result = do_share("f1", "alice@example.com", confirm=True)
        assert isinstance(result, DoResult)
        assert result.operation == "share"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_prepend_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.edit import do_prepend

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "title": "Doc", "body": {"content": [{"endIndex": 50}]},
        }

        result = do_prepend("doc1", "hello")
        assert isinstance(result, DoResult)
        assert result.operation == "prepend"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_append_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.edit import do_append

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "title": "Doc", "body": {"content": [{"endIndex": 50}]},
        }

        result = do_append("doc1", "hello")
        assert isinstance(result, DoResult)
        assert result.operation == "append"

    @patch("retry.time.sleep")
    @patch("tools.edit.get_sync_client")
    def test_replace_text_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.edit import do_replace_text

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_json.return_value = {
            "title": "Doc", "body": {"content": [{"endIndex": 50}]},
        }
        mock_client.post_json.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 1}}],
        }

        result = do_replace_text("doc1", "old", "new")
        assert isinstance(result, DoResult)
        assert result.operation == "replace_text"

    @patch("retry.time.sleep")
    @patch("tools.overwrite.upload_file_content")
    def test_overwrite_returns_do_result(self, mock_upload, _sleep) -> None:
        from tools.overwrite import do_overwrite

        mock_upload.return_value = {"name": "Doc"}

        result = do_overwrite(file_id="doc1", content="hello")
        assert isinstance(result, DoResult)
        assert result.operation == "overwrite"

    @patch("retry.time.sleep")
    @patch("tools.create.get_sync_client")
    def test_create_returns_do_result(self, mock_get_client, _sleep) -> None:
        from tools.create import do_create

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.request.return_value = MagicMock(
            content=orjson.dumps({
                "id": "doc1",
                "webViewLink": "https://docs.google.com/document/d/doc1/edit",
                "name": "Test",
            })
        )

        result = do_create("# Test", "Test")
        assert isinstance(result, DoResult)
        assert result.operation == "create"


class TestRemoteModeFiltering:
    """Remote mode restricts do() to safe operations only."""

    def test_remote_allowed_ops_is_subset_of_operations(self) -> None:
        """Every remote-allowed op must exist in the full OPERATIONS set."""
        assert _REMOTE_ALLOWED_OPS <= OPERATIONS

    def test_remote_blocks_restricted_ops(self) -> None:
        """Restricted operations return clear error in remote mode."""
        restricted = OPERATIONS - _REMOTE_ALLOWED_OPS
        assert len(restricted) > 0, "Test is meaningless if nothing is restricted"

        with patch.object(server, "_REMOTE_MODE", True):
            for op in restricted:
                result = do(operation=op)
                assert result["error"] is True, f"{op} should be blocked"
                assert "remote mode" in result["message"].lower(), f"{op} error unclear"

    def test_remote_error_does_not_leak_restricted_ops(self) -> None:
        """Error message lists only allowed ops, not the full set."""
        with patch.object(server, "_REMOTE_MODE", True):
            result = do(operation="overwrite")
            # Should list allowed ops
            for op in _REMOTE_ALLOWED_OPS:
                assert op in result["message"]
            # Should NOT list restricted ops
            for op in (OPERATIONS - _REMOTE_ALLOWED_OPS):
                assert op not in result["message"]

    def test_remote_allows_safe_ops(self) -> None:
        """Allowed ops pass through the remote gate (may still fail on params)."""
        with patch.object(server, "_REMOTE_MODE", True):
            for op in _REMOTE_ALLOWED_OPS:
                result = do(operation=op)
                # Should NOT get the "remote mode" error — may get param errors instead
                if result.get("error"):
                    assert "remote mode" not in result["message"].lower(), (
                        f"{op} was blocked by remote gate but shouldn't be"
                    )

    def test_stdio_mode_allows_all_ops(self) -> None:
        """In stdio mode, all ops pass through the remote gate."""
        with patch.object(server, "_REMOTE_MODE", False):
            result = do(operation="overwrite")
            # Should NOT get remote mode error (may get param error)
            if result.get("error"):
                assert "remote mode" not in result["message"].lower()


class TestDoResultToDictRoundTrip:
    """DoResult.to_dict() produces the expected MCP response shape."""

    def test_basic_to_dict(self) -> None:
        result = DoResult(
            file_id="f1", title="Test", web_link="https://example.com",
            operation="move", cues={"key": "val"},
        )
        d = result.to_dict()
        assert d == {
            "file_id": "f1", "title": "Test", "web_link": "https://example.com",
            "operation": "move", "cues": {"key": "val"},
        }

    def test_extras_merged_into_dict(self) -> None:
        result = DoResult(
            file_id="f1", title="Test", web_link="https://example.com",
            operation="create", cues={}, extras={"type": "doc"},
        )
        d = result.to_dict()
        assert d["type"] == "doc"
        assert d["operation"] == "create"


class TestFetchResultInlineContent:
    """FetchResult carries inline content for remote mode."""

    def test_to_dict_omits_content_when_none(self) -> None:
        result = FetchResult(
            path="mise/doc--test--abc/", content_file="mise/doc--test--abc/content.md",
            format="markdown", type="doc", metadata={"title": "Test"},
        )
        d = result.to_dict()
        assert "content" not in d
        assert "comments" not in d

    def test_to_dict_includes_content_when_set(self) -> None:
        result = FetchResult(
            path="mise/doc--test--abc/", content_file="mise/doc--test--abc/content.md",
            format="markdown", type="doc", metadata={"title": "Test"},
            content="# Hello\n\nWorld", comments="## Comments\n\n- Fix typo",
        )
        d = result.to_dict()
        assert d["content"] == "# Hello\n\nWorld"
        assert d["comments"] == "## Comments\n\n- Fix typo"


class TestRemoteFetch:
    """Remote fetch reads content back from deposit and returns inline."""

    def _make_fetch_result(self, base_path: Path, content: str, comments: str | None = None) -> FetchResult:
        """Create a deposit folder with content and return a FetchResult pointing to it."""
        folder = base_path / ".mise" / "doc--test--abc123"
        folder.mkdir(parents=True, exist_ok=True)
        content_path = folder / "content.md"
        content_path.write_text(content)
        if comments:
            (folder / "comments.md").write_text(comments)
        return FetchResult(
            path=str(folder), content_file=str(content_path),
            format="markdown", type="doc", metadata={"title": "Test"},
            cues={"files": ["content.md"], "warnings": [], "content_length": len(content)},
        )

    def test_remote_fetch_includes_inline_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected_content = "# Test Document\n\nHello world."
            result = self._make_fetch_result(Path(tmp), expected_content)

            with patch.object(server, "_REMOTE_MODE", True), \
                 patch("tools.remote.do_fetch", return_value=result):
                d = fetch(file_id="abc123", base_path=tmp)

        assert d["content"] == expected_content
        assert "comments" not in d

    def test_remote_fetch_includes_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._make_fetch_result(
                Path(tmp), "# Doc", comments="## Open Comments\n\n- Fix this",
            )

            with patch.object(server, "_REMOTE_MODE", True), \
                 patch("tools.remote.do_fetch", return_value=result):
                d = fetch(file_id="abc123", base_path=tmp)

        assert d["content"] == "# Doc"
        assert d["comments"] == "## Open Comments\n\n- Fix this"

    def test_remote_fetch_uses_temp_dir_when_no_base_path(self) -> None:
        """When base_path is empty, remote fetch creates a temp dir and cleans up."""
        with tempfile.TemporaryDirectory() as tmp:
            result = self._make_fetch_result(Path(tmp), "# Content")

            with patch.object(server, "_REMOTE_MODE", True), \
                 patch("tools.remote.do_fetch", return_value=result) as mock_fetch:
                d = fetch(file_id="abc123")

            # do_fetch was called (base_path will be the temp dir)
            assert mock_fetch.called
            assert d["content"] == "# Content"

    def test_remote_fetch_passes_through_errors(self) -> None:
        error = FetchError(kind="not_found", message="File not found")

        with patch.object(server, "_REMOTE_MODE", True), \
             patch("tools.remote.do_fetch", return_value=error):
            d = fetch(file_id="abc123", base_path="/tmp")

        assert d["error"] is True
        assert d["kind"] == "not_found"

    def test_stdio_fetch_does_not_inline(self) -> None:
        """Stdio mode returns normal result without inline content."""
        result = FetchResult(
            path="mise/doc--test--abc/", content_file="mise/doc--test--abc/content.md",
            format="markdown", type="doc", metadata={"title": "Test"}, cues={},
        )

        with patch.object(server, "_REMOTE_MODE", False), \
             patch("server.do_fetch", return_value=result):
            d = fetch(file_id="abc123", base_path="/tmp/project")

        assert "content" not in d

    def test_remote_fetch_skips_binary_content(self) -> None:
        """Binary formats (images) get metadata but no inline content."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / ".mise" / "image--photo--abc123"
            folder.mkdir(parents=True)
            img_path = folder / "content.png"
            img_path.write_bytes(b"\x89PNG fake image data")

            result = FetchResult(
                path=str(folder), content_file=str(img_path),
                format="image", type="image", metadata={"title": "Photo"},
                cues={"files": ["content.png"], "warnings": [], "content_length": 20},
            )

            with patch.object(server, "_REMOTE_MODE", True), \
                 patch("tools.remote.do_fetch", return_value=result):
                d = fetch(file_id="abc123", base_path=tmp)

        assert "content" not in d
        assert any("binary" in w.lower() for w in d["cues"]["warnings"])


class TestRemoteSearch:
    """Remote search returns full results inline without filesystem deposit."""

    def test_remote_search_returns_full_results(self) -> None:
        search_result = SearchResult(
            query="Q4 planning",
            sources=["drive"],
            drive_results=[
                {"name": "Q4 Report", "id": "abc123", "mimeType": "application/vnd.google-apps.document"},
                {"name": "Q4 Budget", "id": "def456", "mimeType": "application/vnd.google-apps.spreadsheet"},
            ],
        )
        # Simulate do_search setting the path (as it normally does)
        search_result.path = "/tmp/mise/search--q4-planning--2026.json"

        with patch.object(server, "_REMOTE_MODE", True), \
             patch("tools.remote.do_search", return_value=search_result):
            d = search(query="Q4 planning", base_path="/tmp")

        # Remote mode strips path — full results returned inline
        assert "path" not in d
        assert d["query"] == "Q4 planning"
        assert len(d["drive_results"]) == 2

    def test_remote_search_works_without_base_path(self) -> None:
        search_result = SearchResult(
            query="test", sources=["drive"], drive_results=[],
        )
        search_result.path = "/tmp/mise/search--test.json"

        with patch.object(server, "_REMOTE_MODE", True), \
             patch("tools.remote.do_search", return_value=search_result):
            d = search(query="test")

        assert d["query"] == "test"

    def test_stdio_search_requires_base_path(self) -> None:
        with patch.object(server, "_REMOTE_MODE", False):
            d = search(query="test")

        assert d["error"] is True
        assert "base_path" in d["message"]


class TestRemoteFilePathGate:
    """file_path reads the SERVER's filesystem — rejected outright in remote
    mode (the gap CLAUDE.md documented as 'currently no remote gate'). In
    stdio the param is deliberately unrestricted (mise-jebude)."""

    def test_remote_rejects_file_path(self) -> None:
        with patch.object(server, "_REMOTE_MODE", True):
            # create is a remote-ALLOWED op, so this exercises the file_path
            # gate specifically, not the op gate.
            result = do(operation="create", title="t", doc_type="doc",
                        file_path="/tmp/x.md")
            assert result["error"] is True
            assert "file_path" in result["message"]

    def test_stdio_passes_file_path_gate(self) -> None:
        with patch.object(server, "_REMOTE_MODE", False):
            result = do(operation="create", title="t", doc_type="doc",
                        file_path="/nonexistent/never/x.md",
                        base_path="/tmp")
            # Fails on file-not-found downstream — NOT on a remote/file_path gate
            assert result["error"] is True
            assert "not found" in result["message"].lower()


class TestRemoteDraftUpdateGate:
    """Remote 'draft' is create-only: update mode (file_id) rewrites an
    existing draft — destructive to a human's hand-edits, outside the
    audited safe set (mise-wemuki)."""

    def test_remote_rejects_draft_update(self) -> None:
        with patch.object(server, "_REMOTE_MODE", True):
            # draft is a remote-ALLOWED op, so this exercises the update
            # gate specifically, not the op gate.
            result = do(operation="draft", file_id="r123456", content="x")
            assert result["error"] is True
            assert "update" in result["message"].lower()

    def test_remote_still_allows_draft_create(self) -> None:
        with patch.object(server, "_REMOTE_MODE", True), \
                patch("tools.dispatch.do_draft") as mock_draft:
            mock_draft.return_value = {"operation": "draft", "file_id": "r1"}
            result = do(operation="draft", to="a@b.c", subject="s", content="x")
            assert not result.get("error")
            mock_draft.assert_called_once()
