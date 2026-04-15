"""Tests for the Forms creation module — spec parsing, validation, and API mapping."""

import json
from unittest.mock import MagicMock, patch

import yaml

from tools.form_create import (
    _parse_spec,
    _validate_spec,
    _build_question_item,
    _split_title,
    _spec_to_batch_requests,
    create_form,
)
from models import MiseError, ErrorKind


# ============================================================================
# Spec parsing
# ============================================================================


def test_parse_yaml():
    spec = _parse_spec('title: "Test"\nquestions: []')
    assert spec["title"] == "Test"


def test_parse_json():
    spec = _parse_spec('{"title": "Test", "questions": []}')
    assert spec["title"] == "Test"


def test_parse_yaml_preferred_over_json():
    """YAML is tried first — a valid YAML string that's also valid JSON parses as YAML."""
    content = '{"title": "Test"}'
    spec = _parse_spec(content)
    assert spec["title"] == "Test"


def test_parse_invalid_raises():
    import pytest
    with pytest.raises(MiseError) as exc_info:
        _parse_spec("not: [valid: yaml: or json")
    assert exc_info.value.kind == ErrorKind.INVALID_INPUT


def test_parse_non_dict_raises():
    import pytest
    with pytest.raises(MiseError):
        _parse_spec("- just a list")


# ============================================================================
# Spec validation
# ============================================================================


def test_validate_minimal_valid():
    errors = _validate_spec({"title": "Test", "questions": []})
    assert errors == []


def test_validate_missing_title():
    errors = _validate_spec({"questions": []})
    assert any("title" in e for e in errors)


def test_validate_unknown_type():
    errors = _validate_spec({
        "title": "Test",
        "questions": [{"type": "unknown_type", "title": "Q"}],
    })
    assert any("unknown type" in e for e in errors)


def test_validate_missing_options():
    errors = _validate_spec({
        "title": "Test",
        "questions": [{"type": "checkboxes", "title": "Q"}],
    })
    assert any("options" in e for e in errors)


def test_validate_missing_question_title():
    errors = _validate_spec({
        "title": "Test",
        "questions": [{"type": "paragraph"}],
    })
    assert any("title" in e for e in errors)


def test_validate_text_and_section_no_title_required():
    errors = _validate_spec({
        "title": "Test",
        "questions": [
            {"type": "text"},
            {"type": "section_break"},
        ],
    })
    assert errors == []


# ============================================================================
# Question item building
# ============================================================================


def test_build_paragraph():
    item = _build_question_item({"type": "paragraph", "title": "Describe", "required": True})
    assert item["title"] == "Describe"
    q = item["questionItem"]["question"]
    assert q["textQuestion"]["paragraph"] is True
    assert q["required"] is True


def test_build_short_answer():
    item = _build_question_item({"type": "short_answer", "title": "Name"})
    q = item["questionItem"]["question"]
    assert q["textQuestion"]["paragraph"] is False


def test_build_checkboxes():
    item = _build_question_item({
        "type": "checkboxes",
        "title": "Pick",
        "options": ["A", "B"],
        "include_other": True,
    })
    q = item["questionItem"]["question"]
    cq = q["choiceQuestion"]
    assert cq["type"] == "CHECKBOX"
    assert len(cq["options"]) == 3
    assert cq["options"][0] == {"value": "A"}
    assert cq["options"][2] == {"isOther": True}


def test_build_multiple_choice():
    item = _build_question_item({
        "type": "multiple_choice",
        "title": "Pick one",
        "options": ["X", "Y"],
    })
    assert item["questionItem"]["question"]["choiceQuestion"]["type"] == "RADIO"


def test_build_dropdown():
    item = _build_question_item({
        "type": "dropdown",
        "title": "Select",
        "options": ["A"],
    })
    assert item["questionItem"]["question"]["choiceQuestion"]["type"] == "DROP_DOWN"


def test_build_scale():
    item = _build_question_item({
        "type": "scale",
        "title": "Rate",
        "low": 1,
        "high": 10,
        "low_label": "Bad",
        "high_label": "Great",
    })
    sq = item["questionItem"]["question"]["scaleQuestion"]
    assert sq["low"] == 1
    assert sq["high"] == 10
    assert sq["lowLabel"] == "Bad"
    assert sq["highLabel"] == "Great"


def test_build_scale_defaults():
    item = _build_question_item({"type": "scale", "title": "Rate"})
    sq = item["questionItem"]["question"]["scaleQuestion"]
    assert sq["low"] == 1
    assert sq["high"] == 5
    assert "lowLabel" not in sq


def test_build_text():
    item = _build_question_item({"type": "text", "title": "Note", "description": "Details"})
    assert "textItem" in item
    assert item["title"] == "Note"
    assert item["description"] == "Details"


def test_build_section_break():
    item = _build_question_item({"type": "section_break", "title": "Part 2"})
    assert "pageBreakItem" in item
    assert item["title"] == "Part 2"


def test_question_with_description():
    item = _build_question_item({
        "type": "paragraph",
        "title": "Describe",
        "description": "Be specific",
    })
    assert item["description"] == "Be specific"


# ============================================================================
# Title splitting (Forms API rejects newlines in titles)
# ============================================================================


def test_split_title_single_line():
    title, desc = _split_title("Simple question?")
    assert title == "Simple question?"
    assert desc == ""


def test_split_title_multiline():
    title, desc = _split_title("First line\n\nSecond line\nThird line")
    assert title == "First line"
    assert desc == "Second line\nThird line"


def test_split_title_strips_whitespace():
    title, desc = _split_title("  Title  \n  Extra  \n")
    assert title == "Title"
    assert desc == "Extra"


