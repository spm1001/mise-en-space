"""Tests for form overwrite — spec-driven form editing (mise-wemuki)."""

import json
from unittest.mock import patch

import pytest

from models import DoResult, ErrorKind, MiseError
from tools.form_edit import _overwrite_requests, form_overwrite


_SPEC = json.dumps({
    "title": "360 for Rupert",
    "description": "Round two",
    "questions": [
        {"type": "checkboxes", "title": "Which projects?",
         "options": ["Alpha", "Beta", "NEW OPTION"]},
        {"type": "paragraph", "title": "Anything else?"},
    ],
})

_METADATA = {"name": "360 for Rupert", "mimeType": "application/vnd.google-apps.form"}


def _existing_form(item_count: int = 3) -> dict:
    return {
        "formId": "form123",
        "responderUri": "https://docs.google.com/forms/d/e/xyz/viewform",
        "items": [{"itemId": f"i{n}"} for n in range(item_count)],
    }


class TestOverwriteRequests:
    def test_shape_and_order(self) -> None:
        spec = json.loads(_SPEC)
        requests = _overwrite_requests(_existing_form(3), spec)

        # 1 updateFormInfo + 3 deletes + 2 creates
        assert len(requests) == 6
        info = requests[0]["updateFormInfo"]
        assert info["info"]["title"] == "360 for Rupert"
        assert info["updateMask"] == "title,description"

        delete_indexes = [r["deleteItem"]["location"]["index"] for r in requests[1:4]]
        assert delete_indexes == [2, 1, 0]  # descending — indexes stay valid

        create_indexes = [r["createItem"]["location"]["index"] for r in requests[4:]]
        assert create_indexes == [0, 1]

    def test_omitted_description_clears(self) -> None:
        spec = {"title": "T", "questions": []}
        requests = _overwrite_requests(_existing_form(0), spec)
        assert requests[0]["updateFormInfo"]["info"]["description"] == ""


class TestFormOverwrite:
    def test_requires_content(self) -> None:
        result = form_overwrite("form123", None, _METADATA)
        assert result["error"] is True
        assert "spec" in result["message"]

    @patch("tools.form_edit._api_batch_update")
    @patch("tools.form_edit.fetch_form")
    def test_invalid_spec_never_touches_api(self, mock_fetch, mock_batch) -> None:
        bad = json.dumps({"title": "T", "questions": [{"type": "nope"}]})
        result = form_overwrite("form123", bad, _METADATA)
        assert result["error"] is True
        mock_fetch.assert_not_called()
        mock_batch.assert_not_called()

    @patch("tools.form_edit._api_batch_update")
    @patch("tools.form_edit.fetch_form", return_value=_existing_form(3))
    def test_success_reports_replacement(self, mock_fetch, mock_batch) -> None:
        result = form_overwrite("form123", _SPEC, _METADATA)

        assert isinstance(result, DoResult)
        assert result.operation == "overwrite"
        assert result.cues["items_replaced"] == 3
        assert result.cues["question_count"] == 2
        assert any("wholesale" in w for w in result.cues["warnings"])
        # One atomic batchUpdate call
        assert mock_batch.call_count == 1

    @patch("tools.form_edit._api_batch_update")
    @patch("tools.form_edit.fetch_form", return_value=_existing_form(1))
    def test_missing_title_falls_back_to_drive_name(
        self, mock_fetch, mock_batch
    ) -> None:
        spec = json.dumps({"questions": [{"type": "paragraph", "title": "Q"}]})
        result = form_overwrite("form123", spec, _METADATA)

        assert isinstance(result, DoResult)
        requests = mock_batch.call_args.args[1]
        assert requests[0]["updateFormInfo"]["info"]["title"] == "360 for Rupert"

    @patch("tools.form_edit.fetch_form")
    def test_fetch_failure_is_clean_error(self, mock_fetch) -> None:
        mock_fetch.side_effect = MiseError(ErrorKind.NOT_FOUND, "nope")
        result = form_overwrite("form123", _SPEC, _METADATA)
        assert result["error"] is True
        assert result["kind"] == "not_found"
