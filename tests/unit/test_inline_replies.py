"""Inline replies written inside a quoted message survive quote stripping (mise-lopune).

The fixture is hand-built and minimal, with invented people: a question email
and a "response below" reply whose answers sit inside the '>'-quoted copy of
the question — mid-line, rewrapped, the shape the real field report had.
"""

from datetime import datetime, timezone

from models import EmailMessage, GmailThreadData
from extractors.gmail import extract_thread_content
from extractors.inline_replies import find_inline_replies


QUESTION = """Hi Tom,

Two quick questions on the pricing card.

1. When a custom audience is priced in the small, medium or large tier,
does the tier count matched accounts, or the partner's shoppers before
the match?

2. The new guide shows a single custom rate, so are the three tiers
still meant to apply to custom builds at all?

Thanks

Priya

Priya Shah
Analytics Lead | Example Media
<https://example.com/team>
"""

# The reply's plain-text part quotes the answers along with the question,
# rewrapped by the replier's client — which is what hid them.
INLINE_REPLY = """Hi Priya,

Response below, shout if anything is unclear.

Best,
Tom

On Wed, 23 Sept 2026 at 07:01, Priya Shah <priya@example.com> wrote:

> Hi Tom,
>
> Two quick questions on the pricing card.
>
> 1. When a custom audience is priced in the small, medium or large tier,
> does the tier count matched accounts, or the partner's shoppers before
> the match? It counts the matched audience in the clean room, for the
> grocery partner only.
>
> 2. The new guide shows a single custom rate, so are the three tiers
> still meant to apply to custom builds at all? No, the flat custom rate
> replaced the tiers this year; the always-on audiences have their own
> lower flat rate.
>
> Thanks
>
> Priya
>
> Priya Shah
> Analytics Lead | Example Media
> <https://example.com/team>
"""

PLAIN_REPLY = """Thanks Priya, I'll dig out the numbers tomorrow.

Tom

On Wed, 23 Sept 2026 at 07:01, Priya Shah <priya@example.com> wrote:

> Hi Tom,
>
> Two quick questions on the pricing card.
>
> 1. When a custom audience is priced in the small, medium or large tier,
> does the tier count matched accounts, or the partner's shoppers before
> the match?
>
> 2. The new guide shows a single custom rate, so are the three tiers
> still meant to apply to custom builds at all?
>
> Thanks
>
> Priya
"""


def _thread(reply_body: str, question_body: str = QUESTION) -> GmailThreadData:
    return GmailThreadData(
        thread_id="t1",
        subject="Pricing tiers: two quick questions",
        messages=[
            EmailMessage(
                message_id="m1", from_address="Priya Shah <priya@example.com>",
                to_addresses=["tom@example.com"], body_text=question_body,
                date=datetime(2026, 9, 23, 6, 1, tzinfo=timezone.utc),
            ),
            EmailMessage(
                message_id="m2", from_address="Tom Reed <tom@example.com>",
                to_addresses=["priya@example.com"], body_text=reply_body,
                date=datetime(2026, 9, 23, 11, 41, tzinfo=timezone.utc),
            ),
        ],
    )


