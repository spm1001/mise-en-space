"""
Id-shape diagnosis on the fetch failure path — mise-saroca + mise-tuveda.

The two bons merged during implementation. saroca reported that mise's own
`email_context` cue hands out an id that its own fetch 404s on; tuveda reported that
raw 404s carry no recovery route at all. saroca's briefed fix ("emit thread_id, the
gmail adapter has it at search time") turned out to be impossible — the id is regexed
out of a DRIVE file's description by the apps-script exfil, so no Gmail call happens
in that path and no threadId exists to hand out. What is left is tuveda's fallback,
which serves both.

The real ids below come from docs/2026-08-01-usage-review.md:
  18fe27655760c61b      the mid-thread message id that cost a 7-minute detour
  r8287431168042343092  a Gmail draft id retried against a permanent 404, then abandoned
"""

from unittest.mock import MagicMock, patch

import pytest

from models import MiseError, ErrorKind, FetchError, EmailContext
from validation import diagnose_fetch_404
from tools.fetch.router import do_fetch


REAL_MESSAGE_ID = "18fe27655760c61b"
REAL_DRAFT_ID = "r8287431168042343092"
DRIVE_SHAPED_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-x"


class TestDiagnoseFetch404:
    """The pure classifier. One case per shape."""

    def test_draft_id_says_drafts_are_not_threads_and_retrying_is_futile(self):
        out = diagnose_fetch_404(REAL_DRAFT_ID)

        assert out is not None
        assert "draft" in out.lower()
        # The measured failure was retrying a PERMANENT 404, so the text has to close
        # that door explicitly rather than merely describing the id.
        assert "404 forever" in out
        assert REAL_DRAFT_ID in out

    def test_sixteen_hex_names_the_message_vs_thread_ambiguity(self):
        out = diagnose_fetch_404(REAL_MESSAGE_ID)

        assert out is not None
        assert "message id" in out.lower()
        assert "head" in out.lower()          # ...only resolves when it heads the thread
        assert "rfc822msgid" in out           # the concrete next move

    def test_sixteen_hex_after_a_failed_message_lookup_stops_recommending_it(self):
        """Post-fallback the first message would actively mislead — mise-saroca.

        Once fetch has tried messages.get and had that 404 too, telling the caller to
        resolve it as a message sends them at a door already found locked.
        """
        out = diagnose_fetch_404(REAL_MESSAGE_ID, tried_message_lookup=True)

        assert out is not None
        assert "neither a live thread nor a live message" in out
        assert "most likely a mid-thread message id" not in out

    def test_drive_shaped_id_says_absent_or_unshared_not_malformed(self):
        out = diagnose_fetch_404(DRIVE_SHAPED_ID)

        assert out is not None
        assert "not that the id is malformed" in out
        assert "shar" in out.lower()

    def test_empty_input_diagnoses_nothing(self):
        assert diagnose_fetch_404("") is None
        assert diagnose_fetch_404("   ") is None


def _stub_thread(subject: str = "Re: Digiday"):
    """Minimal GmailThreadData stand-in — no messages, so the extraction loop is inert
    and the test stays pointed at the fallback rather than at attachment handling."""
    thread = MagicMock()
    thread.messages = []
    thread.subject = subject
    thread.warnings = []
    return thread


