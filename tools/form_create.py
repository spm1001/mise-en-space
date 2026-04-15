"""
Form creation via Google Forms API v1.

Unlike doc/sheet/slides creation (which use Drive's files().create() with
media upload), Forms require two API calls:
  1. forms.create() — creates an empty form with title
  2. forms.batchUpdate() — adds questions, description, and settings

The content parameter accepts a YAML or JSON spec defining the form structure.
"""

import json
import logging
from typing import Any

import yaml

from adapters.http_client import get_sync_client
from models import DoResult, MiseError, ErrorKind
from retry import with_retry
from validation import sanitize_title

logger = logging.getLogger(__name__)

_FORMS_API = "https://forms.googleapis.com/v1/forms"

# Maps YAML spec question types to Forms API item builders
_QUESTION_TYPE_MAP = {
    "paragraph",
    "short_answer",
    "checkboxes",
    "multiple_choice",
    "dropdown",
    "scale",
    "text",
    "section_break",
}


def _parse_spec(content: str) -> dict[str, Any]:
    """Parse YAML or JSON form spec. Tries YAML first, falls back to JSON."""
    try:
        spec = yaml.safe_load(content)
        if isinstance(spec, dict):
            return spec
    except yaml.YAMLError:
        pass

    try:
        spec = json.loads(content)
        if isinstance(spec, dict):
            return spec
    except (json.JSONDecodeError, ValueError):
        pass

    raise MiseError(
        ErrorKind.INVALID_INPUT,
        "Content must be valid YAML or JSON with a top-level object.",
    )


def _validate_spec(spec: dict[str, Any]) -> list[str]:
    """Validate a form spec. Returns list of errors (empty = valid)."""
    errors: list[str] = []

    if not spec.get("title"):
        errors.append("Missing required field: title")

    questions = spec.get("questions", [])
    if not isinstance(questions, list):
        errors.append("'questions' must be a list")
        return errors

    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            errors.append(f"questions[{i}]: must be an object")
            continue

        qtype = q.get("type")
        if not qtype:
            errors.append(f"questions[{i}]: missing 'type'")
            continue

        if qtype not in _QUESTION_TYPE_MAP:
            errors.append(
                f"questions[{i}]: unknown type '{qtype}'. "
                f"Supported: {sorted(_QUESTION_TYPE_MAP)}"
            )
            continue

        if qtype not in ("text", "section_break") and not q.get("title"):
            errors.append(f"questions[{i}]: missing 'title' for type '{qtype}'")

        if qtype in ("checkboxes", "multiple_choice", "dropdown"):
            opts = q.get("options")
            if not opts or not isinstance(opts, list):
                errors.append(f"questions[{i}]: '{qtype}' requires 'options' list")

    return errors


def _split_title(title: str) -> tuple[str, str]:
    """Split a multi-line title into (title, overflow_description).

    The Forms API rejects newlines in item titles. When a YAML block scalar
    puts detail below the first line, we move it to the description field.
    """
    title = title.strip()
    if "\n" not in title:
        return title, ""
    first, rest = title.split("\n", 1)
    return first.strip(), rest.strip()


def _build_question_item(q: dict[str, Any]) -> dict[str, Any]:
    """Convert a single YAML question spec to a Forms API item."""
    qtype = q["type"]

    if qtype == "section_break":
        item: dict[str, Any] = {"pageBreakItem": {}}
        if q.get("title"):
            title, extra = _split_title(q["title"])
            item["title"] = title
            desc_parts = [d for d in [extra, q.get("description")] if d]
            if desc_parts:
                item["description"] = "\n\n".join(desc_parts)
        elif q.get("description"):
            item["description"] = q["description"]
        return item

    if qtype == "text":
        item = {"textItem": {}}
        if q.get("title"):
            title, extra = _split_title(q["title"])
            item["title"] = title
            desc_parts = [d for d in [extra, q.get("description")] if d]
            if desc_parts:
                item["description"] = "\n\n".join(desc_parts)
        elif q.get("description"):
            item["description"] = q["description"]
        return item

    question_body: dict[str, Any] = {}
    if q.get("required"):
        question_body["required"] = True

    if qtype == "paragraph":
        question_body["textQuestion"] = {"paragraph": True}
    elif qtype == "short_answer":
        question_body["textQuestion"] = {"paragraph": False}
    elif qtype in ("checkboxes", "multiple_choice", "dropdown"):
        type_map = {
            "checkboxes": "CHECKBOX",
            "multiple_choice": "RADIO",
            "dropdown": "DROP_DOWN",
        }
        options = [{"value": str(opt)} for opt in q.get("options", [])]
        if q.get("include_other"):
            options.append({"isOther": True})
        question_body["choiceQuestion"] = {
            "type": type_map[qtype],
            "options": options,
        }
    elif qtype == "scale":
        scale: dict[str, Any] = {
            "low": q.get("low", 1),
            "high": q.get("high", 5),
        }
        if q.get("low_label"):
            scale["lowLabel"] = q["low_label"]
        if q.get("high_label"):
            scale["highLabel"] = q["high_label"]
        question_body["scaleQuestion"] = scale

    title, extra_desc = _split_title(q.get("title", ""))
    item = {
        "title": title,
        "questionItem": {"question": question_body},
    }
    desc_parts = [d for d in [extra_desc, q.get("description")] if d]
    if desc_parts:
        item["description"] = "\n\n".join(desc_parts)
    return item


