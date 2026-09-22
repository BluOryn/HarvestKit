"""The heuristics must read the posting, not the whole page.

A job board renders "similar jobs" cards from *other employers* on every detail
page. Scanning the whole document attributed their tech stack, their
"Homeoffice" and their phone numbers to this posting. `_apply_heuristics` has
carried a guard against that for a while — scope to the description when there
is one — but it ran before anything populated the description, so on every page
without JSON-LD (which is most of them) the guard could not fire.
"""

from __future__ import annotations

from job_scraper.extract import extract_job_from_page

# A German detail page with no JSON-LD at all: the posting is a mechanical
# role, and the sidebar advertises somebody else's DevSecOps job.
PAGE_WITHOUT_JSONLD = """
<html><head><title>Konstrukteur (m/w/d) — Beispiel Maschinenbau GmbH</title></head>
<body>
  <main>
    <h1>Konstrukteur (m/w/d)</h1>
    <div class="job-description">
      <p>Ihre Aufgaben: Konstruktion von Baugruppen in SolidWorks, Abstimmung mit
      der Fertigung, Pflege der Stücklisten. Ihr Profil: abgeschlossenes Studium
      im Maschinenbau, mehrere Jahre Berufserfahrung in der Konstruktion,
      sicherer Umgang mit CAD-Systemen. Wir bieten: einen unbefristeten Vertrag,
      30 Tage Urlaub und eine betriebliche Altersvorsorge. Jetzt bewerben und
      Teil unseres Teams in Stuttgart werden. Die Stelle ist in Vollzeit zu
      besetzen und wird nach Tarif vergütet.</p>
    </div>
  </main>
  <aside class="similar-jobs">
    <h2>Ähnliche Stellen</h2>
    <article>
      <h3>Senior DevSecOps Engineer — Andere Firma AG</h3>
      <p>Kubernetes, Terraform, AWS, Docker, Python. Homeoffice möglich.</p>
      <p>Kontakt: Petra Beispiel, +49 30 12345678, p.beispiel@anderefirma.de</p>
    </article>
  </aside>
</body></html>
"""


def test_a_neighbouring_postings_tech_stack_is_not_attributed_to_this_one():
    listing = extract_job_from_page(PAGE_WITHOUT_JSONLD, "https://beispiel.de/stellen/konstrukteur")
    assert listing is not None, "a German detail page must extract at all"
    stack = (listing.tech_stack or "").lower()
    for foreign in ("kubernetes", "terraform", "aws", "docker"):
        assert foreign not in stack, f"{foreign} belongs to the sidebar card, not this posting"


def test_the_sidebars_remote_flag_does_not_make_this_posting_remote():
    listing = extract_job_from_page(PAGE_WITHOUT_JSONLD, "https://beispiel.de/stellen/konstrukteur")
    assert listing is not None
    assert "remote" not in (listing.remote_type or "").lower()


def test_the_description_is_the_posting_not_the_page():
    listing = extract_job_from_page(PAGE_WITHOUT_JSONLD, "https://beispiel.de/stellen/konstrukteur")
    assert listing is not None
    assert "SolidWorks" in listing.description
    assert "DevSecOps" not in listing.description


def test_a_page_with_no_description_container_still_extracts():
    """The fallback must not have become a requirement."""
    bare = """
    <html><head><title>Developer — Acme</title></head>
    <body><h1>Developer</h1>
    <p>Your tasks: build things. Your profile: experience. We offer: a job.
    Apply now to join the team in Berlin. Responsibilities include writing
    software and reviewing code for the platform team.</p></body></html>
    """
    listing = extract_job_from_page(bare, "https://acme.de/jobs/developer")
    assert listing is not None
    assert listing.title
