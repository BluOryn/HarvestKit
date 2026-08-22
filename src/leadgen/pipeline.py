"""Orchestrate: seed -> resolve people -> assemble -> checkpoint.

Company processing is network-bound and embarrassingly parallel, so it runs in
a thread pool. Each company is isolated: one dead site never aborts the run, and
every failure is counted so the funnel stays inspectable.
"""

from __future__ import annotations

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from .assemble import CompanyContext, build_leads
from .checkpoint import Checkpoint
from .person.cascade import resolve_people
from .person.register import people_from_register
from .person.roles import TARGET_FAMILIES

log = logging.getLogger(__name__)


def process_companies(
    companies: list[CompanyContext],
    http,
    checkpoint: Checkpoint,
    *,
    concurrency: int = 8,
    smtp: bool = True,
    max_pages: int = 8,
    max_person_pages: int = 10,
    max_nav_pages: int = 6,
    guess_without_anchor: bool = True,
    stop_after: int | None = None,
    register: bool = False,
) -> Counter:
    """Resolve people for each company and persist the resulting leads.

    `stop_after` short-circuits once that many email-bearing leads have been
    banked. The run over-fetches on purpose, but there is no point crawling
    another 3,000 companies once the quota is comfortably covered.
    """
    funnel: Counter = Counter()
    counter_lock = Lock()

    def handle(company: CompanyContext) -> None:
        if checkpoint.seen_company(company.domain):
            with counter_lock:
                funnel["already_done"] += 1
            return
        hits = resolve_people(
            company.domain,
            company.country,
            http,
            max_pages=max_pages,
            max_person_pages=max_person_pages,
            max_nav_pages=max_nav_pages,
        )
        checkpoint.record_company(company.domain)
        # People named in the company's own ads cost no extra request and are
        # the only reliable source of HR contacts, so they stand alongside the
        # crawl rather than behind it — a company whose site names nobody is
        # still a lead if its ad does.
        if company.ad_contacts:
            with counter_lock:
                funnel["ad_contacts"] += len(company.ad_contacts)
            hits = hits + company.ad_contacts
        # The public record, only for companies that named nobody themselves.
        # It costs two requests and answers a question the site already
        # answered for everyone else, so it runs last and only when it has to.
        if register and not hits and company.name:
            found = people_from_register(company.name, http)
            if found:
                with counter_lock:
                    funnel["register_people"] += len(found)
                    funnel["register_companies"] += 1
                hits = found
        if not hits:
            with counter_lock:
                funnel["no_person_found"] += 1
            return
        leads = build_leads(company, hits, smtp=smtp, guess_without_anchor=guess_without_anchor)
        with_email = sum(1 for lead in leads if lead.person_email)
        targeted = sum(1 for lead in leads if lead.person_role_family in TARGET_FAMILIES)
        checkpoint.save_leads(leads)
        with counter_lock:
            funnel["companies_with_people"] += 1
            funnel["people_found"] += len(leads)
            funnel["with_email"] += with_email
            funnel["target_role"] += targeted

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(handle, company): company for company in companies}
        for index, future in enumerate(as_completed(futures), 1):
            company = futures[future]
            try:
                future.result()
            except Exception as exc:
                with counter_lock:
                    funnel["company_error"] += 1
                log.warning("pipeline: %s failed: %s", company.domain, exc)
            if index % 25 == 0:
                log.info(
                    "pipeline: %d/%d companies · %d leads with an email · %d in a target role",
                    index,
                    len(companies),
                    funnel["with_email"],
                    funnel["target_role"],
                )
            if stop_after is not None and funnel["with_email"] >= stop_after:
                log.info("pipeline: %d email-bearing leads banked, stopping early", funnel["with_email"])
                for pending in futures:
                    pending.cancel()
                break
    return funnel
