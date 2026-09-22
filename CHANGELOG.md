# Changelog

Notable changes to HarvestKit. No tagged releases yet — everything below lives on `main`.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Will adopt [SemVer](https://semver.org/spec/v2.0.0.html) starting at the first tag.

## [Unreleased]

### Added — a control panel, so nobody has to use a terminal

This is deployed onto employees' own laptops, most of which have no Python, no
Git and nobody who wants to learn either. Two additions between them remove the
terminal entirely.

- **`python run_panel.py`** (`src/harvestkit_ui/`) — a local web page that runs
  HarvestKit. Every CLI option is a labelled control with its reasoning beside
  it; the run streams live with listings/companies/people counting up and the
  `reachability:` line rendered rather than buried; finished CSVs are listed,
  previewed in a table and downloadable. It remembers the last form, so a daily
  run is "open it, press Start". Standard library only, deliberately: it is the
  thing that has to start when a dependency install has half-failed.
  - Bound to 127.0.0.1, a startup token on every request, and a Host-header
    check so DNS rebinding cannot reach that token.
  - **No command box.** A fixed catalogue (`jobs.py`) of typed, range-checked
    options. A submitted value can never become a flag, a path outside the
    project, or a shell string. A text box would be remote code execution the
    first time the port was reachable.
- **`scripts/install.ps1` / `scripts/install.sh`** — one command on a machine
  with nothing installed. Finds or installs Python for the user account (no
  administrator rights, and deliberately *not* prepended to PATH, so it cannot
  break anything else), builds the venv, installs patchright + chromium, writes
  a desktop shortcut, then proves the install by reading a dozen real European
  sites.
- **`tools/doctor.py`** — the health check that predicts whether this machine
  can produce leads: Python, dependencies, browser launch, writable folders,
  disk, config parsing, checkpoint integrity, spreadsheet-injection safety, a
  live run of the real extraction path, DNS, proxies, outbound port 25, and
  whether European company sites are readable from here. Exits non-zero on
  anything that would stop a run, so an install script can gate on it.

### Fixed — companies deleted from the funnel before anyone looked at them

`src/leadgen/company/domain.py` held four separate defects, and every one of
them removed a company rather than degrading it.

- **A bot wall read as "this company has no website".** The probe accepted a
  host only if `http.get()` returned a body, and `get()` returns `None` for
  403. Four of six European employer domains answer 403 to a plain request, so
  the company was dropped with its correct domain sitting in the hint. A host
  that answers *anything* exists; it is the crawl stage's job to get through
  the wall, not the seed's job to give up.
- **The mail domain was whatever subdomain the ad used.** `karriere.sap.com`
  became the company's mail domain, and career subdomains essentially never
  carry MX — live-checked: `sap.com` has MX, `karriere.sap.com`, `jobs.sap.com`,
  `jobs.zalando.de` and `careers.hellofresh.de` do not. Every generated address
  for that employer failed the MX gate. The host to crawl and the domain to
  mail are now separate values.
- **No check that the accepted site belonged to the company.** `company_name`
  was an unused parameter. The denylist was ~70 hosts, so karriere.at (the
  largest Austrian board), jobup.ch, indeed.de, stepstone.nl, glassdoor.de and
  myworkdaysite.com all passed as an employer's own site — and the cascade then
  mined the *board operator's* Impressum and filed its staff under the
  employer. Acceptance now needs corroboration: the domain stem spells the name
  (umlaut-aware, acronym-aware), or the page says so in its title/og:site_name,
  or the source stated it outright. Parked pages are rejected.
- **A stated `company_website` discarded because its homepage 403'd.** A seed
  API asserting the employer's site is stronger evidence than our ability to
  fetch its homepage from this IP.

### Fixed — the browser rung was never running at all

- **Playwright's sync API is pinned to the greenlet that created it.** Every
  call from a deep-scrape worker thread raised `greenlet.error`, and the raise
  was caught at DEBUG — so `use_playwright: true` looked enabled while fetching
  nothing, and a module-level lock could not have helped. New
  `src/job_scraper/browser.py` runs the driver on a dedicated owner thread and
  other threads post work to it. Verified live: four worker threads, four real
  pages, no error.
- **`PlaywrightFetcher` ignored the proxy pool entirely**, so the one fetch path
  used against the most hostile sites was the only one sending the operator's
  real address. It now draws from the same pool and reports success/failure
  back to it.
- **A failed navigation returned an empty `about:blank` DOM** that the HTTP
  cache then stored as a success for 24 h. It returns `None`.
- **Headless Chromium announced itself**: `navigator.webdriver` set,
  `userAgentData` still saying `HeadlessChrome` whatever the UA string claimed,
  no plugins, SwiftShader WebGL. An init script covers those; patchright is
  preferred when installed because it removes the `Runtime.enable` tell that no
  init script can reach.
- **A navigation status is not the rendered page.** A managed challenge answers
  403 or 405 and then solves itself — siemens.com answered 405 and rendered a
  megabyte of real page. `classify_rendered()` lets substantial, structured,
  non-challenge content overturn a *blocked* verdict, and only that verdict.
- Consent walls are dismissed in both browser paths from one shared selector
  list, in the languages this engine actually meets.

### Fixed — proxies, cookies and the cache

- **A host refusing us was charged to the proxy.** Three walled pages on one
  stubborn site cooled down every proxy in the pool, and the run then fell back
  to a direct connection — from the machine whose address was the whole reason
  the pool existed. 403/429/challenge is now `report_block(entry, domain)`, and
  only cools a proxy down when several *unrelated* domains refuse the same one.
  Connect/TLS/timeout failures still count directly.
- **`require_proxy: true`** makes a run refuse to fetch rather than silently
  connect directly when every proxy is cooling down.
- **The "per-proxy cookie jar" isolated nothing.** One shared `requests.Session`
  merges every jar it is handed into `session.cookies` and replays it through
  every exit — a stronger correlation signal than sharing the IP would have
  been. Each proxy now gets its own Session, and therefore its own jar and
  connection pool.
- **`bind://<address>`** entries in `run.proxies` open ordinary direct
  connections from a chosen local source address. On any host with a routed
  IPv6 /64 — Oracle's always-free tier, Hetzner, OVH — that is effectively
  unlimited free rotation that nobody else carries.
- **Index URLs were served from a day-old cache.** A board's `/jobs` feed is a
  stable string whose contents change daily, so `--only-new` legitimately found
  nothing on a daily run. Listing and search URLs now get their own short
  freshness ceiling (`index_cache_ttl_seconds`, default 1 h); detail pages keep
  the long TTL, which is where the cache earns its keep.

### Fixed — rows that were wrong rather than missing

- **The board operator's own support desk shipped as the recruiter's email on
  every row.** `service@jobcloud.ch` is the only address on a jobs.ch detail
  page, and the recruiter-flavour regex fired on the "job" inside "jobcloud",
  so 100% of a ~1,469-posting Swiss run carried JobCloud's customer-service
  address — and any campaign built from it mails them once per row. Mining is
  now scoped to the posting's own content, and operator mailboxes are rejected
  by host for every board this engine touches.
- **The site navigation shipped as a person's name.** "Recruiter Area Deutsch
  Français English Login" — the exact string an earlier fix claimed to have
  eliminated. That guard existed in `extract.py` only; `mine_contacts` had
  none, and `deep_scrape` calls it on every page.
- **An Impressum's `info@` was attributed to the named director**, and because
  `/impressum` is crawled before `/team` and the merge was first-wins, it beat
  the real `a.schmidt@` found a page later. The domain then had no personal
  address left, pattern inference returned nothing, and *everyone* at that
  employer fell back to `first.last` on a domain whose real format was
  `f.last`. Attribution now requires the address to echo the name, and the
  merge ranks candidates instead of trusting arrival order.
- **Pattern anchors were drawn from raw, unattributed hits**, so one
  mis-attributed mailbox or one un-split "Dana Aleff, Erik Mueller" line
  silently destroyed inference for the whole domain.
- **A LinkedIn URL whose slug spells somebody else** is dropped rather than
  shipped — nothing downstream ever checked it.
- **National-format European phone numbers were all rejected.** The
  "un-separated digit blob is an ID" rule ran *after* normalisation, at which
  point every national number is a bare digit blob. It now runs on the raw
  substring, where it means something.

### Fixed — the run that reports success having harvested nothing

- **A run whose entire harvest failed still exported the old checkpoint and
  exited 0.** `all_leads()` reads every lead ever banked, so a day when every
  seed API 403'd and every company served a bot wall wrote a full,
  successful-looking CSV of yesterday's people and returned 0. The operator
  shipped the identical file again. There is now exit code **4** with a stated
  reason, and no export.
- **`tools/verify_leads.py` passed a zero-row CSV.** Every check in it is a
  per-row aggregation, so an empty file satisfied all of them — 0 missing
  fields, 0 duplicates, 0 role accounts — and printed "All hard guarantees
  hold." That is exactly the file a bot-walled machine produces. `--min-rows`
  defaults to 1, and both daily scripts pass it.
- **`daily.ps1` and `setup.ps1` died on the first log line under Windows
  PowerShell 5.1** — the shell a stock Windows box actually has. `2>&1` on a
  native command turns stderr into ErrorRecords, and `$ErrorActionPreference =
  'Stop'` makes the first one terminating; Python logs to stderr. No CSV, no
  verification, exit 1 instead of the documented 0/2/3.
- **The runbook's mandatory smoke run made zero network requests.** Its command
  passed no seed flag, and every seeding branch is gated on one, so an operator
  on a fully bot-walled network got byte-identical output to one on a perfect
  network.
- **Its pre-flight health check called a 403 a pass** — the exact status that
  guarantees the run yields nothing.

### Fixed — the extension

- **The CLI and the extension computed different ids for the same URL.** The
  extension ran the string through `new URL().toString()`, which punycodes IDN
  hosts, percent-encodes accented path bytes and re-serialises the query;
  Python's `urlparse` did none of it. Both write into the same `id` column, so
  every German, French and Nordic posting with an accented slug became two
  rows. Both sides now implement one explicitly-defined canonical form, and
  `extension/tests/canonical-vectors.json` is asserted by **both** pytest and
  `node --test` so they cannot drift again.
- **"Scrape ALL pages" never paginated.** The dashboard sent `maxPages: 100`
  and toasted "paginate → collect → deep-scrape"; the handler sent one
  `EXTRACT_LIST` and never read `msg.maxPages`. The content script had
  implemented `AUTO_PAGINATE` all along and nothing ever sent it — a 36-page
  board returned page one and the other 875 postings were silently never
  requested.
- **The success toast fired before a single page had been fetched.** The crawl
  is deliberately not awaited, and the handler reported `deepScraped =
  urls.length` on the next line — so a run in which every visit 403'd had
  already told the operator "25/25 deep-scraped". It now reports what is true:
  how many were queued, and the run to watch.
- **A killed MV3 service worker lost the whole crawl queue.** The queue lived
  only in worker memory and nothing re-read the run rows on the way back up, so
  a 900-URL crawl 400 in lost the other 500 permanently and its row span as
  "running" forever. Runs now persist their unfinished URLs and options, and
  `resumeInterruptedRuns()` drains them on `onStartup` and on worker install.
- **CSV formula injection**, the same defect the Python side had already fixed,
  with the same phone-number exemption so `+41 44 123 45 67` stays readable.
- **`mergeJobs` used longest-string-wins for every field**, letting a sentence
  overwrite an ISO date and a tracking-laden URL beat a clean one. It now
  mirrors Python's `_FIRST_WINS_FIELDS`.

### Fixed — seeds and dispatch

- **SmartRecruiters was capped at ~98 rows per keyword** out of up to 40,422.
  The search endpoint clamps `limit` and ignores `offset`, `page` and
  `pageSize` outright — verified live, the same 99 rows come back at every
  offset — and the log printed that 99 as though it were the answer. The
  shortfall is now logged, and each employer the search names is expanded
  through the documented per-company postings API, which does paginate.
  Measured: one keyword went from 23 listings to 1,653.
- **Greenhouse's real company name was overwritten with the board slug**, so
  leads shipped under "addepar1" and that string was fed to the domain guesser,
  where the account-disambiguation digit NXDOMAINs on every TLD.
- **Lever states an ISO country on every posting and the seed threw it away**,
  so any city not in `geo.CITY_NAMES` resolved to `""` and the company was
  dropped by `--countries`.
- **Adapter dispatch matched hostnames by substring**, so
  `smartrecruiters.com.evil.example` and `arbeitsagentur.de.phish.tld` were
  routed to those adapters. Matching is now exact-or-suffix on labels.
- **One listing raising aborted the whole deep-scrape batch** and the entire
  run, discarding every job already scraped.

### Changed
- `tools/proxy_sources.py --print-setup` rewritten around what actually works
  and costs nothing: not getting fingerprinted in the first place, splitting
  work across the laptops this is already deployed on, an IPv6 /64, a free-tier
  VM over `ssh -D`, Cloudflare WARP, then Tor and public lists last.
- Test suite 453 → 682 (Python) and 20 → 53 (extension).

### Earlier in this cycle

### Fixed — the lead famine

A run over European company domains was returning a fraction of the people it
should, and reporting the shortfall as "these companies name nobody". It was
not an extraction problem. Measured live over twelve EU company domains before
any of this landed: **five were dropped without a single page ever being
requested.**

- **robots.txt was fetched with bare `urllib`** — outside the session, outside
  the proxy pool, with no browser headers. Two consequences. The operator's
  real IP touched every host in the run no matter how carefully it was
  proxied, which is fatal for anyone who cannot expose their own address. And
  because a naked urllib request is the most blockable thing the engine could
  send, robots.txt was refused *more often than the page it was gating*.
- **A refusal was then read as policy, backwards to the RFC.** RFC 9309
  §2.3.1.3 says a 4xx means the file is unavailable and "the crawler MAY access
  any resources"; §2.3.1.4 says 5xx means disallow. The code had 4xx
  disallowing everything and 5xx allowing everything — both inverted. A
  Cloudflare 403 on `/robots.txt` therefore blackholed the entire host.
  hellofresh.de, getyourguide.com and zalando.de were all being written off
  this way; none of them publishes a robots.txt that disallows anything.
- **A 403 on a page was terminal.** `get()` returned `None` with no retry, no
  proxy rotation and no escalation, despite the class docstring promising a
  retry layer. One bot wall lost the company for the rest of the run.
- **A block and an empty page were the same value.** Both arrived downstream as
  `None`, so `pipeline.py` filed both under `no_person_found` — and the runbook
  sent the reader to debug the team-page parser, which was innocent.

### Added — anti-blocking, egress and safety
- `src/job_scraper/transport.py` — an escalation ladder. Plain `requests`, then
  a real browser TLS/HTTP2 fingerprint via `curl_cffi`, then optionally a
  stealth browser, with per-domain memory in SQLite so a domain pays the
  discovery cost once. Measured on sixteen EU domains, the impersonation rung
  alone recovered three that `requests` could not fetch at all — what Akamai
  and Cloudflare score is the ClientHello, which no header change reaches.
- `classify()` — tells a bot wall from a 404 from a challenge page served with
  HTTP 200. A soft block scored as success is how a run comes to report
  thousands of companies that "name nobody".
- `src/job_scraper/identity.py` — one coherent browser per host. The previous
  code drew a random User-Agent *per request* and attached
  `Sec-CH-UA: Chromium 120` regardless of what it drew, so one host saw Safari
  announce itself as Chrome over a single connection. It also sent
  `Sec-Fetch-Site: same-origin` on first contact, which no real browser can do.
  Accept-Language now follows the target country.
- `src/job_scraper/csv_safe.py` — neutralises spreadsheet formula injection in
  both CSV exporters. Both runbooks end with "import to Google Sheets", and
  every value came off somebody else's page. European phone numbers starting
  `+` are deliberately left untouched.
- `tools/check_egress.py` — answers "can this machine read European company
  sites", which is the question that matters, rather than "did something
  respond". A 403 responds and yields nothing.
- `tools/proxy_sources.py` — replaces `tools/fetch_free_proxies.py`. Validates
  that a proxy is anonymous, TLS-clean and CONNECT-capable before keeping it,
  and `--print-setup` documents free egress you control. The tool it replaces
  kept anything that answered.
- `blocked_no_pages_seen` and `unreachable` funnel counters, plus a
  `reachability:` summary line that names egress as the cause when it is.
- `docs/AUDIT-2026-09.md` — 301 findings from a 12-subsystem parallel audit.
- `docs/ANTIBOT-RESEARCH-2026-09.md` — 65 open-source tools assessed.

### Fixed — data that was wrong rather than missing
- `team.py` handed **the first person's email address and LinkedIn profile to
  everyone else on the page**. `_card_of` climbs three levels, so on a flat
  team page the "card" wraps the whole team and `card.find(mailto)` returned
  one address for all of them. Contact details are now scoped to the span
  between one person's heading and the next.
- A blind `first.last` guess with no anchor behind it was exported as
  `email_status = "verified"`. The SMTP probe says something about the domain,
  never about the address; a buyer filtering to `verified` was getting guesses.
- **Every non-250 SMTP reply was read as proof the domain validates mailboxes**
  — greylisting, rate limits and IP blocklistings included. Only a genuine
  "no such mailbox" counts now, and an inconclusive probe is no longer cached
  for the rest of the run.
- SSRF: a scraped URL that answered `302 Location: http://169.254.169.254/…`
  was followed into cloud metadata, because only the first URL was guarded.
  Redirects are now followed by hand and every hop is checked.

### Fixed — Europe specifically
- `universal.PHONE_RX` matched **no national-format European phone number at
  all**. Its national branch required every repeated digit group to begin with
  a zero, where only the trunk prefix does — so "030 12345678", "044 123 45 67"
  and "01 42 68 53 00" all failed, which is exactly how an Impressum prints a
  number.
- `SALARY_RX` required the currency *before* the amount, so "45.000 EUR" — the
  ordinary European form — never parsed. `_NUM` also demanded a separator after
  three digits, truncating "EUR 60000 - 80000" to 600–800, and the `k` in
  "45k" was matched and discarded, exporting a salary of 45.
- German annual salaries were labelled hourly, because the unit test read
  `"hr" in p` and "jahr" contains "hr". German monthly pay was labelled annual
  for the mirror-image reason.
- `_looks_like_job_page` scored French, Italian, Spanish and Dutch postings at
  zero, so `universal_extract` returned `None` for most of the continent.
- `Ansprechpartner` — simply the German word for a contact person, and the
  label above the recruiter's name on a large share of DACH ads — matched
  nothing in the contact miner.
- finn.no: `published=30` answers **HTTP 404**; it accepts only 1, 2, 3 and 7.
  Three shipped configs asked for 30 and harvested nothing. Now clamped with a
  warning, and the configs corrected.
- NAV: `?page=` is silently ignored by arbeidsplassen — pages 1, 2 and 3
  returned byte-identical results — so every NAV target stopped at 25 ads
  regardless of `max_pages`. It paginates on `from=<row offset>`. Measured
  after the fix: 25 → 70 listings over three pages.

### Changed — anti-blocking and egress
- `obey_robots` now defaults to `false`, at the operator's instruction.
  `docs/OPERATOR-TERMS.md` records the decision and what still binds.
- `net_guard` moved to `src/job_scraper/` so the transport layer can guard
  redirect hops with it. `leadgen.net_guard` re-exports it unchanged.
- New dependencies: `curl_cffi` (the impersonation rung) and `PySocks`
  (`socks5://` proxies, which the README documented without declaring).

### Added
- `configs/` tree gathering every YAML (`configs/`, `configs/regions/`, `configs/sites/`) with a resolver so `--config norway-big` works from any directory. See `configs/README.md`.
- Strict config validation: unknown keys, missing required keys and scalars where lists belong are all rejected by name instead of silently ignored.
- `src/job_scraper/safe_xml.py` — XXE- and billion-laughs-hardened XML parsing for sitemaps and the Personio feed.
- Typed general-mode config (`src/general_scraper/config.py`) validating selector keys.
- Extension: `lib/sha1.ts`, `lib/canonicalUrl.ts`, `content/titleClean.ts`, `background/keepalive.ts`, `background/scheduler.ts`.
- Test suite grows 6 → 53 Python tests plus 16 extension tests, including golden fingerprints that pin the CLI and the extension to the same digest.
- CI jobs validating every shipped config and typechecking + testing the extension.
- NAV `__next_f` adData parser. Pulls `expires`, `employer.{name,sector,homepage}`, `locationList[]`, `workLanguages`, `reference`, `engagementType` from NAV's Next.js streaming payload — covers fields the HTML `<dl>` doesn't expose.
- jobbsafari.no dedicated adapter parsing `__NEXT_DATA__` `props.pageProps.jobEntry`.
- thehub.io detail-page handling: title-prefix stripping ("The Hub | <Title> | <Company>") and "Remote" location synthesis when `jobLocationType: TELECOMMUTE`.
- LLM-fallback adapter (Anthropic Haiku) with per-host SQLite selector cache and monthly USD budget cap.
- Playwright wiring at adapter + deep-scrape layers. Per-target opt-in via `use_playwright: true`. karrierestart detail pages auto re-fetched via Playwright to surface JS-rendered contact blocks.
- Top-5 Norway config (`config.norway-top5.yaml`) with URL filter codes verified by live DOM probe.
- finn.no, NAV (arbeidsplassen.nav.no), karrierestart.no dedicated adapters.
- karrierestart deadline extraction via `.jobad-deadline-date` span.
- Norwegian seniority regex (seniorrådgiver, teamleder, direktør, praktikant) with title-based fallback.
- Norwegian-aware HR contact miner and name/role splitter.
- Per-host token bucket throttle, proxy rotation pool, per-proxy cookie jars, WAF/captcha detector.
- Universal smart-DOM extractor for sites without JSON-LD.
- General mode for Yelp-style business directories (LocalBusiness / Restaurant / Place schema).
- 10 ATS adapters: Greenhouse, Lever, Ashby, Workday, Personio, Recruitee, Workable, SmartRecruiters, Arbeitsagentur, jobs.ch.
- Deep-scrape pipeline: JSON-LD → universal smart-DOM → contact miner → optional LLM fallback.
- EN/DE/FR/IT/NO JD section parser.
- 60-field shared schema between Python CLI and Chrome MV3 extension.
- Chrome MV3 extension with side-panel UI, IndexedDB persistence, and bulk-crawl orchestrator.
- Playwright fallback for JS-heavy sites.
- `pyproject.toml` build config, GitHub Actions CI (lint + test + extension build + CodeQL), Dependabot, issue + PR templates, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, Dockerfile, Makefile.

### Changed
- Fingerprinting unified. `JobListing.fingerprint()` is the single implementation; `normalize.job_fingerprint()` delegates to it and the extension reproduces it byte for byte.
- `merge()` keeps first-wins for dates, IDs and URLs; longest-wins now applies only to prose.
- Tech-stack detection collapses ~330 regex passes into one alternation — same hits, 17x faster on a 200 KB page.
- `robots.txt` fetching gains a 6 s timeout, a thread-safe cache with negative caching, and RFC 9309 handling for 401/403.
- Playwright calls are serialized behind a process-wide lock; the sync API is not thread-safe and was being called from every deep-scrape worker.
- Adapter failures name the target and log the exception instead of one generic line.
- `tools/extract_from_cache.py` is a general argparse CLI rather than a jobs.ch-specific script.
- Docker ships the `configs/` tree in the image, so `--config example` needs no mount.
- `deep_scrape` pipeline reordered: always run JSON-LD → universal smart-DOM → contact miner → LLM fallback. Previously short-circuited after JSON-LD, which lost karrierestart's DOM-only fields.
- karrierestart company extraction prefers `.jobad_company_logo img[alt]` and title-prefix over `.company-desc <a>` (which often pointed to social links).
- `bad_label_rx` extended to reject site-nav junk words (Partnere, Annonsere, Nyheter, Profil, Studier, etc.) so the contact miner doesn't capture them as recruiter names.

### Removed
- `extension/src/` — the pre-rewrite v1 JavaScript tree. Unreferenced by `manifest.json` and `build.mjs`, and a divergent duplicate of `app/src/`. Its two test files were the only thing `npm test` ran, so the code that actually ships had no coverage.
- Root-level `config.yaml` — a byte-identical duplicate of `config.example.yaml`.

### Fixed
- **Five ATS adapters returned nothing on every run.** ashby, lever, personio, recruitee and workable constructed `JobListing(remote=...)`; `remote` is a read-only property, so each raised `TypeError` that was swallowed as a generic "Adapter failed".
- **English JD sections never parsed.** `SECTION_PATTERNS` closed each stem with `\b`, so "Requirements", "Responsibilities" and "Qualifications" all failed to match and those fields came out empty on most English postings.
- `locations.exclude` was a no-op unless `include` was also set, and the `allow_remote` escape hatch was unreachable dead code.
- Heuristics now scan the JSON-LD description as well as the DOM; on ATS pages the whole job body lives there and never reaches the DOM.
- `docker-compose.yml` mounted `./configs`, a directory that did not exist — every documented Docker invocation failed to find its config.
- Phone regex no longer truncates four-group numbers (`+47 95 83 21 97`).
- `get_adapter()` falls back to the shared generic instance, restoring `GenericAdapter`'s recursion guard.
- `Retry-After` in HTTP-date form no longer raises out of the retry loop.
- One failing export sink no longer skips the sinks queued behind it; CSV writes are atomic via `os.replace()`.
- Crawl tracked visited but not queued URLs, so a page linked from ten others was enqueued ten times.
- General-mode pagination terminates on sites that clamp an out-of-range offset back to page 1.
- LLM adapter no longer holds its cache lock across the network call, which serialized every worker behind one slow request.
- Extension: MV3 service worker is kept alive for the duration of a bulk crawl; a lost-wakeup race in the per-host scheduler could stall a queue indefinitely; parallel run-row writes lost counter increments.
- CI lint had been failing (433 ruff errors, 28 unformatted files) and is green again.
- finn.no JSON-LD wrapper key (`script:ld+json`) unwrap for JobPosting blocks.
- jobbsafari pagination uses `?page=N`, not `?side=N`.
- Phone regex rejects 9-12 raw digit blobs (finnkode IDs) and date-like strings.

### Security
- Remote XML (sitemaps, the Personio feed) was parsed with `xml.etree.ElementTree`, which expands internal entities. A hostile or compromised feed could hand us a billion-laughs bomb and exhaust memory. Parsing now goes through `safe_xml`, which prefers `defusedxml` and otherwise refuses any document declaring a DOCTYPE or ENTITY, with a 16 MB cap on either path.
- `robots.txt` is honoured on `head()` as well as `get()`, and a 401/403 on `/robots.txt` is treated as fully disallowed per RFC 9309.

[Unreleased]: https://github.com/BluOryn/HarvestKit/commits/main
