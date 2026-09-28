"""mise-hejeze: a REFUSED directory read is not 'not in the directory'.

Ambient placement (search rows, Gmail fetch participants) is best-effort, and
an own-domain address the directory does not know is an honest absence. A
token minted before admin.directory.user.readonly joined the scopes gets a 403
on EVERY lookup instead, and until this card that refusal was cached as the
same honest absence — so a Gmail fetch of a thread full of colleagues came back
with no people, no people_note and no warning, and nothing said placement had
even been attempted (seen on sameer-macbook-air, 28 Sep 2026).

The refusal is driven through the real get_person -> _convert_error path with
the 403 body measured on tube 2026-09-28 (a narrow-scope refresh of a real ITV
grant: users.get domain_public answered this; the same grant WITH the scope
answered 200).
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from models import EmailMessage, GmailSearchResult, GmailSearchResults, GmailThreadData

ME = "sameer.modha@itv.com"

# Measured 2026-09-28 (trimmed to what _convert_error keeps: the first 300 chars).
SCOPE_BODY = (
    '{\n  "error": {\n    "code": 403,\n    "message": "Request had insufficient '
    'authentication scopes.",\n    "errors": [\n      {\n        "message": '
    '"Insufficient Permission",\n        "domain": "global",\n        "reason": '
    '"insufficientPermissions"\n      }\n    ],\n    "status": "PERMISSION_DENIED"'
)
# The other 403 the module docstring records: domain_public dropped, or the
# domain's Contact sharing off — the scope is present, the read still refused.
NOT_AUTHORIZED_BODY = '{"error": {"code": 403, "message": "Not Authorized to access this resource/api"}}'


def _status_error(status: int, body: str) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://admin.googleapis.com/admin/directory/v1/users/x")
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(status, text=body, request=req))


def _directory(refuse_with: str | None = None, known: dict | None = None, calls: list | None = None):
    """A fake sync client: every lookup 403s with `refuse_with`, else answers from `known` (404 otherwise)."""
    known = known or {}

    def get_json(url, params=None):
        address = url.rsplit("/", 1)[-1]
        if calls is not None:
            calls.append(address)
        if refuse_with is not None:
            raise _status_error(403, refuse_with)
        if address not in known:
            raise _status_error(404, '{"error": {"code": 404, "message": "Resource Not Found: userKey"}}')
        name, mgr = known[address]
        user = {"primaryEmail": address, "name": {"fullName": name},
                "organizations": [{"title": "Role", "department": "Commercial"}]}
        if mgr:
            user["relations"] = [{"type": "manager", "value": mgr}]
        return user

    client = MagicMock()
    client.get_json = get_json
    return client


def _thread(*addresses: str) -> GmailThreadData:
    msg = EmailMessage(
        message_id="m1", from_address=addresses[0],
        to_addresses=[f"Sameer Modha <{ME}>", *addresses[1:]], body_text="",
    )
    return GmailThreadData(thread_id="t1", subject="s", messages=[msg])


COLLEAGUES = _thread("Pat Example <pat.example@itv.com>", "Robin Sample <robin.sample@itv.com>", "ext@gmail.com")
EXTERNALS = _thread("a@gmail.com", "b@outlook.com")


@pytest.fixture(autouse=True)
def _clean_state():
    from adapters.people import clear_profile_cache

    clear_profile_cache()
    yield
    clear_profile_cache()


def _place(thread: GmailThreadData, client):
    import adapters.people as P
    import tools.fetch.gmail_participants as GP

    with patch("adapters.people.get_sync_client", return_value=client), \
         patch.object(P, "current_user_email", return_value=ME), \
         patch.object(GP, "current_user_email", return_value=ME):
        return GP.participants_with_placement(thread)


class TestFetchPlacementNamesARefusal:
    def test_a_scope_refusal_names_the_missing_scope_and_the_fix(self) -> None:
        """The card's red: today this returns ({}, …) — a refusal read as absence."""
        _, extras = _place(COLLEAGUES, _directory(refuse_with=SCOPE_BODY))
        cue = extras.get("people_unavailable")
        assert cue, f"a refused directory read produced no cue at all: {extras}"
        assert "admin.directory.user.readonly" in cue
        assert "setup_oauth" in cue and "force=True" in cue
        assert "lacks" in cue, "a measured scope refusal should say so plainly, not hedge"
        assert "2 own-domain" in cue, "pat + robin went unplaced; ext@gmail.com and the user are not counted"
        assert "people" not in extras
        assert "people_note" not in extras, (
            "people_note calls an absence honest — false when the directory refused"
        )

    def test_another_403_still_names_the_scope_and_the_fix_and_the_other_cause(self) -> None:
        _, extras = _place(COLLEAGUES, _directory(refuse_with=NOT_AUTHORIZED_BODY))
        cue = extras["people_unavailable"]
        assert "admin.directory.user.readonly" in cue and "force=True" in cue
        assert "Contact sharing" in cue, "without the scope text the cause is not proven — name both"
        assert "lacks" not in cue

    def test_an_external_only_thread_gets_no_cue_and_no_lookup_even_when_refused(self) -> None:
        calls: list = []
        refusing = _directory(refuse_with=SCOPE_BODY, calls=calls)
        _place(COLLEAGUES, refusing)  # the refusal is now standing
        _, extras = _place(EXTERNALS, refusing)
        assert extras == {}, "nothing own-domain to place, so nothing was skipped"
        assert all(a.endswith("@itv.com") for a in calls)

    def test_with_the_scope_behaviour_is_unchanged(self) -> None:
        known = {"pat.example@itv.com": ("Pat Example", None)}  # robin: 404, opted out
        _, extras = _place(COLLEAGUES, _directory(known=known))
        assert set(extras["people"]) == {"pat.example@itv.com"}
        assert extras["people_note"].startswith("1 of 3")
        assert "not a failed lookup" in extras["people_note"]
        assert "people_unavailable" not in extras


