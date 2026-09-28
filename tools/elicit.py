"""The elicit-or-confirm seam — the human's yes as a UI event (mise-wagina).

Today every confirm gate in mise routes the human's yes THROUGH the model:
Claude reads the preview, asks, and passes confirm=True itself. MCP
elicitation makes the yes a UI event instead — the server parks the tool
call and asks the CLIENT, which renders a dialog the model cannot see,
answer or detect (probe-confirmed against Claude Code 2.1.241, 2026-08-23).

The gate rides mcp's resolver injection (`Annotated[…, Resolve(fn)]`): do()
carries one gate param (tools/confirm_gate.py) whose resolvers work out the
question — the gated op's own preview message — and ask it, and the
framework asks the client by whatever the negotiated protocol allows — a
standalone elicitation/create request over the back-channel on <= 2025-11-25
(Claude Code today), an InputRequiredResult round-trip on >= 2026-07-28. The
tool body receives the question and the outcome; `settle_gate` decides.

Three facts shape what is here:

- Capability is declared at initialize and READ at the client seam, never
  assumed. The gate asks nothing of a client that did not declare
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
  4 lines — a longer message shows its first 3 plus "… (+N more lines)" (read from its bundle,
  `tXe` with `t2=3`, and seen live: docs/research/2026-09-28-pukiri-hublot/).
  So `tools/confirm_gate.py` asks only when the whole message fits an 80-column
  terminal; anything longer takes the confirm= round-trip, where the model
  shows the full preview. A human must never approve less than the preview
  shows.
"""

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.elicitation import (
    AcceptedElicitation,
    DeclinedElicitation,
    ElicitationResult,
)
from pydantic import BaseModel, Field

# (action, detail): action is "accept" | "decline" | "cancel"; detail is what
# the client answered, in words an op can drop straight into a cue.
ConfirmVerdict = tuple[str, str]

# Why a capable client with a gated call got no dialog (GateQuestion.skipped).
TOO_LONG = "too_long"          # the message would not be shown whole
UNAVAILABLE = "unavailable"    # the question could not be prepared (a Google read failed)
# An answer arrived for a question this round no longer asks. On >= 2026-07-28
# the resolvers re-run when the client retries with its answer; if the state
# changed so the call is no longer gated, the answer would never be read and
# the body would see "no dialog". It must stop instead (mise-pukiri, round-2
# essayeur: update_event wrote after a decline that way).
ORPHANED = "orphaned"


@dataclass(frozen=True)
class GateQuestion:
    """What the dialog carries (or would), and whether it is asked.

    message: the op's preview message, or None when there is nothing to
        confirm here (not gated, confirmed, no capability, invalid inputs).
    skipped: why a gated call was NOT asked — TOO_LONG, UNAVAILABLE or
        ORPHANED — else None.
    Built by tools/confirm_gate.py; lives here so the ops can read it.
    """

    message: str | None = None
    skipped: str | None = None

    @property
    def ask(self) -> bool:
        return self.message is not None and self.skipped is None


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


TOO_LONG_CUE = (
    "no dialog: this preview is longer than a Claude Code confirm dialog shows "
    f"whole ({DIALOG_MAX_LINES} lines of {DIALOG_MAX_COLS} columns on an 80-column "
    "terminal), so the confirm= round-trip applies: show the user the whole "
    "preview and call again with confirm=True on their yes."
)


_SKIP_CUES = {
    TOO_LONG: TOO_LONG_CUE,
    UNAVAILABLE: (
        "no dialog: the confirmation question could not be prepared (a Google read "
        "failed), so the confirm= round-trip applies: show the user this preview "
        "and call again with confirm=True on their yes."
    ),
    ORPHANED: (
        "elicitation: an answer came back for a question this call no longer asks — "
        "what would happen changed while the dialog waited. Nothing was written; "
        "the preview below is the current state, so the confirm= round-trip "
        "applies: show it to the user and call again with confirm=True on their yes."
    ),
}


def skipped_preview(preview: dict[str, Any], question: GateQuestion | None) -> dict[str, Any]:
    """The confirm= preview, plus a cue when a capable client was not asked.

    A fallback says why it fired: without this, a too-long preview, a failed
    read or an orphaned answer looks exactly like a client with no dialog
    support.
    """
    cue = _SKIP_CUES.get(question.skipped) if question and question.skipped else None
    if cue is None:
        return preview
    return {**preview, "cues": {**preview["cues"], "confirm_gate": cue}}


def stale_preview(preview: dict[str, Any], detail: str, nothing: str) -> dict[str, Any]:
    """The current preview when an accepted dialog showed something else.

    The accept covered the words the dialog carried. If the state about to
    be written no longer renders to those words (an event re-read after the
    dialog changed underneath it), the accept does not cover it.
    """
    cues = dict(preview["cues"])
    cues["confirm_gate"] = (
        f"elicitation: {detail}, but what the dialog showed no longer matches what "
        f"would happen now. {nothing}; the preview below is the current state, so "
        "the confirm= round-trip applies: show it to the user and call again with "
        "confirm=True on their yes."
    )
    return {**preview, "cues": cues}


def settle_gate(
    answer: ElicitationResult[ConfirmAnswer] | None,
    question: GateQuestion | None,
    gated: bool,
    preview: Callable[[], dict[str, Any]],
    nothing: str,
    done: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Decide a gated write from the dialog's outcome and the state now.

    Returns (preview, None) when the op must stop and return that preview,
    or (None, cue) when it may write — `cue` is the confirm_gate text to
    attach, None when no dialog decided. `gated` is the op's own test on
    the state it just read; the caller handles a pre-supplied confirm=True
    before calling (policy A), so this never sees one. `preview` is called
    only when needed (it may read the API).

    Once a dialog has been answered, the answer governs whatever the re-read
    says: anything but an accept never writes, and an accept writes only if
    the current preview's message is the one the dialog carried. An answer
    to a question this round no longer asks (ORPHANED) never writes either.
    """
    if question is not None and question.skipped == ORPHANED:
        return skipped_preview(preview(), question), None
    verdict = dialog_verdict(answer)
    if verdict is None:
        if not gated:
            return None, None
        return skipped_preview(preview(), question), None
    action, detail = verdict
    current = preview()
    if action != "accept":
        return unaccepted_preview(current, verdict, nothing), None
    if question is None or current.get("message") != question.message:
        return stale_preview(current, detail, nothing), None
    return None, accepted_cue(detail, done)
