# Architecture

HarvestKit ships two products that share the same 56-field `JobListing` schema:

1. **Python CLI engine** (`src/job_scraper/`) — runs headless, scheduled, in CI / containers.
2. **Chrome MV3 extension** (`extension/`) — runs in the user's real browser, uses the same field schema and most of the same extraction rules ported to TypeScript.

This doc covers the CLI engine. The extension mirror lives in `extension/app/src/content/` and follows the same pipeline shape.

---

## High-level flow

```text
CLI entry (run.py)
    │
    ▼
load_config(YAML)
    │
    ▼
for target in config.targets:
    │
    ▼
get_adapter(target)            ← hostname → adapter (greenhouse/lever/finn/nav/...)
    │
    ▼
adapter.fetch_jobs(target, config.run, http)   ← listing harvest, returns stub JobListings
    │
    ▼
deep_scrape_jobs(listings, http, deep_cfg, playwright_fetcher)
    │
    ├── Per listing:
    │   ├── http.get(detail_url)               ← cached SQLite store
    │   ├── (optional) Playwright re-fetch     ← for JS-rendered sites (KS, webcruiter)
    │   ├── extract_job_from_page(html)        ← JSON-LD + microdata + OpenGraph + heuristics
    │   ├── universal_extract(html)            ← smart-DOM (dl/dt/dd, labels, site-specific)
    │   ├── mine_contacts(html)                ← phone / email / recruiter name+title
    │   └── (optional) llm_enrich(html)        ← Anthropic Haiku selector learner
    │
    ▼
filter (keywords, locations) → dedupe → exports (CSV / Sheets / Notion / Slack)
```

---

## Getting the page at all

Before any extraction can happen the page has to arrive, and on European
company domains that is the step that was failing. Fetching is a ladder, in
`src/job_scraper/transport.py`, tried cheapest-first:

| Rung | Transport | When it earns its cost |
| --- | --- | --- |
| 0 | `requests` | APIs, ATS feeds, most server-rendered HTML |
| 1 | `curl_cffi` | Sites that fingerprint the TLS ClientHello — Akamai, Cloudflare, Imperva. No header change reaches these |
| 2 | Patchright / Playwright | JS-only pages, and challenges that must be executed |

Three things make this workable rather than three times the cost:

- **Per-domain memory** (`TransportMemory`, SQLite). The rung that worked is
  recorded and tried first next time, so a walled domain pays the discovery
  cost once per run rather than once per URL.
- **`classify()`** decides what a response actually was: OK, BLOCKED,
  RATE_LIMITED, NOT_FOUND, SERVER_ERROR. Escalation only happens for BLOCKED —
  a 404 is absent for every client, and a DNS failure will not resolve for a
  fancier one. Crucially it also catches a challenge page served with HTTP 200,
  which previously passed downstream as content.
- **One coherent identity per host** (`src/job_scraper/identity.py`). The UA,
  the client hints, the TLS profile and the Accept-Language all describe the
  same browser, and that browser does not change between two requests to the
  same host. The identity is salted with the proxy, so a new egress IP also
  presents a new browser.

`robots.txt` is fetched through this same ladder, so it travels the configured
proxy and presents the same identity as everything else. Fetching it with bare
`urllib` meant the first request to every host went out from the real egress IP
looking like a bot — an IP leak, and the request in the run most likely to be
refused.

`FetchResponse` carries the reason a page did not arrive. `HttpClient.fetch()`
returns it; `HttpClient.get()` still flattens to "text or None" for the callers
that only want a body, but nothing new should use it: collapsing a bot wall and
an empty page into the same `None` is what made a blocked run look like a thin
market.

---

## Strategy stack (most → least authoritative)

