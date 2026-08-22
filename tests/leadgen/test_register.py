"""The commercial register source.

Every publication string below is verbatim from a real SOGC/SHAB publication.
The register's grammar is the whole difficulty here -- who is joining versus
leaving, which language puts the surname first, which entries are companies --
so tests that used invented text would prove nothing.
"""

from __future__ import annotations

import pytest

from leadgen.person import register
from leadgen.person.roles import classify_role

# Bedag Informatik AG, SHAB Nr. 179, 17.09.2025. One departure, two arrivals.
BEDAG = (
    "Bedag Informatik AG, in Bern, CHE-108.955.156, Aktiengesellschaft "
    "(SHAB Nr. 179 vom 17.09.2025, Publ. 1006434983). Ausgeschiedene Personen und "
    "erloschene Unterschriften: Bieri, Adrian, von Schangnau, in Grossaffoltern, "
    "Präsident des Verwaltungsrates, mit Kollektivunterschrift zu zweien. "
    "Eingetragene Personen neu oder mutierend: Conrad, Reto, von Davos, in Binningen, "
    "Präsident des Verwaltungsrates, mit Kollektivunterschrift zu zweien "
    "[bisher: Mitglied des Verwaltungsrates, ohne Zeichnungsberechtigung]; "
    "Amstutz, Marcel, von Engelberg, in Hünenberg, Mitglied des Verwaltungsrates, "
    "ohne Zeichnungsberechtigung."
)

# Gruyère Energie S.A., FOSC 28.10.2025. French puts the surname first with no
# comma, which is a different parse from the German form entirely.
GRUYERE = (
    "Gruyère Energie S.A., à Bulle, CHE-104.815.533 (FOSC du 28.10.2025, "
    "p. 0/1006468938). Personne radiée: Gremion Nicolas, procuration collective "
    "à deux. Nouvelles personnes inscrites: Ruffieux Alain, de Crésuz, "
    "à Gruyères, directeur, signature collective à deux; Jaquet Lucien, "
    "de Bas-Intyamon, à Riaz, procuration collective à deux."
)

# ELEKTRO-MATERIAL AG, SHAB Nr. 100, 28.05.2026. The departing director carries an
# academic title inside the given-name field.
ELEKTRO_MATERIAL = (
    "ELEKTRO-MATERIAL AG, in Zürich, CHE-106.847.231, Aktiengesellschaft "
    "(SHAB Nr. 100 vom 28.05.2026, Publ. 1006660424). Ausgeschiedene Personen und "
    "erloschene Unterschriften: Burkhalter, Dr. Jean Philippe, von Lützelflüh, "
    "in Otelfingen, Direktor, mit Kollektivunterschrift zu zweien. "
    "Eingetragene Personen neu oder mutierend: Bauer, Lorenz, von Zürich, in Elgg, "
    "Direktor, mit Kollektivunterschrift zu zweien."
)

# EveryWare AG. The only "person" registered is the audit firm.
AUDITOR_ONLY = (
    "EveryWare AG, in Zürich, CHE-114.279.895, Aktiengesellschaft. "
    "Eingetragene Personen neu oder mutierend: BDO AG (CHE-105.952.747), in Zürich, "
    "Revisionsstelle."
)


def names(message: str) -> list[str]:
    return [name for name, _ in register.people_in_publication(message)]


def test_german_entries_are_returned_given_name_first():
    assert names(BEDAG) == ["Reto Conrad", "Marcel Amstutz"]


def test_french_entries_have_no_comma_and_still_parse():
    assert names(GRUYERE) == ["Alain Ruffieux", "Lucien Jaquet"]


@pytest.mark.parametrize(
    "message, departed",
    [
        (BEDAG, "Adrian Bieri"),
        (GRUYERE, "Nicolas Gremion"),
        (ELEKTRO_MATERIAL, "Jean Philippe Burkhalter"),
    ],
)
def test_people_who_left_are_never_returned(message, departed):
    """The arrival and the departure sit in one paragraph, and handing a
    departed director to the buyer as a current one is the worst row we can
    produce -- it is wrong in a way nothing downstream can detect."""
    assert departed not in names(message)


def test_the_register_function_is_kept_and_classifies_as_executive():
    people = dict(register.people_in_publication(BEDAG))
    assert people["Reto Conrad"] == "Präsident des Verwaltungsrates"
    assert classify_role(people["Reto Conrad"]) == "executive"
    assert classify_role(people["Marcel Amstutz"]) == "executive"


def test_a_signatory_with_no_function_still_yields_the_person():
    """ "mit Kollektivunterschrift zu zweien" says how someone signs, not what
    they do. The name is still real and still worth the row."""
    message = (
        "Bedag Informatik AG, in Bern, CHE-108.955.156, Aktiengesellschaft. "
        "Eingetragene Personen neu oder mutierend: Werthmüller, Louis, "
        "von Rumendingen, in Düdingen, mit Kollektivunterschrift zu zweien."
    )
    assert register.people_in_publication(message) == [("Louis Werthmüller", "")]


