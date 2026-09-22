# HarvestKit

> Universal scraper — works on **any website** (job boards, business directories, listings). Python CLI + Chrome extension share one schema and one `id` algorithm.

[![CI](https://github.com/BluOryn/HarvestKit/actions/workflows/ci.yml/badge.svg)](https://github.com/BluOryn/HarvestKit/actions/workflows/ci.yml)
[![CodeQL](https://github.com/BluOryn/HarvestKit/actions/workflows/codeql.yml/badge.svg)](https://github.com/BluOryn/HarvestKit/actions/workflows/codeql.yml)
[![Licence: Proprietary](https://img.shields.io/badge/licence-proprietary-red.svg)](LICENSE)
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

> **Running the daily lead harvest?** Start at
> **[docs/ONBOARDING.md](docs/ONBOARDING.md)** — clone, one setup command, one
> command a day. The rest of this README is the general scraper.

## Quick Start

### The short version, for a machine with nothing on it

No Python, no Git, no terminal. One command, then one icon:

**Windows** — right-click `scripts/install.ps1` → *Run with PowerShell*:

```powershell
.\scripts\install.ps1
```

**macOS / Linux:**

```bash
./scripts/install.sh
```

It finds a Python or installs one for your user account (no administrator
rights), builds the environment, installs the stealth browser, puts a
**HarvestKit** shortcut on the desktop, and then proves the install by reading
a dozen real European websites — because "pip install succeeded" and "this
laptop can scrape" are different claims.

After that, the whole tool is that icon. It opens a local page where the
options are controls, the run streams live, and the finished CSV is there to
look through and download. Nobody has to type a command:

```bash
python run_panel.py          # what the icon runs
```

Check a machine at any time:

```bash
python tools/doctor.py       # Python, deps, browser, disk, configs, network
```

### The long version

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
# Scrape using a bundled config (see configs/)
python run.py --config example

# Bare names resolve under configs/, configs/regions/ and configs/sites/
python run.py --config norway-big
python run.py --config jobsch

# Scrape ad-hoc URLs (adapter auto-detected by hostname)
python run.py --urls https://boards.greenhouse.io/example https://anyrandomsite.com/jobs

# Skip deep-scrape (faster, fewer fields)
python run.py --config example --no-deep-scrape

# Force fresh fetch (ignore cache)
python run.py --config example --no-cache

# Override the CSV destination
python run.py --config example -o output/today.csv
```

Run `make configs` to list every bundled config.

Output → `output/jobs.csv` (or whatever `exports.csv.path` says).

---

## The control panel

`python run_panel.py` (or the desktop shortcut) opens
**http://127.0.0.1:8787/** — a local page with four tabs:

| Tab | What it is for |
|---|---|
| **Run** | Every option the CLI takes, as labelled controls with the reasoning next to them. Start a harvest, watch listings/companies/people count up live, stop it if you need to. |
| **Results** | Every CSV this machine has produced, newest first. Open one to read through it before it goes anywhere; download it with a click. |
| **Network & proxies** | Paste proxies in, save them, check whether this machine can actually read European sites, collect and test free proxies, run the full health check. |
| **Log** | The run's output as it happens, with the lines that matter coloured. |

It remembers what you last chose, so a daily run is: open it, press Start.

Some deliberate constraints, because a web UI that starts processes is easy to
get wrong:

- It listens on **127.0.0.1** only. `--host` can change that, and the page says
  loudly what you are doing if you do.
- Every request carries a token generated at startup, so no other page in the
  same browser can drive it, and a Host-header check closes the DNS-rebinding
  route to that token.
- **There is no command box.** It runs a fixed catalogue of commands with typed,
  range-checked options (`src/harvestkit_ui/jobs.py`). A submitted value can
  never become a flag, a path outside the project, or a shell string. A text box
  would be remote code execution the first time the port was reachable, and
  "it only listens on localhost" is one firewall rule away from untrue.

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
- ❌ Configure fields per site — schema is shared
- ❌ Handle pagination — universal anchor-cluster detector finds next-page links
- ❌ Worry about rate-limits — per-host token bucket throttles automatically
- ❌ Manage WAF retries — auto-backoff on 403/captcha

---

## Config — full reference

```yaml
run:
  # ---- Identity ----
  user_agent: "HarvestKitBot/1.0 (+https://github.com/BluOryn/HarvestKit)"
  rotate_user_agents: true       # pick a browser identity per host (stable per host)
  obey_robots: false              # default. See docs/OPERATOR-TERMS.md
  robots_unreadable_is_allowed: true   # a robots.txt a WAF hid is not a policy
  confirm_permission: true        # required to run — acknowledge you have permission

  # ---- Transport ladder (cheapest rung first) ----
  # Rung 1: a real browser TLS/HTTP2 fingerprint via curl_cffi. The single
  # highest-yield anti-blocking setting there is — `requests` sends a
  # ClientHello no browser has ever sent, and that is what Akamai and
  # Cloudflare score first. Leave this on.
  use_impersonation: true
  escalate_on_block: true         # retry a blocked page on a stronger rung
  # Rung 2: a real browser, for JS-only pages and interactive challenges.
  # ~100 MB and a second or two per page, so it stays off until asked for.
  #   pip install patchright && patchright install chromium
  use_stealth_browser: false
  stealth_browser_headless: true
  stealth_browser_concurrency: 2
  # Which rung worked per domain, so the cost is paid once, not per URL.
  transport_memory_path: ".cache/transport_memory.sqlite"

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
  proxies_file: ""                 # newline-delimited file, merged with the list above
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

## Shared schema

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

The CSV wraps those 56 fields in an `id` column (the fingerprint) plus four book-keeping columns: `source, keywords_matched, saved_at, extras_json` — 61 columns in all.

`extras_json` catches anything outside the schema fields — site-specific labels stash here automatically (e.g. `nav_arbeidstid`, `karrierestart_tiltredelse`).

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
| `finn.no` | `finn.no/job/` | SSR HTML harvest + JSON-LD JobPosting |
| `nav.no` | `arbeidsplassen.nav.no` | SSR HTML + `__next_f` adData payload |
| `karrierestart.no` | `karrierestart.no` | SSR HTML harvest + `.fact-card` label mining |
| `jobbsafari.no` | `jobbsafari.no` | SSR HTML + `__NEXT_DATA__` `jobEntry` payload |
| `generic` | **anything else** | Sitemap + BFS crawl + JSON-LD + universal extractor + LLM-fallback |

For unknown sites, the `generic` adapter clusters anchors by URL pattern (e.g. `/job/`, `/biz/`, `/listing/`, `/ad/`), follows each, and runs the full extraction stack including LLM-fallback if enabled.

---

## Egress: what to do when sites refuse you

The honest ranking, all free, best first — `python tools/proxy_sources.py --print-setup`
prints it with the exact commands:

1. **Don't get flagged.** Keep `use_impersonation: true` (real Chrome TLS
   fingerprint), `concurrency` at 8 or below, a per-host delay. Measured over
   twelve European employer domains: plain `requests` reads 6 of 12; the same
   machine with impersonation reads 9. No proxy involved.
2. **Your own employees' machines.** If this is deployed on several laptops you
   already have what residential-proxy vendors sell. Split by country rather
   than routing: `--countries DE,AT` on one, `--countries FR,IT,ES` on another.
   Each keeps its own checkpoint, so nothing is crawled twice.
3. **An IPv6 /64 you already have.** Any VPS with routed IPv6 gives you 18
   quintillion source addresses. `bind://2a01:4f8:c17:1234::a1` in `run.proxies`
   opens ordinary direct connections *from* that address — not a proxy, so
   nobody else carries the traffic.
4. **A free-tier cloud VM** (Oracle Always Free never expires) reached over
   `ssh -N -D 127.0.0.1:1080`, then `socks5h://127.0.0.1:1080`.
5. **Cloudflare WARP** in proxy mode, for a consumer-grade address.
6. **Tor**, then public proxy lists — last, and `--check` is not optional.

`require_proxy: true` makes a run refuse to fetch rather than fall back to a
direct connection when every proxy is cooling down. Set it on any machine whose
own address must not be seen.

What none of it fixes: a handful of sites demand residential IP reputation and
will stay closed. Those are now reported under `blocked_no_pages_seen` instead
of being counted as companies that named nobody.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Many 403 / WAF blocks | First run `python tools/check_egress.py` — it says which hosts are blocked rather than merely unresponsive. Confirm `curl_cffi` is installed and `use_impersonation: true`; then configure `run.proxies` (`python tools/proxy_sources.py --print-setup`); then lower `deep_per_host_concurrency: 1` and raise `deep_per_host_delay_seconds: 2.0` |
| Lead run reports lots of `no_person_found` | Check `blocked_no_pages_seen` and the `reachability:` line first. If that is a meaningful share, the problem is egress, not the parser — those sites were never read |
| `impersonation rung: MISSING` at startup | `pip install curl_cffi`. Without it every TLS-fingerprinting site stays blocked |
| A site needs JS or solves a challenge | `use_stealth_browser: true`, then `pip install patchright && patchright install chromium` |
| `socks5://` proxy does nothing | `pip install PySocks` — `requests` needs it for SOCKS support |
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
  app/src/lib/           Shared schema, fingerprint, CSV export
  app/src/sidepanel/     React UI (dashboard, library, runs, settings)
  tests/                 node --test suite (runs against app/src)
configs/                 All YAML configs (see configs/README.md)
  example.yaml           Default job-mode config
  general.example.yaml   General-mode (Yelp template)
  regions/               Multi-target country fan-outs (germany, norway-*)
  sites/                 Single-site smoke configs (finn, jobsch, nav, …)
tests/                   pytest suite (no network)
tools/                   Cache salvage + proxy fetcher
run.py                   Entry — dispatches jobs/general by mode
```

---

## Development

```bash
# Python
pip install -e ".[dev]"
make check                 # ruff + black + mypy + pytest

# Extension
cd extension && npm ci
npm run typecheck && npm test && npm run build
npm run watch              # rebuild on change; then hit reload on chrome://extensions/
```

`make configs` lists the bundled configs; `make smoke` runs a one-page scrape.

---

## Licence

Proprietary — see [LICENSE](LICENSE). This is not open-source software. Access
is granted per person, is revocable, and does not include the right to copy,
publish, or reuse it. Anyone running it should read
[docs/OPERATOR-TERMS.md](docs/OPERATOR-TERMS.md) first: the lead lists it
produces are personal data and are not the operator's to keep or forward.