class TestInlineRepliesInTheThread:

    def test_answers_written_inside_the_quote_are_deposited(self):
        data = _thread(INLINE_REPLY)
        content = extract_thread_content(data)
        reply = content.split("[2/2]")[1]
        assert "It counts the matched audience in the clean room, for the grocery partner only." in reply
        assert "No, the flat custom rate replaced the tiers this year" in reply
        assert "lower flat rate." in reply

    def test_each_answer_names_the_quoted_words_it_follows(self):
        content = extract_thread_content(_thread(INLINE_REPLY))
        assert "partner's shoppers before the match?" in content.split("It counts")[0].split("[2/2]")[1]

    def test_the_recovery_is_disclosed_in_warnings(self):
        data = _thread(INLINE_REPLY)
        extract_thread_content(data)
        assert any("Message 2" in w and "inline" in w.lower() for w in data.warnings)

    def test_an_ordinary_reply_still_strips_its_quote(self):
        """The control: a quote that only repeats the earlier message adds nothing."""
        data = _thread(PLAIN_REPLY)
        content = extract_thread_content(data)
        reply = content.split("[2/2]")[1]
        assert "dig out the numbers" in reply
        assert "Inline replies" not in reply
        assert "Two quick questions" not in reply
        assert not any("inline" in w.lower() for w in data.warnings)

    def test_first_message_is_never_treated_as_a_reply(self):
        data = _thread(PLAIN_REPLY, question_body=INLINE_REPLY)
        content = extract_thread_content(data)
        assert "Inline replies" not in content.split("[2/2]")[0]


class TestFindInlineReplies:

    def test_rewrapping_alone_is_not_an_answer(self):
        """Word shingles ignore line breaks: a quote rewrapped at a different width matches."""
        rewrapped = "\n".join("> " + line for line in " ".join(QUESTION.split()).split(". "))
        replies, coverage = find_inline_replies(rewrapped, [QUESTION])
        assert replies == []
        assert coverage > 0.9

    def test_a_quote_of_nothing_in_the_thread_is_not_called_inline(self):
        """Low coverage means the quoted message is not in the thread — not an answer."""
        stranger = "> Minutes from the offsite: we agreed the budget, the hiring plan\n> and a date for the next review of the roadmap with finance."
        replies, coverage = find_inline_replies(stranger, [QUESTION])
        assert replies == []
        assert coverage < 0.5

    def test_no_earlier_messages_means_no_answers(self):
        assert find_inline_replies(INLINE_REPLY, []) == ([], 0.0)

    def test_links_the_quoting_client_added_are_not_answers(self):
        """Gmail links an address as it quotes it; the link is new text but nobody's answer.

        The first real-mail sweep (27 Sep 2026) found this in 5 of 6 firings.
        """
        question = "Lunch on Friday?\n\nSam\n1 Example Road, London, E1 1AA\n"
        reply = (
            "Yes please.\n\n> Lunch on Friday?\n>\n> Sam\n> 1 Example Road, London, E1 1AA\n"
            "> <https://www.google.com/maps/search/1+Example+Road,+London,+E1+1AA?entry=gmail&source=g>\n"
        )
        assert find_inline_replies(reply, [question])[0] == []


FOOTER = (
    "Example Media Limited is registered in England and Wales with its registered "
    "office at 1 Example Road. This email and any attachments are confidential."
)


class TestFalsePositiveGates:
    """Each gate answers a false-positive class from the 27 Sep 2026 real-mail sweep."""

    def test_a_reply_that_does_not_point_into_its_quote_is_left_alone(self):
        """Novel quoted text with no 'below'/'inline' in the reply is not read as an answer."""
        reply = INLINE_REPLY.replace("Response below, shout if anything is unclear.", "Thanks, will do.")
        content = extract_thread_content(_thread(reply))
        assert "Inline replies" not in content

    def test_a_trailing_gateway_footer_is_not_an_answer(self):
        """A footer added in transit sits after the quoted message, with nothing quoted after it."""
        reply = PLAIN_REPLY.replace("Thanks Priya,", "See below, Priya —") + "".join(
            f"> {line}\n" for line in FOOTER.split(". ")
        )
        content = extract_thread_content(_thread(reply))
        assert "Inline replies" not in content

    def test_a_phone_number_the_client_linked_is_not_an_answer(self):
        # Quoted text continues after the linked number, so only the link rule
        # (not the trailing-footer rule) stands between it and a false answer.
        question = "Call me on the number below.\n\nT +44 (0)20 7946 0000\nSam, happy to walk you through the whole plan\n"
        reply = (
            "Answers below.\n\n> Call me on the number below.\n>\n"
            "> T +44 (0)20 7946 0000 <+44%2020%207946%200000>\n> Sam, happy to walk you through the whole plan\n"
        )
        assert find_inline_replies(reply, [question])[0] == []


