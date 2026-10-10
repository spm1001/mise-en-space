"""Reply drafts quote the message they answer (mise-wujuza), and the plain-text
fallback reads HTML the way a person would (mise-miceru)."""

import base64
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email import policy
from unittest.mock import patch

import pytest

from adapters.gmail import DraftResult, ReplyDraftResult, _build_draft_message
from extractors.gmail import parse_message_payload
from html_convert import select_body_text
from models import DoResult, EmailMessage, ErrorKind, GmailThreadData, MiseError
from tools.draft import do_draft
from tools.reply_draft import do_reply_draft
from tools.reply_quote import attribution, build_quote, has_history

BST = timezone(timedelta(hours=1))

# A reply as Gmail sends it: new words on top, the previous message nested in a
# gmail_quote. fetch's extractor strips that nest; the quote must keep it.
ANCHOR_HTML = (
    "<html><head><style>p{color:red}</style></head><body>"
    "<div dir=\"ltr\">Yes &amp; it&#39;s attached.</div><br>"
    "<div class=\"gmail_quote\"><div class=\"gmail_attr\">On Thu, 8 Oct 2026 at 09:00, Me wrote:</div>"
    "<blockquote class=\"gmail_quote\">Could you send the EARLIER-WORDS deck?</blockquote></div>"
    "</body></html>"
)
ANCHOR_TEXT = "Yes & it's attached.\n\nOn Thu, 8 Oct 2026 at 09:00, Me wrote:\n> Could you send the EARLIER-WORDS deck?"


def _msg(mid="m2", body_text=ANCHOR_TEXT, body_html=ANCHOR_HTML, header="<m2@mail.example>",
         sender="Alice <alice@example.com>", labels=None) -> EmailMessage:
    return EmailMessage(
        message_id=mid, from_address=sender, to_addresses=["me@example.com"],
        subject="Deck", date=datetime(2026, 10, 9, 14, 3, tzinfo=BST),
        body_text=body_text, body_html=body_html, message_id_header=header,
        label_ids=labels or [],
    )


def _first() -> EmailMessage:
    return _msg("m1", "Could you send the EARLIER-WORDS deck?", "<div>Could you send the EARLIER-WORDS deck?</div>",
                "<m1@mail.example>", "Me <me@example.com>")


def _thread(*messages: EmailMessage) -> GmailThreadData:
    return GmailThreadData(thread_id="abc123def456abc1", subject="Deck", messages=list(messages))


def _parts(raw: str) -> dict[str, str]:
    msg = message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
    return {p.get_content_type(): p.get_content() for p in msg.walk()
            if p.get_content_type() in ("text/plain", "text/html")}


@pytest.fixture(autouse=True)
def _quiet_gmail(monkeypatch):
    monkeypatch.setattr("tools.reply_draft.list_thread_drafts", lambda thread_id: [])
    monkeypatch.setattr("tools.reply_draft._fetch_signature",
                        lambda: ("<br><div>SIG-HTML</div>", "\n\nSIG-TEXT", []))
    monkeypatch.setattr("tools.draft._fetch_signature",
                        lambda: ("<br><div>SIG-HTML</div>", "\n\nSIG-TEXT", []))


def _reply(thread: GmailThreadData, **kw) -> tuple[DoResult, dict[str, str]]:
    """Run reply_draft against a thread; return the result and the MIME it would send."""
    built: dict[str, str] = {}

    def fake_create(**k):
        built.update(_parts(_build_draft_message(
            k["to"], k["subject"], k["body_text"], k["body_html"],
            in_reply_to=k["in_reply_to"], references=k["references"])))
        return ReplyDraftResult(draft_id="r1", message_id="mm", thread_id=k["thread_id"],
                                web_link="w", to=k["to"], subject=k["subject"])

    with patch("tools.reply_draft.fetch_thread", return_value=thread), \
         patch("tools.reply_draft.create_reply_draft", side_effect=fake_create):
        result = do_reply_draft(file_id="abc123def456abc1", content="Here it is.", **kw)
    return result, built