class TestARefusalIsNeverCachedAsAbsence:
    def test_a_standing_refusal_skips_lookups_but_keeps_cueing(self) -> None:
        calls: list = []
        refusing = _directory(refuse_with=SCOPE_BODY, calls=calls)
        _place(COLLEAGUES, refusing)
        asked = len(calls)
        # Lookups run in parallel: a sibling that starts after the first 403
        # lands sees the standing refusal and skips, so 1 or 2 — never more.
        assert 1 <= asked <= 2, f"at most one ask per colleague, got {calls}"
        _, extras = _place(COLLEAGUES, refusing)
        assert len(calls) == asked, "a standing refusal must not re-ask per address"
        assert "people_unavailable" in extras

    def test_after_the_ttl_the_directory_is_asked_again_and_a_success_clears_it(self) -> None:
        import adapters.directory_refusal as DR

        _place(COLLEAGUES, _directory(refuse_with=SCOPE_BODY))
        known = {"pat.example@itv.com": ("Pat Example", None),
                 "robin.sample@itv.com": ("Robin Sample", "pat.example@itv.com")}
        with patch.object(DR, "_TTL_SECONDS", 0.0):
            _, extras = _place(COLLEAGUES, _directory(known=known))
        assert set(extras["people"]) == {"pat.example@itv.com", "robin.sample@itv.com"}, (
            "a refusal was cached as absence — the re-scoped token can never place anyone"
        )
        assert "people_unavailable" not in extras
        assert DR.reason() is None

    def test_clearing_the_profile_cache_clears_the_refusal(self) -> None:
        """The dead-grant reload in http_client calls clear_profile_cache on a new token."""
        import adapters.directory_refusal as DR
        from adapters.people import clear_profile_cache

        _place(COLLEAGUES, _directory(refuse_with=SCOPE_BODY))
        assert DR.reason()
        clear_profile_cache()
        assert DR.reason() is None and not DR.blocking()

    def test_a_404_is_still_cached_as_the_honest_negative(self) -> None:
        calls: list = []
        client = _directory(calls=calls)
        _place(_thread("ghost@itv.com"), client)
        _, extras = _place(_thread("ghost@itv.com"), client)
        assert calls == ["ghost@itv.com"], "a 404 is asked once, then answered from the cache"
        assert "people_unavailable" not in extras


class TestSearchRowsNameARefusal:
    def _search(self, tmp_path, *senders: str, client):
        import adapters.people as P
        from tools.search import do_search

        rows = GmailSearchResults(results=[
            GmailSearchResult(thread_id=f"t{i}", subject="s", snippet="", from_address=s, last_sender=s)
            for i, s in enumerate(senders)
        ])
        with patch("tools.search.search_threads", return_value=rows), \
             patch("adapters.people.get_sync_client", return_value=client), \
             patch.object(P, "current_user_email", return_value=ME):
            return do_search("q", sources=["gmail"], base_path=tmp_path)

    def test_a_refused_directory_puts_one_cue_on_the_search(self, tmp_path) -> None:
        r = self._search(tmp_path, "Pat Example <pat.example@itv.com>", "ext@gmail.com",
                         client=_directory(refuse_with=SCOPE_BODY))
        assert r.errors == []
        cue = r.cues.get("people_unavailable")
        assert cue and "admin.directory.user.readonly" in cue and "force=True" in cue
        assert "people_context" not in r.cues

    def test_an_external_only_search_gets_no_cue(self, tmp_path) -> None:
        refusing = _directory(refuse_with=SCOPE_BODY)
        self._search(tmp_path, "pat.example@itv.com", client=refusing)  # refusal standing
        r = self._search(tmp_path, "ext@gmail.com", client=refusing)
        assert "people_unavailable" not in r.cues

    def test_a_placed_search_with_the_scope_is_unchanged(self, tmp_path) -> None:
        r = self._search(tmp_path, "pat.example@itv.com",
                         client=_directory(known={"pat.example@itv.com": ("Pat Example", None)}))
        assert "people_unavailable" not in r.cues
        assert "honest absence" in r.cues["people_context"]


