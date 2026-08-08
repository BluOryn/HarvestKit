"""End-to-end: adapter → deep-scrape → filter → dedupe → CSV. No network."""

from __future__ import annotations

import csv
from typing import Any, Optional

import pytest

from job_scraper.adapters import ADAPTERS, get_adapter
from job_scraper.config import CsvExportConfig, ExportsConfig, RunConfig, TargetConfig
from job_scraper.dedupe import dedupe_jobs
from job_scraper.deep_scrape import DeepScrapeConfig, deep_scrape_jobs
from job_scraper.export import run_exports
from job_scraper.models import CSV_COLUMNS, JobListing

DETAIL_HTML = """
<html><head>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"JobPosting","title":"{title}",
 "description":"<p>Work here. We are building the platform that powers our whole product line, and you would own a substantial part of it end to end.</p><h3>Requirements</h3><ul><li>Python and Kubernetes</li></ul>",
 "hiringOrganization":{{"name":"Acme"}},
 "jobLocation":{{"address":{{"addressLocality":"Oslo","addressCountry":"NO"}}}},
 "datePosted":"2026-05-01"}}
</script></head><body><main><h1>{title}</h1>
<p>We use Python and Kubernetes. Contact jobs@acme.test or +47 95 83 21 97.</p>
</main></body></html>
"""


class FakeHttp:
    """Minimal HttpClient stand-in: serves canned bodies, records calls."""

    user_agent = "test"

    def __init__(self, pages: dict, payloads: Optional[dict] = None) -> None:
        self.pages = pages
        self.payloads = payloads or {}
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any):
        self.calls.append(url)
        for fragment, body in self.pages.items():
            if fragment in url:
                return url, body
        return None

    def get_json(self, url: str, **kwargs: Any):
        self.calls.append(url)
        for fragment, payload in self.payloads.items():
            if fragment in url:
                return payload
        return None

    def post_json(self, url: str, payload: Any, **kwargs: Any):
        return None

    def close(self) -> None:
        pass


def test_greenhouse_adapter_then_deep_scrape_then_export(tmp_path):
    listing_payload = {
        "jobs": [
            {
                "title": "Senior Engineer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                "location": {"name": "Oslo"},
                "updated_at": "2026-05-01",
                "content": "card snippet",
            },
            {
                "title": "Junior Engineer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
                "location": {"name": "Oslo"},
                "updated_at": "2026-05-02",
                "content": "card snippet",
            },
        ]
    }
    http = FakeHttp(
        pages={
            "/jobs/1": DETAIL_HTML.format(title="Senior Engineer"),
            "/jobs/2": DETAIL_HTML.format(title="Junior Engineer"),
        },
        payloads={"boards-api.greenhouse.io": listing_payload},
    )

    target = TargetConfig(name="acme", url="https://boards.greenhouse.io/acme", adapter="greenhouse")
    jobs = get_adapter(target).fetch_jobs(target, RunConfig(confirm_permission=True), http)
    assert len(jobs) == 2

    deep_scrape_jobs(jobs, http, DeepScrapeConfig(concurrency=2, per_host_delay_seconds=0.0, max_retries=0))

    # Deep scrape must have replaced the card snippet with real content.
    senior = next(j for j in jobs if j.title == "Senior Engineer")
    assert "Work here" in senior.description
    assert senior.city == "Oslo"
    assert "Python and Kubernetes" in senior.requirements
    assert "python" in senior.tech_stack
    assert senior.recruiter_email == "jobs@acme.test"

    out = tmp_path / "jobs.csv"
    run_exports(dedupe_jobs(jobs), ExportsConfig(csv=CsvExportConfig(enabled=True, path=str(out))))

    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 2
    assert list(rows[0]) == CSV_COLUMNS
    assert all(row["id"] for row in rows)
    assert len({row["id"] for row in rows}) == 2


def test_deep_scrape_reports_failures_without_raising():
    jobs = [JobListing(title="x", job_url="https://dead.test/job/1")]
    http = FakeHttp(pages={})  # every fetch returns None
    result = deep_scrape_jobs(
        jobs, http, DeepScrapeConfig(concurrency=1, per_host_delay_seconds=0.0, max_retries=0)
    )
    assert result is jobs
    assert jobs[0].description == ""


def test_deep_scrape_skips_listings_without_a_url():
    jobs = [JobListing(title="no url")]
    deep_scrape_jobs(jobs, FakeHttp(pages={}), DeepScrapeConfig(max_retries=0))
    assert jobs[0].title == "no url"


def test_export_failure_in_one_sink_does_not_lose_the_others(tmp_path, monkeypatch, caplog):
    """CSV must still be written when a downstream exporter blows up."""
    out = tmp_path / "jobs.csv"

    def boom(jobs, cfg):
        raise RuntimeError("notion is down")

    monkeypatch.setattr("job_scraper.export.export_notion", boom)
    exports = ExportsConfig(csv=CsvExportConfig(enabled=True, path=str(out)))
    exports.notion.enabled = True
    exports.notion.token = "x"
    exports.notion.database_id = "y"

    run_exports([JobListing(title="Eng", job_url="https://x.test/1")], exports)
    assert out.exists()
    assert "notion is down" in caplog.text


def test_csv_export_is_atomic_on_failure(tmp_path, monkeypatch):
    """A crash mid-write must not destroy the previous run's CSV."""
    from job_scraper.export import csv_exporter

    out = tmp_path / "jobs.csv"
    out.write_text("previous,good,data\n", encoding="utf-8")

    class Exploding(JobListing):
        def to_dict(self):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        csv_exporter.export_csv([Exploding(title="x")], CsvExportConfig(path=str(out)))

    assert out.read_text(encoding="utf-8") == "previous,good,data\n"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".harvestkit-")]
    assert leftovers == [], f"temp file left behind: {leftovers}"


def test_every_registered_adapter_is_constructible():
    for name, adapter in ADAPTERS.items():
        assert hasattr(adapter, "fetch_jobs"), name


def test_unknown_adapter_name_falls_back_to_generic():
    target = TargetConfig(name="x", url="https://nowhere.test", adapter="does-not-exist")
    assert get_adapter(target) is ADAPTERS["generic"]
