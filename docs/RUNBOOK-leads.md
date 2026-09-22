# Runbook — EU IT lead list

Produces a CSV of named HR and technical-leadership contacts at EU companies
that employ IT staff, each with an individual email address.

**This must run somewhere with unrestricted outbound HTTP.** The pipeline
fetches pages on thousands of distinct company domains. On a restricted network
roughly half of them fail silently, and the run produces a skewed list whose
gaps are invisible in the output.

## If you would rather not use a terminal

Everything in this runbook is also a button:

```bash
python run_panel.py          # or double-click HarvestKit on the desktop
```

That opens a local page with the same options as the flags below, a live view
of the run as it happens, and the finished file to look through and download.
It is the intended way to operate this on a machine that is not yours. The rest
of this document is the terminal equivalent, and the two do exactly the same
thing.

## Prerequisites

On a machine with nothing installed, run `scripts/install.ps1` (Windows) or
`scripts/install.sh` (macOS/Linux) once. It installs Python if there isn't one,
creates the environment, and makes the desktop shortcut.

Otherwise:

```bash
pip install -r requirements.txt     # includes dnspython, needed for MX checks
```

Then check the machine itself, which is the step people skip and then spend a
day paying for:

```bash
python tools/doctor.py
```

It reports Python, the dependencies, the browser, disk, the configs, the saved
leads, whether outbound port 25 is open for mailbox probing, and — the one that
predicts whether a run yields anything — whether real European company sites
can be read from here. Anything it marks FAIL will stop this machine producing
leads; anything WARN will make it produce less, and the line says why.

For just the network half:

```bash
python tools/check_egress.py
```

It fetches a fixed set of European company domains the way the engine does and
reports, per host, whether a page actually came back.

Do not substitute a `curl` loop that only looks at status codes. The version of
this runbook that did said "anything other than `000` is fine — 301, 403 and
429 all mean the host is reachable", and that is true about *reachability* and
useless for this run: a 403 is a bot wall, and a bot wall yields no people. The
check that matters is not "did the host answer" but "did it answer with a page".
The same curl loop also lies on Windows, where builds without HTTP/3 return
`000` with exit 43 for hosts that are perfectly reachable.

Read the summary line. `blocked` well above zero means configure egress —
`run.proxies`, and keep `run.use_impersonation: true` — before starting a long
run, because those companies will be counted as delivering nobody.

## 1. Smoke run (2–3 minutes)

Always do this first. It costs almost nothing and catches a dead seed API, a
broken config or an empty funnel before the long run.

```bash
python run_leads.py --config configs/leads/eu-it.yaml \
    --search-keywords configs/leads/keywords.txt --countries eu \
    --search-max-pages 1 \
    --target 10 --max-pages 3 --max-person-pages 2 --no-smtp \
    --checkpoint .cache/smoke.sqlite --output output/smoke.csv
```

**The seed flags are not optional.** `eu-it.yaml` ships with `targets: []`, and
every seeding branch in the CLI is gated on one of `--search-keywords`,
`--boards`, `--jobsch-pages` or `--arbeitnow-pages`. Without one the run
completes instantly having made zero HTTP requests, prints `funnel: {}` and
exits — and an operator on a fully bot-walled network gets byte-identical
output to one on a perfect network. The version of this command that omitted
them validated nothing at all. For Switzerland, substitute
`--jobsch-pages 2 --countries CH`.

Read the `seed:` lines. If the run reports `0 listings total`, stop — the seed
API is unreachable or has moved, and no amount of waiting will help. See
Troubleshooting.

Then read the `reachability:` line, which is the one that distinguishes a thin
market from a blocked machine. A run that harvests nothing now exits **4** and
refuses to export, rather than quietly re-delivering yesterday's file.

Then eyeball the output:

```bash
python tools/verify_leads.py output/smoke.csv
```