def _flaky(known: dict, calls: list, down: dict):
    """Answer from `known`, but die in transport while down["now"] is true — the
    'Server disconnected' the essayeur saw live in one of six cold fetches. A
    switch, not a call count: parallel lookups may be skipped during the pause."""
    good = _directory(known=known)

    def get_json(url, params=None):
        calls.append(url.rsplit("/", 1)[-1])
        if down["now"]:
            raise httpx.RemoteProtocolError("Server disconnected")
        return good.get_json(url, params)

    client = MagicMock()
    client.get_json = get_json
    return client


KNOWN = {"pat.example@itv.com": ("Pat Example", None),
         "robin.sample@itv.com": ("Robin Sample", "pat.example@itv.com")}


class TestAFailedLookupIsNotAbsenceEither:
    """essayeur 28 Sep, finding 1: with the scope present, a dropped connection
    was cached as None — the card's exact symptom by another door."""

    def test_a_transport_failure_cues_pauses_then_is_asked_again(self) -> None:
        import adapters.directory_refusal as DR

        calls: list = []
        down = {"now": True}
        client = _flaky(KNOWN, calls, down)
        _, extras = _place(COLLEAGUES, client)
        cue = extras.get("people_unavailable")
        assert cue and "incomplete" in cue and "Server disconnected" in cue, extras
        assert "people_note" not in extras
        assert "setup_oauth" not in cue, "a dropped connection is not a scope problem"
        asked = len(calls)
        _, extras = _place(COLLEAGUES, client)  # inside the pause: skipped, still cued
        assert len(calls) == asked and "people_unavailable" in extras, (
            "a hanging directory would add the request timeout to every call"
        )
        down["now"] = False
        with patch.object(DR, "_FAILURE_PAUSE_SECONDS", 0.0):
            _, extras = _place(COLLEAGUES, client)
        assert set(extras["people"]) == set(KNOWN), "a failure was cached as absence"
        assert "people_unavailable" not in extras

    def test_one_pass_per_call_a_failed_lookup_is_not_re_fired(self) -> None:
        calls: list = []
        _place(_thread("pat.example@itv.com"), _flaky(KNOWN, calls, {"now": True}))
        assert calls == ["pat.example@itv.com"], f"re-asked within one call: {calls}"


class TestMixedCasesWithholdTheHonestAbsenceLine:
    """essayeur finding 2: with a profile cached BEFORE the refusal, people is
    non-empty — the guards that drop people_note / people_context's tail had no test."""

    def _warm_then_refuse(self):
        import adapters.people as P

        with patch("adapters.people.get_sync_client", return_value=_directory(known=KNOWN)), \
             patch.object(P, "current_user_email", return_value=ME):
            P.profiles_for(["pat.example@itv.com"])
        return _directory(refuse_with=SCOPE_BODY)

    def test_fetch(self) -> None:
        _, extras = _place(COLLEAGUES, self._warm_then_refuse())
        assert set(extras["people"]) == {"pat.example@itv.com"}
        assert "1 own-domain" in extras["people_unavailable"]
        assert "people_note" not in extras

    def test_search(self, tmp_path) -> None:
        refusing = self._warm_then_refuse()
        r = TestSearchRowsNameARefusal()._search(
            tmp_path, "pat.example@itv.com", "robin.sample@itv.com", client=refusing)
        assert "people_unavailable" in r.cues
        assert "people_context" in r.cues and "honest absence" not in r.cues["people_context"]


class TestTheGapSurvivesARace:
    def test_a_refusal_cleared_mid_flight_still_leaves_a_cue(self) -> None:
        """essayeur finding 3: a sibling's success could clear the refusal between
        profiles_for and the cue; the gap is read from the cache, so a cue remains."""
        import adapters.directory_refusal as DR
        import adapters.people as P

        _place(COLLEAGUES, _directory(refuse_with=SCOPE_BODY))
        DR.clear()
        with patch.object(P, "current_user_email", return_value=ME):
            cue = P.placement_gap(["pat.example@itv.com", "robin.sample@itv.com"])
        assert cue and "UNPLACED" in cue


class TestALibraryIdentitySwitchForgetsTheRefusal:
    def test_mise_constructor_clears_it(self, tmp_path, monkeypatch) -> None:
        """essayeur finding 4: Mise(token_path=…) cleared the HTTP clients only."""
        import adapters.directory_refusal as DR
        from mise_en_space import Mise

        monkeypatch.delenv("MISE_TOKEN_PATH", raising=False)
        monkeypatch.delenv("MISE_CREDENTIALS", raising=False)
        _place(COLLEAGUES, _directory(refuse_with=SCOPE_BODY))
        assert DR.blocking()
        Mise(token_path=tmp_path / "other.json")
        assert DR.reason() is None and not DR.blocking()
        from token_store import configure_identity
        configure_identity()
