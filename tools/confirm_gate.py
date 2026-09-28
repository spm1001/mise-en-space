"""do()'s one confirm resolver — which ops ask the client, and in what words.

mcp fills do()'s `confirm_answer` param by running `confirm_gate` before the
tool body, on EVERY do() call (tools/elicit.py has the mechanism). It answers
None — no dialog — unless the call is an unconfirmed, gated op on a client
that declared form elicitation; then it asks with the op's own preview
message, so the dialog's text and the confirm= path's preview are one string.

Gated ops, and what makes a call gated (the same test the op's body applies):

- share: always, until confirmed.
- create_event: attendees present — other people's diaries and inboxes.
- update_event: a structural change (time, recurrence, attendees, Meet).

A pre-supplied confirm=True skips the dialog (Sameer's policy A, 2026-09-06):
headless and interactive Claude Code declare the identical capability, so a
server cannot tell a human's cancel from an auto-cancel, and the confirm=
round-trip stays the fallback everywhere a dialog does not decide.

One resolver rather than one per op because mcp runs every resolver on every
call: a single entry point pays one capability check before any API read.
"""

from typing import Annotated, Any

from mcp.server.elicitation import ElicitationResult
from mcp.server.mcpserver import Context, Elicit, Resolve

from tools.create_event import create_event_confirm_message
from tools.elicit import ConfirmAnswer, client_supports_elicitation, confirm_marker
from tools.share import share_confirm_message
from tools.update_event import update_event_confirm_message

CONFIRM_GATED_OPS = frozenset({"share", "create_event", "update_event"})


def confirm_gate(
    operation: str, ctx: Context, file_id: Any = None, to: Any = None,
    role: str | None = None, confirm: bool = False, title: Any = None,
    content: str | None = None, time_min: Any = None, time_max: Any = None,
    attendees: list[str] | str | None = None, location: str | None = None,
    meet: bool | None = None, recurrence: str | list[str] | None = None,
    include: list[str] | None = None, send_updates: str | None = None,
    properties: dict[str, str] | None = None, color: str | None = None,
    visibility: str | None = None, transparency: str | None = None,
) -> Elicit[ConfirmAnswer] | None:
    """Resolver for do()'s `confirm_answer`: the confirmation question, or None.

    Parameters are bound by name from the tool's own arguments. Invalid
    inputs and API errors answer None inside each message builder: the body
    then reports them exactly as it does without a dialog.
    """
    if confirm or operation not in CONFIRM_GATED_OPS or not client_supports_elicitation(ctx):
        return None
    if operation == "share":
        message = share_confirm_message(file_id, to, role)
    elif operation == "create_event":
        message = create_event_confirm_message(
            title, time_min, time_max, attendees, location, bool(meet),
            recurrence, include, send_updates, properties, color, visibility,
            transparency,
        )
    else:
        message = update_event_confirm_message(
            file_id, title, content, location, time_min, time_max, attendees,
            recurrence, include, meet, send_updates, properties, color,
            visibility, transparency,
        )
    return confirm_marker(ctx, message)


# do()'s injected param (server.py): mcp fills it by running confirm_gate and
# asking the client; the union annotation hands the body accept/decline/cancel
# rather than aborting the call on a no. Never a wire param.
ConfirmGateAnswer = Annotated[ElicitationResult[ConfirmAnswer], Resolve(confirm_gate)]