It fails a zero-row file by default. That matters: every other check in it is a
per-row aggregation, so an empty CSV used to satisfy all of them and report
"All hard guarantees hold."


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
| `email_status` | `published` (printed on their site) > `verified` (the domain rejects unknown recipients, so a wrong guess would have bounced — the address itself is still derived) > `inferred_high` (pattern from 2+ known addresses) > `inferred_medium` (pattern from 1) > `inferred_low` (**no anchor at all** — the modal `first.last` applied blind) > `unknown` (probe refused) > `catch_all` (domain accepts everything — unverifiable) |
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

**Lots of `no_person_found` in the funnel.** Now, and only now, this means what
it says: the site answered and named nobody the parser recognised. Capture one
failing page and add a fixture test before changing the parser.

Check `blocked_no_pages_seen` first, though. That is the separate counter for
companies whose pages we never saw, and the run prints a `reachability:` line
summarising it. Until this was split out, both landed in `no_person_found`, so
a run that was simply being refused looked exactly like a parser problem — and
this paragraph used to send you to the parser, which was innocent. If
`blocked_no_pages_seen` is a meaningful share of the funnel, the fix is egress,
not extraction:

- confirm `run.use_impersonation: true` and that `curl_cffi` is installed;
  without it the run logs a warning at startup and every TLS-fingerprinting site
  stays shut
- configure `run.proxies` — `python tools/proxy_sources.py --print-setup`
- for the hardest sites, `run.use_stealth_browser: true`

**`unreachable` climbing.** DNS failures and dead domains, usually from
`guess_domains`. Harmless in small numbers; a large share means domain
resolution is guessing badly for that country.

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

---

# Swiss IT lead list — jobs.ch

The Swiss variant of the run above. Same pipeline, different seed.

## The filter

Everything downstream is decided by one URL, shipped as the default of
`--jobsch-url` and defined in `src/leadgen/seed/jobsch.py`:

```
https://www.jobs.ch/en/vacancies/
  ?category=106&category=146&category=156&category=167
  &employment-type=1&employment-type=2&employment-type=4&employment-type=5
  &publication-date=30&term=
```

| Part | Meaning |
|---|---|
| `category=106` | Information technology / Telecom. |
| `category=146` | Engineering / Technical |
| `category=156` | Management / Consulting |
| `category=167` | Electronics / Electrotechnics |
| `employment-type=1,2,4,5` | Permanent and fixed-term staff contracts — apprenticeships and internships are deliberately excluded |
| `publication-date=30` | Posted in the last 30 days |
| `term=` | No keyword: the categories do the targeting |

The SSR HTML at `/en/vacancies/` honours this filter; the public JSON API at
`/api/v1/public/search` ignores it entirely and returns the whole corpus, which
is why the seed reads HTML. At the time of writing the filter reports ~1,440
matching jobs, 22 per page, so ~35 pages covers it.

Change the sector by changing the URL, not the code:

```bash
python run_leads.py --config configs/leads/swiss-it.yaml \
    --jobsch-url 'https://www.jobs.ch/en/vacancies/?category=106&publication-date=7' \
    --jobsch-pages 40 --countries CH --target 100
```

## Run it

```bash
python run_leads.py --config configs/leads/swiss-it.yaml \
    --jobsch-pages 35 --countries CH --target 200 \
    --concurrency 12 --overfetch 8 2>&1 | tee output/swiss.log
```

Drop `--no-smtp` only where outbound port 25 is open; most consumer and cloud
networks block it, and the run is faster and no less honest without it.

## Why this seed beats the others for Switzerland

- **The employer's own domain arrives free.** `hiringOrganization.sameAs` in the
  posting carries the company website for 83% of employers (measured over 52).
  Domain resolution is the slowest and most lossy step in the pipeline and this
  skips it outright.
- **Swiss ads name a human.** 36% of ads print a contact with a direct phone
  ("Fragen zur Funktion — Philipp Klett, Leiter Data Management, +41 …"), which
  `person.jobad` reads straight out of the description. ATS feeds do not.
- **No ad carries an e-mail.** jobs.ch masks them behind an "E-Mail schreiben"
  link, so 0% of ads yield an address. Every address therefore comes from the
  company site — the Impressum, the Geschäftsleitung page, the team page — which
  is why `--max-pages` matters more here than on the German run.

