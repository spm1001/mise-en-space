"""Directory (people) adapter and search-source tests — mise-mahiho.

The load-bearing one is TestDomainPublicIsAlwaysSent: this capability is
non-admin ONLY because every request carries viewType=domain_public. Drop it
and the call 403s as an admin call, which is a security-shaped regression that
no functional test would catch.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from adapters.people import (
    _DOMAIN_PUBLIC,
    _parse_person,
    expand_profile,
    get_direct_reports,
    get_person,
    search_people,
)
from models import DirectoryPerson, ErrorKind, MiseError

# A real Admin SDK domain_public response, trimmed — shape taken from the live
# 2026-08-10 probe against ITV's tenant.
RAW_USER = {
    "kind": "admin#directory#user",
    "id": "116349052227889351829",
    "primaryEmail": "richard.pearce1@itv.com",
    "name": {"givenName": "Richard", "familyName": "Pearce", "fullName": "Richard Pearce"},
    "relations": [{"value": "Samir.Ahmad@itv.com", "type": "manager"}],
    "organizations": [
        {
            "name": "ITV Services Limited",
            "title": "Head of Strategy",
            "customType": "work",
            "department": "Strategy, Policy & Regulation",
            "location": "ITV - White City",
        }
    ],
}


def _client(get_json):
    c = MagicMock()
    c.get_json = get_json
    return c


class TestParsePerson:
    def test_pulls_the_fields_that_answer_who_is_this(self) -> None:
        p = _parse_person(RAW_USER)
        assert p.email == "richard.pearce1@itv.com"
        assert p.full_name == "Richard Pearce"
        assert p.title == "Head of Strategy"
        assert p.department == "Strategy, Policy & Regulation"
        assert p.location == "ITV - White City"
        assert p.manager_email == "Samir.Ahmad@itv.com"

    def test_survives_a_profile_with_nothing_but_an_address(self) -> None:
        p = _parse_person({"primaryEmail": "x@itv.com"})
        assert p.email == "x@itv.com"
        assert p.title is None and p.manager_email is None

    def test_ignores_non_manager_relations(self) -> None:
        p = _parse_person({**RAW_USER, "relations": [{"value": "a@itv.com", "type": "assistant"}]})
        assert p.manager_email is None

    def test_to_dict_omits_absent_fields_rather_than_nulling_them(self) -> None:
        d = _parse_person({"primaryEmail": "x@itv.com", "name": {"fullName": "X"}}).to_dict()
        assert d == {"email": "x@itv.com", "name": "X"}


class TestDomainPublicIsAlwaysSent:
    """The whole non-admin story rests on this parameter. Pin it per call site."""

    def test_constant_is_what_we_think_it_is(self) -> None:
        assert _DOMAIN_PUBLIC == {"viewType": "domain_public"}

    @pytest.mark.parametrize(
        "call",
        [
            lambda: get_person("a@itv.com"),
            lambda: search_people("Neil Charles"),
            lambda: get_direct_reports("a@itv.com"),
        ],
        ids=["get_person", "search_people", "get_direct_reports"],
    )
    def test_every_call_site_sends_domain_public(self, call) -> None:
        seen = []

        def get_json(url, params=None, **kw):
            seen.append(params or {})
            return {"users": [RAW_USER], **RAW_USER}

        with patch("adapters.people.get_sync_client", return_value=_client(get_json)):
            call()

        assert seen, "no request was made"
        for params in seen:
            assert params.get("viewType") == "domain_public", (
                "A Directory API call went out WITHOUT viewType=domain_public. "
                "That is the admin-only view: it will 403 for a non-admin, and it "
                "is not the capability this scope was granted for (mise-mahiho)."
            )

    def test_admin_view_never_appears_in_executable_code(self) -> None:
        """Prose may discuss admin_view; code may not name it.

        ast ignores comments outright, so stripping docstrings leaves exactly
        the string literals that could reach a request.
        """
        import ast
        from pathlib import Path

        tree = ast.parse((Path(__file__).parents[2] / "adapters" / "people.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant
                ) and isinstance(body[0].value.value, str):
                    body.pop(0)  # drop the docstring

        offenders = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and "admin_view" in n.value
        ]
        assert not offenders, (
            f"adapters/people.py names admin_view in executable code: {offenders}. "
            "That is the administrator-only view — this module is non-admin by "
            "construction and must only ever send domain_public (mise-mahiho)."
        )


class TestGetPerson:
    def test_returns_a_parsed_profile(self) -> None:
        with patch(
            "adapters.people.get_sync_client",
            return_value=_client(lambda *a, **k: RAW_USER),
        ):
            assert get_person("richard.pearce1@itv.com").title == "Head of Strategy"

    def test_404_teaches_the_three_real_causes(self) -> None:
        def boom(*a, **k):
            raise httpx.HTTPStatusError(
                "nope", request=MagicMock(), response=httpx.Response(404, text="{}")
            )

        with patch("adapters.people.get_sync_client", return_value=_client(boom)):
            with pytest.raises(MiseError) as ei:
                get_person("ghost@itv.com")
        assert ei.value.kind == ErrorKind.NOT_FOUND
        assert "opted out" in ei.value.message

    def test_403_names_the_re_auth_remedy_not_just_the_wall(self) -> None:
        """A stale token is the commonest cause — the error must name setup_oauth."""

        def boom(*a, **k):
            raise httpx.HTTPStatusError(
                "nope", request=MagicMock(), response=httpx.Response(403, text="{}")
            )

        with patch("adapters.people.get_sync_client", return_value=_client(boom)):
            with pytest.raises(MiseError) as ei:
                get_person("a@itv.com")
        assert ei.value.kind == ErrorKind.PERMISSION_DENIED
        assert "setup_oauth" in ei.value.message
        assert "Contact sharing" in ei.value.message


class TestSearchPeople:
    def test_page_token_means_truncated_not_complete(self) -> None:
        payload = {"users": [RAW_USER], "nextPageToken": "more"}
        with patch(
            "adapters.people.get_sync_client",
            return_value=_client(lambda *a, **k: payload),
        ):
            r = search_people("Pearce")
        assert r.truncated is True and len(r.people) == 1

    def test_no_users_key_is_an_empty_result_not_a_crash(self) -> None:
        with patch(
            "adapters.people.get_sync_client", return_value=_client(lambda *a, **k: {})
        ):
            r = search_people("nobody")
        assert r.people == [] and r.truncated is False

    def test_query_rides_through_untouched_so_field_scoping_works(self) -> None:
        seen = {}

        def get_json(url, params=None, **kw):
            seen.update(params or {})
            return {}

        with patch("adapters.people.get_sync_client", return_value=_client(get_json)):
            search_people("orgDepartment:Commercial")
        assert seen["query"] == "orgDepartment:Commercial"


class TestBestEffortEnrichment:
    """Decoration on an already-successful search must never fail it."""

    def test_direct_reports_swallows_failure(self) -> None:
        def boom(*a, **k):
            raise RuntimeError("directory down")

        with patch("adapters.people.get_sync_client", return_value=_client(boom)):
            assert get_direct_reports("a@itv.com") == []

    def test_expand_profile_keeps_the_manager_address_when_the_name_lookup_dies(
        self,
    ) -> None:
        person = _parse_person(RAW_USER)
        with patch("adapters.people.get_person", side_effect=RuntimeError("boom")), patch(
            "adapters.people.get_direct_reports", return_value=[]
        ):
            ctx = expand_profile(person)
        assert ctx["manager"] == {"email": "Samir.Ahmad@itv.com"}

    def test_expand_profile_resolves_names_when_it_can(self) -> None:
        mgr = DirectoryPerson(
            email="Samir.Ahmad@itv.com", full_name="Samir Ahmad", title="Director"
        )
        report = DirectoryPerson(
            email="hethvi.gada@itv.com", full_name="Hethvi Gada", title="Strategy Manager"
        )
        with patch("adapters.people.get_person", return_value=mgr), patch(
            "adapters.people.get_direct_reports", return_value=[report]
        ):
            ctx = expand_profile(_parse_person(RAW_USER))
        assert ctx["manager"]["name"] == "Samir Ahmad"
        assert ctx["direct_reports"][0]["name"] == "Hethvi Gada"

    def test_no_manager_means_no_manager_key(self) -> None:
        person = DirectoryPerson(email="ceo@itv.com", full_name="Boss")
        with patch("adapters.people.get_direct_reports", return_value=[]):
            assert expand_profile(person) == {}


class TestPeopleSourceThroughDoSearch:
    """Exercise the source through do_search, not just the adapter.

    Regression guard for a name-shadowing bug that every adapter-level test
    was structurally blind to: do_search set a local `search_people = "people"
    in sources`, which shadowed the imported adapter function of the same name,
    so the source raised "'bool' object is not callable" on every real call.
    The unit tests all called the adapter directly and never crossed that
    scope; only a live end-to-end run surfaced it. Any test here must therefore
    enter through do_search.
    """

    def _search(self, tmp_path, people, **kw):
        from models import PeopleSearchResults
        from tools.search import do_search

        with patch(
            "tools.search.search_people",
            return_value=PeopleSearchResults(people=people),
        ), patch("tools.search.expand_profile", return_value=kw.pop("context", {})):
            return do_search(
                kw.pop("query", "Richard Pearce"),
                sources=["people"],
                base_path=tmp_path,
                **kw,
            )

    def test_a_people_search_actually_runs(self, tmp_path) -> None:
        r = self._search(tmp_path, [_parse_person(RAW_USER)])
        assert r.errors == [], f"people source errored: {r.errors}"
        assert len(r.people_results) == 1
        assert r.people_results[0]["title"] == "Head of Strategy"

    def test_a_single_hit_is_expanded(self, tmp_path) -> None:
        r = self._search(
            tmp_path,
            [_parse_person(RAW_USER)],
            context={"manager": {"email": "s@itv.com", "name": "Samir Ahmad"}},
        )
        assert r.people_results[0]["manager"]["name"] == "Samir Ahmad"

    def test_several_hits_are_NOT_expanded(self, tmp_path) -> None:
        """Two extra calls per result is the cost this gate exists to avoid."""
        from models import PeopleSearchResults
        from tools.search import do_search

        expand = MagicMock(return_value={"manager": {"name": "nope"}})
        with patch(
            "tools.search.search_people",
            return_value=PeopleSearchResults(
                people=[_parse_person(RAW_USER), _parse_person(RAW_USER)]
            ),
        ), patch("tools.search.expand_profile", expand):
            do_search("Pearce", sources=["people"], base_path=tmp_path)
        expand.assert_not_called()

    def test_zero_hits_teach_the_syntax_rather_than_reading_as_absence(
        self, tmp_path
    ) -> None:
        r = self._search(tmp_path, [], query="Data Scientist")
        note = r.cues.get("people_note", "")
        assert "NAME and EMAIL only" in note
        assert "orgTitle='Head of Strategy'" in note, (
            "the zero-hit cue must show the equals-and-single-quotes form — the "
            "colon form on a multi-word value returns zero silently"
        )

    def test_the_manager_field_is_always_qualified_when_results_exist(
        self, tmp_path
    ) -> None:
        """`manager` is an account field, not an HR record. Never ship it bare."""
        r = self._search(tmp_path, [_parse_person(RAW_USER)])
        assert "account" in r.cues.get("people_source", "")


class TestPlacingSenders:
    """mise-fajabe: an unfamiliar name arrives already placed.

    The whole design rests on one empirical claim — that inbox senders repeat,
    so a process-lifetime cache turns "a lookup per row" into "a lookup per
    colleague, once". test_repeat_search_costs_nothing is that claim; if it
    ever goes red the feature is quietly making an API call per result and the
    reason for enriching at search rather than fetch has evaporated.
    """

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        from adapters.people import clear_profile_cache

        clear_profile_cache()
        yield
        clear_profile_cache()

    def _counting_get_person(self, calls: list):
        def fake(address):
            calls.append(address)
            if not address.endswith("@itv.com"):
                raise MiseError(ErrorKind.NOT_FOUND, "nope")
            return DirectoryPerson(
                email=address,
                full_name=address.split("@")[0].replace(".", " ").title(),
                title="Insight Manager",
                department="Commercial",
                manager_email="boss@itv.com",
            )

        return fake

    def test_address_of_strips_the_display_name(self) -> None:
        from adapters.people import address_of

        assert address_of("Meghan Baddely <Meghan.Baddely@itv.com>") == "meghan.baddely@itv.com"
        assert address_of("plain@itv.com") == "plain@itv.com"
        assert address_of(None) is None
        assert address_of("") is None

    def test_only_own_domain_addresses_are_looked_up(self) -> None:
        from adapters import people as P

        calls: list = []
        with patch.object(P, "get_person", self._counting_get_person(calls)), patch.object(
            P, "current_user_email", return_value="sameer.modha@itv.com"
        ):
            got = P.profiles_for(["a@itv.com", "outsider@gmail.com", "b@itv.com"])

        assert set(got) == {"a@itv.com", "b@itv.com"}
        assert "outsider@gmail.com" not in calls, (
            "an external address must not be sent to the directory at all"
        )

    def test_duplicates_collapse_to_one_lookup(self) -> None:
        from adapters import people as P

        calls: list = []
        with patch.object(P, "get_person", self._counting_get_person(calls)), patch.object(
            P, "current_user_email", return_value="s@itv.com"
        ):
            P.profiles_for(["a@itv.com"] * 8 + ["b@itv.com"] * 4)
        assert sorted(calls) == ["a@itv.com", "b@itv.com"]

    def test_repeat_search_costs_nothing(self) -> None:
        """The claim the whole search-time design rests on."""
        from adapters import people as P

        calls: list = []
        with patch.object(P, "get_person", self._counting_get_person(calls)), patch.object(
            P, "current_user_email", return_value="s@itv.com"
        ):
            P.profiles_for(["a@itv.com", "b@itv.com"])
            first = len(calls)
            P.profiles_for(["a@itv.com", "b@itv.com", "a@itv.com"])

        assert first == 2
        assert len(calls) == 2, (
            f"second search made {len(calls) - first} extra call(s) — the cache is "
            "not holding, and enriching at search time is no longer cheap"
        )

    def test_an_unresolvable_address_is_not_retried(self) -> None:
        """A negative must cache too, or every search re-asks about strangers."""
        from adapters import people as P

        calls: list = []
        # Own-domain but absent from the directory (opted out, or departed).
        def fake(address):
            calls.append(address)
            raise MiseError(ErrorKind.NOT_FOUND, "gone")

        with patch.object(P, "get_person", fake), patch.object(
            P, "current_user_email", return_value="s@itv.com"
        ):
            assert P.profiles_for(["ghost@itv.com"]) == {}
            assert P.profiles_for(["ghost@itv.com"]) == {}
        assert len(calls) == 1

    def test_no_resolvable_identity_means_no_lookups_at_all(self) -> None:
        from adapters import people as P

        calls: list = []
        with patch.object(P, "get_person", self._counting_get_person(calls)), patch.object(
            P, "current_user_email", return_value=None
        ):
            assert P.profiles_for(["a@itv.com"]) == {}
        assert calls == []

    def test_attach_profiles_annotates_rows_and_counts_people(self) -> None:
        from adapters import people as P

        rows = [
            {"from": "A <a@itv.com>", "last_sender": "B <b@itv.com>"},
            {"from": "A <a@itv.com>", "last_sender": "A <a@itv.com>"},
            {"from": "x@gmail.com", "last_sender": "x@gmail.com"},
        ]
        with patch.object(P, "get_person", self._counting_get_person([])), patch.object(
            P, "current_user_email", return_value="s@itv.com"
        ):
            placed = P.attach_profiles(rows)

        assert placed == 2
        assert set(rows[0]["people"]) == {"a@itv.com", "b@itv.com"}
        assert set(rows[1]["people"]) == {"a@itv.com"}
        assert "people" not in rows[2], (
            "an external-only row must carry no people key — an absent entry is "
            "an honest absence, not a failed lookup"
        )

    def test_a_directory_outage_leaves_rows_untouched(self) -> None:
        from adapters import people as P

        rows = [{"from": "a@itv.com", "last_sender": "a@itv.com"}]
        with patch.object(P, "get_person", side_effect=RuntimeError("down")), patch.object(
            P, "current_user_email", return_value="s@itv.com"
        ):
            assert P.attach_profiles(rows) == 0
        assert "people" not in rows[0]


class TestRelationBetween:
    """Reporting structure is arithmetic on data already in hand — no calls."""

    BOSS = {"email": "kate@itv.com", "name": "Kate Waters"}
    REPORT = {"email": "sameer@itv.com", "name": "Sameer Modha", "manager": "kate@itv.com"}
    PEER = {"email": "rupert@itv.com", "name": "Rupert Coghlan", "manager": "kate@itv.com"}

    def test_manager_is_named_as_such_in_both_orders(self) -> None:
        from adapters.people import relation_between

        assert relation_between(self.REPORT, self.BOSS) == "Kate Waters is Sameer Modha's manager"
        assert relation_between(self.BOSS, self.REPORT) == "Kate Waters is Sameer Modha's manager"

    def test_shared_manager_reads_as_same_team(self) -> None:
        from adapters.people import relation_between

        assert relation_between(self.REPORT, self.PEER) == (
            "Sameer Modha and Rupert Coghlan report to the same manager"
        )

    def test_unrelated_people_get_nothing_rather_than_a_guess(self) -> None:
        from adapters.people import relation_between

        assert relation_between(self.BOSS, {"email": "z@itv.com", "name": "Z"}) is None


class TestSenderRender:
    """The render is the thing we iterate — pin its shape, not its richness."""

    def test_one_line_places_the_LAST_sender_not_the_originator(self) -> None:
        from models import _describe_sender

        row = {
            "from": "Old Starter <old@itv.com>",
            "last_sender": "Meghan Baddely <meghan@itv.com>",
            "people": {
                "old@itv.com": {"name": "Old Starter", "title": "Analyst"},
                "meghan@itv.com": {
                    "name": "Meghan Baddely",
                    "title": "Insight Manager",
                    "department": "Commercial",
                },
            },
        }
        assert _describe_sender(row) == "Meghan Baddely — Insight Manager"

    def test_falls_back_to_the_originator_when_the_last_sender_is_external(self) -> None:
        from models import _describe_sender

        row = {
            "from": "Meghan Baddely <meghan@itv.com>",
            "last_sender": "someone@outside.com",
            "people": {"meghan@itv.com": {"name": "Meghan Baddely", "title": "Insight Manager"}},
        }
        assert _describe_sender(row) == "Meghan Baddely — Insight Manager"

    def test_no_people_means_no_line_rather_than_an_empty_one(self) -> None:
        from models import _describe_sender

        assert _describe_sender({"from": "x@y.com"}) is None
        assert _describe_sender({"from": "x@y.com", "people": {}}) is None


class TestRelationIsWiredNotJustBuilt:
    """relation_between used to be tested and never called. Pin the call site."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        from adapters.people import clear_profile_cache

        clear_profile_cache()
        yield
        clear_profile_cache()

    def test_a_row_with_a_boss_and_their_report_says_so(self) -> None:
        from adapters import people as P

        profiles = {
            "kate@itv.com": DirectoryPerson(email="kate@itv.com", full_name="Kate Waters",
                                            title="Director"),
            "sameer@itv.com": DirectoryPerson(email="sameer@itv.com", full_name="Sameer Modha",
                                              title="Lead", manager_email="kate@itv.com"),
        }
        rows = [{"from": "Kate <kate@itv.com>", "last_sender": "Sameer <sameer@itv.com>"}]
        with patch.object(P, "get_person", lambda a: profiles[a]), patch.object(
            P, "current_user_email", return_value="someone.else@itv.com"
        ):
            P.attach_profiles(rows)
        assert rows[0]["people_relation"] == "Kate Waters is Sameer Modha's manager"

    def test_one_placed_person_gets_no_relation_key(self) -> None:
        from adapters import people as P

        person = DirectoryPerson(email="a@itv.com", full_name="A", title="T")
        rows = [{"from": "a@itv.com", "last_sender": "a@itv.com"}]
        with patch.object(P, "get_person", lambda a: person), patch.object(
            P, "current_user_email", return_value="me@itv.com"
        ):
            P.attach_profiles(rows)
        assert "people_relation" not in rows[0]