class TestReplyDraftQuotes:
    def test_quote_sits_under_the_signature_in_both_parts(self) -> None:
        result, parts = _reply(_thread(_first(), _msg()))
        html, text = parts["text/html"], parts["text/plain"]
        assert 'class="gmail_quote gmail_quote_container"' in html
        assert html.index("SIG-HTML") < html.index("gmail_attr") < html.index("attached")
        assert text.index("SIG-TEXT") < text.index("> Yes & it's attached.")
        assert "On Fri, 9 Oct 2026 at 14:03, Alice <alice@example.com> wrote:" in text
        assert "quoted" in result.cues

    def test_quote_carries_the_anchors_own_history_from_its_raw_parts(self) -> None:
        # The whole point: fetch strips the nested quote, the draft must not.
        _, parts = _reply(_thread(_first(), _msg()))
        assert "EARLIER-WORDS" in parts["text/html"]
        assert "> > Could you send the EARLIER-WORDS deck?" in parts["text/plain"]

    def test_document_furniture_is_not_embedded(self) -> None:
        _, parts = _reply(_thread(_first(), _msg()))
        assert "<style>" not in parts["text/html"] and "<html" not in parts["text/html"].lower().split("gmail_quote")[1]

    def test_quote_none_leaves_the_reply_bare(self) -> None:
        result, parts = _reply(_thread(_first(), _msg()), quote="none")
        assert "gmail_quote" not in parts["text/html"]
        assert "attached" not in parts["text/plain"]
        assert "quoted" not in result.cues

    def test_unknown_quote_mode_refuses(self) -> None:
        result = do_reply_draft(file_id="abc123def456abc1", content="x", quote="full")
        assert result["error"] is True and "'last'" in result["message"]

    def test_plain_only_anchor_is_quoted_from_its_text(self) -> None:
        _, parts = _reply(_thread(_msg(body_text="Line one\nLine <two>", body_html=None)))
        assert "Line one<br>" in parts["text/html"] and "Line &lt;two&gt;" in parts["text/html"]


class TestHistoryCue:
    def test_anchor_without_history_after_earlier_messages_warns(self) -> None:
        bare = _msg(body_text="Attached.", body_html="<div>Attached.</div>")
        result, _ = _reply(_thread(_first(), bare))
        assert any("quotes none of the 1 earlier message" in w for w in result.cues["warnings"])

    def test_anchor_with_history_does_not_warn(self) -> None:
        result, _ = _reply(_thread(_first(), _msg()))
        assert "warnings" not in result.cues

    def test_first_message_has_nothing_to_carry(self) -> None:
        result, _ = _reply(_thread(_first()))
        assert "warnings" not in result.cues

    def test_trashed_messages_do_not_count_as_earlier(self) -> None:
        bare = _msg(body_text="Attached.", body_html="<div>Attached.</div>")
        binned = _msg("m0", labels=["TRASH"])
        result, _ = _reply(_thread(binned, bare))
        assert "warnings" not in result.cues

    @pytest.mark.parametrize("html,text", [
        ('<div id="divRplyFwdMsg">From: X</div>', ""),
        ("", "Thanks\n\n-----Original Message-----\nFrom: X"),
        ("", "Thanks\n\nFrom: X <x@y>\nSent: Monday"),
        ("", "Thanks\n> earlier"),
    ])
    def test_history_markers(self, html: str, text: str) -> None:
        assert has_history(_msg(body_text=text, body_html=html or None))


class TestInlineImages:
    def test_cid_images_become_alt_text_with_a_warning(self) -> None:
        html = '<div>Chart:<img src="cid:ii_abc" alt="Q3 chart" width="400"> and <img src=cid:ii_def></div>'
        q = build_quote(_msg(body_html=html), 0)
        assert "cid:" not in q.html
        assert "[image: Q3 chart]" in q.html and "[image]" in q.html
        assert any("2 inline image(s)" in w for w in q.warnings)

    def test_hosted_images_are_kept(self) -> None:
        q = build_quote(_msg(body_html='<img src="https://example.com/a.png">'), 0)
        assert "https://example.com/a.png" in q.html and not q.warnings


class TestAttribution:
    def test_dated_in_the_messages_own_offset(self) -> None:
        assert attribution(_msg()) == "On Fri, 9 Oct 2026 at 14:03, Alice <alice@example.com> wrote:"

    def test_sender_markup_is_escaped_in_html(self) -> None:
        q = build_quote(_msg(sender="<script>x</script> <a@b.c>"), 0)
        assert "<script>" not in q.html


