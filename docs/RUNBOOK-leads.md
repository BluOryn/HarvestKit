# Runbook — EU IT lead list

Produces a CSV of named HR and technical-leadership contacts at EU companies
that employ IT staff, each with an individual email address.

**This must run somewhere with unrestricted outbound HTTP.** The pipeline
fetches pages on thousands of distinct company domains. On a restricted network
roughly half of them fail silently, and the run produces a skewed list whose
gaps are invisible in the output.

## Prerequisites

```bash
pip install -r requirements.txt     # includes dnspython, needed for MX checks
```

Verify the environment can actually reach company sites before spending hours:

```bash
for h in sap.com siemens.com hellofresh.de getyourguide.com zalando.de; do
  printf "%s %s\n" "$(curl -s -o /dev/null -w '%{http_code}' https://www.$h/ --max-time 10)" "$h"
done
```

Anything other than `000` is fine — 301, 403 and 429 all mean the host is
reachable. Several `000` results mean egress is filtered; fix that first or the
run is wasted.

## 1. Smoke run (2–3 minutes)

Always do this first. It costs almost nothing and catches a dead seed API, a
broken config or an empty funnel before the long run.

```bash
python run_leads.py --config configs/leads/eu-it.yaml \
    --target 10 --max-pages 3 --max-person-pages 2 --no-smtp \
    --checkpoint .cache/smoke.sqlite --output output/smoke.csv
```

Read the `seed:` lines. If every target reports `0 listings`, stop — the seed
API is unreachable or has moved, and no amount of waiting will help. See
Troubleshooting.

Then eyeball the output:

```bash
python tools/verify_leads.py output/smoke.csv
```

## 2. Full run (1–4 hours)

```bash
python run_leads.py --config configs/leads/eu-it.yaml \
    --boards configs/leads/boards.txt \
    --search-keywords configs/leads/keywords.txt \
    --search-keywords-multilingual configs/leads/keywords-multilingual.txt \
    --countries eu --target 1000 --concurrency 16 2>&1 | tee output/run.log
```

`--countries` is not optional once `--search-keywords` is in play: the search
seed pairs each keyword with each country, so without one there is nowhere to
search and the seed returns nothing. The run warns rather than failing silently.

Interrupted runs resume — the checkpoint records every company already
processed, so re-running the same command picks up where it stopped rather than
starting over.

Useful flags:

| Flag | Why |
|---|---|
| `--boards FILE` | Seed from public Greenhouse/Lever/Personio boards listed in `configs/leads/boards.txt` |
| `--search-keywords FILE` | **The geography-first seed.** Pairs each term with each `--countries` entry against Workable's cross-company search |
| `--search-keywords-multilingual FILE` | SmartRecruiters, which has no geography parameter — the language of the term stands in for one |
| `--search-max-pages N` | Pages per (country, keyword) pair. 15 is ample; most pairs exhaust well before it |
| `--roles hr,tech_leadership` | Role families to keep. Default adds `executive`. `--roles any` keeps everyone |
| `--no-smtp` | Skip catch-all probing. Faster; rows land `inferred_*` instead of `verified` |
| `--no-guess` | Never apply the modal `first.last` format to a domain with no published address. Raises precision, cuts volume hard |
| `--overfetch 4` | Bank 4× the target before cutting. Higher = better final quality, longer run |
| `--concurrency 12` | More parallel companies. Per-host throttling still applies |
| `--max-person-pages 0` | Disable sitemap mining if it proves slow on your network |
| `--select-only` | Re-cut an existing checkpoint without crawling — free, instant |

`--select-only` is the one to reach for after a full run: it lets you retarget
(say 500 instead of 1000) or change the country ceiling without refetching
anything.

## 3. Verify before delivering

```bash
python tools/verify_leads.py output/leads.csv --expect 1000
```

