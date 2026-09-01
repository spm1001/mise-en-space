"""Tests for Google Doc tab placement (mise-wisuzu).

The regression that matters most here is the SINGLE-BATCH HAZARD PIN:
[addDocumentTab + insertText with no tabId] in one batch returns 200 and
silently writes into the ORIGINAL tab (probed live 2026-08-24,
docs/research/2026-08-24-givige-tab-probe/probe_one_batch_fill.py). The
route must stay two sequential batchUpdates — these tests fail if anyone
"optimises" the add and the fill into one call, or lets a caller-supplied
tabId ride the add (a categorical 400 on the real API).
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from models import DoResult, ErrorKind, MiseError
from tools.doc_tabs import add_tab_with_content, get_doc_tabs_meta
from tools.edit import do_append, do_replace_text


def _http_error(status: int, reason: str = "boom") -> httpx.HTTPStatusError:
    """A REAL wire-shaped error. MiseSyncClient raises httpx.HTTPStatusError,
    not MiseError — a test injecting MiseError exercises an exception type
    the wire never produces (essayeur catch 2026-08-24: the first orphan
    test passed while the production path was dead code)."""
    req = httpx.Request(
        "POST", "https://docs.googleapis.com/v1/documents/doc123:batchUpdate"
    )
    resp = httpx.Response(
        status, request=req, json={"error": {"message": reason}}
    )
    return httpx.HTTPStatusError(str(status), request=req, response=resp)

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"

MINTED = {"tabId": "t.minted123", "title": "Redraft", "index": 1}


@pytest.fixture(autouse=True)
def _stub_restore_point(monkeypatch):
    """The tab path captures a pre-edit restore point like every other doc
    mutation; wiring is asserted in test_restore_point.py — never network here."""
    monkeypatch.setattr(
        "tools.edit.capture_restore_point", lambda file_id, comment=False: {}
    )


def _mock_client_for_add(minted: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.post_json.return_value = {
        "replies": [{"addDocumentTab": {"tabProperties": minted or MINTED}}]
    }
    return client


def _doc_meta(tabs: list[dict] | None = None, title: str = "Test Doc") -> dict:
    if tabs is None:
        tabs = [{"tab_id": "t.0", "title": "Tab 1", "index": 0, "depth": 0}]
    return {"title": title, "tabs": tabs}


# =============================================================================
# add_tab_with_content — the two-batch contract
# =============================================================================

class TestAddTabWithContent:
    @patch("tools.doc_tabs.get_sync_client")
    def test_two_sequential_batches_add_then_fill(self, mock_get) -> None:
        client = _mock_client_for_add()
        mock_get.return_value = client

        minted = add_tab_with_content("doc123", "Redraft", "body text")

        assert minted == MINTED
        assert client.post_json.call_count == 2
        first = client.post_json.call_args_list[0].kwargs["json_body"]
        second = client.post_json.call_args_list[1].kwargs["json_body"]

        # HAZARD PIN: the first batch is add-ONLY. A single batch carrying
        # both requests returns 200 and writes the ORIGINAL tab (probed).
        assert len(first["requests"]) == 1
        assert "addDocumentTab" in first["requests"][0]
        assert not any("insertText" in r for r in first["requests"])

        # HAZARD PIN: no caller-supplied tabId on the add — the server
        # mints it; supplying one is a categorical 400 (probed).
        add_props = first["requests"][0]["addDocumentTab"]["tabProperties"]
        assert "tabId" not in add_props
        assert add_props["title"] == "Redraft"

        # The fill addresses the MINTED tabId explicitly, at index 1.
        insert = second["requests"][0]["insertText"]
        assert insert["location"] == {"tabId": "t.minted123", "index": 1}
        assert insert["text"] == "body text"

    @patch("tools.doc_tabs.get_sync_client")
    def test_fill_failure_names_the_orphan_tab(self, mock_get) -> None:
        """Uses the REAL exception type the wire produces (httpx), so this
        test is red if the httpx→MiseError conversion ever drops out of the
        fill path again."""
        client = _mock_client_for_add()
        client.post_json.side_effect = [
            {"replies": [{"addDocumentTab": {"tabProperties": MINTED}}]},
            _http_error(400, "bad insert"),
        ]
        mock_get.return_value = client

        with pytest.raises(MiseError) as exc:
            add_tab_with_content("doc123", "Redraft", "body")
        # The orphan empty tab is named, and the no-blind-retry warning is
        # explicit — a retry would mint a SECOND tab.
        assert "t.minted123" in exc.value.message
        assert "ANOTHER tab" in exc.value.message
        assert exc.value.retryable is False


class TestGetDocTabsMeta:
    @patch("tools.doc_tabs.get_sync_client")
    def test_flattens_child_tabs_depth_first(self, mock_get) -> None:
        client = MagicMock()
        client.get_json.return_value = {
            "title": "Doc",
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0", "title": "Tab 1", "index": 0},
                    "childTabs": [
                        {"tabProperties": {"tabId": "t.c", "title": "Child", "index": 0}}
                    ],
                },
                {"tabProperties": {"tabId": "t.1", "title": "Tab 2", "index": 1}},
            ],
        }
        mock_get.return_value = client

        meta = get_doc_tabs_meta("doc123")
        assert [t["tab_id"] for t in meta["tabs"]] == ["t.0", "t.c", "t.1"]
        assert [t["depth"] for t in meta["tabs"]] == [0, 1, 0]
        # Content must never ride this read — properties-only fields mask.
        params = client.get_json.call_args.kwargs.get("params") or client.get_json.call_args.args[1]
        assert "documentTab" not in str(params.get("fields", ""))

    @patch("tools.doc_tabs.get_sync_client")
    def test_single_tab_doc_reports_one(self, mock_get) -> None:
        client = MagicMock()
        client.get_json.return_value = {
            "title": "Doc",
            "tabs": [{"tabProperties": {"tabId": "t.0", "title": "Tab 1", "index": 0}}],
        }
        mock_get.return_value = client
        assert len(get_doc_tabs_meta("doc123")["tabs"]) == 1


# =============================================================================
# do_append tab= routing
# =============================================================================

class TestAppendTabRouting:
    @patch("tools.edit.add_tab_with_content", return_value=MINTED)
    @patch("tools.edit.get_doc_tabs_meta", return_value=_doc_meta())
    def test_tab_on_doc_returns_tab_cues(self, _meta, mock_add) -> None:
        result = do_append(
            "doc123", "redraft body", metadata={"mimeType": GOOGLE_DOC_MIME},
            tab="Redraft",
        )
        assert isinstance(result, DoResult)
        assert result.operation == "append"
        assert result.cues["tab_id"] == "t.minted123"
        assert result.cues["tab_title"] == "Redraft"
        assert result.cues["tab_index"] == 1
        assert result.cues["inserted_chars"] == len("redraft body")
        # The honesty cue is always on: tabs are plain text.
        assert "plain text" in result.cues["note"]
        # web_link deep-links to the new tab.
        assert result.web_link.endswith("?tab=t.minted123")
        assert "warnings" not in result.cues
        mock_add.assert_called_once_with("doc123", "Redraft", "redraft body")

    @patch("tools.edit.add_tab_with_content", return_value=MINTED)
    @patch(
        "tools.edit.get_doc_tabs_meta",
        return_value=_doc_meta(
            tabs=[
                {"tab_id": "t.0", "title": "Tab 1", "index": 0, "depth": 0},
                {"tab_id": "t.x", "title": "Redraft", "index": 1, "depth": 0},
            ]
        ),
    )
    def test_duplicate_title_warns_not_refuses(self, _meta, _add) -> None:
        result = do_append(
            "doc123", "body", metadata={"mimeType": GOOGLE_DOC_MIME}, tab="Redraft"
        )
        assert isinstance(result, DoResult)
        assert any("already existed" in w for w in result.cues["warnings"])

    def test_tab_on_sheet_refuses_with_teaching(self) -> None:
        result = do_append(
            "sheet123", "body", metadata={"mimeType": GOOGLE_SHEET_MIME}, tab="New"
        )
        assert result["error"] is True
        assert "spreadsheet" in result["message"]

    def test_tab_on_plain_file_refuses(self) -> None:
        result = do_append(
            "file123", "body", metadata={"mimeType": "text/markdown"}, tab="New"
        )
        assert result["error"] is True
        assert "Google Docs" in result["message"]

    def test_blank_tab_title_refuses(self) -> None:
        result = do_append(
            "doc123", "body", metadata={"mimeType": GOOGLE_DOC_MIME}, tab="   "
        )
        assert result["error"] is True
        assert "non-empty" in result["message"]

    @patch("tools.edit.add_tab_with_content", return_value=MINTED)
    @patch("tools.edit.get_doc_tabs_meta", return_value=_doc_meta())
    def test_tab_reaches_handler_through_do(self, _meta, mock_add) -> None:
        """The seam test: tab= survives server.py -> dispatch -> handler."""
        from server import do

        with patch(
            "tools.dispatch.get_file_metadata",
            return_value={"mimeType": GOOGLE_DOC_MIME, "name": "Test Doc"},
        ):
            result = do(
                operation="append", file_id="doc123", content="body",
                tab="Redraft",
            )
        assert result.get("error") is None
        assert result["cues"]["tab_id"] == "t.minted123"
        mock_add.assert_called_once()

    @patch("tools.edit.get_doc_tabs_meta")
    def test_meta_read_wire_error_returns_kinded_error(self, mock_meta) -> None:
        """A 403 on the pre-add tabs read must surface with its real kind,
        not a generic INTERNAL — proves the tab path's except-MiseError is
        live against converted wire errors."""
        mock_meta.side_effect = MiseError(
            ErrorKind.PERMISSION_DENIED, "no docs scope"
        )
        result = do_append(
            "doc123", "body", metadata={"mimeType": GOOGLE_DOC_MIME}, tab="R"
        )
        assert result["error"] is True
        assert result["kind"] == "permission_denied"

    def test_tab_on_create_refuses_not_drops(self) -> None:
        """Accept-and-drop rail: the wisuzu brief's draft grammar put tab=
        on create — that guess must teach, never silently mint a tabless doc."""
        from server import do

        result = do(operation="create", content="body", title="T", tab="Redraft")
        assert result["error"] is True
        assert "append" in result["message"]

    def test_tab_on_overwrite_refuses_not_drops(self) -> None:
        from server import do

        result = do(operation="overwrite", file_id="doc123", content="c", tab="X")
        assert result["error"] is True
        assert "append" in result["message"]

    @patch("tools.edit._append")
    def test_no_tab_keeps_existing_append_path(self, mock_append) -> None:
        mock_append.return_value = DoResult(
            file_id="doc123", title="T", web_link="w", operation="append", cues={}
        )
        result = do_append("doc123", "body", metadata={"mimeType": GOOGLE_DOC_MIME})
        assert isinstance(result, DoResult)
        mock_append.assert_called_once_with("doc123", "body", False)


# =============================================================================
# Overwrite multi-tab guard
# =============================================================================

class TestOverwriteMultiTabGuard:
    """do(overwrite) rides Drive's whole-file import, which flattens a
    multi-tab doc to one tab (probe_drive_import_vs_tabs.py) — the guard
    refuses rather than corrupt."""

    @patch("tools.overwrite.capture_restore_point")
    @patch("tools.overwrite.upload_file_content")
    @patch(
        "tools.overwrite.get_doc_tabs_meta",
        return_value=_doc_meta(
            tabs=[
                {"tab_id": "t.0", "title": "Tab 1", "index": 0, "depth": 0},
                {"tab_id": "t.1", "title": "Redraft", "index": 1, "depth": 0},
            ]
        ),
    )
    def test_multi_tab_doc_refuses_before_any_write(
        self, _meta, mock_upload, mock_restore
    ) -> None:
        from tools.overwrite import do_overwrite

        result = do_overwrite(
            "doc123", content="new", metadata={"mimeType": GOOGLE_DOC_MIME, "name": "D"}
        )
        assert result["error"] is True
        assert "2 tabs" in result["message"]
        assert "'Redraft'" in result["message"]
        assert "tab='Title'" in result["message"]  # teaches the append route
        # Refusal fires BEFORE the restore point and BEFORE the import —
        # no [agent] comment, no revision, no write.
        mock_upload.assert_not_called()
        mock_restore.assert_not_called()

    @patch("tools.overwrite.capture_restore_point", return_value={})
    @patch("tools.overwrite.upload_file_content", return_value={"name": "D"})
    @patch("tools.overwrite.get_doc_tabs_meta", return_value=_doc_meta())
    def test_single_tab_doc_proceeds_clean(self, _meta, mock_upload, _r) -> None:
        from tools.overwrite import do_overwrite

        result = do_overwrite(
            "doc123", content="new", metadata={"mimeType": GOOGLE_DOC_MIME, "name": "D"}
        )
        assert isinstance(result, DoResult)
        assert "warnings" not in result.cues
        mock_upload.assert_called_once()

    @patch("tools.overwrite.capture_restore_point", return_value={})
    @patch("tools.overwrite.upload_file_content", return_value={"name": "D"})
    @patch(
        "tools.overwrite.get_doc_tabs_meta",
        side_effect=MiseError(ErrorKind.PERMISSION_DENIED, "no docs scope"),
    )
    def test_guard_permission_refusal_fails_open_with_warning(
        self, _meta, mock_upload, _r
    ) -> None:
        """A Drive-scoped credential can write where a Docs read is refused
        — permission-shaped failure is the ONE fail-open case, disclosed."""
        from tools.overwrite import do_overwrite

        result = do_overwrite(
            "doc123", content="new", metadata={"mimeType": GOOGLE_DOC_MIME, "name": "D"}
        )
        assert isinstance(result, DoResult)
        assert any("tab structure" in w for w in result.cues["warnings"])
        mock_upload.assert_called_once()

    @patch("tools.overwrite.capture_restore_point", return_value={})
    @patch("tools.overwrite.upload_file_content")
    @patch(
        "tools.overwrite.get_doc_tabs_meta",
        side_effect=MiseError(ErrorKind.RATE_LIMITED, "quota", retryable=True),
    )
    def test_guard_transient_failure_fails_closed(
        self, _meta, mock_upload, _r
    ) -> None:
        """A transient that survives the read's retries must NOT become a
        silent multi-tab destruction — refuse, don't proceed (essayeur
        catch: the first cut failed open on exactly this class)."""
        from tools.overwrite import do_overwrite

        result = do_overwrite(
            "doc123", content="new", metadata={"mimeType": GOOGLE_DOC_MIME, "name": "D"}
        )
        assert result["error"] is True
        assert "refusing" in result["message"]
        mock_upload.assert_not_called()

    @patch("tools.overwrite.capture_restore_point", return_value={})
    @patch("tools.overwrite.upload_file_content")
    @patch("tools.overwrite.get_doc_tabs_meta", side_effect=KeyError("tabs"))
    def test_guard_schema_drift_fails_closed(
        self, _meta, mock_upload, _r
    ) -> None:
        from tools.overwrite import do_overwrite

        result = do_overwrite(
            "doc123", content="new", metadata={"mimeType": GOOGLE_DOC_MIME, "name": "D"}
        )
        assert result["error"] is True
        mock_upload.assert_not_called()


# =============================================================================
# Surgical-op aiming disclosure on multi-tab docs (essayeur F1)
# =============================================================================

def _client_with_meta(tabs: list[str], occurrences: int = 2) -> MagicMock:
    client = MagicMock()
    client.get_json.return_value = {
        "title": "Doc",
        "tabs": [
            {
                "tabProperties": {"tabId": f"t.{i}", "title": t},
                "documentTab": {"body": {"content": [{"endIndex": 40}]}},
            }
            for i, t in enumerate(tabs)
        ],
    }
    client.post_json.return_value = {
        "replies": [{"replaceAllText": {"occurrencesChanged": occurrences}}]
    }
    return client


class TestSurgicalOpAimingDisclosure:
    @patch("tools.edit.get_sync_client")
    def test_replace_text_multi_tab_discloses_all_tabs(self, mock_get) -> None:
        """replaceAllText with no tabsCriteria hits ALL tabs (verified live)
        — on a multi-tab doc the total must say so, loudly."""
        mock_get.return_value = _client_with_meta(["Tab 1", "Redraft"], 2)
        result = do_replace_text(
            "doc123", find="Dear all", content="Dear team",
            metadata={"mimeType": GOOGLE_DOC_MIME},
        )
        assert isinstance(result, DoResult)
        assert result.cues["occurrences_changed"] == 2
        assert any("ALL" in w for w in result.cues["warnings"])

    @patch("tools.edit.get_sync_client")
    def test_replace_text_single_tab_stays_quiet(self, mock_get) -> None:
        mock_get.return_value = _client_with_meta(["Tab 1"], 1)
        result = do_replace_text(
            "doc123", find="x", content="y",
            metadata={"mimeType": GOOGLE_DOC_MIME},
        )
        assert isinstance(result, DoResult)
        assert "warnings" not in result.cues

    @patch("tools.edit.get_sync_client")
    def test_append_multi_tab_warns_first_tab_only(self, mock_get) -> None:
        client = _client_with_meta(["Tab 1", "Redraft"])
        client.post_json.return_value = {}
        mock_get.return_value = client
        result = do_append(
            "doc123", "more text", metadata={"mimeType": GOOGLE_DOC_MIME}
        )
        assert isinstance(result, DoResult)
        assert any("FIRST tab" in w for w in result.cues["warnings"])
        assert any("'Tab 1'" in w for w in result.cues["warnings"])