class TestGroupAddressesAreRoutineNotExceptional:
    """mit-group@itv.com is in a large share of real threads (measured 2026-08-10)."""

    def test_400_userKey_reads_as_a_group_not_a_mystery(self) -> None:
        from adapters import people as P

        def boom(*a, **k):
            raise httpx.HTTPStatusError(
                "bad", request=MagicMock(),
                response=httpx.Response(400, text='{"error":{"message":"Type not supported: userKey"}}'),
            )

        with patch("adapters.people.get_sync_client", return_value=_client(boom)):
            with pytest.raises(MiseError) as ei:
                P.get_person("mit-group@itv.com")
        assert ei.value.kind == ErrorKind.NOT_FOUND
        assert "group or alias" in ei.value.message
        assert ei.value.details.get("is_group") is True

    def test_an_unrelated_400_is_not_mislabelled_as_a_group(self) -> None:
        from adapters import people as P

        def boom(*a, **k):
            raise httpx.HTTPStatusError(
                "bad", request=MagicMock(),
                response=httpx.Response(400, text='{"error":{"message":"Invalid Input"}}'),
            )

        with patch("adapters.people.get_sync_client", return_value=_client(boom)):
            with pytest.raises(MiseError) as ei:
                P.get_person("whatever@itv.com")
        assert ei.value.details.get("is_group") is None


