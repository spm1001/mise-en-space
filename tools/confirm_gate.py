"""do()'s confirm gate — which ops ask the client, in what words, and what came back.

mcp fills do()'s `confirm_gate` param by running the resolvers below before
the tool body, on EVERY do() call (tools/elicit.py has the mechanism). Three
resolvers, composed so the body learns BOTH what the dialog showed and what
the client answered:

- `gate_question` works out the question: the op's own preview message, or
  why there is none. It asks only for an unconfirmed, gated op on a client
  that declared form elicitation, and checks that capability before any API
  read.
- `confirm_ask` raises the dialog when the question should be asked.
- `gate_result` hands the body both, as a `GateResult`.

mcp runs each resolver once per round, so the message the body receives is
the message the dialog carried (on >= 2026-07-28 an answer to a differently
worded question is discarded and re-asked). That lets the body refuse to
write when the state it is about to write no longer renders to the words the
human approved (mise-pukiri: a re-read event can change while a dialog waits).

Gated ops, and what makes a call gated (the same test the op's body applies):

- share: always, until confirmed.
- create_event: attendees present — other people's diaries and inboxes.
- update_event: a structural change (time, recurrence, attendees, Meet).

A pre-supplied confirm=True skips the dialog (Sameer's policy A, 2026-09-06):
headless and interactive Claude Code declare the identical capability, so a
server cannot tell a human's cancel from an auto-cancel, and the confirm=
round-trip stays the fallback everywhere a dialog does not decide.
"""

from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server.elicitation import ElicitationResult
from mcp.server.mcpserver import Context, Elicit, Resolve

from models import MiseError
from tools.create_event import create_event_confirm_message
from tools.elicit import (
    ORPHANED,
    TOO_LONG,
    UNAVAILABLE,
    ConfirmAnswer,
    GateQuestion,
    client_supports_elicitation,
    fits_dialog,
)
from tools.share import share_confirm_message
from tools.update_event import update_event_confirm_message

CONFIRM_GATED_OPS = frozenset({"share", "create_event", "update_event"})


@dataclass(frozen=True)
class GateResult:
    """What the body receives: the question and the client's outcome."""

    question: GateQuestion
    outcome: ElicitationResult[ConfirmAnswer] | None


def gate_question(
    operation: str, ctx: Context, file_id: Any = None, to: Any = None,
    role: str | None = None, confirm: bool = False, title: Any = None,
    content: str | None = None, time_min: Any = None, time_max: Any = None,
    attendees: list[str] | str | None = None, location: str | None = None,
    meet: bool | None = None, recurrence: str | list[str] | None = None,
    include: list[str] | None = None, send_updates: str | None = None,
    properties: dict[str, str] | None = None, color: str | None = None,
    visibility: str | None = None, transparency: str | None = None,
) -> GateQuestion:
    """Resolver: the confirmation question for this call, or why there is none.

    Parameters are bound by name from the tool's own arguments. A missing
    token and Google errors mean no dialog (UNAVAILABLE) — named here, so the
    body then reports them with their teaching text, or falls back with a cue
    saying why, instead of the resolver failing the whole call opaquely.
    Anything else propagates.
    """
    if confirm or operation not in CONFIRM_GATED_OPS or not client_supports_elicitation(ctx):
        return GateQuestion()
    try:
        if operation == "share":
            message = share_confirm_message(file_id=file_id, to=to, role=role)
        elif operation == "create_event":
            message = create_event_confirm_message(
                title=title, time_min=time_min, time_max=time_max,
                attendees=attendees, location=location, meet=bool(meet),
                recurrence=recurrence, include=include, send_updates=send_updates,
                properties=properties, color=color, visibility=visibility,
                transparency=transparency,
            )
        else:
            message = update_event_confirm_message(
                file_id=file_id, title=title, content=content, location=location,
                time_min=time_min, time_max=time_max, attendees=attendees,
                recurrence=recurrence, include=include, meet=meet,
                send_updates=send_updates, properties=properties, color=color,
                visibility=visibility, transparency=transparency,
            )
    except (MiseError, FileNotFoundError):  # FileNotFoundError = no token (adapters/http_client.py)
        question = GateQuestion(skipped=UNAVAILABLE)
    else:
        if message is None:
            question = GateQuestion()
        elif not fits_dialog(message):
            question = GateQuestion(message=message, skipped=TOO_LONG)
        else:
            question = GateQuestion(message=message)
    # On >= 2026-07-28 the client retries with its answer and these resolvers
    # re-run. If this round would not ask, that answer would go unread and
    # the body would see "no dialog" — so say it was orphaned, and the body
    # stops. do()'s only elicitation is confirm_ask, so any response is ours.
    if not question.ask and ctx.input_responses:
        return GateQuestion(message=question.message, skipped=ORPHANED)
    return question


def confirm_ask(
    question: Annotated[GateQuestion, Resolve(gate_question)],
) -> Elicit[ConfirmAnswer] | None:
    """Resolver: raise the dialog carrying the question's message, or don't."""
    if not question.ask:
        return None
    assert question.message is not None
    return Elicit(message=question.message, schema=ConfirmAnswer)


def gate_result(
    question: Annotated[GateQuestion, Resolve(gate_question)],
    outcome: Annotated[ElicitationResult[ConfirmAnswer], Resolve(confirm_ask)],
) -> GateResult:
    """Resolver: the question and the outcome together, for the body."""
    return GateResult(question=question, outcome=outcome)


# do()'s injected param (server.py). The union annotation on `outcome` hands
# the body accept/decline/cancel rather than aborting the call on a no.
# Never a wire param.
ConfirmGate = Annotated[GateResult, Resolve(gate_result)]


def gate_outcome(gate: Any) -> ElicitationResult[ConfirmAnswer] | None:
    """The client's outcome from do()'s gate param (None outside a live call)."""
    return gate.outcome if isinstance(gate, GateResult) else None


def gate_asked(gate: Any) -> GateQuestion:
    """The question from do()'s gate param (an empty one outside a live call)."""
    return gate.question if isinstance(gate, GateResult) else GateQuestion()
