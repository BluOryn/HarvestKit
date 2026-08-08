# EU IT-sector lead list — 1,000 rows with named HR/CTO contacts

**Date:** 2026-08-08
**Status:** design, awaiting approval
**Deliverable:** one CSV of exactly 1,000 leads, due 2026-08-09

## Goal

Produce a one-off list of 1,000 EU leads. Each row is a **named person** — an HR
contact or a CTO/technical leader — at a company that employs IT staff, with an
individual email address and enough surrounding fields to be usable in a CRM.

This is a single delivery, not a scheduled system. The automation layer
(ledger, cron, adaptive allocator, idempotent Sheets upsert) is deliberately out
of scope and specified separately once this list ships.

### Success criteria

1. Exactly 1,000 rows. Not 998, not 1,040.
2. Every row has a named person, a role, a company, a country, and an email.
3. Every row carries `email_status` so the buyer can see what they are getting
   and drop the weak tail if they want only published/verified addresses.
4. Every row carries the URL each fact came from. A lead nobody can trace back
   to a source is not defensible and not worth delivering.
5. Zero duplicate people, zero role accounts (`info@`, `jobs@`) in the person
   email column.

### Non-goals

- Cross-run state, scheduling, resumability across days.
- Google Sheets API integration. The deliverable is a CSV the user imports.
- Per-address SMTP RCPT verification (see Decisions). One probe *per domain* for
  catch-all detection is in scope; probing every candidate address is not.
- Any source whose terms forbid scraping. No LinkedIn, no Apollo, no ZoomInfo.

## Key insight

"Any sector which employs IT" is equivalent to "posts IT job ads", which makes
job boards the natural seed. This matters for three compounding reasons:

1. A company appearing on an IT job board **satisfies the targeting criterion by
   construction**. No industry classifier needed.
2. Job ads are the one place a company voluntarily publishes a **named HR
   contact with a direct email**. That is half the brief, delivered by the seed
   step itself.
3. That recruiter address is the **pattern anchor**. Knowing
   `anna.schmidt@acme.de` reveals the domain's format (`first.last@`), which is
   then applied to the CTO found on `/impressum` or `/team`.

Without an anchor, an inferred address is a guess across five candidate formats.
With one, it is a single high-confidence derivation. This is what makes
"individual email on every row" achievable inside a day rather than a fantasy.

## Pipeline

```
  seed: EU job boards, IT roles
    │      reuses the 15 existing adapters
    ▼
  company + HR contact (name, role, email when the ad publishes one)
    │
    ▼
  domain resolution  ──────────────► company website
    │
    ▼
  site crawl: /impressum /team /about /kontakt + sitemap + schema.org Person
    │      multilingual path dictionary, driven by country
    ▼
  CTO / technical leadership (name, role)
    │
    ▼
  email pattern inference, anchored on the recruiter address
    │
    ▼
  MX validation + catch-all detection
    │
    ▼
  completeness score → rank → cut to exactly 1,000
    │
    ▼
  CSV
```

## Modules

New code lives in `src/leadgen/`. Each module has one job and is testable
without network access.

| Module | Responsibility | Depends on |
|---|---|---|
| `seed/jobboard.py` | Walk EU job boards for IT roles, emit `CompanySeed` | existing adapters, `crawl`, `http` |
| `company/domain.py` | Resolve a company name to its own website; reject aggregators and ATS hosts | `http` |
| `company/sitecrawl.py` | Fetch the pages likely to name people, bounded per company | `http`, `discovery` |
| `person/paths.py` | Country → candidate path list (data, not code) | — |
| `person/cascade.py` | Run the extraction strategies in order, merge results | `extract`, `universal.mine_contacts` |
| `person/strategies/*.py` | One file per strategy: impressum, team, sitemap, jsonld_person, press | `bs4` |
| `email/pattern.py` | Infer a domain's address format from known examples | — |
| `email/validate.py` | Syntax, MX lookup, catch-all detection, role-account rejection | `dnspython` |
| `score/completeness.py` | Field-weighted score per lead | — |
| `score/quota.py` | Rank, deduplicate, cut to exactly N | — |
| `models.py` | `Lead` dataclass, CSV column order, provenance | — |
| `cli.py` | `python -m leadgen --config configs/leads/eu-it.yaml` | `config` |