def _headers(in_reply_to="<m2@mail.example>", thread_id="abc123def456abc1"):
    h = {"to": "alice@example.com", "subject": "Re: Deck"}
    if in_reply_to:
        h["in-reply-to"] = in_reply_to
    return {"headers": h, "thread_id": thread_id}


def _update(headers, thread=None, fetch_error=None, quoted_now=True, **kw):
    built: dict[str, str] = {}

    def fake_update(draft_id, **k):
        built.update(_parts(_build_draft_message(k["to"], k["subject"], k["body_text"], k["body_html"])))
        return DraftResult(draft_id=draft_id, message_id="m9", web_link="w", to=k["to"], subject=k["subject"])

    fetch = patch("tools.draft.fetch_thread",
                  side_effect=fetch_error) if fetch_error else patch("tools.draft.fetch_thread", return_value=thread)
    with patch("tools.draft.get_draft_headers", return_value=headers), \
         patch("tools.draft.get_draft_attachments", return_value=("m0", [], quoted_now)), \
         patch("tools.draft.download_draft_attachments", return_value=[]), \
         patch("tools.draft.update_draft", side_effect=fake_update), fetch:
        result = do_draft(file_id="r1", content="v2", **kw)
    return result, built


class TestDraftUpdateRequotes:
    def test_update_of_a_reply_draft_keeps_its_quote(self) -> None:
        result, parts = _update(_headers(), _thread(_first(), _msg()))
        assert "EARLIER-WORDS" in parts["text/html"] and "quoted" in result.cues

    def test_update_with_quote_none_drops_it(self) -> None:
        _, parts = _update(_headers(), _thread(_first(), _msg()), quote="none")
        assert "gmail_quote" not in parts["text/html"]

    def test_answered_message_gone_warns_and_still_updates(self) -> None:
        result, parts = _update(_headers("<gone@x>"), _thread(_first(), _msg()))
        assert isinstance(result, DoResult) and "gmail_quote" not in parts["text/html"]
        assert any("no longer in its thread" in w for w in result.cues["warnings"])

    def test_unreadable_thread_warns_and_still_updates(self) -> None:
        result, _ = _update(_headers(), fetch_error=MiseError(ErrorKind.NETWORK_ERROR, "down"))
        assert isinstance(result, DoResult)
        assert any("could not be read (down)" in w for w in result.cues["warnings"])

    def test_non_reply_draft_ignores_quote_out_loud(self) -> None:
        result, _ = _update(_headers(in_reply_to=None), quote="last")
        assert any("not a reply" in w for w in result.cues["warnings"])

    def test_create_refuses_quote(self) -> None:
        result = do_draft(to="a@b.c", subject="s", content="c", quote="last")
        assert result["error"] is True and "replies" in result["message"]


class TestDispatchCarriesQuote:
    def test_both_draft_ops_consume_quote(self) -> None:
        from tools.dispatch import OP_PARAMS
        assert "quote" in OP_PARAMS["reply_draft"] and "quote" in OP_PARAMS["draft"]


class TestPlainTextFallback:
    """mise-miceru: an HTML-only body read without markitdown came back as one
    run-on line with &#39; for every apostrophe."""

    HTML = "<p>Hi Jo,</p><p>It&#39;s here &amp; ready.</p><div>Line one</div><div>Line two</div>"

    def test_html_only_body_keeps_lines_and_decodes_entities(self) -> None:
        with patch("markitdown.MarkItDown", side_effect=Exception("absent")):
            body, _ = select_body_text(None, self.HTML)
        assert body == "Hi Jo,\n\nIt's here & ready.\n\nLine one\nLine two"

    def test_style_text_never_reaches_the_body(self) -> None:
        with patch("markitdown.MarkItDown", side_effect=Exception("absent")):
            body, _ = select_body_text(None, "<html><head><style>.x{color:red}</style></head><body><p>Hi</p></body></html>")
        assert body == "Hi"

    def test_mise_draft_writer_round_trip_reads_back_clean(self) -> None:
        # The WRITER half of miceru, measured clean live on 2026-10-10: mise's own
        # text/plain part keeps paragraphs and apostrophes. This never reaches the
        # fallback (the plain part wins); the fallback tests above pin the reader.
        raw = _build_draft_message("a@b.c", "s", "It's one.\n\nTwo & three.", "<p>It's one.</p><p>Two &amp; three.</p>")
        payload = message_from_bytes(base64.urlsafe_b64decode(raw))
        api_payload = {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": p.get_content_type(),
             "body": {"data": base64.urlsafe_b64encode(p.get_payload(decode=True)).decode()}}
            for p in payload.walk() if p.get_content_type().startswith("text/")]}
        plain, html = parse_message_payload(api_payload)
        assert select_body_text(plain, html)[0] == "It's one.\n\nTwo & three."


