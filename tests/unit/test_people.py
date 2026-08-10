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
