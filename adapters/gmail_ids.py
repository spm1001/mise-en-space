"""
Gmail id resolution — alternate identifiers → the thread id fetch needs.

Gmail gives one conversation several names: the thread id, per-message ids
sharing the thread id's shape, the RFC 822 Message-ID header, and the web
UI's encoded tokens. fetch() speaks thread ids; these helpers resolve the
others to one. Both are failure-path helpers, fields-masked to the single
id they exist to learn.

Split from adapters/gmail.py 2026-08-07 (module-size ratchet, mise-nebewe):
the id-resolution concern is separable from thread/message content fetching.
"""

from adapters.gmail import _GMAIL_API, _is_own_address
from adapters.http_client import get_sync_client
from models import EmailMessage, MiseError, ErrorKind
from retry import with_retry
from validation import gmail_thread_web_url


def thread_web_link_or_warn(
    messages: list[EmailMessage],
    thread_id: str,
    warnings: list[str],
) -> str | None:
    """Clickable Gmail web URL for a fetched thread — or a warning saying why not.

    Returns a URL only when another party is visibly in the thread (some
    message's From provably not the user): a delivered thread is f-family,
    whose web token is arithmetically derivable from the API id. A thread
    authored solely by the user may be self-sent (thread-a), which has NO
    derivable token (mise-lerulo) — minting one would open the wrong
    conversation, so those get a warning appended naming the gap instead.
    This helper carries the whole feature so its ratchet-frozen call site
    (tools/fetch/gmail.py) pays as few lines as possible (mise-hetaba).
    """
    if any(_is_own_address(m.from_address) is False for m in messages):
        return gmail_thread_web_url(thread_id)
    warnings.append(
        "No web_link: every message here is from you (or identity is unresolved), "
        "so this may be a self-sent thread — its web token cannot be derived from "
        "the API id, and a minted link could open the wrong conversation. Find it "
        "in Gmail by subject search."
    )
    return None


@with_retry(max_attempts=3, delay_ms=1000)
def get_thread_id_for_message(message_id: str) -> str:
    """
    Resolve a Gmail MESSAGE id to the id of the thread that holds it.

    Gmail thread ids and message ids share one shape (16 hex) and one id space, but
    a message id only resolves against threads.get when that message HEADS its
    thread. Mid-conversation, a perfectly valid message id 404s as a thread with
    nothing in the error to say why — that cost a 7-minute detour on 2026-07-31 and
    is the whole of mise-saroca.

    Deliberately fields-masked to `threadId`. This runs only on a failure path, and
    fetch_message's format=full would download an entire message body — attachments
    metadata, both MIME parts — to learn one id.

    Args:
        message_id: A Gmail message id.

    Returns:
        The id of the thread holding that message.

    Raises:
        MiseError: NOT_FOUND when the id is not a live message either. Callers read
            that as "neither a thread nor a message on this account" and should pass
            tried_message_lookup=True to validation.diagnose_fetch_404, so the advice
            stops suggesting a message lookup that has already failed.
    """
    client = get_sync_client()

    msg = client.get_json(
        f"{_GMAIL_API}/messages/{message_id}",
        params={"fields": "threadId"},
    )

    # Defensive: list endpoints under a fields mask can return an empty body rather
    # than {} (drafts.list does exactly that — see list_thread_drafts), so don't
    # assume a dict came back just because the status was 2xx.
    thread_id = msg.get("threadId") if isinstance(msg, dict) else None
    if not thread_id:
        raise MiseError(
            ErrorKind.NOT_FOUND,
            f"messages.get returned no threadId for '{message_id}'",
        )

    return str(thread_id)


@with_retry(max_attempts=3, delay_ms=1000)
def get_thread_id_for_draft(draft_id: str) -> str:
    """
    Resolve a Gmail DRAFT id to the id of the thread holding the draft.

    mise's own draft/reply_draft results carry a web link of the form
    …/mail/#drafts/<draft-id>, and until mise-jujoti closed the loop mise could
    not read the URL it had itself written. Drafts live in their own id space
    (r + digits); drafts.get is the only bridge to the thread, where Gmail
    renders the draft in place.

    Fields-masked to message/threadId — one id is all this call exists to
    learn. NOT_FOUND here is a *lifecycle* answer, not a malformed-id answer:
    drafts disappear when sent or discarded, so the link expires. The router
    says so; this raises plain.

    Args:
        draft_id: A Gmail draft id (r + digits).

    Returns:
        The id of the thread holding the draft.

    Raises:
        MiseError: NOT_FOUND when the draft no longer exists.
    """
    client = get_sync_client()

    draft = client.get_json(
        f"{_GMAIL_API}/drafts/{draft_id}",
        params={"fields": "message/threadId"},
    )

    # Same defensive shape as get_thread_id_for_message: a fields-masked
    # endpoint can return an empty body rather than {}.
    message = draft.get("message") if isinstance(draft, dict) else None
    thread_id = message.get("threadId") if isinstance(message, dict) else None
    if not thread_id:
        raise MiseError(
            ErrorKind.NOT_FOUND,
            f"drafts.get returned no threadId for draft '{draft_id}'",
        )

    return str(thread_id)


@with_retry(max_attempts=3, delay_ms=1000)
def get_thread_id_for_rfc822_message_id(message_id: str) -> str:
    """
    Resolve an RFC 822 Message-ID header to the id of the thread holding it.

    rfc822msgid: is an exact-match operator — a Message-ID names one message in
    one mailbox, so this returns 0 or 1 hits, never a ranking. It is the
    deterministic route into threads whose web tokens decode to thread-a
    (self-sent, ~2018+), where no arithmetic transform exists (mise-lerulo;
    the negatives are recorded on validation._decode_gmail_web_token).

    Fields-masked to id+threadId — one id is all this call exists to learn.

    Args:
        message_id: The Message-ID without angle brackets.

    Returns:
        The id of the thread holding that message.

    Raises:
        MiseError: NOT_FOUND when no message in this mailbox carries the id.
    """
    client = get_sync_client()

    result = client.get_json(
        f"{_GMAIL_API}/messages",
        params={
            "q": f"rfc822msgid:{message_id}",
            "maxResults": 1,
            "fields": "messages(id,threadId)",
        },
    )

    messages = result.get("messages") if isinstance(result, dict) else None
    thread_id = messages[0].get("threadId") if messages else None
    if not thread_id:
        raise MiseError(
            ErrorKind.NOT_FOUND,
            f"No message with Message-ID '{message_id}' in this mailbox. The "
            f"rfc822msgid: lookup is exact-match — check the id was copied "
            f"whole from Show original (everything between the angle "
            f"brackets). If this is an email ADDRESS rather than a "
            f"Message-ID, use search('from:…') instead.",
        )

    return str(thread_id)
