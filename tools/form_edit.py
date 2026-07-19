"""
Form overwrite — replace an existing Google Form's structure from a spec.

The generic-primitive route to form editing (mise-wemuki): rather than a
bespoke edit DSL, `do(overwrite)` on a form takes the SAME YAML/JSON spec
as `do(create, doc_type='form')`. The edit loop is fetch (structure.json
deposits with every form fetch) → tweak the spec → overwrite. "Append a
checkbox option to Q1" is a one-line spec change.

Mechanics: one atomic forms.batchUpdate — updateFormInfo (title +
description), deleteItem for every existing item (descending index), then
createItem per spec question. Atomic: an invalid spec leaves the form
untouched.

Wholesale-replace caveat (mirrors doc overwrite's "destroys content"): all
existing items are deleted and recreated, so if the form already has
responses, their linkage to the old questions is lost. Fine for the
build-then-send flow; edit response-bearing forms in the Forms UI.
"""

import logging
from typing import Any

from adapters.forms import fetch_form
from models import DoResult, MiseError
from tools.form_create import (
    _api_batch_update,
    _build_question_item,
    _parse_spec,
    _validate_spec,
)
from validation import sanitize_title

logger = logging.getLogger(__name__)


def _overwrite_requests(
    existing: dict[str, Any], spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the atomic batchUpdate request list for a form overwrite."""
    requests: list[dict[str, Any]] = [{
        "updateFormInfo": {
            # description defaults empty — overwrite is wholesale, so an
            # omitted description clears the old one (documented behaviour).
            "info": {
                "title": spec["title"],
                "description": spec.get("description", ""),
            },
            "updateMask": "title,description",
        }
    }]

    item_count = len(existing.get("items", []))
    for index in range(item_count - 1, -1, -1):
        requests.append({"deleteItem": {"location": {"index": index}}})

    for i, q in enumerate(spec.get("questions", [])):
        requests.append({
            "createItem": {
                "item": _build_question_item(q),
                "location": {"index": i},
            }
        })

    return requests


def form_overwrite(
    file_id: str,
    content: str | None,
    metadata: dict[str, Any],
) -> DoResult | dict[str, Any]:
    """Replace a form's title, description, and questions from a spec.

    Args:
        file_id: The form's Drive file ID (== Forms API formId)
        content: YAML or JSON form spec (same shape as create)
        metadata: Pre-fetched Drive metadata (from dispatch)

    Returns:
        DoResult on success, error dict on failure
    """
    if not content:
        return {"error": True, "kind": "invalid_input",
                "message": "Form overwrite requires 'content' with a YAML or "
                           "JSON form spec (same shape as create: title, "
                           "description, questions). Tip: fetch the form "
                           "first — structure.json shows the current state."}

    try:
        spec = _parse_spec(content)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    if spec.get("title"):
        spec["title"] = sanitize_title(spec["title"])
    else:
        # Overwrite may reasonably omit title — keep the form's current one
        spec["title"] = metadata.get("name", "Untitled form")

    errors = _validate_spec(spec)
    if errors:
        return {"error": True, "kind": "invalid_input",
                "message": f"Invalid form spec: {'; '.join(errors)}"}

    try:
        existing = fetch_form(file_id)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value,
                "message": f"Could not load form '{file_id}': {e.message}"}

    requests = _overwrite_requests(existing, spec)
    replaced = len(existing.get("items", []))
    questions = spec.get("questions", [])

    logger.info(
        "form overwrite: form=%s items %d -> %d", file_id, replaced, len(questions)
    )

    try:
        _api_batch_update(file_id, requests)
    except MiseError as e:
        return {"error": True, "kind": e.kind.value, "message": e.message}

    return DoResult(
        file_id=file_id,
        title=spec["title"],
        web_link=f"https://docs.google.com/forms/d/{file_id}/edit",
        operation="overwrite",
        cues={
            "question_count": len(questions),
            "items_replaced": replaced,
            "warnings": [
                "Form overwrite replaces ALL questions wholesale — if this "
                "form already has responses, their linkage to the old "
                "questions is lost. Edit response-bearing forms in the "
                "Forms UI instead."
            ],
            "responder_url": existing.get("responderUri", ""),
        },
        extras={"type": "form"},
    )