Reused unchanged: `http` (throttle, retries, proxies, robots), `crawl`,
`discovery`, `extract`, `universal.mine_contacts`, `config`, `safe_xml`.

## Data model

`Lead` is person-centric — one row is one human, not one company. A company
contributing both an HR contact and a CTO produces two rows sharing company
fields.

```
person_name, person_role, person_role_family (hr | tech_leadership | other),
person_email, email_status, email_confidence, person_linkedin, person_phone,

company_name, company_domain, company_website, company_size_hint,
company_country, company_region, company_city,

source_seed_url, source_person_url, source_email_url, evidence_json,
tech_stack, seniority, scraped_at, id
```

`evidence_json` maps each populated field to the URL it came from. This is
cheap to carry and it is what makes the list auditable.

`id` is a SHA-1 over `(lower(person_email) or lower(person_name) + domain)`,
reusing the fingerprint approach already in `job_scraper.models`.

## Person resolution cascade

Strategies run in order; later ones fill only what earlier ones missed, mirroring
how `extract.py` already layers JSON-LD → microdata → OG → heuristics.

| Order | Strategy | Yield | Notes |
|---|---|---|---|
| 1 | Job-ad recruiter block | high for HR | Already implemented in `mine_contacts` |
| 2 | Impressum / mentions légales | very high in DE/AT/CH | Legally mandatory under §5 TMG; names managing directors |
| 3 | `schema.org/Person` in JSON-LD | medium, very clean | `employee`, `founder`, `member` |
| 4 | Team/about pages | high | Multilingual path dictionary |
| 5 | Sitemap mining | medium | `/team/jane-doe` URLs surface directly |
| 6 | Press/news attribution | low | `"…said Jane Doe, CTO"` |

Deferred to a later slice, not built for this delivery: PDF extraction and
Wayback recovery. Both are real yield but neither fits the deadline.

### Role classification

Titles are matched against a multilingual pattern table (data, not code):

- **HR:** HR, People, Talent, Recruiting, Personal, Personalwesen, Ressources
  Humaines, Risorse Umane, Recursos Humanos, Human Resources
- **Tech leadership:** CTO, VP Engineering, Head of Engineering, Technischer
  Leiter, Directeur Technique, IT-Leiter, Chief Technology

A person matching neither is retained but scores lower; they are not the target
but they are not worthless either.

## Email resolution

**Pattern inference.** Given one or more known `(name, email)` pairs on a domain,
determine the format from the candidate set `first.last`, `f.last`, `first`,
`firstlast`, `last.first`, `f_last`. A pattern derived from two or more
consistent examples is `inferred_high`; from exactly one it is `inferred_medium`.

**Validation ladder**, cheapest first, stopping at the first failure:

1. Syntax (RFC 5322 subset).
2. Domain has an MX record. No MX means no mail, so the lead is dropped.
3. Not a role account. `info@ jobs@ karriere@ hr@ contact@ sales@ office@` and
   their EU-language equivalents are never emitted as a person's address.
4. Catch-all detection — one SMTP probe **per domain** (not per address) using a
   deliberately invalid local part. If the domain accepts it, every address there
   is unverifiable and is marked `catch_all` rather than being silently scored as
   good. Roughly 3,000 domains, one connection each, cached per domain and
   behind a `--no-smtp` flag if it causes trouble. Best-effort: a provider that
   blocks probing yields `unknown`, which scores between `inferred_high` and
   `catch_all` rather than being treated as a pass.

**`email_status` values:** `published` (literally on a page) > `verified` >
`inferred_high` > `inferred_medium` > `catch_all`.

## Quota engine

The mechanism that makes an exact count survivable:

1. **Over-fetch.** Target 6,000–10,000 companies for 1,000 rows. Person-with-
   individual-email conversion runs 10–20% of companies discovered.
