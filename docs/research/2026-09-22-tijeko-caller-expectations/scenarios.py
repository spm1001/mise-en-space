# /// script
# requires-python = ">=3.11"
# ///
"""mise-tijeko: the scenarios put to blank-slate Claudes, and the prompt builder.

Each scenario is a real accept-and-drop (or accept-and-vacuous) path found by
the handler sweep on 2026-09-22 — a param the do() gate lets through because
the op DOES consume it, which the handler then ignores on one branch. The
options are keyed so answers can be scored without reading prose; their
presentation order, and the scenario order, are shuffled per subject with a
recorded seed so no option benefits from always sitting first.

The key naming the CURRENT behaviour is recorded here (CURRENT) and never
shown to subjects.

    uv run --script scenarios.py prompt <seed>   # print one subject's prompt
"""
import json
import random
import sys
from pathlib import Path

SCENARIOS: list[dict] = [
    {
        "id": "C1-reply-all-plus-cc",
        "situation": (
            "A Gmail thread's last message is from alice@example.com, sent To you, "
            "Cc bob@example.com and carol@example.com. You want to reply to everyone "
            "on it, and also bring dave@example.com in on Cc."
        ),
        "call": "do(operation='reply_draft', file_id='18c2f0a9d3e4b7f1', "
                "content='Thanks all — looping in Dave.', reply_all=True, cc='dave@example.com')",
        "options": {
            "union": "Draft To alice; Cc bob, carol AND dave — the reply-all recipients plus the explicit cc.",
            "replace": "Draft To alice; Cc dave only — an explicit cc= replaces the Cc that reply_all would have inferred. Nothing says so.",
            "replace_warn": "Draft To alice; Cc dave only (cc= replaces the inferred Cc), and the result carries a warning naming bob and carol as dropped.",
            "refuse": "Error: reply_all=True and cc= can't be combined. Nothing is drafted.",
        },
    },
    {
        "id": "C2-move-two-folder-ids",
        "situation": (
            "You want to move a Drive file into a folder. You passed both folder_id and "
            "destination_folder_id (both appear in the tool's parameter list), with "
            "DIFFERENT folder ids."
        ),
        "call": "do(operation='move', file_id='1AbC', folder_id='FOLDER_A', destination_folder_id='FOLDER_B')",
        "options": {
            "first_wins": "Moved into FOLDER_A (folder_id wins). Nothing mentions FOLDER_B.",
            "first_wins_warn": "Moved into FOLDER_A, and the result warns that destination_folder_id was ignored.",
            "second_wins": "Moved into FOLDER_B (destination_folder_id wins). Nothing mentions FOLDER_A.",
            "refuse": "Error naming the two conflicting folder ids. Nothing is moved.",
        },
    },
    {
        "id": "C3-folder-with-content",
        "situation": "You call create with doc_type='folder' and also pass content.",
        "call": "do(operation='create', doc_type='folder', title='Q3 planning', "
                "content='# Q3 planning\\n\\nGoals for the quarter: ...')",
        "options": {
            "ignore": "The folder is created. The content is ignored; nothing says so.",
            "ignore_warn": "The folder is created, and the result warns that content was ignored because a folder holds no content.",
            "consume": "The folder is created AND a Google Doc holding the content is created inside it.",
            "refuse": "Error: a folder takes no content (with a pointer to creating a doc inside it via folder_id). Nothing is created.",
        },
    },
    {
        "id": "C4-page-setup-on-form",
        "situation": "You create a Google Form from a valid spec and also pass page_setup='pageless'.",
        "call": "do(operation='create', doc_type='form', title='Team survey', "
                "content='<a valid YAML form spec>', page_setup='pageless')",
        "options": {
            "ignore": "The form is created. page_setup is ignored; nothing says so.",
            "ignore_warn": "The form is created, and the result notes that page_setup applies only to Docs and was ignored.",
            "refuse": "Error: page_setup is only valid for doc_type='doc'. Nothing is created.",
        },
    },
    {
        "id": "C5-form-from-file-path",
        "situation": (
            "/tmp/survey.yaml on the machine running the tool holds a valid YAML form spec. "
            "You create the form by pointing at the file instead of pasting the spec."
        ),
        "call": "do(operation='create', doc_type='form', title='Team survey', file_path='/tmp/survey.yaml')",
        "options": {
            "consume": "The file is read as the form spec and the form is created.",
            "refuse_generic": "Error: \"Form creation requires 'content' with a YAML or JSON form spec.\" Nothing is created.",
            "refuse_teach": "Error: file_path isn't supported for forms — pass the spec text itself as content=. Nothing is created.",
        },
    },
    {
        "id": "C6-form-into-folder",
        "situation": "You create a Google Form from a valid spec and ask for it to land in a particular folder.",
        "call": "do(operation='create', doc_type='form', title='Team survey', "
                "content='<a valid YAML form spec>', folder_id='FOLDER_A')",
        "options": {
            "consume": "The form is created and placed in FOLDER_A.",
            "root_warn": "The form is created in My Drive root, and the result says folder placement isn't supported for forms.",
            "root_silent": "The form is created in My Drive root. Nothing mentions the folder.",
            "refuse": "Error: forms can't be created in a folder. Nothing is created.",
        },
    },
    {
        "id": "C7-restore-comment-on-sheet",
        "situation": (
            "You have learned that overwrite on a Google DOC posts an '[agent]' comment "
            "naming the pre-edit version (so it can be restored), and that "
            "restore_comment=False suppresses that comment. Now you are overwriting a "
            "Google SHEET with CSV, and you pass restore_comment=False so nobody gets a "
            "comment notification."
        ),
        "call": "do(operation='overwrite', file_id='1SheetId', content='a,b\\n1,2', restore_comment=False)",
        "options": {
            "ignore": "The sheet is overwritten; no comment is posted (Sheets never get that comment). Nothing is said about restore_comment.",
            "ignore_note": "The sheet is overwritten, and the result notes that restore_comment has no effect on Sheets.",
            "refuse": "Error: restore_comment applies only to Google Docs. Nothing is written.",
        },
    },
    {
        "id": "C8-event-confirm-without-attendees",
        "situation": (
            "You book a solo focus-time block — no attendees. Out of caution you pass "
            "confirm=True and send_updates='all' anyway."
        ),
        "call": "do(operation='create_event', title='Focus time', time_min='2026-09-24T09:00:00+01:00', "
                "time_max='2026-09-24T11:00:00+01:00', confirm=True, send_updates='all')",
        "options": {
            "ignore": "The event is created. Nothing is said about confirm or send_updates.",
            "ignore_note": "The event is created, and the result notes that confirm and send_updates had no effect because nobody was invited.",
            "refuse": "Error: confirm/send_updates only apply when there are attendees. Nothing is created.",
        },
    },
    {
        "id": "C9-meet-false-on-update",
        "situation": (
            "An existing calendar event has a Google Meet link. The meeting is now in "
            "person, so you want to rename it and remove the Meet link."
        ),
        "call": "do(operation='update_event', file_id='evt_7h2k9', title='Offsite (in person)', meet=False)",
        "options": {
            "consume": "The event is renamed and its Meet link is removed.",
            "partial_silent": "The event is renamed. The Meet link stays. Nothing says so.",
            "partial_warn": "The event is renamed. The Meet link stays, and the result warns that meet=False cannot remove an existing link.",
            "refuse": "Error: removing a Meet link isn't supported. Nothing is changed.",
        },
    },
]

