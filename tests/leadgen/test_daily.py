"""Running this every morning has to produce different people every morning.

Two ledgers make that true, and both are easy to get subtly wrong in ways that
only show up on day two -- by which point the buyer has been sent yesterday's
list twice.
"""

from __future__ import annotations

import pytest

from leadgen.checkpoint import Checkpoint
from leadgen.models import Lead


@pytest.fixture()
def checkpoint(tmp_path):
    cp = Checkpoint(str(tmp_path / "daily.sqlite"))
    yield cp
    cp.close()


def _lead(name: str, domain: str = "acme.ch") -> Lead:
    return Lead(
        person_name=name,
        person_email=f"{name.split()[0].lower()}@{domain}",
        person_role="Geschäftsführer",
        person_role_family="executive",
        company_name="Acme AG",
        company_domain=domain,
        company_country="CH",
    )


def test_nothing_is_delivered_until_it_is_marked(checkpoint):
    checkpoint.save_leads([_lead("Anna Meier")])
    assert checkpoint.delivered_ids() == set()


def test_a_delivered_lead_is_not_offered_again(checkpoint):
    first, second = _lead("Anna Meier"), _lead("Reto Conrad")
    checkpoint.save_leads([first, second])
    checkpoint.mark_delivered([first], batch="leads-2026-08-23.csv")

    already = checkpoint.delivered_ids()
    remaining = [lead for lead in checkpoint.all_leads() if lead.fingerprint() not in already]
    assert [lead.person_name for lead in remaining] == ["Reto Conrad"]


def test_marking_the_same_lead_twice_is_harmless(checkpoint):
    """A run that is retried after a crash re-marks what it already marked."""
    lead = _lead("Anna Meier")
    checkpoint.save_leads([lead])
    checkpoint.mark_delivered([lead])
    checkpoint.mark_delivered([lead])
    assert checkpoint.delivered_count() == 1


def test_the_ledger_survives_reopening_the_file(tmp_path):
    """It is the memory between two runs on two different days, so it has to be
    on disk and not in the process that wrote it."""
    path = str(tmp_path / "daily.sqlite")
    lead = _lead("Anna Meier")
    first = Checkpoint(path)
    first.save_leads([lead])
    first.mark_delivered([lead])
    first.close()

    second = Checkpoint(path)
    try:
        assert second.delivered_ids() == {lead.fingerprint()}
    finally:
        second.close()


def test_a_crawled_company_is_skipped_by_default(checkpoint):
    checkpoint.record_company("acme.ch")
    assert checkpoint.seen_company("acme.ch") is True
    assert checkpoint.seen_company("other.ch") is False


def test_a_company_is_crawled_again_once_it_is_old_enough(checkpoint):
    """Staff change. An employer crawled in January may name three new directors
    by June, and "crawled once" meaning "crawled forever" never finds them."""
    checkpoint.record_company("acme.ch")
    # Backdate it 40 days. julianday is in days, so this is arithmetic, not sleep.
    checkpoint.conn.execute("UPDATE companies SET seen_at = seen_at - 40 WHERE domain = ?", ("acme.ch",))
    checkpoint.conn.commit()

    assert checkpoint.seen_company("acme.ch", max_age_days=30) is False
    assert checkpoint.seen_company("acme.ch", max_age_days=90) is True
    # No age given means the old behaviour: crawled once, never again.
    assert checkpoint.seen_company("acme.ch") is True


def test_recrawling_refreshes_the_timestamp(checkpoint):
    checkpoint.record_company("acme.ch")
    checkpoint.conn.execute("UPDATE companies SET seen_at = seen_at - 40 WHERE domain = ?", ("acme.ch",))
    checkpoint.conn.commit()
    assert checkpoint.seen_company("acme.ch", max_age_days=30) is False

    checkpoint.record_company("acme.ch")
    assert checkpoint.seen_company("acme.ch", max_age_days=30) is True


def test_a_recrawl_does_not_resend_the_people_it_already_sent(checkpoint):
    """The two ledgers have to compose: revisiting a company is only useful if
    it delivers the new names and not the old ones again."""
    known, fresh = _lead("Anna Meier"), _lead("Reto Conrad")
    checkpoint.save_leads([known])
    checkpoint.mark_delivered([known])

    # Second crawl of the same company finds one familiar person and one new.
    checkpoint.save_leads([known, fresh])

    already = checkpoint.delivered_ids()
    remaining = [lead for lead in checkpoint.all_leads() if lead.fingerprint() not in already]
    assert [lead.person_name for lead in remaining] == ["Reto Conrad"]