Exit code 0 means every hard guarantee holds: no missing name or email, no
duplicate addresses, no role accounts (`info@`, `jobs@`) sitting in a person's
email column, and every row traceable to a source URL.

It also prints the status mix, role split and country spread. Those are
judgement calls, not pass/fail — but if `catch_all` dominates or one country is
80% of the list, you want to know that before someone else does.

## 4. Import to Google Sheets

`File → Import → Upload → Replace current sheet`. The CSV is UTF-8 with a BOM,
so European names import correctly without any encoding dance.

## Reading the output

| Column | Meaning |
|---|---|
| `email_status` | `published` (printed on their site) > `verified` (SMTP confirmed) > `inferred_high` (pattern from 2+ known addresses) > `inferred_medium` (pattern from 1) > `inferred_low` (**no anchor at all** — the modal `first.last` applied blind) > `unknown` (probe refused) > `catch_all` (domain accepts everything — unverifiable) |
| `email_confidence` | `high` / `medium` / `low` — set only for inferred rows |
| `person_role_family` | `hr` / `tech_leadership` / `executive` / `other` |
| `evidence_json` | Source URL per field. `inferred:first.last` means the address was derived, not found |

If the buyer wants only addresses that were actually observed, filter to
`email_status = published`. For a conservative send, drop `catch_all` — those
domains accept any address, so a bounce tells you nothing and a delivery
doesn't prove the mailbox exists.

## Shortfall

The run never pads. If it finds 840 qualifying leads it writes 840, prints
`SHORTFALL 160` and exits 2. Padding to 1000 with rows that fail the criteria
would make the other 840 untrustworthy.

If you hit a shortfall: raise `--overfetch`, add seed targets to the config, or
relax the target. Do not lower the bar on what counts as a lead.

## Troubleshooting

**Every seed target returns 0 listings.** The seed is the Bundesagentur für
Arbeit API. Its path has moved between versions more than once, and a stale path
answers `403 No match found for request` rather than 404 — which reads like an
auth failure and sends you hunting in the wrong place. The adapter probes four
known paths on startup and logs which one answered; if it logs that none did,
the service has moved again and `BASE_CANDIDATES` in
`src/job_scraper/adapters/arbeitsagentur.py` needs a new entry.

**Lots of `no_person_found` in the funnel.** Companies are reachable but no
person is being extracted. Usually means the sites in question use a layout the
team-page parser does not recognise. Capture one failing page and add a fixture
test before changing the parser.

**`company_error` climbing.** Check the log for the underlying exception. TLS
failures on older German hosts are common and mostly harmless at low rates.

**The list is heavily German.** Partly real — §5 TMG makes an Impressum legally
mandatory, so DACH genuinely has the highest person coverage in Europe. But
check the seed before accepting it: `--boards` alone is US-dominated (measured:
983 IT companies, 113 in the EU), because the public datasets those slugs come
from are US-centric. `--search-keywords` is the fix, since it asks each country
directly instead of filtering afterwards.

**Few HR contacts, mostly executives.** Company pages structurally favour
executives — an Impressum must name the Geschäftsführer, a team page shows
whoever the company wants seen. HR contacts come from `--search-keywords`
ads via the job-ad miner, so a run seeded only from `--boards` will be
executive-heavy: Greenhouse and Lever do not expose the recruiter block that
European ads print.

## What this does not do

Out of scope for this run, specified separately: the cross-run ledger, the
Friday cron, the adaptive geographic allocator, and writing to Google Sheets via
the API. This produces one CSV, once.

## Compliance note for whoever receives the list

The data is business-context personal data on EU data subjects, so GDPR applies.
The lawful basis for B2B prospecting is legitimate interest, which the design
supports — every field carries its source URL in `evidence_json`, only
professional-context fields are collected, and everything came from public pages.

The obligations that fall on the *sender*, not on this tool: a privacy notice at
first contact, an honest and working opt-out, and honouring objections. Worth
stating explicitly when handing the file over.