| Strategy | Source | Coverage when present |
| --- | --- | --- |
| **Native API** | Public JSON endpoints (Greenhouse, Lever, Ashby, Workday, …) | 100% across most fields |
| **JSON-LD JobPosting** | `<script type="application/ld+json">` | 90%+ title / company / dates / salary |
| **`__NEXT_DATA__` / `__next_f`** | Next.js SSR payload (jobbsafari, NAV) | 100% structured fields when site uses it |
| **Microdata** | `itemtype=schema.org/JobPosting` | Varies |
| **OpenGraph + meta** | `<meta property="og:*">` | Title / image / fallback |
| **Universal smart-DOM** | dl/dt/dd, semantic classes, label-value text | 60–80% depending on site |
| **Site-specific hooks** | `_nav_no_specific` / `_karrierestart_specific` / `_jobbsafari_specific` | Plugs site-shaped gaps |
| **HR contact miner** | Multi-lang regex over body text | 70–95% phone / 30–50% email |
| **LLM-fallback** | Anthropic Haiku learns CSS selectors per host | Covers unknown sites; cached forever |

Each strategy runs in sequence; later ones only fill what earlier ones missed. The merge is biased toward **longer / non-empty** values.

---

## Module map

| Module | Role |
| --- | --- |
| `src/job_scraper/main.py` | Entry point. Loads config, opens HttpClient + Playwright, dispatches adapters, runs deep-scrape, applies filters, runs exports. |
| `src/job_scraper/config.py` | Dataclasses for `RunConfig`, `TargetConfig`, `ExportsConfig`. Loads YAML. |
| `src/job_scraper/http.py` | `HttpClient` with per-host token bucket, SQLite cache, proxy rotation, UA rotation, WAF detection. |
| `src/job_scraper/crawl.py` | `PlaywrightFetcher` context manager + the BFS link crawler. Draws its proxy from the client's pool, and returns `None` on a failed navigation rather than an empty DOM. |
| `src/job_scraper/browser.py` | The Playwright driver, owned by one dedicated thread. Its sync API is pinned to the greenlet that created it, so a second thread calling in raises `greenlet.error` no matter what lock is held — which is why every browser call from the deep-scrape pool used to fail silently. Also owns the stealth init script and the consent-wall selectors. |
| `src/job_scraper/transport.py` | The escalation ladder: `requests` → `curl_cffi` (real Chrome TLS/HTTP2 fingerprint) → stealth browser, with per-domain memory in SQLite. `classify()` is the part worth reading — it tells a block from a real 404 and from a 200 carrying a challenge. |
| `src/job_scraper/identity.py` | One coherent browser identity per host: UA, client hints, Accept-Language and TLS profile that all describe the same browser, salted by proxy so a new exit IP also means a new identity. |
| `src/job_scraper/extract.py` | JSON-LD / microdata / OpenGraph / heuristics extractor. Section parser EN/DE/FR/IT/NO. Salary regex. |
| `src/job_scraper/universal.py` | Smart-DOM extractor + HR contact miner + site-specific hooks (NAV `<dl>`, NAV `__next_f` adData, KS `.fact-card`, jobbsafari `__NEXT_DATA__`). |
| `src/job_scraper/deep_scrape.py` | Per-listing pipeline orchestrator. Throttle + retries + Playwright re-fetch + LLM-fallback hook. |
| `src/job_scraper/llm_adapter.py` | Anthropic Haiku integration. Condenses HTML, asks for selector map, caches per host in SQLite, tracks monthly budget. |
| `src/job_scraper/models.py` | `JobListing` dataclass (56 fields + `extras` dict). `merge()`, CSV column order, JSON ser/de. |
| `src/job_scraper/normalize.py` | URL canonicalization, junk-path filtering. |
| `src/job_scraper/adapters/*.py` | One file per source. Each implements `fetch_jobs(target, run_config, http) → List[JobListing]`. |
| `src/job_scraper/export/` | CSV / Google Sheets / Notion / Slack writers. `run_exports()` isolates each sink's failures. |
| `src/job_scraper/safe_xml.py` | Hardened XML parsing for untrusted sitemaps and feeds. |
| `src/job_scraper/csv_safe.py` | Neutralises spreadsheet formula injection on export, with a numeric exemption so `+41 44 …` stays readable. |
| `src/job_scraper/net_guard.py` | Refuses URLs resolving into private/link-local space. Enforced on every redirect hop, not just the first URL. |
| `src/harvestkit_ui/` | The control panel: a stdlib-only local web server (`server.py`), a subprocess runner that parses the run log into readable state (`runner.py`), and the fixed catalogue of commands it may run (`jobs.py`). |
| `src/job_scraper/config.py` | Strict YAML loader + `configs/` path resolver (`--config norway-big`). |

