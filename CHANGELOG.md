# Changelog

Notable changes to HarvestKit. No tagged releases yet — everything below lives on `main`.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Will adopt [SemVer](https://semver.org/spec/v2.0.0.html) starting at the first tag.

## [Unreleased]

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
