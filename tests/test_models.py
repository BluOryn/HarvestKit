"""Schema, fingerprinting and merge semantics."""

from __future__ import annotations

import csv
import io

import pytest

from job_scraper.dedupe import dedupe_jobs
from job_scraper.models import CSV_COLUMNS, JOB_FIELDS, JobListing, canonicalize_url
from job_scraper.normalize import job_fingerprint


def test_csv_columns_cover_every_field():
    assert CSV_COLUMNS[0] == "id"
    assert set(JOB_FIELDS).issubset(CSV_COLUMNS)
    assert len(CSV_COLUMNS) == len(set(CSV_COLUMNS)), "duplicate CSV column"


def test_to_dict_emits_exactly_the_csv_columns():
    row = JobListing(title="Eng").to_dict()
    assert set(row) == set(CSV_COLUMNS)


def test_dedupe_key_matches_exported_id():
    """The id written to CSV must be the same string dedupe keys on.

    Two implementations used to disagree: one canonicalised the URL, the other
    hashed the raw string, so a job could dedupe under one id and export another.
    """
    job = JobListing(title="Eng", company="Acme", job_url="https://x.test/job/1")
    assert job_fingerprint(job) == job.to_dict()["id"]


def test_tracking_params_do_not_split_duplicates():
    plain = JobListing(title="Eng", company="Acme", job_url="https://x.test/job/1")
    tagged = JobListing(
        title="Eng", company="Acme", job_url="https://x.test/job/1?utm_source=news&fbclid=abc"
    )
    assert plain.fingerprint() == tagged.fingerprint()
    assert len(dedupe_jobs([plain, tagged])) == 1


def test_canonicalize_url_keeps_meaningful_query():
    assert canonicalize_url("https://x.test/s?q=dev&utm_medium=cpc") == "https://x.test/s?q=dev"
    assert canonicalize_url("https://x.test/a/#frag") == "https://x.test/a"
    assert canonicalize_url("") == ""


def test_remote_is_a_property_not_a_constructor_kwarg():
    """Adapters must pass remote_type=; remote= raises. Regression guard for the
    TypeError that silently disabled five ATS adapters."""
    with pytest.raises(TypeError):
        JobListing(remote="remote")
    job = JobListing(remote_type="remote")
    assert job.remote == "remote"


def test_merge_prefers_longer_prose_but_keeps_first_date():
    stub = JobListing(title="Eng", description="short", posted_date="2026-05-01")
    detail = JobListing(
        description="a much longer full description",
        posted_date="Published sometime last week",
    )
    stub.merge(detail)
    assert stub.description == "a much longer full description"
    assert stub.posted_date == "2026-05-01", "prose must not overwrite a real date"


def test_list_valued_field_is_joined_not_repr():
    job = JobListing(title="Eng")
    job.employment_type = ["Vollzeit", "Teilzeit"]  # type: ignore[assignment]
    assert job.to_dict()["employment_type"] == "Vollzeit, Teilzeit"


def test_extras_round_trip_through_csv():
    job = JobListing(title="Eng", job_url="https://x.test/1")
    job.set_extra("nav_uuid", "abc-123")
    job.set_extra("empty", "")  # dropped
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerow(job.to_dict())
    row = next(csv.DictReader(io.StringIO(buf.getvalue())))
    assert '"nav_uuid": "abc-123"' in row["extras_json"]
    assert "empty" not in row["extras_json"]


def test_salary_property_handles_partial_data():
    assert JobListing().salary == ""
    assert JobListing(salary_min="50000", salary_currency="EUR").salary == "50000 EUR"
    assert (
        JobListing(salary_min="50000", salary_max="70000", salary_currency="EUR", salary_period="year").salary
        == "50000-70000 EUR / year"
    )