class TestPointsIntoQuote:

    def test_the_phrasings_that_send_a_reader_into_the_quote(self):
        from extractors.inline_replies import reply_points_into_quote
        for own in ["Response below.", "Answers in red", "My comments inline.",
                    "see my notes", "Replies in bold, thanks", ""]:
            assert reply_points_into_quote(own), own
        for own in ["Thanks, will do.", "Great, speak Friday."]:
            assert not reply_points_into_quote(own), own


class TestInsertionAndAddedText:
    """The second round of gates, from the forward-header, footer and chain cases."""

    EARLIER = (
        "Hi Tom,\n\nCan you send the Q3 numbers before Friday's review?\n\nThanks\n\nPriya\n\n"
        "On Mon, 21 Sept 2026 at 09:00, Tom Reed <tom@example.com> wrote:\n\n"
        "> Happy to help with the review pack next week.\n"
    )

    def test_a_footer_between_two_quoted_messages_is_not_an_answer(self):
        """Added in transit between the signature and the older quote — adjacent originally,
        so only the footer rule (not the insertion rule) catches it."""
        reply = (
            "See below.\n\n> Hi Tom,\n>\n> Can you send the Q3 numbers before Friday's review?\n>\n"
            "> Thanks\n>\n> Priya\n>\n> " + FOOTER + "\n>\n"
            "> On Mon, 21 Sept 2026 at 09:00, Tom Reed <tom@example.com> wrote:\n>\n"
            "> > Happy to help with the review pack next week.\n"
        )
        assert find_inline_replies(reply, [self.EARLIER])[0] == []

    def test_a_forward_header_block_is_not_an_answer(self):
        reply = (
            "See below for info.\n\n> Hi Tom,\n>\n> Can you send the Q3 numbers before Friday's review?\n>\n"
            "> ---------- Forwarded message ---------\n> From: Dana Lee <dana@example.org>\n"
            "> Date: Fri, 12 Jun 2026 at 10:30\n> Subject: Quarterly review pack and timings\n"
            "> To: Priya Shah <priya@example.com>\n>\n"
            "> Thanks\n>\n> Priya\n>\n"
            "> On Mon, 21 Sept 2026 at 09:00, Tom Reed <tom@example.com> wrote:\n>\n"
            "> > Happy to help with the review pack next week.\n"
        )
        # Enough known text that coverage clears the floor, so only the header
        # rule stands between the block and a false answer.
        replies, coverage = find_inline_replies(reply, [self.EARLIER])
        assert coverage > 0.3
        assert replies == []

    def test_text_that_was_never_between_those_lines_is_not_an_answer(self):
        """Quoted history from outside the thread sits before the known text, not inside it."""
        reply = (
            "Comments below.\n\n> Dana, the vendor confirmed the renewal price holds for another year\n"
            "> and the contract can move to the new entity without a fresh signature.\n>\n"
            "> Hi Tom,\n>\n> Can you send the Q3 numbers before Friday's review?\n>\n> Thanks\n>\n> Priya\n"
        )
        assert find_inline_replies(reply, [self.EARLIER])[0] == []

    def test_an_answer_between_the_lines_still_counts(self):
        reply = (
            "Answers below.\n\n> Hi Tom,\n>\n> Can you send the Q3 numbers before Friday's review?\n"
            "> Yes, they will be with you by Thursday lunchtime at the latest.\n>\n"
            "> Thanks\n>\n> Priya\n"
        )
        replies, _ = find_inline_replies(reply, [self.EARLIER])
        assert [r.text for r in replies] == ["Yes, they will be with you by Thursday lunchtime at the latest."]
        assert replies[0].after.endswith("before Friday's review?")
