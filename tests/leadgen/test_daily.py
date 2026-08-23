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


# --------------------------------------------------------------------------
# The jobs.ch filter is the targeting. Everything the operator can aim lives
# in this URL, so building it wrong aims the whole run somewhere else.
# --------------------------------------------------------------------------


def test_the_builder_reproduces_the_shipped_filter_exactly():
    """The shipped constant is what every measured number in the runbook was
    taken against. If the builder drifts from it, those numbers stop meaning
    anything."""
    from leadgen.seed.jobsch import DEFAULT_FILTER_URL, build_filter_url

    assert build_filter_url() == DEFAULT_FILTER_URL


@pytest.mark.parametrize("days, expected", [(1, 1), (3, 3), (30, 30), (0, 1), (-5, 1)])
def test_the_recency_window_is_always_at_least_a_day(days, expected):
    from leadgen.seed.jobsch import build_filter_url

    assert f"publication-date={expected}" in build_filter_url(days=days)


def test_a_search_term_is_escaped_not_pasted():
    """A term with a space produces a URL jobs.ch reads as a different query."""
    from leadgen.seed.jobsch import build_filter_url

    assert "term=data+engineer" in build_filter_url(term="data engineer")


def test_categories_choose_the_sector():
    from leadgen.seed.jobsch import build_filter_url

    url = build_filter_url(categories=(106,))
    assert "category=106" in url and "category=146" not in url


def test_unparseable_categories_fall_back_to_the_shipped_set():
    """A typo in a comma list must not silently widen the run to every sector
    on the board."""
    from leadgen.cli import _int_list

    assert _int_list("106,146", (1,)) == (106, 146)
    assert _int_list("", (106,)) == (106,)
    assert _int_list("oops", (106,)) == (106,)
    assert _int_list("106,oops,146", (1,)) == (106, 146)


def test_an_explicit_url_is_taken_whole(monkeypatch):
    """Somebody who pasted a search out of their browser means that search,
    not that search with our defaults grafted back on."""
    from leadgen.cli import build_parser

    args = build_parser().parse_args(
        ["--config", "x", "--jobsch-url", "https://www.jobs.ch/en/vacancies/?term=foo"]
    )
    assert args.jobsch_url.endswith("term=foo")


# --------------------------------------------------------------------------
# The runner scripts build a command line the CLI has to accept. A typo there
# is invisible until somebody runs the harvest and it dies on argv.
# --------------------------------------------------------------------------

CH_REGION_ARGS = [
    "--countries",
    "CH",
    "--roles",
    "hr,tech_leadership,executive",
    "--target",
    "300",
    "--overfetch",
    "0",
    "--recrawl-after",
    "30",
    "--only-new",
    "--checkpoint",
    ".cache/daily-ch.sqlite",
    "--output",
    "output/leads-ch-2026-08-23.csv",
    "--config",
    "configs/leads/swiss-it.yaml",
    "--jobsch-pages",
    "65",
    "--jobsch-days",
    "30",
    "--jobsch-categories",
    "106,146,156,167",
    "--jobsch-term",
    "engineer",
    "--no-smtp",
    "--register",
]

EUROPE_REGION_ARGS = [
    "--countries",
    "europe",
    "--roles",
    "hr,tech_leadership,executive",
    "--target",
    "300",
    "--overfetch",
    "0",
    "--recrawl-after",
    "30",
    "--only-new",
    "--checkpoint",
    ".cache/daily-europe.sqlite",
    "--output",
    "output/leads-europe-2026-08-23.csv",
    "--config",
    "configs/leads/eu-it.yaml",
    "--search-keywords",
    "configs/leads/keywords.txt",
    "--search-keywords-multilingual",
    "configs/leads/keywords-multilingual.txt",
    "--search-locations",
    "configs/leads/locations-eu.txt",
    "--search-max-pages",
    "3",
    "--search-delay",
    "0.4",
    "--arbeitnow-pages",
    "20",
]


@pytest.mark.parametrize("argv", [CH_REGION_ARGS, EUROPE_REGION_ARGS], ids=["ch", "europe"])
def test_every_flag_a_runner_passes_is_one_the_cli_accepts(argv):
    from leadgen.cli import build_parser

    parsed = build_parser().parse_args(argv)
    assert parsed.only_new is True
    assert parsed.overfetch == 0


@pytest.mark.parametrize(
    "path",
    [
        "configs/leads/swiss-it.yaml",
        "configs/leads/eu-it.yaml",
        "configs/leads/keywords.txt",
        "configs/leads/keywords-multilingual.txt",
        "configs/leads/locations-eu.txt",
    ],
)
def test_every_file_a_runner_names_actually_ships(path):
    """The Europe runner names four input files by path. A rename that misses
    one turns a full harvest into a silent zero."""
    from pathlib import Path

    assert Path(path).is_file(), f"{path} is referenced by scripts/daily.* but not in the repo"