# What mise does TODAY for each scenario (as of 73b1da4 + the 2026-09-22 sweep).
# Never shown to subjects.
CURRENT = {
    "C1-reply-all-plus-cc": "replace",
    "C2-move-two-folder-ids": "first_wins",
    "C3-folder-with-content": "ignore",
    "C4-page-setup-on-form": "ignore",
    "C5-form-from-file-path": "refuse_generic",
    "C6-form-into-folder": "root_warn",
    "C7-restore-comment-on-sheet": "ignore",
    "C8-event-confirm-without-attendees": "ignore",
    "C9-meet-false-on-update": "partial_silent",
}

PREAMBLE = """\
You are an AI agent that uses tools to get work done for a user. Below is the \
description of a real tool, `do`, from a Google Workspace MCP server, exactly as \
a calling agent sees it, followed by its parameter list. Then come {n} short \
scenarios. In each one you have just made the call shown. For each scenario, \
answer three things as the calling agent:

1. predict — which option do you think this tool ACTUALLY does? (your best \
guess about the real implementation, not your wish)
2. prefer — which option would you WANT it to do?
3. distrust — which options, if the tool did them, would make you trust this \
tool less for the rest of your work? (a list; may be empty)

Plus one short sentence of why for your preference.

There are no trick answers and nothing to look up — answer from the \
description and your own experience of calling tools. Do not use any tools.

=== TOOL DESCRIPTION (tool name: do) ===
{description}

=== PARAMETERS (name: type = default) ===
{signature}

=== SCENARIOS ===
{scenarios}

=== ONE LAST QUESTION ===
"echo_defaults": When you call a tool whose optional boolean parameters have \
defaults (say meet: bool = False), do you usually pass them explicitly at their \
default value, or leave them out? Answer "explicit", "omit", or "depends", plus \
one sentence.

=== ANSWER FORMAT ===
Reply with ONE JSON object and nothing else:
{{"answers": {{"<scenario id>": {{"predict": "<key>", "prefer": "<key>", \
"distrust": ["<key>", ...], "why": "<one sentence>"}}, ...}}, \
"echo_defaults": {{"answer": "explicit|omit|depends", "why": "<one sentence>"}}}}
Use the option keys exactly as given (the word before the colon).
"""

