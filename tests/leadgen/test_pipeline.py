"""Checkpoint durability and CSV output, no network."""

from __future__ import annotations

import csv

import pytest

from leadgen.checkpoint import Checkpoint
from leadgen.export import write_csv
from leadgen.models import LEAD_CSV_COLUMNS, Lead


def test_write_csv_emits_the_schema_header(tmp_path):
    out = tmp_path / "leads.csv"
    write_csv([Lead(person_name="Jane Doe", person_email="j@acme.de")], out)
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert list(rows[0]) == LEAD_CSV_COLUMNS
    assert rows[0]["person_name"] == "Jane Doe"


def test_write_csv_preserves_non_ascii_names(tmp_path):
    out = tmp_path / "leads.csv"
    write_csv([Lead(person_name="Jörg Müller", person_email="j@acme.de")], out)
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert rows[0]["person_name"] == "Jörg Müller"


def test_write_csv_is_atomic_on_failure(tmp_path):
    out = tmp_path / "leads.csv"
    out.write_text("previous,data\n", encoding="utf-8")

    class Exploding(Lead):
        def to_dict(self):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        write_csv([Exploding(person_name="x")], out)
    assert out.read_text(encoding="utf-8") == "previous,data\n"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".leadgen-")]
    assert leftovers == [], f"temp file left behind: {leftovers}"


def test_checkpoint_remembers_companies(tmp_path):
    checkpoint = Checkpoint(tmp_path / "run.sqlite")
    assert checkpoint.seen_company("acme.de") is False
    checkpoint.record_company("acme.de")
    assert checkpoint.seen_company("acme.de") is True
    checkpoint.close()


def test_checkpoint_survives_reopen(tmp_path):
    path = tmp_path / "run.sqlite"
    first = Checkpoint(path)
    first.record_company("acme.de")
    first.save_leads([Lead(person_name="Jane Doe", person_email="j@acme.de", company_domain="acme.de")])
    first.close()

    second = Checkpoint(path)
    assert second.seen_company("acme.de") is True
    assert len(second.all_leads()) == 1
    assert second.all_leads()[0].person_name == "Jane Doe"
    second.close()


def test_checkpoint_round_trips_evidence(tmp_path):
    path = tmp_path / "run.sqlite"
    lead = Lead(person_name="Jane Doe", person_email="j@acme.de")
    lead.set_evidence("person_name", "https://acme.de/team")
    first = Checkpoint(path)
    first.save_leads([lead])
    first.close()

    second = Checkpoint(path)
    assert second.all_leads()[0].evidence["person_name"] == "https://acme.de/team"
    second.close()


def test_checkpoint_deduplicates_saved_leads(tmp_path):
    checkpoint = Checkpoint(tmp_path / "run.sqlite")
    lead = Lead(person_name="Jane Doe", person_email="j@acme.de", company_domain="acme.de")
    checkpoint.save_leads([lead])
    checkpoint.save_leads([lead])
    assert len(checkpoint.all_leads()) == 1
    checkpoint.close()


def test_saving_no_leads_is_a_noop(tmp_path):
    checkpoint = Checkpoint(tmp_path / "run.sqlite")
    checkpoint.save_leads([])
    assert checkpoint.all_leads() == []
    checkpoint.close()