def test_multiline_title_moves_to_description():
    item = _build_question_item({
        "type": "paragraph",
        "title": "Rate these behaviours\n\n• Behaviour A\n• Behaviour B",
        "required": True,
    })
    assert "\n" not in item["title"]
    assert item["title"] == "Rate these behaviours"
    assert "Behaviour A" in item["description"]
    assert "Behaviour B" in item["description"]


def test_multiline_title_merges_with_existing_description():
    item = _build_question_item({
        "type": "paragraph",
        "title": "Main question\n\nContext here",
        "description": "Additional guidance",
    })
    assert item["title"] == "Main question"
    assert "Context here" in item["description"]
    assert "Additional guidance" in item["description"]


# ============================================================================
# Batch request building
# ============================================================================


def test_batch_requests_with_description():
    requests = _spec_to_batch_requests({
        "title": "Test",
        "description": "Form description",
        "questions": [
            {"type": "paragraph", "title": "Q1"},
        ],
    })
    assert requests[0]["updateFormInfo"]["info"]["description"] == "Form description"
    assert requests[1]["createItem"]["location"]["index"] == 0


def test_batch_requests_without_description():
    requests = _spec_to_batch_requests({
        "title": "Test",
        "questions": [
            {"type": "paragraph", "title": "Q1"},
            {"type": "short_answer", "title": "Q2"},
        ],
    })
    assert len(requests) == 2
    assert all("createItem" in r for r in requests)
    assert requests[0]["createItem"]["location"]["index"] == 0
    assert requests[1]["createItem"]["location"]["index"] == 1


# ============================================================================
# create_form integration (mocked API)
# ============================================================================


MOCK_CREATE_RESPONSE = {
    "formId": "test-form-id-123",
    "info": {"title": "Test Form"},
    "responderUri": "https://docs.google.com/forms/d/e/xxx/viewform",
}


def _mock_client():
    client = MagicMock()
    client.post_json.return_value = MOCK_CREATE_RESPONSE
    return client


@patch("tools.form_create.get_sync_client")
def test_create_form_yaml(mock_get_client):
    mock_get_client.return_value = _mock_client()
    spec_yaml = yaml.dump({
        "title": "360 Feedback",
        "description": "Please provide feedback",
        "questions": [
            {"type": "paragraph", "title": "Highlights?", "required": True},
            {"type": "checkboxes", "title": "Projects", "options": ["A", "B"]},
        ],
    })

    result = create_form(content=spec_yaml)

    assert hasattr(result, "file_id")
    assert result.file_id == "test-form-id-123"
    assert result.web_link == "https://docs.google.com/forms/d/test-form-id-123/edit"
    assert result.cues["question_count"] == 2
    assert result.cues["responder_url"] == "https://docs.google.com/forms/d/e/xxx/viewform"
    assert result.extras["type"] == "form"


@patch("tools.form_create.get_sync_client")
def test_create_form_title_override(mock_get_client):
    mock_get_client.return_value = _mock_client()
    result = create_form(
        content='{"title": "Original", "questions": []}',
        title="Override Title",
    )
    assert hasattr(result, "title")
    assert result.title == "Override Title"


@patch("tools.form_create.get_sync_client")
def test_create_form_folder_id_warning(mock_get_client):
    mock_get_client.return_value = _mock_client()
    result = create_form(
        content='{"title": "Test", "questions": []}',
        folder_id="some-folder-id",
    )
    assert "folder_warning" in result.cues


def test_create_form_no_content():
    result = create_form()
    assert result["error"] is True
    assert "content" in result["message"].lower()


def test_create_form_invalid_spec():
    result = create_form(content="not valid yaml or json {{{{")
    assert result["error"] is True


def test_create_form_validation_errors():
    result = create_form(content='{"questions": []}')
    assert result["error"] is True
    assert "title" in result["message"]


@patch("tools.form_create.get_sync_client")
def test_create_form_api_calls(mock_get_client):
    """Verify the actual API calls made — create then batchUpdate."""
    client = _mock_client()
    mock_get_client.return_value = client

    spec = {
        "title": "Test",
        "description": "Intro text",
        "questions": [
            {"type": "paragraph", "title": "Q1", "required": True},
        ],
    }

    create_form(content=json.dumps(spec))

    calls = client.post_json.call_args_list
    assert len(calls) == 2

    # First call: create empty form
    create_call = calls[0]
    assert "forms.googleapis.com" in create_call.args[0]
    assert create_call.kwargs["json_body"]["info"]["title"] == "Test"

    # Second call: batchUpdate with description + question
    batch_call = calls[1]
    assert "batchUpdate" in batch_call.args[0]
    batch_requests = batch_call.kwargs["json_body"]["requests"]
    assert len(batch_requests) == 2
    assert "updateFormInfo" in batch_requests[0]
    assert "createItem" in batch_requests[1]


@patch("tools.form_create.get_sync_client")
def test_create_form_empty_questions(mock_get_client):
    """Form with no questions — only create call, no batchUpdate needed (unless description)."""
    client = _mock_client()
    mock_get_client.return_value = client

    create_form(content='{"title": "Empty Form", "questions": []}')

    calls = client.post_json.call_args_list
    assert len(calls) == 1  # Only the create call


@patch("tools.form_create.get_sync_client")
def test_create_form_batch_update_failure(mock_get_client):
    """If batchUpdate fails, error mentions the form was created."""
    client = MagicMock()
    client.post_json.side_effect = [
        MOCK_CREATE_RESPONSE,
        MiseError(ErrorKind.NETWORK_ERROR, "timeout"),
    ]
    mock_get_client.return_value = client

    result = create_form(content='{"title": "Test", "questions": [{"type": "paragraph", "title": "Q"}]}')
    assert result["error"] is True
    assert "test-form-id-123" in result["message"]
