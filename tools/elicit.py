"""The elicit-or-confirm seam — the human's yes as a UI event (mise-wagina).

Today every confirm gate in mise routes the human's yes THROUGH the model:
Claude reads the preview, asks, and passes confirm=True itself. MCP
elicitation makes the yes a UI event instead — the server parks the tool
call and asks the CLIENT, which renders a dialog the model cannot see,
answer or detect (probe-confirmed against Claude Code 2.1.241, 2026-08-23).

The gate rides mcp's resolver injection (`Annotated[…, Resolve(fn)]`): do()
carries ONE resolver (tools/confirm_gate.py) that returns
`confirm_marker(ctx, preview_text)` for whichever gated op is being called,
and the framework asks the client by whatever the negotiated protocol allows — a
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
- A dialog shows only what fits it. Claude Code 2.1.283 clips every line of
  the message to (terminal width − 6) columns, never wraps, and shows at most
  4 lines — the rest collapse to "… (+N more lines)" (read from its bundle,
  `tXe` with `t2=3`, and seen live: docs/research/2026-09-28-pukiri-hublot/).
  So `confirm_marker` asks only when the whole message fits an 80-column
  terminal; anything longer takes the confirm= round-trip, where the model
  shows the full preview. A human must never approve less than the preview
  shows.
"""

import unicodedata
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


# Claude Code's dialog budget on an 80-column terminal (see module docstring).
DIALOG_MAX_LINES = 4
DIALOG_MAX_COLS = 80 - 6


def _display_width(text: str) -> int:
    """Terminal columns: wide and full-width characters take two."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def fits_dialog(message: str) -> bool:
    """Would Claude Code show this message whole, on an 80-column terminal?"""
    lines = message.split("\n")
    return len(lines) <= DIALOG_MAX_LINES and all(
        _display_width(line) <= DIALOG_MAX_COLS for line in lines
    )


def confirm_marker(ctx: Any, message: str | None) -> Elicit[ConfirmAnswer] | None:
    """What a confirm resolver returns: the question, or None to fall back.

    None whenever the client cannot render the dialog, there is nothing to
    confirm, or the message would not be shown whole — decided here, once,
    so an op never reasons about capabilities or screen budgets itself.
    """
    if message is None or ctx is None or not fits_dialog(message):
        return None
    if not client_supports_elicitation(ctx):
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


def accepted_cue(detail: str, done: str) -> str:
    """The confirm_gate cue after an accepted dialog: the mechanism, then what ran.

    `done` is the op's past tense ("shared", "booked", "updated"). The cue says
    what the client answered, never that a human approved.
    """
    return f"elicitation: {detail}; {done} on that answer"


def unaccepted_preview(
    preview: dict[str, Any], verdict: ConfirmVerdict, nothing: str,
) -> dict[str, Any]:
    """The preview an op returns when the dialog did not end in a yes.

    The same preview the confirm= path returns, plus a confirm_gate cue
    saying what the dialog did. `nothing` names what did not happen
    ("Nothing was shared"), without a closing stop.

    - cancel: no dialog decided this (headless clients auto-cancel; a
      dismissed dialog cancels), so the confirm= round-trip stays open.
    - decline: an answer. confirm_required is withdrawn so the model is not
      nudged into supplying the yes the dialog just refused.
    """
    action, detail = verdict
    cues = dict(preview["cues"])
    if action == "decline":
        cues.pop("confirm_required", None)
        cues["confirm_gate"] = (
            f"elicitation: declined — {detail}. {nothing}. The user said no "
            "through the dialog; ask them again before any further attempt."
        )
    else:
        cues["confirm_gate"] = (
            f"elicitation: {action} — {detail}. {nothing}; no dialog decided "
            "this, so the confirm= round-trip applies: show this preview to the user "
            "and call again with confirm=True on their yes."
        )
    return {**preview, "cues": cues}