SIGNATURE = """\
operation: str
content: str | None = None
title: str | None = None
doc_type: str = 'doc'
folder_id: str | None = None
page_setup: str | None = None
file_id: str | list[str] | None = None
destination_folder_id: str | None = None
source: str | None = None
base_path: str | None = None
file_path: str | None = None
find: str | None = None
to: str | None = None
subject: str | None = None
cc: str | None = None
include: list[str] | None = None
reply_all: bool = False
role: str | None = None
confirm: bool = False
label: str | None = None
remove: bool = False
comment_id: str | None = None
action: str | None = None
force: bool = False
restore_comment: bool = True
supersede: bool = False
range: str | None = None
tab: str | None = None
anchor: str | None = None
suggest: bool = False
attendees: list[str] | str | None = None
time_min: str | None = None
time_max: str | None = None
location: str | None = None
meet: bool = False
recurrence: str | list[str] | None = None
send_updates: str | None = None
duration: int | None = None
properties: dict[str, str] | None = None
color: str | None = None
visibility: str | None = None
transparency: str | None = None"""


def build_prompt(seed: int, description: str) -> str:
    rng = random.Random(seed)
    order = SCENARIOS[:]
    rng.shuffle(order)
    blocks = []
    for sc in order:
        keys = list(sc["options"])
        rng.shuffle(keys)
        opts = "\n".join(f"  {k}: {sc['options'][k]}" for k in keys)
        blocks.append(
            f"--- id: {sc['id']} ---\nSituation: {sc['situation']}\n"
            f"Call: {sc['call']}\nOptions:\n{opts}\n"
        )
    return PREAMBLE.format(n=len(order), description=description,
                           signature=SIGNATURE, scenarios="\n".join(blocks))


if __name__ == "__main__":
    here = Path(__file__).parent
    if sys.argv[1:2] == ["prompt"]:
        desc = (here / "do_description.txt").read_text()
        print(build_prompt(int(sys.argv[2]), desc))
    elif sys.argv[1:2] == ["keys"]:
        print(json.dumps({s["id"]: list(s["options"]) for s in SCENARIOS}, indent=2))