def test_a_registered_company_is_not_a_person():
    assert register.people_in_publication(AUDITOR_ONLY) == []


def test_a_publication_that_names_nobody_yields_nothing():
    message = (
        "Victorinox AG, in Schwyz, CHE-105.977.463, Aktiengesellschaft. "
        "Die Gesellschaft hat ihre Adresse geändert."
    )
    assert register.people_in_publication(message) == []


def test_a_foreign_national_has_no_home_town_and_still_parses():
    message = (
        "Selecta Group Management AG, in Cham, CHE-228.069.761, Aktiengesellschaft. "
        "Eingetragene Personen neu oder mutierend: Shantaram, Venkatesh, "
        "britischer Staatsangehöriger, in Goring (GB), Mitglied des Verwaltungsrates, "
        "mit Einzelunterschrift."
    )
    assert register.people_in_publication(message) == [
        ("Venkatesh Shantaram", "Mitglied des Verwaltungsrates")
    ]


@pytest.mark.parametrize(
    "wanted, candidate",
    [
        ("Deloitte", "Deloitte AG"),
        ("Gruyère Energie", "Gruyère Energie S.A."),
        ("Elektro Material", "ELEKTRO-MATERIAL AG"),
        ("CP Automation", "CPAutomation SA"),
        ("Ekkiden", "Ekkiden Group SA"),
    ],
)
def test_legal_form_and_spelling_do_not_break_the_match(wanted, candidate):
    assert register._entity_matches(wanted, candidate)


@pytest.mark.parametrize(
    "wanted, candidate",
    [
        # Every one of these was matched by a fuzzy search during development,
        # and every one is a different company than the one asked for.
        ("Kistler", "Andy Kistler"),
        ("FREITAG", "FREITAGS AG"),
        ("Migros", "Migros Bank AG"),
        ("DPD", "dp-dp GmbH"),
        ("EGS", "EGSL AG"),
        ("Altis", "Altiste SA"),
    ],
)
def test_a_near_miss_is_not_a_match(wanted, candidate):
    """A lead filed under the wrong company is worse than no lead: it is
    confidently wrong and nothing downstream can tell."""
    assert not register._entity_matches(wanted, candidate)


class _Recorder:
    """An http double that fails the test if it is ever called."""

    def __init__(self):
        self.calls: list[str] = []

    def post_json(self, url, payload, headers=None):
        self.calls.append(url)
        return []

    def get_json(self, url, headers=None):
        self.calls.append(url)
        return None


def test_without_credentials_the_register_is_never_contacted(monkeypatch):
    monkeypatch.delenv(register.USER_ENV, raising=False)
    monkeypatch.delenv(register.PASSWORD_ENV, raising=False)
    http = _Recorder()
    assert register.people_from_register("Victorinox AG", http) == []
    assert http.calls == []


def test_no_matching_entity_means_no_uid_lookup(monkeypatch):
    """Stopping at the search saves a request, but the point is correctness:
    with no exact entity there is nothing safe to attribute people to."""
    monkeypatch.setenv(register.USER_ENV, "user")
    monkeypatch.setenv(register.PASSWORD_ENV, "secret")
    http = _Recorder()
    assert register.people_from_register("Kistler", http) == []
    assert len(http.calls) == 1


def test_people_are_attributed_to_the_entity_that_was_matched(monkeypatch):
    monkeypatch.setenv(register.USER_ENV, "user")
    monkeypatch.setenv(register.PASSWORD_ENV, "secret")

    class Http:
        def post_json(self, url, payload, headers=None):
            return [
                {"name": "Andy Kistler", "uid": "CHE111111111"},
                {"name": "Bedag Informatik AG", "uid": "CHE108955156"},
            ]

        def get_json(self, url, headers=None):
            assert "CHE108955156" in url
            return {"ehraid": 42, "sogcPub": [{"sogcDate": "2025-09-17", "message": BEDAG}]}

    hits = register.people_from_register("Bedag Informatik AG", Http())
    assert [hit.name for hit in hits] == ["Reto Conrad", "Marcel Amstutz"]
    assert all(hit.strategy == "register" for hit in hits)
    # The licence asks for attribution and the row needs an audit trail; the
    # entity's own detail page is both.
    assert all(hit.source_url.endswith("/42") for hit in hits)


def test_the_newest_publications_are_read_first(monkeypatch):
    """Each publication only reports a change, so an older one increasingly
    names people who have since left."""
    monkeypatch.setenv(register.USER_ENV, "user")
    monkeypatch.setenv(register.PASSWORD_ENV, "secret")

    class Http:
        def post_json(self, url, payload, headers=None):
            return [{"name": "Bedag Informatik AG", "uid": "CHE108955156"}]

        def get_json(self, url, headers=None):
            return {
                "ehraid": 42,
                "sogcPub": [
                    {"sogcDate": "2019-01-01", "message": ELEKTRO_MATERIAL},
                    {"sogcDate": "2025-09-17", "message": BEDAG},
                ],
            }

    hits = register.people_from_register("Bedag Informatik AG", Http(), max_publications=1)
    assert [hit.name for hit in hits] == ["Reto Conrad", "Marcel Amstutz"]


