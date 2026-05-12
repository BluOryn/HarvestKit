# HarvestKit

> Universal scraper — works on **any website** (job boards, business directories, listings). Python CLI + Chrome extension share the same 60-field schema.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Chrome MV3](https://img.shields.io/badge/Chrome-MV3-green.svg)](https://developer.chrome.com/docs/extensions/mv3/intro/)

HarvestKit harvests structured data from any site. It tries every extraction strategy in order until one works:

1. **Native API** (Greenhouse / Lever / Ashby / Workday / Personio / Recruitee / Workable / SmartRecruiters / Arbeitsagentur / jobs.ch / finn / NAV / karrierestart)
2. **Schema.org JSON-LD** (JobPosting / LocalBusiness / Restaurant / Product)
3. **Microdata + OpenGraph** tags
4. **Universal smart-DOM** (label/value pairs, dl/dt/dd, definition lists, semantic classes)
5. **HR contact mining** (multi-lang regex EN/DE/FR/IT/NO — phone, email, recruiter name + title)
6. **LLM-fallback** (Anthropic Haiku auto-learns CSS selectors for unknown hosts, caches them — covers brand-new sites with zero adapter code)

A Chrome MV3 extension piggybacks on the real browser session to bypass DataDome / Cloudflare / PerimeterX bot walls (Yelp, LinkedIn) that defeat headless scraping.

---

## Quick Start (5 minutes)

### 1. Install Python 3.10+

- **Windows**: [python.org installer](https://www.python.org/downloads/) — tick "Add to PATH"
- **macOS**: `brew install python@3.11`
- **Linux**: `sudo apt install python3.11 python3.11-venv`

### 2. Clone + install

```bash
git clone https://github.com/BluOryn/HarvestKit.git
cd HarvestKit
python -m venv .venv
```

**Windows (PowerShell):**
```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux:**
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. (Optional) Enable LLM-fallback for any unknown site

```bash
# Get a key at https://console.anthropic.com/
# Then set as env var:

# Windows (PowerShell)
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or paste into config:
```yaml
run:
  llm_fallback_enabled: true
  llm_api_key: "sk-ant-..."          # or leave blank → reads from env
  llm_monthly_budget_usd: 5.0        # hard cap; LLM auto-disables when exceeded
```

> Cost: ~$0.001 per **new** host. Once HarvestKit learns selectors for a host, they're cached in `.cache/llm_selectors.sqlite` and reused free forever.

### 4. (Optional) Playwright for JS-heavy SPA sites

```bash
python -m playwright install chromium
```

Then in config:
```yaml
run:
  use_playwright: true
```

### 5. Run

```bash
# Scrape using a config file
python run.py --config config.example.yaml

# Scrape ad-hoc URLs (adapter auto-detected by hostname)
python run.py --urls https://boards.greenhouse.io/example https://anyrandomsite.com/jobs

# Skip deep-scrape (faster, fewer fields)
python run.py --config config.example.yaml --no-deep-scrape

# Force fresh fetch (ignore cache)
python run.py --config config.example.yaml --no-cache
```

Output → `output/jobs.csv` (or whatever `exports.csv.path` says).

---

## Will it work on my site?

| Site type | Works? | What you need to do |
| --- | --- | --- |
| Greenhouse / Lever / Ashby / Workday / Workable / SmartRecruiters / Personio / Recruitee / Arbeitsagentur / jobs.ch / finn.no / NAV / karrierestart | Yes, out-of-box | Just add URL to `targets:` in config |
| Any site with **Schema.org JSON-LD** | Yes, out-of-box | Add URL — works automatically |
| Static HTML w/ semantic labels (dt/dd, label-value pairs) | Yes, out-of-box | Add URL — universal extractor handles it |
| **Brand-new site, no API, no schema** | Yes, with LLM-fallback | Enable `llm_fallback_enabled: true` — Haiku learns selectors on first hit, caches forever |
| JS-only SPA (no server-side HTML) | Yes, with Playwright | `use_playwright: true` + `python -m playwright install chromium` |
| DataDome / Cloudflare / PerimeterX bot walls (Yelp, LinkedIn) | **No headless** | Use Chrome extension — runs in your real browser session |
| Auth-walled content (login required) | No | Out of scope — log in via extension, then click scrape |

---

## What you don't have to do

- ❌ Write adapter code for new sites — LLM-fallback handles it
- ❌ Hand-craft CSS selectors — auto-learned and cached
- ❌ Configure 60 fields per site — schema is shared
- ❌ Handle pagination — universal anchor-cluster detector finds next-page links
- ❌ Worry about rate-limits — per-host token bucket throttles automatically
- ❌ Manage WAF retries — auto-backoff on 403/captcha

---

## Config — full reference

```yaml
run:
  # ---- Identity ----
  user_agent: "HarvestKitBot/1.0 (+contact@example.com)"
  rotate_user_agents: true       # rotate Chrome/Firefox/Safari pool
  obey_robots: false              # respect robots.txt; set true if required
  confirm_permission: true        # required to run — acknowledge you have permission

  # ---- Pacing ----
  delay_seconds: 0.3              # min spacing between requests
  max_pages: 10                   # listing pages to walk per target

  # ---- Deep-scrape (visit each posting) ----
  deep_scrape: true
  deep_concurrency: 6             # global parallel workers
  deep_per_host_concurrency: 2    # max concurrent requests per host
  deep_per_host_delay_seconds: 0.5
  deep_max_retries: 2

  # ---- Cache ----
  cache_enabled: true
  cache_ttl_seconds: 86400
  cache_path: ".cache/http_cache.sqlite"

  # ---- Proxies (optional, for high volume) ----
  proxies:
    - "http://user:pass@host:port"
    - "socks5://host:port"
  proxy_rotation: round_robin     # or "random"
  proxy_max_failures: 3
  proxy_cooldown_seconds: 300

  # ---- Playwright fallback (JS-heavy sites) ----
  use_playwright: false

  # ---- LLM auto-adapter (Anthropic Haiku) ----
  llm_fallback_enabled: false     # set true to enable
  llm_api_key: ""                  # or read from $ANTHROPIC_API_KEY
  llm_model: "claude-haiku-4-5-20251001"
  llm_min_fields: 5                # trigger LLM when heuristics fill < N fields
  llm_max_html_chars: 60000        # truncate HTML before sending
  llm_cache_path: ".cache/llm_selectors.sqlite"
  llm_monthly_budget_usd: 5.0      # hard cap

keywords:
  include: ["engineer", "developer"]
  exclude: ["sales", "marketing"]

locations:
  include: ["Berlin", "Munich", "Remote"]
  allow_remote: true

targets:
  - name: my-site
    url: "https://example.com/jobs"
    adapter: auto                  # auto | greenhouse | lever | generic | ...

exports:
  csv:
    enabled: true
    path: "output/jobs.csv"
  gsheets:
    enabled: false
    service_account_json: "creds.json"
    spreadsheet_id: ""
  notion:
    enabled: false
    token: ""
    database_id: ""
  slack:
    enabled: false
    webhook_url: ""
```

---

## Chrome Extension — install + use

### Build (one-time)

```bash
cd extension
npm install
node build.mjs                 # one-shot build → extension/dist/
node build.mjs --watch         # rebuild on file change
```

### Install (load unpacked)

1. Open `chrome://extensions/` (or `edge://extensions/`)
2. Toggle **Developer mode** (top-right)
3. Click **Load unpacked**
4. Select the `extension/` directory (the one containing `manifest.json`)
5. Pin toolbar icon — clicking opens side panel

### Use

**Job mode (default):**
1. Open any job site (Yelp, LinkedIn, finn.no, jobs.ch, indeed, …)
2. Click toolbar icon → side panel opens
3. Click **🔥 Scrape Everything** — auto-paginates → snapshots cards → deep-scrapes each
4. View results in **Library** → export CSV / JSON / NDJSON
5. **Runs** tab shows progress + retry failures

**General mode (businesses, places):**
1. Toggle **General** in side panel
2. Open Yelp / Google Maps / Tripadvisor / Yellowpages page
3. Click **🔥 Scrape ALL listings**

The extension runs in your real browser, so it bypasses DataDome / Cloudflare / PerimeterX. Headless CLI cannot do this.

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
source_ats, source_domain, raw_jsonld, confidence, scraped_at, extras_json
```

`extras_json` catches anything outside the 60 fields — site-specific labels stash here automatically (e.g. `nav_arbeidstid`, `karrierestart_tiltredelse`).

General-mode schema (32 fields): `name, category, subcategories, description, address, street_address, city, region, country, postal_code, latitude, longitude, phone, email, website, social_links, rating, review_count, price_range, hours, image, tags, amenities, menu_url, reservation_url, is_claimed, external_id, source_url, source_listing_url, source_domain, raw_jsonld, scraped_at`.

---

## Adapters

| Adapter | Hosts | Type |
| --- | --- | --- |
| `greenhouse` | `boards.greenhouse.io` | Public JSON API |
| `lever` | `jobs.lever.co` | Public JSON API |
| `ashby` | `jobs.ashbyhq.com` | Public JSON API |
| `workday` | `*.wdN.myworkdayjobs.com` | CXS API |
| `personio` | `*.jobs.personio.de/com` | XML feed |
| `recruitee` | `*.recruitee.com` | Public JSON API |
| `workable` | `*.workable.com` | Widget + v3 API |
| `smartrecruiters` | `smartrecruiters.com` | Public JSON API |
| `arbeitsagentur` | `arbeitsagentur.de` | Bundesagentur REST API |
| `jobs.ch` | `jobs.ch` | Public JSON search API |
| `finn.no` | `finn.no/job/` | SSR HTML harvest |
| `nav.no` | `arbeidsplassen.nav.no` | SSR HTML harvest |
| `karrierestart.no` | `karrierestart.no` | SSR HTML harvest |
| `generic` | **anything else** | Sitemap + BFS crawl + JSON-LD + universal extractor + LLM-fallback |

For unknown sites, the `generic` adapter clusters anchors by URL pattern (e.g. `/job/`, `/biz/`, `/listing/`, `/ad/`), follows each, and runs the full extraction stack including LLM-fallback if enabled.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Many 403 / WAF blocks | Lower `deep_per_host_concurrency: 1`, raise `deep_per_host_delay_seconds: 2.0` |
| Empty descriptions on SPA | Set `use_playwright: true` + `python -m playwright install chromium` |
| New site returns 0 fields | Enable `llm_fallback_enabled: true` + set `ANTHROPIC_API_KEY` |
| LLM cache stale (site changed layout) | Delete `.cache/llm_selectors.sqlite` — will re-learn next run |
| LLM budget exhausted | Raise `llm_monthly_budget_usd` or wait until next month (resets) |
| Cache poisoned with errors | `rm -r .cache/` — auto-rebuilds |
| Cloudflare / DataDome blocks (Yelp, LinkedIn) | Use extension instead of CLI — runs in real browser |
| `playwright not installed` | `python -m playwright install chromium` |
| Extension shows wrong apply_url | Canonical URL filter rejects `/account/`, `/applications/`, `/recommendations/` |

---

## Compliance

- Designed for sites you have permission to scrape.
- Default `obey_robots: true` respects `robots.txt`.
- `confirm_permission: true` (config) or `--confirm-permission` (CLI) required to run.
- WAF / 403 / captcha detection auto-backs off — never hammers.
- Per-host throttle prevents accidental DoS.

Don't use against sites that prohibit scraping. Don't bypass paywalls / login walls. Don't run for spam / harassment.

---

## Repo layout

```text
src/job_scraper/         Python engine (adapters, extract, universal, deep_scrape, llm_adapter)
src/general_scraper/     General-mode (businesses, places)
extension/               Chrome MV3 extension (TypeScript + React)
  app/src/content/       Per-page extractor + site adapters
  app/src/background/    Service worker + bulk crawl orchestrator
  app/src/sidepanel/     React UI (dashboard, library, runs, settings)
config.example.yaml      Default job-mode config
config.general.example.yaml  General-mode (Yelp template)
config.norway-big.yaml   Norway IT — 87 targets fan-out
run.py                   Entry — dispatches jobs/general by mode
```

---

## Development

```bash
# Python
pip install -r requirements.txt
python -m pytest

# Extension watch + reload
cd extension && node build.mjs --watch
# Then click reload icon on chrome://extensions/ after each rebuild
```

---

## License

MIT — see [LICENSE](LICENSE).
