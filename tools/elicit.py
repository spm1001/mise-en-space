"""The elicit-or-confirm seam — the human's yes as a UI event (mise-wagina).

Today every confirm gate in mise routes the human's yes THROUGH the model:
Claude reads the preview, asks, and passes confirm=True itself. MCP
elicitation makes the yes a UI event instead — the server parks the tool
call and asks the CLIENT, which renders a dialog the model cannot see,
answer or detect (probe-confirmed against Claude Code 2.1.241, 2026-08-23).

The gate rides mcp's resolver injection (`Annotated[…, Resolve(fn)]`): an op
declares a resolver that returns `confirm_marker(ctx, preview_text)`, and the
framework asks the client by whatever the negotiated protocol allows — a
standalone elicitation/create request over the back-channel on <= 2025-11-25
(Claude Code today), an InputRequiredResult round-trip on >= 2026-07-28. The
tool body receives the outcome; `dialog_verdict` reads it.

Three facts shape what is here:

- Capability is declared at initialize and READ at the client seam, never
  assumed. `confirm_marker` returns None for a client that did not declare
  form elicitation, and the op runs its preview-then-confirm=True round-trip
  unchanged. (The framework would otherwise refuse the whole call with
  MISSING_REQUIRED_CLIENT_CAPABILITY.)
- Headless and non-interactive clients auto-CANCEL cleanly. A cancel is "no
  dialog decided this", not a no — the op keeps confirm= open as the
  fallback. A decline is an answer.
- The model cannot distinguish a human's accept from an auto-resolved one,
  so nothing built on this may say "the human approved". Verdict details
  name the mechanism and the client's answer.
"""

from typing import Any

from mcp.server.elicitation import (
    AcceptedElicitation,
    DeclinedElicitation,
    ElicitationResult,
)
from mcp.server.mcpserver import Elicit
from pydantic import BaseModel, Field

# (action, detail): action is "accept" | "decline" | "cancel"; detail is what
# the client answered, in words an op can drop straight into a cue.
ConfirmVerdict = tuple[str, str]


class ConfirmAnswer(BaseModel):
    """The one-field form the client renders. Primitives only, per the spec."""

    proceed: bool = Field(description="Go ahead?")


def client_supports_elicitation(ctx: Any) -> bool:
    """Did the connected client declare FORM elicitation?

    Mirrors the framework's own rule: a bare `elicitation: {}` (the only
    shape before modes existed) counts as form support; url-only does not.
    Outside a live request — a direct call to do() from a test or the
    facade — there is no session, and the answer is no.
    """
    try:
        capabilities = ctx.client_capabilities
    except (AttributeError, ValueError):
        return False
    elicitation = capabilities.elicitation if capabilities is not None else None
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


def confirm_marker(ctx: Any, message: str | None) -> Elicit[ConfirmAnswer] | None:
    """What a confirm resolver returns: the question, or None to fall back.

    None whenever the client cannot render the dialog or there is nothing
    to confirm — decided here, once, so an op never reasons about
    capabilities itself.
    """
    if message is None or ctx is None or not client_supports_elicitation(ctx):
        return None
    return Elicit(message=message, schema=ConfirmAnswer)


def dialog_verdict(outcome: ElicitationResult[ConfirmAnswer] | None) -> ConfirmVerdict | None:
    """Read a resolved outcome: None when no dialog was asked, else the verdict.

    A resolver that returned None (nothing to ask) reaches the body as an
    accepted outcome carrying None — that is "not asked", not a yes.
    """
    if outcome is None:
        return None
    if isinstance(outcome, AcceptedElicitation):
        if not isinstance(outcome.data, ConfirmAnswer):
            return None
        if outcome.data.proceed:
            return "accept", "the client answered proceed=true"
        return "decline", "the client answered proceed=false"
    if isinstance(outcome, DeclinedElicitation):
        return "decline", "the client declined the dialog"
    return "cancel", "the client cancelled the dialog without an answer"
