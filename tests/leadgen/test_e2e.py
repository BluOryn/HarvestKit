"""End-to-end over a local HTTP server: seed -> crawl -> assemble -> CSV.

The only thing stubbed is DNS/SMTP. Everything else — HTTP, HTML parsing, the
person cascade, pattern inference, scoring, the quota cut and the CSV writer —
is the real code path the production run takes.
"""

from __future__ import annotations

import csv
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from job_scraper.models import JobListing
from leadgen.assemble import CompanyContext
from leadgen.checkpoint import Checkpoint
from leadgen.email import validate as email_validate
from leadgen.export import write_csv
from leadgen.pipeline import process_companies
from leadgen.score.quota import select
from leadgen.seed.jobboard import companies_from_listings

IMPRESSUM = """<html><body><main>
<h1>Impressum</h1>
<p>Acme Software GmbH, Maximilianstrasse 12, 80539 M&uuml;nchen</p>
<p>Gesch&auml;ftsf&uuml;hrer: Anna Schmidt</p>
<p>E-Mail: anna.schmidt@acme-fixture.test</p>
</main></body></html>"""

TEAM = """<html><body><main>
<h1>Unser Team</h1>
<div><h3>Anna Schmidt</h3><p>Chief Technology Officer</p></div>
<div><h3>Peter Wolf</h3><p>Head of HR</p></div>
<div><h3>Marketing Team</h3><p>Marketing</p></div>
</main></body></html>"""

DOMAIN = "acme-fixture.test"

SITEMAP = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://{DOMAIN}/team/klaus-berg</loc></url>
</urlset>"""

KLAUS = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Person","name":"Klaus Berg",
 "jobTitle":"VP of Engineering"}
</script></head><body><h1>Klaus Berg</h1></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        routes = {
            "/impressum": IMPRESSUM,
            "/team": TEAM,
            "/sitemap.xml": SITEMAP,
            "/team/klaus-berg": KLAUS,
            "/": "<html><body>Acme</body></html>",
        }
        body = routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    monkeypatch.setattr(email_validate, "has_mx", lambda domain: True)
    monkeypatch.setattr(email_validate, "is_catch_all", lambda domain: False)


class LocalHttp:
    """Real HTTP against the fixture server, whatever host the URL names.

    The cascade builds https://<domain><path>. Rewriting only the authority
    keeps the company's domain realistic — which matters, because the inferred
    address is built from it and "peter.wolf@127.0.0.1:8000" is not a valid
    address, so a loopback domain would silently test nothing.
    """

    def __init__(self, host: str) -> None:
        self.host = host
        import requests

        self.session = requests.Session()

    def get(self, url, **kwargs):
        remainder = url.split("://", 1)[-1].split("/", 1)
        path = "/" + (remainder[1] if len(remainder) > 1 else "")
        response = self.session.get(f"http://{self.host}{path}", timeout=5)
        if response.status_code != 200:
            return None
        return url, response.text


def test_full_pipeline_produces_a_scored_csv(server, tmp_path):
    http = LocalHttp(server)
    company = CompanyContext(
        name="Acme Software GmbH",
        domain=DOMAIN,
        website=f"https://{DOMAIN}",
        country="DE",
        city="Munich",
        seed_url="http://seed.test/jobs/1",
    )
    checkpoint = Checkpoint(tmp_path / "run.sqlite")

    funnel = process_companies([company], http, checkpoint, concurrency=1, smtp=False)
    assert funnel["companies_with_people"] == 1

    leads, report = select(checkpoint.all_leads(), target=10)
    by_name = {lead.person_name: lead for lead in leads}

    # Impressum, team page and the sitemap-mined person page all contributed.
    assert "Anna Schmidt" in by_name
    assert "Peter Wolf" in by_name
    assert "Klaus Berg" in by_name, "sitemap-mined person page was not reached"

    # The Impressum's published address anchored the domain's format, so the
    # two people who published nothing still got a deliverable address.
    assert by_name["Anna Schmidt"].email_status == "published"
    assert by_name["Peter Wolf"].person_email == f"peter.wolf@{DOMAIN}"
    assert by_name["Peter Wolf"].email_status.startswith("inferred")

    # Roles resolved to the two families the brief asked for.
    assert by_name["Anna Schmidt"].person_role_family == "tech_leadership"
    assert by_name["Peter Wolf"].person_role_family == "hr"
    assert by_name["Klaus Berg"].person_role_family == "tech_leadership"

    # A department card is not a person.
    assert "Marketing Team" not in by_name

    out = tmp_path / "leads.csv"
    write_csv(leads, out)
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert len(rows) == len(leads)
    assert all(row["person_email"] and row["person_name"] for row in rows)
    assert all(row["evidence_json"] for row in rows), "every row must be traceable"
    assert report.shortfall == 10 - len(leads)


def test_rerunning_skips_companies_already_processed(server, tmp_path):
    http = LocalHttp(server)
    company = CompanyContext(name="Acme", domain=DOMAIN, country="DE")
    checkpoint = Checkpoint(tmp_path / "run.sqlite")

    process_companies([company], http, checkpoint, concurrency=1, smtp=False)
    again = process_companies([company], http, checkpoint, concurrency=1, smtp=False)
    assert again["already_done"] == 1
    assert again["people_found"] == 0


def test_seed_drops_a_company_whose_own_domain_is_unreachable(server):
    listings = [
        JobListing(title="Backend Engineer", company="Ghost GmbH", job_url="https://nope.invalid/jobs/1")
    ]

    class Dead:
        def get(self, url, **kwargs):
            return None

    assert companies_from_listings(listings, Dead()) == []