---

## Shared schema (56 fields + extras)

The `JobListing` dataclass is the contract between CLI and extension. Adding a field requires:

1. Update `src/job_scraper/models.py` (`JobListing` + `CSV_COLUMNS`)
2. Mirror in `extension/app/src/content/extractor.ts`
3. Update any extractor that knows where to find the value
4. Add a CHANGELOG entry noting the schema bump

For anything site-specific that doesn't fit the schema, use `listing.set_extra(key, value)`. Stored in `extras_json` column.

---

## Throttling & politeness

- **Per-host token bucket**: separate from global pacing — prevents accidental DoS on slow sites.
- **Proxy-aware throttle**: keys on `(host, proxy)` so the same host can be hit through multiple proxies in parallel.
- **WAF detector**: identifies CloudFront 403s + DataDome captchas + Cloudflare challenges. On hit:
  - Skip the response (don't poison cache)
  - Back off with exponential jitter
  - After N failures, route through next proxy
- **Respects `robots.txt`** when `obey_robots: true` (default).

---

## When to use Playwright vs HTTP

| Site type | Path |
| --- | --- |
| ATS w/ public JSON API | Direct HTTP, no JS needed |
| Schema.org JSON-LD in SSR HTML | Direct HTTP |
| Next.js / Nuxt SSR (jobbsafari, NAV) | Direct HTTP — `__NEXT_DATA__` is in the static markup |
| Hydrated SPA w/ client-only content (KS contact block) | Playwright re-fetch per detail page |
| TLS-fingerprinted WAF (Akamai, Cloudflare, Imperva) | `curl_cffi` rung — no header change reaches these; what they score is the ClientHello |
| JS challenge that resolves itself | Browser rung. The navigation may answer 403 or 405 and then render the real page, so `classify_rendered()` judges the DOM, not the first byte |
| DataDome / residential-only reputation | Often nothing headless will pass. Reported as `blocked_no_pages_seen`, not as "this company names nobody" |

Enable per-target via `use_playwright: true` in the YAML, or globally via
`run.use_playwright: true`. The ladder escalates on its own when
`escalate_on_block` is set, and remembers per domain which rung worked.

---

## Caching layers

| Layer | Path | TTL | Purpose |
| --- | --- | --- | --- |
| HTTP response (detail pages) | `.cache/http_cache.sqlite` | 24h default | Skip repeat fetches across runs |
| HTTP response (index/search URLs) | same file | 1h (`index_cache_ttl_seconds`) | A board's `/jobs` feed is a stable string whose contents change daily. Serving it from the long TTL is why `--only-new` returned nothing on a daily run |
| Transport memory | `.cache/transport_memory.sqlite` | forever | Which rung a domain needs, so it is not re-discovered per URL |
| LLM selectors | `.cache/llm_selectors.sqlite` | forever | Learned per-host CSS maps + monthly budget tracker |

Clear both: `rm -r .cache/` (or `make clean`).
The cache auto-purges entries that match the WAF/captcha signature — block pages never persist.

---

## Failure modes & recovery

| Symptom | Likely cause | Recovery |
| --- | --- | --- |
| Many 403s on one host | Burst above WAF threshold | Lower `deep_per_host_concurrency`, raise `deep_per_host_delay_seconds` |
| Empty descriptions on JS site | SPA hydrates client-only | Set `use_playwright: true` |
| New site returns 0 fields | No adapter, no schema | Enable `llm_fallback_enabled: true` + set `ANTHROPIC_API_KEY` |
| LLM giving wrong values | Site layout changed since cache | `rm .cache/llm_selectors.sqlite` to relearn |
| Process aborts mid-run | OS sleep / network drop | Re-run with cache enabled — picks up where it left off |