class TestOrgMap:
    """org_map.json is hand-edited DATA — a typo must fail here, not silently.

    The file has no code in it by design, which means nothing else validates
    it. These tests are the whole safety net for a file a human will edit
    between releases.
    """

    def test_the_shipped_map_is_valid_json_with_the_expected_shape(self) -> None:
        import json
        from pathlib import Path

        raw = json.loads((Path(__file__).parents[2] / "org_map.json").read_text())
        itv = raw["domains"]["itv.com"]
        assert itv["departments"], "no exact department rows"
        for pair in itv["patterns"]:
            assert len(pair) == 2 and all(isinstance(x, str) for x in pair), (
                f"pattern rows must be [match, division] string pairs — got {pair!r}"
            )
            assert pair[0] == pair[0].lower(), (
                f"pattern {pair[0]!r} must be lowercase — matching lowercases the "
                "department, so an uppercase pattern can never fire"
            )

    def test_exact_match_wins_over_pattern(self) -> None:
        from adapters.people import division_for

        # 'Strategy, Policy & Regulation' would hit no commercial pattern, but
        # 'BE Studio' would hit the 'studios' pattern with a worse answer.
        assert division_for("x@itv.com", "BE Studio") == "Studios (Bright Entertainment)"

    def test_the_corporate_centre_is_NOT_commercial(self) -> None:
        """The caution the whole file exists to encode.

        'Strategy, Policy & Regulation' is the corporate centre. A keyword rule
        files it under Commercial and misplaces exactly the senior people it
        matters most to place correctly (Richard Pearce sits here).
        """
        from adapters.people import division_for

        got = division_for("x@itv.com", "Strategy, Policy & Regulation")
        assert got == "Corporate centre"
        assert "Commercial" not in (got or "")

    def test_an_unmapped_department_yields_nothing_rather_than_a_guess(self) -> None:
        from adapters.people import division_for

        assert division_for("x@itv.com", "Some Team Invented Yesterday") is None
        assert division_for("x@itv.com", None) is None
        assert division_for("", "Commercial Analysis") is None

    def test_another_domain_finds_no_entry_so_the_file_is_inert_there(self) -> None:
        """mise-home is built from this source and must not inherit ITV's map."""
        from adapters.people import division_for

        assert division_for("x@planetmodha.com", "Client Strategy & Commercial Marketing") is None

    def test_a_missing_or_broken_map_costs_divisions_never_an_exception(self) -> None:
        from pathlib import Path

        from adapters import people as P

        original = P._org_map
        try:
            P._org_map = None
            with patch.object(P, "_ORG_MAP_FILE", Path("/nonexistent/org_map.json")):
                assert division_for_safe(P) is None
        finally:
            P._org_map = original

    def test_render_appends_the_division_when_there_is_one(self) -> None:
        from models import _describe_sender

        row = {
            "last_sender": "M <m@itv.com>",
            "people": {"m@itv.com": {"name": "M", "title": "Insight Manager",
                                     "division": "Commercial"}},
        }
        assert _describe_sender(row) == "M — Insight Manager, Commercial"

    def test_render_omits_the_division_when_unmapped(self) -> None:
        from models import _describe_sender

        row = {
            "last_sender": "M <m@itv.com>",
            "people": {"m@itv.com": {"name": "M", "title": "Insight Manager"}},
        }
        assert _describe_sender(row) == "M — Insight Manager"


def division_for_safe(module):
    return module.division_for("x@itv.com", "Client Strategy & Commercial Marketing")