class TestEssayeurFindings:
    """The 2026-10-10 essayeur pass on the first cut (36f94b3, never pushed)."""

    def test_fallback_survives_an_omitted_head_close(self) -> None:
        # HTML lets </head> go unsaid; skipping <head> to its close emptied the body.
        html = "<html><head><meta charset=utf-8><body><p>Hello Jo, the numbers are in.</p></body></html>"
        with patch("markitdown.MarkItDown", side_effect=Exception("absent")):
            assert select_body_text(None, html)[0] == "Hello Jo, the numbers are in."

    def test_forwarded_attachment_html_is_not_the_outer_body(self) -> None:
        # Through the adapter: an outer plain-only message forwarding an HTML one.
        from adapters.gmail import _build_message

        def part(mime, text):
            return {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()}}
        raw = {"id": "m1", "labelIds": [], "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "Bob <bob@example.com>"},
                        {"name": "Message-ID", "value": "<b@x>"}],
            "parts": [part("text/plain", "BOB-OWN-WORDS see attached"),
                      {"mimeType": "message/rfc822", "parts": [{"mimeType": "multipart/alternative", "parts": [
                          part("text/plain", "FORWARDED-TEXT"), part("text/html", "<p>FORWARDED-HTML</p>")]}]}]}}
        msg = _build_message(raw)
        assert msg.body_html is None
        q = build_quote(msg, 0)
        assert "BOB-OWN-WORDS" in q.html and "FORWARDED-HTML" not in q.html

    def test_quote_answers_the_live_message_not_a_trailing_trashed_aside(self) -> None:
        client = _msg("m2", body_text="CLIENT-WORDS", body_html="<div>CLIENT-WORDS</div>")
        aside = _msg("m3", body_text="ASIDE-WORDS", body_html="<div>ASIDE-WORDS</div>",
                     header="<m3@x>", sender="Colleague <c@example.com>", labels=["TRASH"])
        _, parts = _reply(_thread(_first(), client, aside))
        assert "CLIENT-WORDS" in parts["text/html"] and "ASIDE-WORDS" not in parts["text/html"]

    def test_earlier_count_skips_the_trailing_aside(self) -> None:
        bare = _msg("m2", body_text="CLIENT-WORDS", body_html="<div>CLIENT-WORDS</div>")
        aside = _msg("m3", header="<m3@x>", labels=["TRASH"])
        result, _ = _reply(_thread(_first(), bare, aside))
        assert any("quotes none of the 1 earlier message" in w for w in result.cues["warnings"])

    def test_style_script_and_body_attributes_do_not_ride_into_the_reply(self) -> None:
        html = ('<head><meta charset=utf-8><body bgcolor="#000" dir="rtl">'
                '<style>p,div{color:#fff;font-size:1px}</style><script>x()</script><p>Kept</p>')
        q = build_quote(_msg(body_html=html), 0)
        for leaked in ("<style", "color:#fff", "<script", "x()", "bgcolor", "<body", "<head", "<meta"):
            assert leaked not in q.html, leaked
        assert "<p>Kept</p>" in q.html

    @pytest.mark.parametrize("hostile", ["<img " * 40000, "<body " * 40000, "<head " * 40000,
                                         "<style " * 40000, "<img src=cid:x " * 20000],
                             ids=["img", "body", "head", "style", "cid-img"])
    def test_hostile_markup_quotes_in_linear_time(self, hostile: str) -> None:
        import time
        t0 = time.perf_counter()
        build_quote(_msg(body_html=hostile), 0)
        assert time.perf_counter() - t0 < 1.0


