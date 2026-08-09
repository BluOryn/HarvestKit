"""Press-quote attribution and sitemap person-URL mining."""

from __future__ import annotations

from leadgen.person.strategies import press, sitemap

PRESS_EN = """
<html><body><article>
<p>The rollout doubled throughput, said Anna Schmidt, CTO of Acme Software.</p>
<p>"We hire for curiosity," says Peter Wolf, Head of HR at Acme.</p>
<p>The market grew 4% last year, according to analysts.</p>
</article></body></html>
"""

PRESS_DE = """
<html><body><article>
<p>Die Migration lief reibungslos, sagt Klaus Berg, Technischer Leiter bei Acme.</p>
</article></body></html>
"""

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://acme.de/sitemap-pages.xml</loc></sitemap>
</sitemapindex>
"""

SITEMAP_PAGES = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://acme.de/team/anna-schmidt</loc></url>
  <url><loc>https://acme.de/team/peter-wolf</loc></url>
  <url><loc>https://acme.de/team/index</loc></url>
  <url><loc>https://acme.de/blog/why-we-use-rust</loc></url>
  <url><loc>https://acme.de/produkte</loc></url>
</urlset>
"""


class StubHttp:
    def __init__(self, pages):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return (url, self.pages[url]) if url in self.pages else None


def test_press_finds_a_quoted_executive():
    hits = press.extract(PRESS_EN, "https://acme.de/news/1")
    by_name = {h.name: h for h in hits}
    assert "Anna Schmidt" in by_name
    assert "CTO" in by_name["Anna Schmidt"].role


def test_press_finds_the_trailing_attribution_form():
    hits = press.extract(PRESS_EN, "https://acme.de/news/1")
    assert any(h.name == "Peter Wolf" and "HR" in h.role for h in hits)


def test_press_handles_german_attribution():
    hits = press.extract(PRESS_DE, "https://acme.de/presse/1")
    assert any(h.name == "Klaus Berg" for h in hits)


def test_press_ignores_prose_that_is_not_an_attribution():
    """'according to analysts' must not become a person."""
    names = {h.name for h in press.extract(PRESS_EN, "https://acme.de/news/1")}
    assert not any("analyst" in name.lower() for name in names)


def test_press_keeps_only_target_role_families():
    html = "<html><body><p>It went well, said Jane Roe, Barista at Acme.</p></body></html>"
    assert press.extract(html, "u") == []


def test_press_records_its_strategy():
    for hit in press.extract(PRESS_EN, "https://acme.de/news/1"):
        assert hit.strategy == "press"


def test_sitemap_follows_an_index_and_finds_person_pages():
    http = StubHttp(
        {
            "https://acme.de/sitemap.xml": SITEMAP_INDEX,
            "https://acme.de/sitemap-pages.xml": SITEMAP_PAGES,
        }
    )
    urls = sitemap.person_urls("acme.de", http)
    assert "https://acme.de/team/anna-schmidt" in urls
    assert "https://acme.de/team/peter-wolf" in urls


def test_sitemap_rejects_section_indexes_and_unrelated_pages():
    http = StubHttp(
        {
            "https://acme.de/sitemap.xml": SITEMAP_INDEX,
            "https://acme.de/sitemap-pages.xml": SITEMAP_PAGES,
        }
    )
    urls = sitemap.person_urls("acme.de", http)
    assert "https://acme.de/team/index" not in urls
    assert "https://acme.de/blog/why-we-use-rust" not in urls
    assert "https://acme.de/produkte" not in urls


def test_sitemap_respects_the_limit():
    http = StubHttp(
        {
            "https://acme.de/sitemap.xml": SITEMAP_INDEX,
            "https://acme.de/sitemap-pages.xml": SITEMAP_PAGES,
        }
    )
    assert len(sitemap.person_urls("acme.de", http, limit=1)) == 1


def test_sitemap_on_a_site_without_one_yields_nothing():
    assert sitemap.person_urls("acme.de", StubHttp({})) == []


def test_sitemap_needs_a_domain():
    assert sitemap.person_urls("", StubHttp({})) == []


def test_sitemap_survives_a_client_that_raises():
    class Exploding:
        def get(self, url, **kwargs):
            raise RuntimeError("reset")

    assert sitemap.person_urls("acme.de", Exploding()) == []


def test_sitemap_rejects_an_xml_bomb():
    """Remote XML goes through safe_xml, so an entity bomb must not expand."""
    bomb = """<?xml version="1.0"?>
    <!DOCTYPE lolz [ <!ENTITY lol "lol">
      <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;"> ]>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>&lol2;</loc></url>
    </urlset>"""
    http = StubHttp({"https://acme.de/sitemap.xml": bomb})
    assert sitemap.person_urls("acme.de", http) == []
