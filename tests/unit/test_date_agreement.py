"""One message, one clock time, whichever surface shows it (mise-janago).

Search used to stamp a Gmail result in the sender's own offset while fetch's
Date lines are UTC with a Z (mise-hopife), so the same instant read as two
different times across two calls — 13:41+01:00 in search, 12:41Z in fetch.
"""

from datetime import datetime, timedelta, timezone

from extractors.gmail import extract_thread_content
from models import EmailMessage, GmailSearchResult, GmailThreadData
from tools.search_format import format_gmail_result

# A sender two hours ahead of UTC, the shape that made the stamps disagree.
SENT = datetime(2026, 9, 23, 13, 41, 33, tzinfo=timezone(timedelta(hours=2)))


def test_search_and_fetch_show_the_same_clock_time_and_zone():
    searched = format_gmail_result(GmailSearchResult(thread_id="t1", subject="s", snippet="", date=SENT))
    fetched = extract_thread_content(GmailThreadData(thread_id="t1", subject="s", messages=[
        EmailMessage(message_id="m1", from_address="a@example.com", to_addresses=[], body_text="hi", date=SENT),
    ]))
    assert searched["date"] == "2026-09-23 11:41Z"
    assert f"Date: {searched['date']}" in fetched
