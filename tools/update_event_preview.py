"""update_event's preview: old → new per field, and the same facts in words.

Split from tools/update_event.py (mise-pukiri) when the dialog text joined
the preview. The words matter twice: `message` is what the confirm= path
shows the model, and it is also the text of the dialog a capable client
renders (tools/confirm_gate.py) — so the human approves exactly what the
preview shows, never less.
"""

from dataclasses import dataclass
from typing import Any

from cues_util import with_identity
from tools.events_util import describe_when, extract_meet_link

STRUCTURAL = "structural"
COSMETIC = "cosmetic"


@dataclass
class Edit:
    """The event as read plus validated changes, shared by preview, dialog and patch."""

    event: dict[str, Any]
    disclosure: dict[str, Any]
    changes: dict[str, str]  # field -> structural|cosmetic
    emails: list[str]
    recurrence_lines: list[str]
    programme_keys: dict[str, str]
    color_id: str | None
    vis: str | None
    transp: str | None
    warnings: list[str]
    structural: list[str]
    effective_updates: str


_UPDATE_EMAILS = {
    "all": "update emails go to every attendee",
    "externalOnly": "update emails go only to attendees outside your organisation",
    "none": "NO update emails — attendees see the change only on their calendars",
}


def _preview_message(edit: Edit, described: dict[str, Any]) -> str:
    """Everything the preview shows, in words — this IS the dialog's text.

    The attendees being added and the count already on the event belong
    here: the human must never approve less than the preview shows.
    """
    event = edit.event
    lines = [(
        f"Would update '{event.get('summary', 'untitled')}' (now "
        f"{describe_when(event.get('start', {}), event.get('end', {}))}):"
    )]
    if "time" in described:
        to = described["time"]["to"]
        lines.append(f"- move it to {to['start']} – {to['end']}")
    if "recurrence" in described:
        rec = described["recurrence"]
        was = "; ".join(rec["from"]) if rec["from"] else "does not repeat"
        lines.append(f"- repeat: {was} → {'; '.join(rec['to'])}")
    if "attendees_to_add" in described:
        lines.append(f"- add attendees: {', '.join(described['attendees_to_add'])}")
    if "meet" in described:
        lines.append(f"- {described['meet']}")
    if "also_cosmetic" in described:
        lines.append(f"- also change: {', '.join(described['also_cosmetic'])}")
    count = len(event.get("attendees", []))
    lines.append(
        f"The event has {count} attendee(s) now; "
        f"{_UPDATE_EMAILS[edit.effective_updates]}."
    )
    if edit.disclosure.get("resolved_from_thread"):
        lines.append(
            f"Event found from invite thread {edit.disclosure['resolved_from_thread']}."
        )
    lines.extend(f"Warning: {w}" for w in edit.warnings)
    return "\n".join(lines)


def edit_preview(
    edit: Edit, time_min: str | None, time_max: str | None, meet: bool | None,
) -> dict[str, Any]:
    """The gated preview for a structural edit: old → new, nothing changed yet."""
    described = _describe_changes(
        edit.event, edit.changes, time_min, time_max, edit.emails,
        edit.recurrence_lines, meet,
    )
    return {
        "preview": True,
        "operation": "update_event",
        "title": edit.event.get("summary"),
        "event_id": edit.event.get("id"),
        "changes": described,
        "send_updates": edit.effective_updates,
        "attendee_count": len(edit.event.get("attendees", [])),
        "message": _preview_message(edit, described),
        "cues": with_identity({
            "confirm_required": (
                "This is a preview — nothing has changed and nobody has "
                "been emailed. Structural edits (time, recurrence, "
                "attendees, Meet) touch other people's diaries: show this "
                "to the user, then call again with confirm=True."
            ),
            **edit.disclosure,
        }),
    }


def _describe_changes(
    event: dict[str, Any],
    changes: dict[str, str],
    time_min: str | None,
    time_max: str | None,
    emails: list[str],
    recurrence_lines: list[str],
    meet: bool | None,
) -> dict[str, Any]:
    """Old → new, per changed field, for the preview."""
    described: dict[str, Any] = {}
    if "time" in changes:
        described["time"] = {
            "from": {"start": event.get("start"), "end": event.get("end")},
            "to": {"start": time_min, "end": time_max},
        }
    if "recurrence" in changes:
        described["recurrence"] = {
            "from": event.get("recurrence"),
            "to": recurrence_lines,
        }
    if "attendees" in changes:
        existing = {
            a.get("email", "").lower() for a in event.get("attendees", [])
        }
        described["attendees_to_add"] = [
            e for e in emails if e.lower() not in existing
        ]
    if "meet" in changes:
        described["meet"] = "add a Meet link" if meet else (
            f"remove the Meet link ({extract_meet_link(event) or 'conference'})"
        )
    for cosmetic in (
        "description", "title", "location", "attachments",
        "properties", "color", "visibility", "transparency",
    ):
        if cosmetic in changes:
            described.setdefault("also_cosmetic", []).append(cosmetic)
    return described
