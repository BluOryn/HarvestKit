"""Lead schema, fingerprinting and evidence tracking."""

from __future__ import annotations

import json

from leadgen.models import EMAIL_STATUS_ORDER, LEAD_CSV_COLUMNS, LEAD_FIELDS, Lead


def test_csv_columns_cover_every_field():
    assert LEAD_CSV_COLUMNS[0] == "id"
    assert set(LEAD_FIELDS).issubset(LEAD_CSV_COLUMNS)
    assert len(LEAD_CSV_COLUMNS) == len(set(LEAD_CSV_COLUMNS))


def test_to_dict_emits_exactly_the_csv_columns():
    row = Lead(person_name="Jane Doe").to_dict()
    assert set(row) == set(LEAD_CSV_COLUMNS)


def test_fingerprint_keys_on_email_when_present():
    a = Lead(person_name="Jane Doe", person_email="J.Doe@Acme.de", company_domain="acme.de")
    b = Lead(person_name="Jane D.", person_email="j.doe@acme.de", company_domain="acme.de")
    assert a.fingerprint() == b.fingerprint(), "email should dominate identity, case-insensitively"


def test_fingerprint_falls_back_to_name_plus_domain():
    a = Lead(person_name="Jane Doe", company_domain="acme.de")
    b = Lead(person_name="jane  doe", company_domain="acme.de")
    c = Lead(person_name="Jane Doe", company_domain="other.de")
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != c.fingerprint()


def test_evidence_round_trips_as_json():
    lead = Lead(person_name="Jane Doe", person_role="CTO")
    lead.set_evidence("person_name", "https://acme.de/impressum")
    lead.set_evidence("person_role", "https://acme.de/team")
    row = lead.to_dict()
    evidence = json.loads(row["evidence_json"])
    assert evidence["person_name"] == "https://acme.de/impressum"
    assert evidence["person_role"] == "https://acme.de/team"


def test_empty_evidence_is_not_written():
    assert Lead(person_name="x").to_dict()["evidence_json"] == ""


def test_email_status_order_is_best_first():
    assert EMAIL_STATUS_ORDER[0] == "published"
    assert EMAIL_STATUS_ORDER[-1] == "catch_all"