## Measured yield

One run over 12 of the ~35 pages: 522 postings -> 143 companies -> 2,276 people
found -> **188 delivered rows** (53% executive, 40% HR, 7% tech leadership),
every row with a name, an address, a country and a source URL. A full 35-page
pass is roughly 3x that, so 100+/day sits inside a single daily run.

Per-company coverage on that run: **80% of companies yielded at least one named
person, 43% yielded someone in a target family.**

## Getting every name, not just the target families

The three target families are a *filter*, not the ceiling. The same crawl banked
2,266 people; the cut discarded 2,071 of them purely for role. To keep everyone:

```bash
python run_leads.py --config configs/leads/swiss-it.yaml     --select-only --roles any --target 2000     --checkpoint .cache/swiss.sqlite --output output/all-people.csv
```

`--select-only` re-cuts the existing checkpoint, so this costs no requests and
runs instantly. On the run above it produced **2,000 rows**.

## Where the remaining coverage goes

29 of 143 companies named nobody anywhere on their site. That is not a parsing
failure — consumer brands (Victorinox, FREITAG) and large groups simply do not
publish staff. Reaching those needs a different source, not a better parser,
which is what `--register` below is for.

Three things buy coverage inside the current design, in order of effect:

| Lever | Why |
|---|---|
| `--max-pages 10` or higher | Each guessed path is one more chance at an Impressum or team page |
| `--max-nav-pages` (default 6) | Follows the site's *own* navigation — this is what took coverage from 61% to 80%, because a guessed path only finds a layout somebody anticipated |
| `--max-person-pages 8` | Per-person pages listed in the site's sitemap, known to exist |

## The commercial register — for companies that publish no staff at all

Swiss law requires every registered company to file its board, its managing
officers and everyone holding signature authority, and to publish every change
in the Swiss Official Gazette of Commerce. That record names exactly the people
this list targets, and it exists for companies whose own website names nobody.

`--register` consults it, and only for those companies:

```bash
python run_leads.py --config configs/leads/swiss-it.yaml \
    --jobsch-pages 40 --countries CH --target 100 --register \
    --checkpoint .cache/swiss.sqlite --output output/swiss-leads.csv
```

### Setting it up

The Zefix Public REST API is free but credentialled. Register at
<https://www.zefix.admin.ch/en/search/entity/welcome>, then:

```bash
export ZEFIX_USER=your-account
export ZEFIX_PASSWORD=your-password
```

Without both variables `--register` logs a warning and does nothing — it does
not fall back to scraping the public site, which disallows crawlers in
robots.txt. Holding credentials *is* the authorisation, which is why the run
exempts that one host from robots and no other.

### What it will and will not give you

| | |
|---|---|
| Names | Board members, directors, managing officers, authorised signatories |
| Roles | `Präsident des Verwaltungsrates`, `Direktor`, `Geschäftsführer`, `directeur` — all classify as **executive** |
| Emails | None. The register publishes no addresses, so these rows depend on pattern inference like any other |
| Coverage | Only companies whose registered name matches exactly |

The match is deliberately strict — whole name, after folding away accents,
punctuation and legal form. `Deloitte` matches `Deloitte AG` and
`Elektro Material` matches `ELEKTRO-MATERIAL AG`, but `Kistler` does **not**
match the sole trader `Andy Kistler`, and `FREITAG` does not match
`FREITAGS AG`. A near miss is a different company, and a lead filed under the
wrong company is worse than no lead: nothing downstream can tell it is wrong.

Departures are excluded. One publication announces arrivals and departures in
the same paragraph, and the parser cuts the `Ausgeschiedene Personen` /
`Personnes radiées` section away before reading anything.

### Attribution

Zefix data is open government data under
[opendata.swiss "Open use. Must provide the source."](https://opendata.swiss/en/terms-of-use#terms_by).
Every register-sourced row carries the entity's Zefix detail URL in
`source_person_url`; keep that column when you pass the list on.