class TestMidThreadMessageIdFallback:
    """fetch_gmail's thread-404 → messages.get → refetch rescue."""

    @patch("tools.fetch.gmail.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="body")
    @patch("tools.fetch.gmail.get_thread_id_for_message", return_value="18fd8caa12fed511")
    @patch("tools.fetch.gmail.fetch_thread")
    def test_resolves_and_refetches_the_real_thread(
        self, mock_fetch_thread, mock_resolve, *_
    ):
        """The 2026-07-31 case, end to end: 18fe…→404, resolve, refetch 18fd…."""
        from tools.fetch.gmail import fetch_gmail

        mock_fetch_thread.side_effect = [
            MiseError(ErrorKind.NOT_FOUND, "Requested entity was not found."),
            _stub_thread(),
        ]

        fetch_gmail(REAL_MESSAGE_ID)

        mock_resolve.assert_called_once_with(REAL_MESSAGE_ID)
        # Second call must use the RESOLVED id, not the original.
        assert mock_fetch_thread.call_args_list[-1].args[0] == "18fd8caa12fed511"

    @patch("tools.fetch.gmail.lookup_exfiltrated", return_value={})
    @patch("tools.fetch.gmail.get_deposit_folder")
    @patch("tools.fetch.gmail.write_manifest")
    @patch("tools.fetch.gmail.write_content")
    @patch("tools.fetch.gmail.extract_thread_content", return_value="body")
    @patch("tools.fetch.gmail.get_thread_id_for_message", return_value="18fd8caa12fed511")
    @patch("tools.fetch.gmail.fetch_thread")
    def test_the_rescue_is_disclosed_never_silent(
        self, mock_fetch_thread, mock_resolve, *_
    ):
        """A fetch that quietly returns a different id than you asked for is the
        accept-and-drop shape this repo is named for, wearing a helpful face."""
        from tools.fetch.gmail import fetch_gmail

        mock_fetch_thread.side_effect = [
            MiseError(ErrorKind.NOT_FOUND, "Requested entity was not found."),
            _stub_thread(),
        ]

        result = fetch_gmail(REAL_MESSAGE_ID)

        warnings = (result.cues or {}).get("warnings") or []
        disclosure = " ".join(warnings)
        assert REAL_MESSAGE_ID in disclosure
        assert "18fd8caa12fed511" in disclosure
        assert "MESSAGE id" in disclosure

    @patch("tools.fetch.gmail.get_thread_id_for_message")
    @patch("tools.fetch.gmail.fetch_thread")
    def test_double_404_teaches_that_it_is_neither_and_marks_itself_diagnosed(
        self, mock_fetch_thread, mock_resolve
    ):
        from tools.fetch.gmail import fetch_gmail

        mock_fetch_thread.side_effect = MiseError(ErrorKind.NOT_FOUND, "not found")
        mock_resolve.side_effect = MiseError(ErrorKind.NOT_FOUND, "not found")

        with pytest.raises(MiseError) as caught:
            fetch_gmail(REAL_MESSAGE_ID)

        assert "neither a live thread nor a live message" in caught.value.message
        # The flag stops the router appending the generic advice on top, which would
        # recommend the very lookup that just failed.
        assert caught.value.details.get("diagnosed") is True

    @patch("tools.fetch.gmail.get_thread_id_for_message")
    @patch("tools.fetch.gmail.fetch_thread")
    def test_non_gmail_shaped_id_is_not_sent_down_the_fallback(
        self, mock_fetch_thread, mock_resolve
    ):
        """The fallback is gated on the 16-hex Gmail band. A Drive-shaped id that 404s
        must not cost a pointless Gmail round trip."""
        from tools.fetch.gmail import fetch_gmail

        mock_fetch_thread.side_effect = MiseError(ErrorKind.NOT_FOUND, "not found")

        with pytest.raises(MiseError):
            fetch_gmail(DRIVE_SHAPED_ID)

        mock_resolve.assert_not_called()


class TestRouterAttachesDiagnosisTo404s:
    """The funnel half — ids arriving from anywhere, not just the cue."""

    @patch("tools.fetch.router.fetch_drive")
    def test_draft_id_404_gains_the_draft_advice(self, mock_drive):
        mock_drive.side_effect = MiseError(ErrorKind.NOT_FOUND, "File not found.")

        result = do_fetch(REAL_DRAFT_ID)

        assert isinstance(result, FetchError)
        assert result.kind == "not_found"
        assert "File not found." in result.message   # Google's text survives
        assert "draft" in result.message.lower()     # ...and ours is added

    @patch("tools.fetch.router.fetch_gmail")
    def test_an_already_diagnosed_error_is_not_double_diagnosed(self, mock_gmail):
        mock_gmail.side_effect = MiseError(
            ErrorKind.NOT_FOUND,
            "'18fe27655760c61b' is neither a live thread nor a live message.",
            details={"diagnosed": True},
        )

        result = do_fetch(REAL_MESSAGE_ID)

        assert isinstance(result, FetchError)
        assert "most likely a mid-thread message id" not in result.message

    @patch("tools.fetch.router.fetch_drive")
    def test_non_404_errors_are_left_alone(self, mock_drive):
        mock_drive.side_effect = MiseError(ErrorKind.PERMISSION_DENIED, "no access")

        result = do_fetch(DRIVE_SHAPED_ID)

        assert isinstance(result, FetchError)
        assert result.message == "no access"


class TestEmailContextCueDoesNotPromiseWhatItCannotKeep:
    """The saroca half at its source — the cue that minted the bad id."""

    def test_cue_names_the_id_type(self):
        cue = EmailContext(message_id=REAL_MESSAGE_ID).to_cue()

        assert "not a thread id" in cue["hint"].lower()
        assert REAL_MESSAGE_ID in cue["hint"]