def _spec_to_batch_requests(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a full form spec to a list of Forms API batchUpdate requests."""
    requests: list[dict[str, Any]] = []

    if spec.get("description"):
        requests.append({
            "updateFormInfo": {
                "info": {"description": spec["description"]},
                "updateMask": "description",
            }
        })

    for i, q in enumerate(spec.get("questions", [])):
        item = _build_question_item(q)
        requests.append({
            "createItem": {
                "item": item,
                "location": {"index": i},
            }
        })

    return requests


@with_retry(max_attempts=3, delay_ms=1000)
def _api_create_form(title: str) -> dict[str, Any]:
    """Create an empty form via Forms API."""
    client = get_sync_client()
    return client.post_json(
        _FORMS_API,
        json_body={"info": {"title": title, "documentTitle": title}},
    )


@with_retry(max_attempts=3, delay_ms=1000)
def _api_batch_update(form_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Add questions and settings to a form via batchUpdate."""
    client = get_sync_client()
    return client.post_json(
        f"{_FORMS_API}/{form_id}:batchUpdate",
        json_body={"requests": requests},
    )


def create_form(
    content: str | None = None,
    title: str | None = None,
    folder_id: str | None = None,
) -> DoResult | dict[str, Any]:
    """Create a Google Form from a YAML or JSON spec.

    Args:
        content: YAML or JSON string defining the form structure
        title: Form title (overrides spec title if both provided)
        folder_id: Ignored — Forms API doesn't support folder placement at creation.
                   Included for do_create interface consistency.

    Returns:
        DoResult on success, error dict on failure
    """
    if not content:
        return {"error": True, "kind": "invalid_input",
                "message": "Form creation requires 'content' with a YAML or JSON form spec."}

    try:
        spec = _parse_spec(content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    if title:
        spec["title"] = sanitize_title(title)
    elif spec.get("title"):
        spec["title"] = sanitize_title(spec["title"])

    errors = _validate_spec(spec)
    if errors:
        return {"error": True, "kind": "invalid_input",
                "message": f"Invalid form spec: {'; '.join(errors)}"}

    form_title = spec["title"]
    questions = spec.get("questions", [])

    logger.info("create form: title=%r questions=%d", form_title, len(questions))

    try:
        result = _api_create_form(form_title)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}
    except Exception as e:
        return {"error": True, "kind": "INTERNAL", "message": f"Form creation failed: {e}"}

    form_id = result["formId"]
    responder_uri = result.get("responderUri", "")

    batch_requests = _spec_to_batch_requests(spec)
    if batch_requests:
        try:
            _api_batch_update(form_id, batch_requests)
        except MiseError as e:
            return {"error": True, "kind": e.kind.value,
                    "message": f"Form created ({form_id}) but adding questions failed: {e.message}"}
        except Exception as e:
            return {"error": True, "kind": "INTERNAL",
                    "message": f"Form created ({form_id}) but batchUpdate failed: {e}"}

    cues: dict[str, Any] = {
        "question_count": len(questions),
        "responder_url": responder_uri,
    }
    if folder_id:
        cues["folder_warning"] = "Forms API doesn't support folder placement at creation. Form was created in My Drive root."

    return DoResult(
        file_id=form_id,
        title=form_title,
        web_link=f"https://docs.google.com/forms/d/{form_id}/edit",
        operation="create",
        cues=cues,
        extras={"type": "form"},
    )