class TestUpdateKeepsWhatTheDraftHad:
    def test_bare_reply_draft_stays_bare(self) -> None:
        result, parts = _update(_headers(), _thread(_first(), _msg()), quoted_now=False)
        assert "gmail_quote" not in parts["text/html"]
        assert "quoted" not in result.cues  # 'quoted' only ever means a quote was added
        assert "stays bare" in result.cues["quote_kept_bare"]

    def test_bare_reply_draft_gains_a_quote_when_asked(self) -> None:
        _, parts = _update(_headers(), _thread(_first(), _msg()), quoted_now=False, quote="last")
        assert "EARLIER-WORDS" in parts["text/html"]

    def test_unknown_state_requotes(self) -> None:
        _, parts = _update(_headers(), _thread(_first(), _msg()), quoted_now=None)
        assert "EARLIER-WORDS" in parts["text/html"]

    def test_adapter_reads_quote_state_from_the_body_not_attachments(self) -> None:
        from adapters.gmail_draft_attachments import _quotes

        def html(text, **extra):
            return {"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()}, **extra}
        assert _quotes({"mimeType": "multipart/alternative", "parts": [html('<div class="gmail_quote">x</div>')]}) is True
        assert _quotes({"mimeType": "multipart/alternative", "parts": [html("<p>bare</p>")]}) is False
        assert _quotes({"mimeType": "multipart/mixed", "parts": [
            html("<p>bare</p>"), html('<div class="gmail_quote">x</div>', filename="old.html")]}) is False


class TestEssayeurRoundTwo:
    def test_forwarded_only_message_still_reads(self) -> None:
        # Forward-as-attachment with nothing typed: no outer body at all.
        from adapters.gmail import _build_message
        from extractors.gmail import extract_message_content

        def part(mime, text):
            return {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()}}
        raw = {"id": "m1", "labelIds": [], "payload": {
            "mimeType": "multipart/mixed", "headers": [{"name": "From", "value": "Bob <bob@example.com>"}],
            "parts": [{"mimeType": "message/rfc822", "parts": [{"mimeType": "multipart/alternative", "parts": [
                part("text/plain", "FORWARDED-PLAIN complaint text"), part("text/html", "<p>x</p>")]}]}]}}
        text, warnings = extract_message_content(_build_message(raw))
        assert "FORWARDED-PLAIN complaint text" in text
        assert "Message has no body content" not in warnings

    @pytest.mark.parametrize("hostile", ["<style>" * 80000, "<title>" * 80000, "<head>" * 80000],
                             ids=["style", "title", "head"])
    def test_unclosed_blocks_strip_in_linear_time(self, hostile: str) -> None:
        import time
        t0 = time.perf_counter()
        build_quote(_msg(body_html=hostile), 0)
        assert time.perf_counter() - t0 < 1.0

    @pytest.mark.parametrize("img,alt", [('<img alt="Q3 > Q2" src="cid:a">', "Q3 &gt; Q2"),
                                         ('<img alt="a<b" src="cid:a">', "a&lt;b"),
                                         ("<img alt='1>0' src='cid:a'>", "1&gt;0")])
    def test_angle_brackets_inside_attribute_values(self, img: str, alt: str) -> None:
        q = build_quote(_msg(body_html=f"<div>{img} after</div>"), 0)
        assert f"[image: {alt}] after" in q.html and "cid:" not in q.html

    def test_body_attribute_with_a_comparison_does_not_leak(self) -> None:
        q = build_quote(_msg(body_html='<body onload="if(a>b)x()" style="c"><p>Kept</p></body>'), 0)
        assert "x()" not in q.html and "<p>Kept</p>" in q.html

    @pytest.mark.parametrize("html", ['<blockquote type="cite">earlier</blockquote>',
                                      '<div id="divRplyFwdMsg">From: X</div>'])
    def test_other_clients_quotes_count_as_quoted(self, html: str) -> None:
        from adapters.gmail_draft_attachments import _quotes
        data = base64.urlsafe_b64encode(f"<p>reply</p>{html}".encode()).decode()
        assert _quotes({"mimeType": "text/html", "body": {"data": data}}) is True