def test_a_uid_that_covers_branches_contributes_all_of_their_publications(monkeypatch):
    """`company/uid` answers with a list, not an object: one UID can cover a
    head office and its branches, and a branch manager is a real contact.
    Reading the response as a single entity drops every one of them."""
    monkeypatch.setenv(register.USER_ENV, "user")
    monkeypatch.setenv(register.PASSWORD_ENV, "secret")

    class Http:
        def post_json(self, url, payload, headers=None):
            return [{"name": "Bedag Informatik AG", "uid": "CHE108955156"}]

        def get_json(self, url, headers=None):
            return [
                {"ehraid": 42, "sogcPub": [{"sogcDate": "2025-09-17", "message": BEDAG}]},
                {"ehraid": 43, "sogcPub": [{"sogcDate": "2026-05-28", "message": ELEKTRO_MATERIAL}]},
            ]

    hits = register.people_from_register("Bedag Informatik AG", Http())
    assert {hit.name for hit in hits} == {"Reto Conrad", "Marcel Amstutz", "Lorenz Bauer"}


def test_a_search_response_wrapped_in_a_list_key_is_still_read(monkeypatch):
    monkeypatch.setenv(register.USER_ENV, "user")
    monkeypatch.setenv(register.PASSWORD_ENV, "secret")

    class Http:
        def post_json(self, url, payload, headers=None):
            return {"list": [{"name": "Bedag Informatik AG", "uid": "CHE108955156"}]}

        def get_json(self, url, headers=None):
            return [{"ehraid": 42, "sogcPub": [{"sogcDate": "2025-09-17", "message": BEDAG}]}]

    assert [hit.name for hit in register.people_from_register("Bedag Informatik AG", Http())] == [
        "Reto Conrad",
        "Marcel Amstutz",
    ]


def _pipeline_bits(tmp_path):
    from leadgen import pipeline
    from leadgen.assemble import CompanyContext
    from leadgen.checkpoint import Checkpoint

    return pipeline, CompanyContext, Checkpoint(str(tmp_path / "cp.sqlite"))


class _NoHttp:
    def get(self, url, **kwargs):
        return None


def test_the_register_rescues_a_company_whose_site_names_nobody(tmp_path, monkeypatch):
    pipeline, CompanyContext, checkpoint = _pipeline_bits(tmp_path)
    monkeypatch.setattr(pipeline, "resolve_people", lambda *a, **k: [])
    monkeypatch.setattr(
        pipeline,
        "people_from_register",
        lambda name, http, **k: [register.PersonHit(name="Reto Conrad", role="Direktor")],
    )
    try:
        company = CompanyContext(name="Victorinox AG", domain="victorinox.com", country="CH")
        funnel = pipeline.process_companies([company], _NoHttp(), checkpoint, smtp=False, register=True)
        assert funnel["no_person_found"] == 0
        assert funnel["register_companies"] == 1
        assert [lead.person_name for lead in checkpoint.all_leads()] == ["Reto Conrad"]
    finally:
        checkpoint.close()


def test_the_register_is_never_consulted_for_a_company_the_crawl_answered(tmp_path, monkeypatch):
    """It costs two requests to answer a question the site already answered,
    and the site is the better source when it has one."""
    pipeline, CompanyContext, checkpoint = _pipeline_bits(tmp_path)
    monkeypatch.setattr(pipeline, "resolve_people", lambda *a, **k: [register.PersonHit(name="Anna Meier")])
    called: list[str] = []
    monkeypatch.setattr(pipeline, "people_from_register", lambda name, http, **k: called.append(name) or [])
    try:
        company = CompanyContext(name="Bedag Informatik AG", domain="bedag.ch", country="CH")
        pipeline.process_companies([company], _NoHttp(), checkpoint, smtp=False, register=True)
        assert called == []
    finally:
        checkpoint.close()


def test_the_register_is_off_unless_it_is_asked_for(tmp_path, monkeypatch):
    pipeline, CompanyContext, checkpoint = _pipeline_bits(tmp_path)
    monkeypatch.setattr(pipeline, "resolve_people", lambda *a, **k: [])
    called: list[str] = []
    monkeypatch.setattr(pipeline, "people_from_register", lambda name, http, **k: called.append(name) or [])
    try:
        company = CompanyContext(name="Victorinox AG", domain="victorinox.com", country="CH")
        pipeline.process_companies([company], _NoHttp(), checkpoint, smtp=False)
        assert called == []
    finally:
        checkpoint.close()


def test_a_broken_api_response_never_raises(monkeypatch):
    monkeypatch.setenv(register.USER_ENV, "user")
    monkeypatch.setenv(register.PASSWORD_ENV, "secret")

    class Http:
        def post_json(self, url, payload, headers=None):
            raise RuntimeError("connection reset")

        def get_json(self, url, headers=None):
            raise RuntimeError("connection reset")

    assert register.people_from_register("Bedag Informatik AG", Http()) == []
