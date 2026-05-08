# HarvestKit

> Universal job + business scraper. Python CLI engine + Chrome MV3 extension share a 60-field schema.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Chrome MV3](https://img.shields.io/badge/Chrome-MV3-green.svg)](https://developer.chrome.com/docs/extensions/mv3/intro/)

HarvestKit is a dual-mode scraper: a **Python CLI** for headless harvesting at scale, and a **Chrome MV3 extension** for one-click capture from any open tab. Both write to the same 60-column schema, so CSVs from one are drop-in for the other.

It is **API-first** — Greenhouse / Lever / Ashby / Workday / Personio / Recruitee / Workable / SmartRecruiters / Arbeitsagentur / jobs.ch hit native JSON endpoints when available. Everything else falls through a structured pipeline: JSON-LD `JobPosting` → microdata → OpenGraph → DOM heuristics → recruiter contact mining. A separate **general mode** scrapes Yelp-style business directories using `LocalBusiness` / `Restaurant` Schema.org.

---

## Why

Most scrapers fall over on three things:

1. **Rate-limit blast damage** — naive concurrency triggers WAF blocks (CloudFront 403s, captchas) and you lose 30–80 % of pages.
2. **Brittle URL handling** — SPAs redirect mid-extraction and you save the redirect target as the canonical URL (e.g. `/account/applications/` instead of the actual posting).
3. **Shallow extraction** — JSON-LD title + description, but salary / recruiter / sections / apply-URL all empty.

HarvestKit fixes all three: per-host token bucket throttle, multi-source canonical-URL resolution with junk-path filtering, and a section parser that walks JD HTML in EN/DE/FR/IT.

---

## Features

| Capability | Detail |
| --- | --- |
| **Shared 60-field schema** | CLI + extension write identical CSV columns |
| **10 ATS adapters** | Greenhouse, Lever, Ashby, Workday, Personio, Recruitee, Workable, SmartRecruiters, Arbeitsagentur, jobs.ch |
| **Deep-scrape pipeline** | Visit each posting, merge JSON-LD + microdata + OpenGraph + heuristics + recruiter |
| **JD section parser** | Responsibilities / requirements / benefits in EN, DE, FR, IT |
| **Recruiter mining** | Name (Frau/Herr/Mr/Mrs forms), title, phone (`+41 …`), email, LinkedIn |
| **Per-host throttle** | Token bucket — separate from global pacing — prevents WAF blocks |
| **WAF detection** | Identifies CloudFront 403s + captcha pages, backs off, never poisons cache |
| **SQLite HTTP cache** | Gzip-compressed, TTL-bounded, auto-purges block pages |
| **UA rotation** | Realistic Chrome / Firefox / Safari pool |
| **Playwright fallback** | Cookie banner dismissal + content expansion + JobPosting wait |
| **Browser extension** | MV3 side panel, IndexedDB persistence, retry-failed UI |
| **General mode** | Businesses / restaurants / clinics — same engine, `LocalBusiness` schema |
| **Exports** | CSV, JSON, NDJSON; Google Sheets / Notion / Slack hooks |

---

## Repo layout

```text
src/job_scraper/         Python job scraper engine + adapters
src/general_scraper/     Python general scraper (Yelp-style directories)
extension/               Chrome MV3 extension (TypeScript + React)
  app/src/content/       Content-script extractors + site adapters
  app/src/background/    Service worker + bulk crawl orchestrator
  app/src/sidepanel/     React UI (dashboard / library / runs / settings)
config.example.yaml      Default job-mode config
config.general.example.yaml  General-mode config (Yelp template)
config.jobsch.yaml       jobs.ch full-coverage config
run.py                   Entry — dispatches jobs/general by mode
```

---

## Local CLI — install

```bash
git clone https://github.com/BluOryn/HarvestKit.git
cd HarvestKit
```

### Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium     # optional — only if use_playwright: true
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium     # optional
```

---

## Local CLI — run

### Job scraper

```bash
# Default config.yaml
python run.py --confirm-permission

# Custom config (e.g. jobs.ch full coverage — ~25 min for 1900+ jobs)
python run.py --config config.jobsch.yaml

# Ad-hoc URLs (adapter auto-detected by hostname)
python run.py \
  --urls https://boards.greenhouse.io/example https://careers.example.com \
  --keywords "engineer,python" \
  --confirm-permission

# Skip deep-scrape (faster, fewer fields)
python run.py --config config.yaml --no-deep-scrape

# Disable HTTP cache (force fresh fetch)
python run.py --config config.yaml --no-cache

# Verbose logging
python run.py --config config.yaml --log-level DEBUG
```

### General scraper (businesses, places)

```bash
# Auto-detects mode: general from config
python run.py --config config.general.example.yaml --confirm-permission

# Force general mode regardless of mode field
python run.py --general --config config.general.example.yaml --confirm-permission
```

---

## Config knobs

```yaml
run:
  user_agent: "HarvestKitBot/1.0 (+contact@example.com)"
  delay_seconds: 1.0           # global pacing between requests
  obey_robots: true            # respect robots.txt
  confirm_permission: true     # required to run
  use_playwright: false        # fallback to Chromium for JS-heavy sites

  deep_scrape: true            # visit each posting for richer fields
  deep_concurrency: 4          # global parallelism
  deep_per_host_concurrency: 2 # max in-flight requests per host
  deep_per_host_delay_seconds: 1.0
  deep_max_retries: 2

  cache_enabled: true
  cache_ttl_seconds: 86400
  cache_path: ".cache/http_cache.sqlite"
  rotate_user_agents: true     # rotate realistic Chrome/Firefox/Safari UAs

keywords:
  include: ["engineer", "developer"]
  exclude: ["sales", "marketing"]
  sectors: ["software", "ai", "cloud"]

locations:
  include: ["Berlin", "Munich", "Remote"]
  allow_remote: true

targets:
  - name: example-greenhouse
    url: "https://boards.greenhouse.io/example"
    adapter: greenhouse        # auto | greenhouse | lever | ashby | jobs.ch | ...

exports:
  csv:
    enabled: true
    path: "output/jobs.csv"
```

---

## Extension — build

```bash
cd extension
npm install
node build.mjs                 # one-shot build → extension/dist/
node build.mjs --watch         # rebuild on file change
```

Outputs:

```text
extension/dist/content.js      66 kB   — content script (per-page extractor)
extension/dist/background.js  273 kB   — service worker (bulk crawl orchestrator)
extension/dist/sidepanel.js   584 kB   — React side-panel UI
extension/dist/sidepanel.html
extension/dist/sidepanel.css
```

## Extension — install (load unpacked)

1. Open `chrome://extensions/` (or `edge://extensions/`)
2. Toggle **Developer mode** (top-right)
3. Click **Load unpacked**
4. Select the `extension/` directory (the one containing `manifest.json`, **not** `dist/`)
5. Pin the toolbar icon — clicking it opens the side panel

## Extension — usage

### Job mode (default)

1. Open any job listing site (jobs.ch, LinkedIn, Indeed, Greenhouse boards, …)
2. Click toolbar icon → side panel opens
3. Click **🔥 Scrape Everything** — auto-paginates → snapshots cards → deep-scrapes each posting
4. Or use individual buttons: *Scrape this page* / *Snapshot list* / *Deep-scrape list*
5. View saved jobs in **Library** → export as CSV / JSON / NDJSON
6. **Runs** tab shows progress and lets you retry failed URLs

### General mode (businesses, places)

1. Toggle **General** in the top-right of the side panel
2. Open a Yelp / Google Maps / business-directory page
3. Click **🔥 Scrape ALL listings**
4. Records appear in **Library** with category / address / rating / phone / website / hours

---

## Shared 60-field schema

```text
title, company, company_logo, company_size, company_industry, company_website,
department, team, location, city, region, country, postal_code,
remote_type, employment_type, seniority,
salary_min, salary_max, salary_currency, salary_period, equity,
posted_date, valid_through, start_date, language,
description, responsibilities, requirements, qualifications, benefits,
tech_stack, skills, education_required, experience_years,
work_authorization, visa_sponsorship, relocation, travel_required,
recruiter_name, recruiter_title, recruiter_email, recruiter_phone, recruiter_linkedin,
hiring_manager, hiring_manager_email, application_email, application_phone,
apply_url, job_url, external_id, requisition_id,
source_ats, source_domain, raw_jsonld, confidence, scraped_at
```

General-mode schema (separate, 32 fields): `name, category, subcategories, description, address, street_address, city, region, country, postal_code, latitude, longitude, phone, email, website, social_links, rating, review_count, price_range, hours, image, tags, amenities, menu_url, reservation_url, is_claimed, external_id, source_url, source_listing_url, source_domain, raw_jsonld, scraped_at`.

---

## Adapters

| Adapter | Hosts | Type |
| --- | --- | --- |
| `greenhouse` | `boards.greenhouse.io`, `boards-api.greenhouse.io` | Public JSON API |
| `lever` | `jobs.lever.co`, `jobs.eu.lever.co` | Public JSON API |
| `ashby` | `jobs.ashbyhq.com` | Public JSON API |
| `workday` | `*.wdN.myworkdayjobs.com` | CXS API |
| `personio` | `*.jobs.personio.de`, `*.jobs.personio.com` | XML feed |
| `recruitee` | `*.recruitee.com` | Public JSON API |
| `workable` | `*.workable.com`, `apply.workable.com` | Widget + v3 API |
| `smartrecruiters` | `smartrecruiters.com` | Public JSON API |
| `arbeitsagentur` | `arbeitsagentur.de` | Bundesagentur REST API |
| `jobs.ch` | `www.jobs.ch` | Public JSON search API |
| `generic` | anything else | Sitemap + BFS crawl + JSON-LD extraction |

---

## Compliance

- Designed for sites you have permission to scrape.
- Respects `robots.txt` by default (`obey_robots: true`).
- Confirm permission via `confirm_permission: true` in config or `--confirm-permission` CLI flag.
- WAF / 403 / captcha detection backs off on block rather than hammering.
- Per-host throttle prevents accidental DoS.

Don't use this against sites that prohibit scraping. Don't bypass paywalls or login walls. Don't run it for spam or harassment.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Many 403 / WAF blocks | `deep_per_host_concurrency: 1`, `deep_per_host_delay_seconds: 2.0` |
| Empty descriptions | `use_playwright: true` for SPA sites |
| jobs.ch returns 0 jobs | Uses public JSON API directly — should work without Playwright |
| Cache poisoned with errors | Auto-purged when WAF detector trips; manual purge: `rm -r .cache/` |
| Extension shows wrong apply_url | Canonical URL filter rejects `/account/`, `/applications/`, `/recommendations/` |
| `playwright not installed` | `python -m playwright install chromium` |

---

## Development

```bash
# Python tests / lint
pip install -r requirements.txt
python -m pytest                 # if tests exist
python -m ruff check src/         # if ruff configured

# Extension watch + reload loop
cd extension && node build.mjs --watch
# After each rebuild, click reload icon on chrome://extensions/
```

---

## License

MIT — see [LICENSE](LICENSE).
