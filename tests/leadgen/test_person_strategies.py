"""Each person-extraction strategy against a realistic fixture."""

from __future__ import annotations

from leadgen.person.paths import candidate_paths
from leadgen.person.strategies import impressum, jsonld_person, team

IMPRESSUM_DE = """
<html><body><main>
<h1>Impressum</h1>
<p>Acme Software GmbH<br/>Maximilianstrasse 12<br/>80539 M&uuml;nchen</p>
<p>Vertretungsberechtigter Gesch&auml;ftsf&uuml;hrer: Dr. Anna Schmidt</p>
<p>Technischer Leiter: Peter Wolf</p>
<p>E-Mail: anna.schmidt@acme.de</p>
<p>Registergericht: Amtsgericht M&uuml;nchen HRB 123456</p>
</main></body></html>
"""

IMPRESSUM_FR = """
<html><body><main>
<h1>Mentions l&eacute;gales</h1>
<p>Acme SARL, 12 rue de Rivoli, 75001 Paris</p>
<p>Directeur de la publication : Émilie Durand</p>
</main></body></html>
"""

JSONLD_PEOPLE = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization","name":"Acme",
 "employee":[
   {"@type":"Person","name":"Anna Schmidt","jobTitle":"CTO","email":"anna.schmidt@acme.de",
    "sameAs":["https://www.linkedin.com/in/annaschmidt"]},
   {"@type":"Person","name":"Peter Wolf","jobTitle":"Head of People"}
 ]}
</script></head><body></body></html>
"""

TEAM_PAGE = """
<html><body><main>
<h1>Our team</h1>
<div class="member"><h3>Anna Schmidt</h3><p class="role">Chief Technology Officer</p>
  <a href="mailto:anna.schmidt@acme.de">Email</a>
  <a href="https://www.linkedin.com/in/annaschmidt">LinkedIn</a></div>
<div class="member"><h3>Peter Wolf</h3><p class="role">Head of HR</p></div>
<div class="member"><h3>Sales Team</h3><p class="role">Sales</p></div>
</main></body></html>
"""


def test_impressum_finds_the_managing_director_and_role():
    hits = impressum.extract(IMPRESSUM_DE, "https://acme.de/impressum")
    by_name = {h.name: h for h in hits}
    assert "Dr. Anna Schmidt" in by_name or "Anna Schmidt" in by_name
    hit = by_name.get("Dr. Anna Schmidt") or by_name["Anna Schmidt"]
    assert "schäftsführer" in hit.role.lower() or "ftsführer" in hit.role.lower()
    assert hit.source_url == "https://acme.de/impressum"


def test_impressum_finds_a_second_labelled_person():
    hits = impressum.extract(IMPRESSUM_DE, "https://acme.de/impressum")
    assert any(h.name == "Peter Wolf" and "Leiter" in h.role for h in hits)


def test_impressum_does_not_return_the_company_as_a_person():
    hits = impressum.extract(IMPRESSUM_DE, "https://acme.de/impressum")
    assert not any("GmbH" in h.name for h in hits)


def test_impressum_handles_the_french_form():
    hits = impressum.extract(IMPRESSUM_FR, "https://acme.fr/mentions-legales")
    assert any("Durand" in h.name for h in hits)


def test_impressum_attributes_a_lone_page_email():
    hits = impressum.extract(IMPRESSUM_DE, "https://acme.de/impressum")
    assert any(h.email == "anna.schmidt@acme.de" for h in hits)


def test_jsonld_person_reads_employee_array():
    hits = jsonld_person.extract(JSONLD_PEOPLE, "https://acme.de/about")
    assert {h.name for h in hits} == {"Anna Schmidt", "Peter Wolf"}
    anna = next(h for h in hits if h.name == "Anna Schmidt")
    assert anna.role == "CTO"
    assert anna.email == "anna.schmidt@acme.de"
    assert anna.linkedin.endswith("/in/annaschmidt")


def test_jsonld_person_survives_a_broken_block():
    hits = jsonld_person.extract(
        '<html><head><script type="application/ld+json">{not json</script></head></html>', "u"
    )
    assert hits == []


def test_team_page_pairs_names_roles_emails_and_linkedin():
    hits = team.extract(TEAM_PAGE, "https://acme.de/team")
    anna = next(h for h in hits if h.name == "Anna Schmidt")
    assert anna.role == "Chief Technology Officer"
    assert anna.email == "anna.schmidt@acme.de"
    assert anna.linkedin.endswith("/in/annaschmidt")


def test_team_page_rejects_a_non_person_card():
    hits = team.extract(TEAM_PAGE, "https://acme.de/team")
    assert not any(h.name == "Sales Team" for h in hits)


def test_every_hit_records_its_strategy_and_source():
    for module, html, url in (
        (impressum, IMPRESSUM_DE, "https://acme.de/impressum"),
        (team, TEAM_PAGE, "https://acme.de/team"),
    ):
        for hit in module.extract(html, url):
            assert hit.strategy
            assert hit.source_url == url


def test_candidate_paths_are_country_aware_and_always_include_english():
    german = candidate_paths("DE")
    assert "/impressum" in german
    assert "/team" in german
    french = candidate_paths("FR")
    assert "/mentions-legales" in french
    assert "/equipe" in french
    unknown = candidate_paths("XX")
    assert "/team" in unknown and "/about" in unknown


def test_candidate_paths_put_localised_first():
    assert candidate_paths("DE")[0] == "/impressum"


def test_candidate_paths_have_no_duplicates():
    paths = candidate_paths("DE")
    assert len(paths) == len(set(paths))


def test_team_parser_rejects_shouting_marketing_headings():
    """Real bug from a live run: all-caps nav copy was extracted as people."""
    html = """
    <html><body><main>
    <div><h3>RUN YOUR BUSINESS</h3><p>SumUp POS</p></div>
    <div><h3>TAKE PAYMENTS</h3><p>Self-service Kiosk</p></div>
    <div><h3>MANAGE FINANCES</h3><p>SumUp Wealth</p></div>
    <div><h3>Anna Schmidt</h3><p>CTO</p></div>
    </main></body></html>
    """
    names = {hit.name for hit in team.extract(html, "https://x.test/about")}
    assert names == {"Anna Schmidt"}


def test_team_parser_rejects_nav_and_marketing_phrases():
    html = """
    <html><body><main>
    <div><h3>About Us</h3><p>Company</p></div>
    <div><h3>Book A Demo</h3><p>Sales</p></div>
    <div><h3>Our Mission</h3><p>Values</p></div>
    <div><h3>Peter Wolf</h3><p>Head of HR</p></div>
    </main></body></html>
    """
    names = {hit.name for hit in team.extract(html, "https://x.test/about")}
    assert names == {"Peter Wolf"}


def test_team_parser_still_accepts_real_names_with_particles_and_initials():
    html = """
    <html><body><main>
    <div><h3>Jan van der Berg</h3><p>CTO</p></div>
    <div><h3>J. P. Mueller</h3><p>Head of People</p></div>
    <div><h3>Émilie Durand</h3><p>Directrice Technique</p></div>
    </main></body></html>
    """
    names = {hit.name for hit in team.extract(html, "https://x.test/team")}
    assert names == {"Jan van der Berg", "J. P. Mueller", "Émilie Durand"}