2. **Score** each lead on weighted field completeness. Email status carries the
   heaviest weight, then role-family match (HR or tech leadership), then named
   person, then company location, then the enrichment fields.
3. **Deduplicate** on `id`, then on `(normalized_name, domain)` to catch the same
   person found twice through different strategies.
4. **Rank and cut** to exactly 1,000.
5. **Geographic spread:** a per-country ceiling of 25% so the list is not 700
   German rows. Applied as a constraint during the cut, not as a pre-allocation
   — a single run has no yield history to learn from, so the full adaptive
   allocator is not built here.
6. **Shortfall is reported, never padded.** If the run yields 840 qualifying
   leads, it says so and writes 840. Filling the gap with rows that fail the
   criteria would make the whole list untrustworthy.

## Error handling

- Per-company failures are isolated. One dead site never aborts the run.
- The run checkpoints discovered companies and resolved people to SQLite, so an
  interrupted run resumes rather than restarting. Cheap here and it is the
  foundation the later cron slice needs anyway.
- `robots.txt` is honoured throughout, as the existing `http` layer already does.
- Per-host throttling applies; with thousands of distinct hosts the per-host
  limit does not bind on overall throughput.
- Every dropped lead is logged with a reason, so the funnel is inspectable:
  discovered → domain resolved → person found → email resolved → passed
  validation → made the cut.

## Testing

Unit tests, no network, following the existing `tests/` patterns:

- `pattern.py`: each format inferred correctly from examples; conflicting
  examples yield no pattern rather than a wrong one.
- `validate.py`: role accounts rejected in every supported language;
  catch-all detection; missing MX drops the lead.
- Person strategies: fixture HTML per strategy, including a real German
  Impressum layout, a French mentions légales, and a `schema.org/Person` block.
- Role classification: multilingual title table.
- `quota.py`: returns exactly N; respects the country ceiling; reports shortfall
  instead of padding; deduplicates the same person found by two strategies.
- End-to-end on a local fixture server, as the existing suite already does.

## Compliance

The list contains business-context personal data on EU data subjects, so GDPR
applies. This is ordinary B2B prospecting and the lawful basis is legitimate
interest (Art. 6(1)(f)), which carries obligations the design satisfies rather
than assumes:

- **Provenance.** `evidence_json` records the source URL for every field, so any
  record can be traced and justified.
- **Minimisation.** Only business-context fields are collected. No personal
  addresses, no personal social accounts, nothing outside the professional role.
- **Public sources only.** Nothing behind a login, nothing behind a paywall,
  nothing whose terms forbid collection.
- **Suppression list.** A file of domains and addresses to exclude, applied
  before export. Empty for this run, but the hook exists so an objection can be
  honoured immediately rather than requiring code.

The recipient is responsible for the sending side — privacy notice at first
contact, right to object, and the local marketing rules. Worth stating to them
explicitly when the list is handed over.

## Decisions taken

| Decision | Rationale |
|---|---|
| Job boards as seed | Targeting criterion satisfied by construction; recruiter address is the pattern anchor |
| CSV, not Sheets API | Removes a credential dependency from the critical path |
| MX yes, SMTP RCPT probe no | ~6,000 probes from one IP gets rate-limited and returns unreliable answers; costs hours, buys noise. Rows land `inferred_high` rather than `verified` |
| PDF + Wayback deferred | Real yield, does not fit the deadline |
| Named person with no email is dropped | The brief requires an email on every row. Such leads are still scored and retained in the checkpoint DB, so a later run can use them |
| Per-country ceiling, no adaptive allocator | One run has no yield history to learn from |

## Open question

`configs/leads/eu-it.yaml` needs a starting board list. The existing adapters
cover Greenhouse, Lever, Ashby, Workday, Personio, Recruitee, Workable,
SmartRecruiters, Arbeitsagentur and jobs.ch — strong in DACH and pan-EU ATS
coverage, thin in southern and eastern Europe. Whether to add boards for those
regions depends on whether the buyer cares about spread or only about count.
